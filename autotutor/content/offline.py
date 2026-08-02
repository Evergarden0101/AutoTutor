"""The fully offline lesson composer.

Builds a coherent talk out of the bundled corpus and keeps adding material
until the requested narration length is reached: an opening line, one or more
themed passages joined by spoken transitions, and a closing line.  Nothing
here touches the network.

For the longer presets a single passage is not enough, so the composer widens
its search in a deliberate order - more passages on the same topic, then
neighbouring levels of the same topic, then a related topic - and records in
``lesson.warnings`` whenever it had to step outside the requested level.

The requested register (conversational or written) is a preference, not a
filter: there is no conversational material above N3, so a spoken request at
N1 quietly falls back to the polite passages and says so.  Whichever way it
lands, the opener, transitions and closer are chosen to match the body that
was actually collected - a casual talk that opens with みなさん、こんにちは
sounds like two different speakers spliced together.
"""

from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Sequence, Tuple

from ..models import (
    REGISTER_AUTO,
    REGISTER_SPOKEN,
    GenerationRequest,
    Lesson,
    estimate_seconds,
    target_seconds,
)
from ..topics import CUSTOM_TOPIC, RANDOM_TOPIC, TOPICS, Topic, get_topic
from .builder import build_lesson
from .corpus import (
    REGISTER_NEUTRAL,
    CorpusSentence,
    Passage,
    TopicCorpus,
    available_topic_ids,
    connectives,
    frame_sentences,
    infer_register,
    load_topic,
    match_topic,
    nearest_levels,
)

_RECENT_LIMIT = 24

Pair = Tuple[str, str]

_COMPOUND_RE = re.compile(r"[一-鿿]{2,}")
# Measured over the bundled corpus: unrelated passages on the same topic share
# almost nothing (median 0.00, p90 0.15), while the conversational retelling of
# a passage overlaps its polite twin at 0.32-0.39. Anything above this is the
# same story told twice, which is the opposite of a lesson that develops.
_OVERLAP_LIMIT = 0.25
# Close enough to the target that widening would cost more than it gains.
_CLOSE_ENOUGH = 0.9


def _topic_terms(pairs: Sequence[Pair]) -> set:
    """The multi-kanji compounds a block is about."""
    return set(_COMPOUND_RE.findall("".join(ja for ja, _ in pairs)))


def _overlap(left: set, right: set) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass
class _Block:
    """A run of sentences that belongs together, plus how to introduce it."""

    pairs: List[Pair] = field(default_factory=list)
    topic_id: str = ""
    level: str = ""
    title_ja: str = ""
    title_zh: str = ""
    register: str = REGISTER_NEUTRAL
    needs_transition: bool = False

    @property
    def seconds(self) -> float:
        return estimate_seconds([ja for ja, _ in self.pairs])


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
        return (matched or self.rng.choice(topic_ids)), warnings

    # -- building blocks ---------------------------------------------------
    def _key(self, corpus_id: str, passage: Passage) -> str:
        return f"{corpus_id}:{passage.level}:{passage.title_ja}"

    def _passage_blocks(
        self,
        corpus: TopicCorpus,
        level: str,
        exclude: set,
        register: str = REGISTER_AUTO,
    ) -> List[_Block]:
        """Every unused passage for ``level``, best register first.

        ``passages_for`` returns the requested register ahead of the neutral
        fallback, so the shuffle has to stay inside each group or the
        preference is thrown away.
        """
        options = [
            p for p in corpus.passages_for(level, register)
            if self._key(corpus.id, p) not in exclude
        ]
        wanted = [p for p in options if p.register == register]
        rest = [p for p in options if p.register != register]
        self.rng.shuffle(wanted)
        self.rng.shuffle(rest)
        return [
            _Block(
                pairs=[(s.ja, s.zh) for s in passage.sentences],
                topic_id=corpus.id,
                level=passage.level,
                title_ja=passage.title_ja,
                title_zh=passage.title_zh,
                register=passage.register,
            )
            for passage in wanted + rest
            if passage.sentences
        ]

    def _extras_block(self, corpus: TopicCorpus, level: str) -> Optional[_Block]:
        """Loose sentences for ``level``, joined with connectives."""
        items: List[CorpusSentence] = corpus.extras_for(level)
        if not items:
            return None
        self.rng.shuffle(items)
        joiners = connectives(level) or [""]
        pairs: List[Pair] = []
        for extra in items:
            joiner = self.rng.choice(joiners)
            ja = extra.ja
            if joiner and not ja.startswith(joiner):
                ja = joiner + ja
            pairs.append((ja, extra.zh))
        return _Block(
            pairs=pairs,
            topic_id=corpus.id,
            level=level,
            register=infer_register([ja for ja, _ in pairs]),
        )

    def _transition(
        self,
        level: str,
        topic: Optional[Topic],
        title: str,
        register: str = REGISTER_NEUTRAL,
        used: Optional[set] = None,
    ) -> Optional[Pair]:
        options = frame_sentences("transitions", level, register)
        if not options:
            return None
        # Without replacement: a long lesson has ten seams, and hearing
        # 続いて、別の角度から… three times is worse than hearing it once. When
        # the pool runs out, start again but never straight back onto the line
        # that was just used.
        if used is not None:
            fresh = [o for o in options if o.get("ja") not in used]
            if not fresh:
                last = next(reversed(list(used)), None) if used else None
                used.clear()
                fresh = [o for o in options if o.get("ja") != last] or options
            options = fresh
        chosen = self.rng.choice(options)
        if used is not None:
            used.add(chosen.get("ja"))
        name_ja = topic.name_ja if topic else "この話題"
        name_zh = topic.name_zh if topic else "这个话题"
        ja = (chosen.get("ja", "")
              .replace("{topic}", name_ja)
              .replace("{title}", title or name_ja))
        zh = (chosen.get("zh", "")
              .replace("{topic_zh}", name_zh)
              .replace("{title}", title or name_zh))
        return (ja, zh) if ja else None

    def _frame(
        self,
        kind: str,
        level: str,
        topic: Optional[Topic],
        register: str = REGISTER_NEUTRAL,
    ) -> Optional[Pair]:
        options = frame_sentences(kind, level, register)
        if not options:
            return None
        chosen = self.rng.choice(options)
        name_ja = topic.name_ja if topic else "日本語"
        name_zh = topic.name_zh if topic else "日语"
        ja = chosen.get("ja", "").replace("{topic}", name_ja).replace("{topic_zh}", name_zh)
        zh = chosen.get("zh", "").replace("{topic_zh}", name_zh).replace("{topic}", name_ja)
        return (ja, zh) if ja else None

    # -- collection --------------------------------------------------------
    def _collect_blocks(
        self,
        corpus: TopicCorpus,
        level: str,
        budget: float,
        warnings: List[str],
        register: str = REGISTER_AUTO,
        avoid_repeats: bool = True,
    ) -> List[_Block]:
        """Gather blocks until ``budget`` seconds of speech are covered."""
        used: set = set()
        blocks: List[_Block] = []
        total = 0.0

        covered: List[set] = []
        # Widening costs the learner something - a level warning, or material
        # from another topic - so it is only worth it for a real shortfall.
        # Anything above this is inside the tolerance _trim_to_budget allows,
        # and the extra block would mostly be trimmed away again.
        enough = budget * _CLOSE_ENOUGH

        def take(candidates: List[_Block], mark_transition: bool) -> None:
            nonlocal total
            for block in candidates:
                if total >= budget:
                    return
                # Every topic has a conversational retelling of its polite
                # passage. Playing both means saying the same thing twice, so
                # whichever the register preference picked first wins.
                terms = _topic_terms(block.pairs)
                if any(_overlap(terms, seen) > _OVERLAP_LIMIT for seen in covered):
                    continue
                covered.append(terms)
                if block.title_ja:
                    used.add(self._key(block.topic_id, Passage(
                        block.level, block.title_ja, block.title_zh, [])))
                    self._recent.append(f"{block.topic_id}:{block.level}:{block.title_ja}")
                block.needs_transition = mark_transition and bool(blocks)
                blocks.append(block)
                total += block.seconds

        asked = register if register != REGISTER_AUTO else ""
        stepped_out = False

        def fresh_first(blocks: List[_Block]) -> List[_Block]:
            """Demote passages the learner heard recently, keeping order otherwise."""
            return sorted(blocks, key=lambda b: (
                avoid_repeats and self._key(b.topic_id, Passage(
                    b.level, b.title_ja, b.title_zh, [])) in self._recent,
            ))

        def in_register(blocks: List[_Block]) -> List[_Block]:
            return [b for b in blocks if b.register == asked] if asked else blocks

        # 1. Passages at the requested level, in the requested register.
        preferred = self._passage_blocks(corpus, level, used, register)
        take(fresh_first(in_register(preferred)), mark_transition=True)

        # 2. The same register one level away, before giving up on the voice.
        #    A casual talk that switches to です・ます halfway sounds like two
        #    different people; one level off with a consistent voice does not.
        if asked and total < enough:
            for candidate_level in nearest_levels(level)[1:3]:
                if total >= budget:
                    break
                more = in_register(
                    self._passage_blocks(corpus, candidate_level, used, register)
                )
                if more:
                    stepped_out = True
                    take(fresh_first(more), mark_transition=True)

        # 3. Whatever else this level has - the neutral です・ます fallback.
        if total < enough:
            take(fresh_first(self._passage_blocks(corpus, level, used, register)),
                 mark_transition=True)

        # 4. Neighbouring levels of the same topic, any register - still whole
        #    passages, so the lesson keeps reading as connected prose.
        if total < enough:
            for candidate_level in nearest_levels(level)[1:]:
                if total >= budget:
                    break
                more = self._passage_blocks(corpus, candidate_level, used, register)
                if more:
                    stepped_out = True
                    take(more, mark_transition=True)
        if stepped_out:
            warnings.append(
                f"所选长度超出了「{level}」级别在这个主题下的语料量，"
                "已补充相邻级别的同主题内容。想要严格贴合级别，请选择更短的长度。"
            )

        # 3. A different topic, same level.
        if total < enough:
            others = [t for t in available_topic_ids() if t != corpus.id]
            self.rng.shuffle(others)
            added_topics: List[str] = []
            for topic_id in others:
                if total >= budget:
                    break
                other = load_topic(topic_id)
                if not other:
                    continue
                more = self._passage_blocks(other, level, used, register)
                if more:
                    added_topics.append(topic_id)
                    take(more[:1], mark_transition=True)
            if added_topics:
                names = "、".join(
                    (TOPICS[t].name_zh if t in TOPICS else t) for t in added_topics[:3]
                )
                warnings.append(f"为了达到所选时长，课文后半段加入了其他主题：{names}。")

        # 4. Loose sentences, last of all. `extras` are single facts joined by
        #    connectives - useful padding, but they read as a list rather than
        #    a paragraph, so every whole passage is tried before them.
        if total < enough:
            used_extras = False
            for candidate_level in nearest_levels(level):
                if total >= budget:
                    break
                extras = self._extras_block(corpus, candidate_level)
                if extras:
                    used_extras = True
                    take([extras], mark_transition=False)
            if used_extras:
                warnings.append(
                    "内置课文不足以填满所选时长，结尾补充了一些独立的例句。"
                    "想要完整连贯的段落，请选择更短的长度。"
                )

        return blocks

    # -- composition -------------------------------------------------------
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

        target = target_seconds(request.length)
        register = request.register or REGISTER_AUTO
        # The frames have to match the body, and the body is not known until it
        # has been collected, so budget with the polite frames (the longest) and
        # re-pick them afterwards.
        estimate = self._frame("openers", level, topic)
        frame_seconds = 2 * estimate_seconds([estimate[0]] if estimate else [])
        budget = max(10.0, target - frame_seconds)

        # A seed is a promise of reproducibility, and the anti-repeat history
        # spans calls - it would make the same seed give a different lesson the
        # second time round.
        blocks = self._collect_blocks(
            corpus, level, budget, warnings, register,
            avoid_repeats=request.seed is None,
        )
        spoken = _effective_register(blocks)
        if register == REGISTER_SPOKEN and spoken != REGISTER_SPOKEN:
            warnings.append(
                "这个主题在所选级别下没有足够的口语体课文，已改用礼貌体（です・ます）的内容。"
                "口语体在 N5–N3 最完整。"
            )

        opener = self._frame("openers", level, topic, spoken)
        closer = self._frame("closers", level, topic, spoken)

        body: List[Pair] = []
        seen_transitions: set = set()
        for block in blocks:
            if block.needs_transition:
                block_topic = TOPICS.get(block.topic_id, topic)
                transition = self._transition(
                    level, block_topic, block.title_ja, block.register,
                    used=seen_transitions,
                )
                if transition:
                    body.append(transition)
            body.extend(block.pairs)

        pairs: List[Pair] = []
        if opener:
            pairs.append(opener)
        pairs.extend(_trim_to_budget(body, budget))
        if closer:
            pairs.append(closer)

        first = blocks[0] if blocks else None
        title_ja = (first.title_ja if first else "") or (topic.name_ja if topic else "")
        title_zh = (first.title_zh if first else "") or (topic.name_zh if topic else "")

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


def _effective_register(blocks: List[_Block]) -> str:
    """The register the collected body actually reads in.

    A vote weighted by length, but neutral material abstains: です・ます extras
    suit either request, so they should not out-vote the passages that gave the
    lesson its voice.
    """
    weights: dict = {}
    for block in blocks:
        if block.register == REGISTER_NEUTRAL:
            continue
        weights[block.register] = weights.get(block.register, 0.0) + block.seconds
    if not weights:
        return REGISTER_NEUTRAL
    return max(weights.items(), key=lambda item: item[1])[0]


_MIN_BODY_SENTENCES = 3


def _trim_to_budget(body: List[Pair], budget: float) -> List[Pair]:
    """Take sentences until ``budget`` seconds are covered, without overshooting.

    Stops as soon as the budget is met, and refuses a sentence that would push
    the total well past it - the long presets otherwise overshoot by a whole
    sentence, which at N1 is ten seconds of audio.
    """
    if not body:
        return body

    limit = budget * 1.15
    kept: List[Pair] = []
    for pair in body:
        seconds = estimate_seconds([p[0] for p in kept] + [pair[0]])
        if seconds > limit and len(kept) >= _MIN_BODY_SENTENCES:
            break
        kept.append(pair)
        if seconds >= budget:
            break
    return kept or body[:1]


_DEFAULT = OfflineGenerator()


def generate(request: GenerationRequest) -> Lesson:
    """Module-level helper using a shared generator (keeps the anti-repeat history)."""
    return _DEFAULT.generate(request)
