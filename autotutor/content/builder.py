"""Turn raw (Japanese, Chinese) pairs into a fully annotated :class:`Lesson`."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from ..levels import split_sentences
from ..models import Lesson, Sentence, VocabEntry
from ..reading import annotate, kana_of, normalise
from ..topics import topic_label


def build_sentence(ja: str, zh: str = "", note: str = "") -> Sentence:
    """Annotate a single Japanese sentence with ruby and a kana reading."""
    ja = (ja or "").strip()
    ruby = annotate(ja)
    return Sentence(ja=ja, kana=kana_of(ja, ruby), zh=(zh or "").strip(), ruby=ruby, note=note)


def build_lesson(
    pairs: Sequence[Tuple[str, str]],
    *,
    level: str,
    topic: str,
    title_ja: str = "",
    title_zh: str = "",
    source: str = "offline",
    source_label: str = "",
    source_url: str = "",
    vocab: Optional[Iterable[dict]] = None,
    warnings: Optional[Iterable[str]] = None,
) -> Lesson:
    """Build a :class:`Lesson` from ``(japanese, chinese)`` pairs."""
    sentences: List[Sentence] = []
    for ja, zh in pairs:
        ja = (ja or "").strip()
        if not ja:
            continue
        sentences.append(build_sentence(ja, zh))

    entries: List[VocabEntry] = []
    for item in vocab or []:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        entries.append(
            VocabEntry(
                word=word,
                kana=item.get("kana") or kana_of(word),
                zh=(item.get("zh") or "").strip(),
            )
        )

    return Lesson(
        title_ja=title_ja,
        title_zh=title_zh,
        level=level,
        topic=topic,
        topic_label=topic_label(topic, title_zh or topic),
        sentences=sentences,
        vocab=entries,
        source=source,
        source_label=source_label,
        source_url=source_url,
        warnings=list(warnings or []),
    )


def split_into_sentences(text: str) -> List[str]:
    """Normalise free text and split it into narratable sentences."""
    return split_sentences(normalise(text))
