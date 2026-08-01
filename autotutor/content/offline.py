"""The fully offline lesson composer.

Builds a coherent monologue out of the bundled corpus: a level-appropriate
opening line, a themed passage, optional extra sentences joined with
connectives, and a closing line.  Nothing here touches the network.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Deque, List, Optional, Tuple

from ..models import GenerationRequest, Lesson, target_sentence_count
from ..topics import CUSTOM_TOPIC, RANDOM_TOPIC, TOPICS, Topic, get_topic
from .builder import build_lesson
from .corpus import (
    CorpusSentence,
    Passage,
    TopicCorpus,
    available_topic_ids,
    connectives,
    frame_sentences,
    load_topic,
    match_topic,
    nearest_levels,
)

_RECENT_LIMIT = 12


class OfflineGenerator:
    """Composes lessons from the bundled corpus."""

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self.rng = rng or random.Random()
        self._recent: Deque[str] = deque(maxlen=_RECENT_LIMIT)

    # -- topic selection ---------------------------------------------------
    def resolve_topic(self, request: GenerationRequest) -> Tuple[str, List[str]]:
        """Return ``(topic_id, warnings)`` for the requested topic."""
        topic_ids = list(available_topic_ids())
        warnings: List[str] = []
        if not topic_ids:
            return "", ["找不到内置语料库，请检查安装是否完整。"]

        query = request.topic_query
        if request.topic == RANDOM_TOPIC or (not request.topic and not query):
            return self.rng.choice(topic_ids), warnings

        if request.topic == CUSTOM_TOPIC or query:
            matched = match_topic(query, topic_ids)
            if matched:
                return matched, warnings
            choice = self.rng.choice(topic_ids)
            topic = get_topic(choice)
            warnings.append(
                f"离线语料中没有「{query}」这个主题，已改用最接近的内置主题"
                f"「{topic.name_zh if topic else choice}」。联网模式可以直接搜索任意主题。"
            )
            return choice, warnings

        if request.topic in topic_ids:
            return request.topic, warnings

        matched = match_topic(request.topic, topic_ids)
        if matched:
            return matched, warnings
        return self.rng.choice(topic_ids), warnings

    # -- composition -------------------------------------------------------
    def _pick_passage(self, corpus: TopicCorpus, level: str) -> Optional[Passage]:
        for candidate_level in nearest_levels(level):
            options = corpus.passages_for(candidate_level)
            if not options:
                continue
            fresh = [
                p for p in options
                if f"{corpus.id}:{p.level}:{p.title_ja}" not in self._recent
            ]
            passage = self.rng.choice(fresh or options)
            self._recent.append(f"{corpus.id}:{passage.level}:{passage.title_ja}")
            return passage
        return None

    def _extra_pool(self, corpus: TopicCorpus, level: str, wanted: int) -> List[CorpusSentence]:
        pool: List[CorpusSentence] = []
        for candidate_level in nearest_levels(level):
            items = corpus.extras_for(candidate_level)
            self.rng.shuffle(items)
            pool.extend(items)
            if len(pool) >= wanted:
                break
        return pool[:wanted]

    def _frame(self, kind: str, level: str, topic: Optional[Topic]) -> Optional[Tuple[str, str]]:
        options = frame_sentences(kind, level)
        if not options:
            return None
        chosen = self.rng.choice(options)
        name_ja = topic.name_ja if topic else "日本語"
        name_zh = topic.name_zh if topic else "日语"
        ja = chosen.get("ja", "").replace("{topic}", name_ja).replace("{topic_zh}", name_zh)
        zh = chosen.get("zh", "").replace("{topic_zh}", name_zh).replace("{topic}", name_ja)
        return (ja, zh) if ja else None

    def generate(self, request: GenerationRequest) -> Lesson:
        if request.seed is not None:
            self.rng.seed(request.seed)

        topic_id, warnings = self.resolve_topic(request)
        corpus = load_topic(topic_id) if topic_id else None
        topic = TOPICS.get(topic_id)
        level = request.level

        if corpus is None:
            return build_lesson(
                [("すみません、教材データが読み込めませんでした。", "抱歉，未能读取教材数据。")],
                level=level,
                topic=topic_id,
                source="offline",
                source_label="离线语料库",
                warnings=warnings + ["语料文件缺失或损坏。"],
            )

        total = target_sentence_count(request.length)
        opener = self._frame("openers", level, topic)
        closer = self._frame("closers", level, topic)
        frame_count = int(bool(opener)) + int(bool(closer))
        body_target = max(2, total - frame_count)

        passage = self._pick_passage(corpus, level)
        body: List[Tuple[str, str]] = []
        if passage:
            body.extend((s.ja, s.zh) for s in passage.sentences)

        if len(body) < body_target:
            extras = self._extra_pool(corpus, level, body_target - len(body))
            joiners = connectives(level)
            for extra in extras:
                joiner = self.rng.choice(joiners) if joiners else ""
                ja = extra.ja
                if joiner and not ja.startswith(joiner):
                    ja = joiner + ja
                body.append((ja, extra.zh))

        # Still short (very long lessons): borrow another passage of a nearby level.
        while len(body) < body_target:
            extra_passage = self._pick_passage(corpus, level)
            if not extra_passage:
                break
            added = [(s.ja, s.zh) for s in extra_passage.sentences]
            if not added:
                break
            body.extend(added)
            if len(self._recent) >= _RECENT_LIMIT:
                break

        body = body[:body_target]

        pairs: List[Tuple[str, str]] = []
        if opener:
            pairs.append(opener)
        pairs.extend(body)
        if closer:
            pairs.append(closer)

        title_ja = passage.title_ja if passage else (topic.name_ja if topic else "")
        title_zh = passage.title_zh if passage else (topic.name_zh if topic else "")

        return build_lesson(
            pairs,
            level=level,
            topic=topic_id,
            title_ja=title_ja,
            title_zh=title_zh,
            source="offline",
            source_label="离线语料库（内置）",
            vocab=corpus.vocab_for(level) or corpus.vocab_for(nearest_levels(level)[0]),
            warnings=warnings,
        )


_DEFAULT = OfflineGenerator()


def generate(request: GenerationRequest) -> Lesson:
    """Module-level helper using a shared generator (keeps the anti-repeat history)."""
    return _DEFAULT.generate(request)
