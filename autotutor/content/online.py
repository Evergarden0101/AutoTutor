"""Turn online Japanese material into a lesson.

The individual fetchers live in :mod:`autotutor.content.sources`; this module
picks which of them to ask, in what order, and cuts the result down to a
passage that matches the learner's level, register and requested length.

Two knobs drive the search:

* **level** - a sliding window over each article picks the stretch whose
  estimated difficulty is closest to the target.
* **register** - spoken sources (podcast notes, video captions) are tried
  first when the learner asked for colloquial Japanese, written ones (news,
  encyclopedia) when they asked for formal, and the window ranking nudges in
  the same direction.
"""

from __future__ import annotations

import random
import re
from typing import List, Optional, Sequence, Tuple

from ..config import Settings
from ..levels import LEVELS, analyse, get_level, score_text, split_sentences
from ..models import (
    REGISTER_SPOKEN,
    REGISTER_WRITTEN,
    GenerationRequest,
    Lesson,
    estimate_seconds,
    target_seconds,
)
from ..net import NetworkError
from ..reading import normalise
from ..topics import RANDOM_TOPIC, get_topic, search_terms_for
from ..translate import Translator, to_japanese
from .builder import build_lesson
from .corpus import available_topic_ids
from .sources import (
    Article,
    SourceError,
    fetch_youtube_captions,
    sources_for,
    strip_html,
    youtube_video_id,
)

# Warn once the text is more than a full JLPT level away from the request.
_LEVEL_GAP_WARNING = 1.0
# Candidates within this much of the best score are treated as tied and one is
# picked at random. Well under the cost of a register miss or a level, so the
# variety never comes at the expense of a match the learner would notice.
_TIE_BAND = 0.35
# How many articles to gather before ranking them.
_MAX_ARTICLES = 8

__all__ = [
    "Article",
    "OnlineError",
    "OnlineGenerator",
    "best_window",
    "clean_sentences",
    "colloquial_score",
    "strip_html",
    "window_for_duration",
]


class OnlineError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Text clean-up
# --------------------------------------------------------------------------


def clean_sentences(text: str) -> List[str]:
    """Split into sentences and drop anything that will not read aloud well."""
    text = normalise(text)
    text = re.sub(r"[（(][^）)]{0,80}[）)]", "", text)  # inline glosses / readings
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    out: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("="):
            continue
        for sentence in split_sentences(line):
            sentence = sentence.strip()
            if len(sentence) < 8 or len(sentence) > 140:
                continue
            if not sentence.endswith(("。", "！", "？")):
                continue
            if re.search(r"[a-zA-Z]{12,}|https?://|\|", sentence):
                continue
            # Reference-heavy or list-like fragments read badly.
            if sentence.count("、") > 8:
                continue
            out.append(sentence)
    return out


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------

def colloquial_score(text: str) -> float:
    """0.0 (formal written prose) .. 1.0 (clearly conversational).

    Positive evidence on both sides, from the sentence-ending statistics the
    difficulty model shares, so the two never disagree about what "casual"
    means. です・ます lands in the middle at 0.5: polite Japanese is neither
    speech nor literary prose, and a learner who asked for either can live
    with it. So does unmarked plain form - real speech is full of it, and
    treating every 気がする as an essay is what made conversational passages
    score as formal.
    """
    if not (text or "").strip():
        return 0.0
    stats = analyse(text)
    score = 0.5 + 0.5 * stats.casual_ratio - 0.5 * stats.literary_ratio
    return max(0.0, min(1.0, score))


def _register_penalty(text: str, register: str) -> float:
    """How badly ``text`` misses the requested register (0 = perfect)."""
    if register == REGISTER_SPOKEN:
        return 1.0 - colloquial_score(text)
    if register == REGISTER_WRITTEN:
        return colloquial_score(text)
    return 0.0


# A register miss is worth about one JLPT level when ranking candidates: a
# learner who asked for conversational Japanese would rather hear a slightly
# off-level podcast note than a perfectly graded encyclopedia entry.
_REGISTER_COST = 0.8


def _article_register_cost(article: Article, window: Sequence[str], register: str) -> float:
    """Penalty for an article in the wrong register, declared and measured."""
    if register not in (REGISTER_SPOKEN, REGISTER_WRITTEN):
        return 0.0
    declared = _REGISTER_COST if article.register != register else 0.0
    return declared + _register_penalty("".join(window), register) * _REGISTER_COST


# Also worth about one JLPT level. A single continuous text is the whole point
# of listening practice; stitching four unrelated fragments together to hit the
# duration produces something no one would ever actually listen to.
_COVERAGE_COST = 0.8


def _coverage_cost(window: Sequence[str], target: float) -> float:
    """How far this source falls short of carrying the lesson on its own."""
    if target <= 0:
        return 0.0
    covered = min(1.0, estimate_seconds(list(window)) / target)
    return (1.0 - covered) * _COVERAGE_COST


# --------------------------------------------------------------------------
# Level-aware window selection
# --------------------------------------------------------------------------


def best_window(
    sentences: Sequence[str], level: str, size: int, register: str = "auto"
) -> Tuple[List[str], float]:
    """Pick ``size`` consecutive sentences closest to ``level``."""
    if not sentences:
        return [], 99.0
    target = float(get_level(level).rank)
    size = max(2, min(size, len(sentences)))
    scores = [score_text(s) for s in sentences]

    best_index, best_cost = 0, float("inf")
    for start in range(0, len(sentences) - size + 1):
        window = scores[start: start + size]
        mean = sum(window) / len(window)
        spread = max(window) - min(window)
        text = "".join(sentences[start: start + size])
        # Prefer windows near the target level, internally consistent, and in
        # the requested register; a small bonus keeps us near the top.
        cost = (
            abs(mean - target)
            + spread * 0.15
            + _register_penalty(text, register) * 1.2
            + start * 0.01
        )
        if cost < best_cost:
            best_cost, best_index = cost, start

    window = list(sentences[best_index: best_index + size])
    mean = sum(scores[best_index: best_index + size]) / size
    return window, abs(mean - target)


def window_for_duration(
    sentences: Sequence[str], level: str, seconds: float, register: str = "auto"
) -> Tuple[List[str], float]:
    """Like :func:`best_window`, but sized to fill ``seconds`` of narration."""
    if not sentences:
        return [], 99.0
    size = 2
    while size < len(sentences) and estimate_seconds(list(sentences[:size])) < seconds:
        size += 1
    return best_window(sentences, level, size, register)


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


class OnlineGenerator:
    def __init__(self, settings: Settings, rng: Optional[random.Random] = None) -> None:
        self.settings = settings
        self.rng = rng or random.Random()

    # -- gathering ---------------------------------------------------------
    def _search_terms(self, request: GenerationRequest) -> Tuple[str, List[str]]:
        timeout = self.settings.request_timeout
        topic_id = request.topic
        if topic_id == RANDOM_TOPIC:
            ids = list(available_topic_ids())
            topic_id = self.rng.choice(ids) if ids else "daily_life"

        query = request.topic_query
        if query:
            query = to_japanese(query, timeout=timeout)
        terms = search_terms_for(topic_id, query)
        if not terms:
            topic = get_topic(topic_id)
            terms = [topic.name_ja] if topic else ["日本語"]
        self.rng.shuffle(terms)
        return topic_id, terms

    def _collect(self, request: GenerationRequest, warnings: List[str]) -> List[Article]:
        timeout = self.settings.request_timeout
        _topic_id, terms = self._search_terms(request)

        articles: List[Article] = []
        failures: List[str] = []
        enabled = self.settings.enabled_source_ids()

        for index, source in enumerate(sources_for(request.register, request.level)):
            if len(articles) >= _MAX_ARTICLES:
                break
            if source.id not in enabled:
                continue
            # A different search term per source widens what comes back, so two
            # runs on the same topic are not looking at the same page twice.
            term = terms[index % len(terms)] if terms else ""
            try:
                found = source.fetch(term, timeout)
            except (SourceError, NetworkError) as exc:
                failures.append(f"{source.label_zh}：{exc}")
                continue
            articles.extend(found)

        if failures and articles:
            warnings.append("部分来源没有取到内容（" + "；".join(failures[:2]) + "）。")
        elif failures and not articles:
            warnings.extend(f"来源不可用 — {f}" for f in failures[:3])
        return articles

    def _from_url(self, url: str, warnings: List[str]) -> List[Article]:
        try:
            return [fetch_youtube_captions(url, self.settings.request_timeout)]
        except (SourceError, NetworkError) as exc:
            raise OnlineError(str(exc)) from exc

    # -- generation --------------------------------------------------------
    def generate(self, request: GenerationRequest) -> Lesson:
        warnings: List[str] = []

        # A pasted video link is treated as an explicit request for its captions.
        video_url = request.topic_query if youtube_video_id(request.topic_query) else ""
        if video_url:
            articles = self._from_url(video_url, warnings)
        else:
            articles = self._collect(request, warnings)

        if not articles:
            raise OnlineError(
                "没有搜到合适的日语内容。请检查网络连接，或换一个主题／来源再试。"
            )

        target = float(target_seconds(request.length))
        candidates: List[Tuple[Article, List[str], float, float]] = []
        for article in articles:
            sentences = clean_sentences(article.text)
            if len(sentences) < 3:
                continue
            window, distance = window_for_duration(
                sentences, request.level, target, request.register
            )
            if window:
                cost = (
                    distance
                    + _article_register_cost(article, window, request.register)
                    + _coverage_cost(window, target)
                )
                candidates.append((article, window, distance, cost))

        if not candidates:
            raise OnlineError("搜到的内容无法拆成合适的句子，请换一个主题或来源再试。")

        # Ranked on level, register *and* whether one source can carry the whole
        # lesson. Sorting on level alone gives a montage of fragments: the point
        # of listening practice is a continuous piece of speech, so a text long
        # enough to stand on its own is worth about a level of difficulty miss.
        candidates.sort(key=lambda item: item[3])
        # Then pick at random from the ones that are near enough to tied. Taking
        # the strict minimum makes the same topic give the same lesson forever,
        # and the difference between a 0.4 and a 0.5 cost is not something the
        # learner can hear.
        best = candidates[0][3]
        contenders = [c for c in candidates if c[3] <= best + _TIE_BAND]
        chosen = self.rng.choice(contenders)
        article, window, distance, _ = chosen
        remaining = [c for c in candidates if c is not chosen]

        # Only if one source truly cannot fill the time. Same source first, so
        # a podcast is extended with the same programme rather than a news feed.
        extra_sources: List[Article] = []
        rest = sorted(
            remaining,
            key=lambda item: (item[0].source_id != article.source_id, item[3]),
        )
        for other, other_window, _, _cost in rest:
            if estimate_seconds(window) >= target:
                break
            window = window + other_window
            extra_sources.append(other)
        if extra_sources:
            same = all(a.source_id == article.source_id for a in extra_sources)
            titles = "、".join(a.title for a in extra_sources[:3] if a.title)
            if titles:
                warnings.append(
                    ("为了达到所选时长，课文接续了同一来源的其他内容：" if same
                     else "为了达到所选时长，课文还合并了其他来源：") + titles + "。"
                    "想要一段完整连贯的内容，可以选择更短的时长。"
                )

        estimated = sum(score_text(s) for s in window) / len(window)
        if distance > _LEVEL_GAP_WARNING:
            level_name = LEVELS[request.level].label_zh
            warnings.append(
                f"这段网络文本的估计难度约为 {estimated:.1f} 级"
                f"（1=N5，5=N1），与所选的 {request.level}（{level_name}）差距较大。"
                "如需完全贴合级别，请使用离线语料模式。"
            )
        if estimate_seconds(window) < target * 0.6:
            warnings.append(
                "取到的内容比所选时长短，已按实际长度生成。"
                "想要更长的音频，可以换用离线语料或 AI 生成模式。"
            )
        if request.register in (REGISTER_SPOKEN, REGISTER_WRITTEN):
            # Both signals matter: the source that was actually used, and how
            # the text reads. Merging a second article to fill the time can
            # pull the measured score back to the middle while the lesson is
            # still led by a source in the wrong register.
            actual = colloquial_score("".join(window))
            wanted_spoken = request.register == REGISTER_SPOKEN
            off_text = actual < 0.5 if wanted_spoken else actual > 0.5
            if article.register != request.register or off_text:
                warnings.append(
                    "这段内容的语体和所选的不完全一致，"
                    "可以在「设置 → 联网」里调整启用的来源。"
                )

        translator = Translator(
            timeout=self.settings.request_timeout,
            api_key=self.settings.anthropic_api_key,
            model=self.settings.anthropic_model,
            enabled=self.settings.online_translate and request.translate,
        )
        translations, warning = translator.translate(window)
        if warning:
            warnings.append(warning)

        topic_id = request.topic if request.topic != RANDOM_TOPIC else ""
        topic = get_topic(topic_id)
        return build_lesson(
            list(zip(window, translations)),
            level=request.level,
            topic=topic_id or (request.topic_query or "web"),
            title_ja=article.title,
            title_zh=request.topic_query or (topic.name_zh if topic else article.title),
            source="online",
            source_label=article.source_label or "网络搜索",
            source_url=article.url,
            warnings=warnings,
        )
