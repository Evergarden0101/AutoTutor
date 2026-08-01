"""Data structures shared by the generators, the UI and the exporters."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence


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
        """Rough narration length, before the configured sentence pauses."""
        return round(
            estimate_seconds([s.kana or s.ja for s in self.sentences], is_kana=True), 1
        )

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


# --------------------------------------------------------------------------
# Register (how formal the Japanese sounds)
# --------------------------------------------------------------------------

REGISTER_AUTO = "auto"
REGISTER_SPOKEN = "spoken"
REGISTER_WRITTEN = "written"


@dataclass(frozen=True)
class RegisterOption:
    """One choice in the 语体 selector."""

    id: str
    label_zh: str
    label_ja: str
    description_zh: str


REGISTER_OPTIONS: List[RegisterOption] = [
    RegisterOption(REGISTER_AUTO, "自动", "自動",
                   "由级别和来源决定，通常是礼貌体（です・ます）。"),
    RegisterOption(REGISTER_SPOKEN, "口语·日常", "話し言葉",
                   "贴近日常会话：常体、语气词（ね・よ・んだ）、随意的说法。"),
    RegisterOption(REGISTER_WRITTEN, "书面·正式", "書き言葉",
                   "接近新闻和书面文章的文体：常体、书面连接词、名词化表达。"),
]

REGISTER_IDS = [r.id for r in REGISTER_OPTIONS]


def get_register(register_id: str) -> Optional[RegisterOption]:
    """Return the :class:`RegisterOption` for ``register_id``, if it exists."""
    for option in REGISTER_OPTIONS:
        if option.id == register_id:
            return option
    return None


@dataclass
class GenerationRequest:
    """Everything the UI collects before asking for a lesson."""

    level: str = "N4"
    topic: str = "daily_life"
    custom_topic: str = ""
    length: str = "medium"  # see LENGTH_PRESETS
    source: str = "offline"  # offline | online | llm | custom
    register: str = REGISTER_AUTO  # see REGISTER_OPTIONS
    custom_text: str = ""
    translate: bool = True
    seed: Optional[int] = None

    @property
    def topic_query(self) -> str:
        """The free-text topic the user actually wants, if any."""
        return (self.custom_topic or "").strip()

    @property
    def target_seconds(self) -> int:
        return target_seconds(self.length)


# --------------------------------------------------------------------------
# Narration length
# --------------------------------------------------------------------------

# Measured against the bundled Open JTalk voice over the corpus: 6.9 kana per
# second including the small silence the engine puts around each sentence.
KANA_PER_SECOND = 6.9
# Japanese text averages 1.28 kana per written character (kanji expand).
KANA_PER_CHAR = 1.28
# Default gap the narrator inserts between sentences.
DEFAULT_SENTENCE_GAP = 0.55


def estimate_seconds(
    texts: Sequence[str],
    is_kana: bool = False,
    gap: float = DEFAULT_SENTENCE_GAP,
) -> float:
    """Estimate how long ``texts`` take to read aloud, in seconds."""
    texts = [t for t in texts if t]
    if not texts:
        return 0.0
    characters = sum(len(t) for t in texts)
    mora = characters if is_kana else characters * KANA_PER_CHAR
    return mora / KANA_PER_SECOND + gap * len(texts)


@dataclass(frozen=True)
class LengthPreset:
    """A listening-length choice, expressed as a target narration duration."""

    id: str
    label_zh: str
    minutes_zh: str
    target_seconds: int

    @property
    def display(self) -> str:
        return f"{self.label_zh} · {self.minutes_zh}"


LENGTH_PRESETS: List[LengthPreset] = [
    LengthPreset("short", "短", "1分", 60),
    LengthPreset("medium", "中", "2分", 130),
    LengthPreset("long", "长", "4分", 240),
    LengthPreset("xlong", "超长", "8分", 450),
]

LENGTH_BY_ID: Dict[str, LengthPreset] = {p.id: p for p in LENGTH_PRESETS}
LENGTH_IDS: List[str] = [p.id for p in LENGTH_PRESETS]


def get_length(length: str) -> LengthPreset:
    return LENGTH_BY_ID.get(length or "", LENGTH_BY_ID["medium"])


def target_seconds(length: str) -> int:
    return get_length(length).target_seconds


def target_sentence_count(length: str, level: str = "N4") -> int:
    """How many sentences the target duration works out to at ``level``.

    Used by the back-ends that must ask for a sentence count up front (the web
    search window and the LLM prompt) rather than growing until they are full.
    """
    from .levels import get_level

    # Typical sentence length in characters at each level, from the corpus.
    typical_chars = {1: 26, 2: 34, 3: 44, 4: 54, 5: 60}[get_level(level).rank]
    per_sentence = typical_chars * KANA_PER_CHAR / KANA_PER_SECOND + DEFAULT_SENTENCE_GAP
    return max(3, int(round(target_seconds(length) / per_sentence)))
