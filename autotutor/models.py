"""Data structures shared by the generators, the UI and the exporters."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class RubySegment:
    """One chunk of a sentence, optionally carrying a kana reading.

    ``reading`` is empty for text that needs no annotation (kana, punctuation,
    latin letters, digits).
    """

    text: str
    reading: str = ""

    @property
    def needs_ruby(self) -> bool:
        return bool(self.reading) and self.reading != self.text

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text, "reading": self.reading}


@dataclass
class Sentence:
    """A single Japanese sentence with its annotations."""

    ja: str
    kana: str = ""
    zh: str = ""
    ruby: List[RubySegment] = field(default_factory=list)
    note: str = ""

    @property
    def furigana_inline(self) -> str:
        """``漢字(かんじ)`` style annotation - safe in any font or terminal."""
        if not self.ruby:
            return self.ja
        out: List[str] = []
        for seg in self.ruby:
            if seg.needs_ruby:
                out.append(f"{seg.text}({seg.reading})")
            else:
                out.append(seg.text)
        return "".join(out)

    @property
    def html_ruby(self) -> str:
        """True ``<ruby>`` markup for the HTML export."""
        from html import escape

        if not self.ruby:
            return escape(self.ja)
        out: List[str] = []
        for seg in self.ruby:
            if seg.needs_ruby:
                out.append(
                    f"<ruby>{escape(seg.text)}<rp>(</rp>"
                    f"<rt>{escape(seg.reading)}</rt><rp>)</rp></ruby>"
                )
            else:
                out.append(escape(seg.text))
        return "".join(out)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ruby"] = [seg.to_dict() for seg in self.ruby]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Sentence":
        ruby = [RubySegment(**seg) for seg in data.get("ruby", [])]
        return cls(
            ja=data.get("ja", ""),
            kana=data.get("kana", ""),
            zh=data.get("zh", ""),
            ruby=ruby,
            note=data.get("note", ""),
        )


@dataclass
class VocabEntry:
    """A word worth highlighting under the passage."""

    word: str
    kana: str = ""
    zh: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class Lesson:
    """A complete generated listening lesson."""

    title_ja: str = ""
    title_zh: str = ""
    level: str = "N4"
    topic: str = ""
    topic_label: str = ""
    sentences: List[Sentence] = field(default_factory=list)
    vocab: List[VocabEntry] = field(default_factory=list)
    source: str = "offline"
    source_label: str = ""
    source_url: str = ""
    created_at: str = field(
        default_factory=lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    warnings: List[str] = field(default_factory=list)

    # -- convenience views -------------------------------------------------
    @property
    def plain_text(self) -> str:
        return "".join(s.ja for s in self.sentences)

    @property
    def kana_text(self) -> str:
        return "".join(s.kana or s.ja for s in self.sentences)

    @property
    def furigana_text(self) -> str:
        return "".join(s.furigana_inline for s in self.sentences)

    @property
    def chinese_text(self) -> str:
        return "".join(s.zh for s in self.sentences if s.zh)

    @property
    def char_count(self) -> int:
        return len(self.plain_text)

    @property
    def estimated_seconds(self) -> float:
        """Rough narration length: Japanese TTS averages ~6.5 mora/second."""
        return round(len(self.kana_text) / 6.5 + 0.6 * len(self.sentences), 1)

    def slug(self) -> str:
        """Filesystem-friendly stem for exported files."""
        import re

        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"AutoTutor_{self.level}_{self.topic or 'lesson'}_{stamp}"
        return re.sub(r"[^0-9A-Za-z_.-]+", "_", base)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title_ja": self.title_ja,
            "title_zh": self.title_zh,
            "level": self.level,
            "topic": self.topic,
            "topic_label": self.topic_label,
            "source": self.source,
            "source_label": self.source_label,
            "source_url": self.source_url,
            "created_at": self.created_at,
            "warnings": list(self.warnings),
            "sentences": [s.to_dict() for s in self.sentences],
            "vocab": [v.to_dict() for v in self.vocab],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Lesson":
        lesson = cls(
            title_ja=data.get("title_ja", ""),
            title_zh=data.get("title_zh", ""),
            level=data.get("level", "N4"),
            topic=data.get("topic", ""),
            topic_label=data.get("topic_label", ""),
            source=data.get("source", "offline"),
            source_label=data.get("source_label", ""),
            source_url=data.get("source_url", ""),
            warnings=list(data.get("warnings", [])),
        )
        if data.get("created_at"):
            lesson.created_at = data["created_at"]
        lesson.sentences = [Sentence.from_dict(s) for s in data.get("sentences", [])]
        lesson.vocab = [VocabEntry(**v) for v in data.get("vocab", [])]
        return lesson


@dataclass
class GenerationRequest:
    """Everything the UI collects before asking for a lesson."""

    level: str = "N4"
    topic: str = "daily_life"
    custom_topic: str = ""
    length: str = "medium"  # short | medium | long
    source: str = "offline"  # offline | online | llm | custom
    custom_text: str = ""
    translate: bool = True
    seed: Optional[int] = None

    @property
    def topic_query(self) -> str:
        """The free-text topic the user actually wants, if any."""
        return (self.custom_topic or "").strip()


SENTENCE_TARGETS: Dict[str, int] = {"short": 4, "medium": 8, "long": 14}


def target_sentence_count(length: str) -> int:
    return SENTENCE_TARGETS.get(length, SENTENCE_TARGETS["medium"])
