"""Loading and querying the bundled offline corpus."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..config import data_dir
from ..levels import LEVEL_CODES, get_level

COMMON_FILE = "_common.json"


@dataclass(frozen=True)
class CorpusSentence:
    ja: str
    zh: str = ""


@dataclass(frozen=True)
class Passage:
    level: str
    title_ja: str
    title_zh: str
    sentences: List[CorpusSentence] = field(default_factory=list)


@dataclass
class TopicCorpus:
    id: str
    passages: List[Passage] = field(default_factory=list)
    extras: Dict[str, List[CorpusSentence]] = field(default_factory=dict)
    vocab: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

    def passages_for(self, level: str) -> List[Passage]:
        return [p for p in self.passages if p.level == level]

    def extras_for(self, level: str) -> List[CorpusSentence]:
        return list(self.extras.get(level, []))

    def vocab_for(self, level: str) -> List[Dict[str, str]]:
        return list(self.vocab.get(level, []))


def corpus_dir() -> Path:
    return data_dir() / "corpus"


@lru_cache(maxsize=None)
def available_topic_ids() -> tuple:
    directory = corpus_dir()
    if not directory.is_dir():
        return tuple()
    return tuple(
        sorted(
            p.stem
            for p in directory.glob("*.json")
            if not p.name.startswith("_")
        )
    )


@lru_cache(maxsize=None)
def load_topic(topic_id: str) -> Optional[TopicCorpus]:
    """Load ``<topic_id>.json`` from the bundled corpus (cached)."""
    path = corpus_dir() / f"{topic_id}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    passages = [
        Passage(
            level=str(p.get("level", "N4")).upper(),
            title_ja=p.get("title_ja", ""),
            title_zh=p.get("title_zh", ""),
            sentences=[
                CorpusSentence(s.get("ja", ""), s.get("zh", ""))
                for s in p.get("sentences", [])
                if s.get("ja")
            ],
        )
        for p in raw.get("passages", [])
    ]
    extras = {
        str(level).upper(): [
            CorpusSentence(s.get("ja", ""), s.get("zh", ""))
            for s in items
            if s.get("ja")
        ]
        for level, items in (raw.get("extras") or {}).items()
    }
    vocab = {
        str(level).upper(): [dict(v) for v in items]
        for level, items in (raw.get("vocab") or {}).items()
    }
    return TopicCorpus(id=topic_id, passages=passages, extras=extras, vocab=vocab)


@lru_cache(maxsize=1)
def load_common() -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    """Shared openers, closers and connectives."""
    path = corpus_dir() / COMMON_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"openers": {}, "closers": {}, "connectives": {}}


def nearest_levels(level: str) -> List[str]:
    """Level codes ordered by distance from ``level`` (closest first)."""
    rank = get_level(level).rank
    return sorted(LEVEL_CODES, key=lambda code: (abs(get_level(code).rank - rank), code))


def frame_sentences(kind: str, level: str) -> List[Dict[str, str]]:
    """Openers or closers for ``level`` with a graceful fallback."""
    common = load_common()
    bucket = common.get(kind, {}) or {}
    for candidate in nearest_levels(level):
        items = bucket.get(candidate)
        if items:
            return list(items)
    return []


def connectives(level: str) -> List[str]:
    common = load_common()
    bucket = common.get("connectives", {}) or {}
    for candidate in nearest_levels(level):
        items = bucket.get(candidate)
        if items:
            return list(items)
    return [""]


def corpus_stats() -> Dict[str, int]:
    """Sentence counts per topic - used by the About dialog and tests."""
    stats: Dict[str, int] = {}
    for topic_id in available_topic_ids():
        corpus = load_topic(topic_id)
        if not corpus:
            continue
        count = sum(len(p.sentences) for p in corpus.passages)
        count += sum(len(v) for v in corpus.extras.values())
        stats[topic_id] = count
    return stats


def match_topic(query: str, topic_ids: Sequence[str]) -> Optional[str]:
    """Best-effort mapping of a free-text topic onto a bundled topic id."""
    from ..topics import TOPICS

    query = (query or "").strip().lower()
    if not query:
        return None
    if query in topic_ids:
        return query

    best: Optional[str] = None
    best_score = 0
    for topic_id in topic_ids:
        topic = TOPICS.get(topic_id)
        if not topic:
            continue
        haystacks = [
            topic.id,
            topic.name_ja,
            topic.name_zh,
            topic.name_en.lower(),
            *topic.search_terms,
        ]
        score = 0
        for hay in haystacks:
            hay = str(hay).lower()
            if not hay:
                continue
            if query == hay:
                score = max(score, 100)
            elif query in hay or hay in query:
                score = max(score, 60 + min(len(hay), 20))
        if score > best_score:
            best_score, best = score, topic_id
    return best if best_score >= 60 else None
