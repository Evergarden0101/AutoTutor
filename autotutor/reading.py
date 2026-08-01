"""Kana readings and furigana alignment.

Three back-ends are tried in order; the first one that imports wins:

1. ``pyopenjtalk`` - the same engine that drives the offline narration, so the
   printed furigana always matches what the learner hears.  Its dictionary
   handles compounds well (日本語 -> ニホンゴ, 今日 -> キョウ).
2. ``fugashi`` + ``unidic-lite`` - a full MeCab/UniDic analysis.
3. ``pykakasi`` - pure Python, no dictionary compilation, used as a last
   resort so the application still runs on a bare Python install.

The alignment step turns a (surface, reading) pair into ruby segments that put
kana only above the kanji: 食べます + タベマス becomes ``食(た)べます``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .models import RubySegment

# --------------------------------------------------------------------------
# Character helpers
# --------------------------------------------------------------------------

_HIRAGANA_START, _HIRAGANA_END = 0x3041, 0x3096
_KATAKANA_START, _KATAKANA_END = 0x30A1, 0x30F6
_PROLONGED = "ー"

_KANJI_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆ヶ]")
_LATIN_DIGIT_RE = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]")


def is_kanji(ch: str) -> bool:
    return bool(_KANJI_RE.match(ch))


def is_hiragana(ch: str) -> bool:
    return _HIRAGANA_START <= ord(ch) <= _HIRAGANA_END


def is_katakana(ch: str) -> bool:
    return _KATAKANA_START <= ord(ch) <= _KATAKANA_END


def is_kana(ch: str) -> bool:
    return is_hiragana(ch) or is_katakana(ch) or ch == _PROLONGED


def to_hiragana(text: str) -> str:
    """Convert every katakana character to hiragana (ー is preserved)."""
    out: List[str] = []
    for ch in text or "":
        if is_katakana(ch):
            out.append(chr(ord(ch) - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def to_katakana(text: str) -> str:
    out: List[str] = []
    for ch in text or "":
        if is_hiragana(ch):
            out.append(chr(ord(ch) + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def has_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text or ""))


# Readings that the analysers are known to get wrong in isolation.
READING_OVERRIDES: Dict[str, str] = {
    "日本語": "にほんご",
    "日本人": "にほんじん",
    "日本": "にほん",
    "一日中": "いちにちじゅう",
    "何時": "なんじ",
    "四時": "よじ",
    "七時": "しちじ",
    "九時": "くじ",
    "四日": "よっか",
    "七日": "なのか",
    "八日": "ようか",
    "二十歳": "はたち",
    "今日": "きょう",
    "明日": "あした",
    "昨日": "きのう",
    "今朝": "けさ",
    "今年": "ことし",
    "一人": "ひとり",
    "二人": "ふたり",
    "大人": "おとな",
    "上手": "じょうず",
    "下手": "へた",
    "仕方": "しかた",
    "手伝": "てつだ",
    "他人": "たにん",
    "生物": "せいぶつ",
    "本屋": "ほんや",
    "毎日": "まいにち",
}


# --------------------------------------------------------------------------
# Tokenisation back-ends
# --------------------------------------------------------------------------


@dataclass
class Token:
    surface: str
    reading: str = ""  # katakana as produced by the analyser


class _BaseReader:
    name = "none"

    def available(self) -> bool:  # pragma: no cover - trivial
        return False

    def tokenize(self, text: str) -> List[Token]:  # pragma: no cover - trivial
        return [Token(text)]


class OpenJTalkReader(_BaseReader):
    """Readings straight from the Open JTalk front-end."""

    name = "pyopenjtalk"

    def __init__(self) -> None:
        self._frontend = None
        try:  # pragma: no cover - depends on optional dependency
            import pyopenjtalk

            self._frontend = pyopenjtalk.run_frontend
        except Exception:
            self._frontend = None

    def available(self) -> bool:
        return self._frontend is not None

    def tokenize(self, text: str) -> List[Token]:
        if self._frontend is None:
            return [Token(text)]
        try:
            features = self._frontend(text)
        except Exception:
            return [Token(text)]
        tokens: List[Token] = []
        for feat in features:
            if isinstance(feat, dict):
                surface = feat.get("string", "")
                reading = feat.get("read", "") or ""
            else:  # older builds return raw label strings
                surface, reading = str(feat), ""
            if not surface:
                continue
            if reading in ("*", "、", "。"):
                reading = ""
            tokens.append(Token(surface, reading))
        return tokens


class FugashiReader(_BaseReader):
    """MeCab/UniDic analysis via fugashi."""

    name = "fugashi"

    def __init__(self) -> None:
        self._tagger = None
        try:  # pragma: no cover - depends on optional dependency
            import fugashi
            import unidic_lite

            self._tagger = fugashi.Tagger(f"-d {unidic_lite.DICDIR}")
        except Exception:
            try:
                import fugashi

                self._tagger = fugashi.Tagger()
            except Exception:
                self._tagger = None

    def available(self) -> bool:
        return self._tagger is not None

    def tokenize(self, text: str) -> List[Token]:
        if self._tagger is None:
            return [Token(text)]
        tokens: List[Token] = []
        try:
            for word in self._tagger(text):
                reading = ""
                feature = getattr(word, "feature", None)
                for attr in ("kana", "pron", "reading"):
                    value = getattr(feature, attr, None) if feature else None
                    if value and value != "*":
                        reading = value
                        break
                tokens.append(Token(word.surface, reading))
        except Exception:
            return [Token(text)]
        return tokens


class KakasiReader(_BaseReader):
    """Pure-python fallback."""

    name = "pykakasi"

    def __init__(self) -> None:
        self._kks = None
        try:  # pragma: no cover - depends on optional dependency
            import pykakasi

            self._kks = pykakasi.kakasi()
        except Exception:
            self._kks = None

    def available(self) -> bool:
        return self._kks is not None

    def tokenize(self, text: str) -> List[Token]:
        if self._kks is None:
            return [Token(text)]
        try:
            items = self._kks.convert(text)
        except Exception:
            return [Token(text)]
        return [
            Token(item.get("orig", ""), item.get("kana", ""))
            for item in items
            if item.get("orig")
        ]


_READER_CACHE: Optional[_BaseReader] = None


def get_reader(force: str = "") -> _BaseReader:
    """Return the best available reading back-end (cached)."""
    global _READER_CACHE
    if force:
        for cls in (OpenJTalkReader, FugashiReader, KakasiReader):
            if cls.name == force:
                reader = cls()
                return reader if reader.available() else _BaseReader()
    if _READER_CACHE is not None:
        return _READER_CACHE
    for cls in (OpenJTalkReader, FugashiReader, KakasiReader):
        reader = cls()
        if reader.available():
            _READER_CACHE = reader
            break
    else:
        _READER_CACHE = _BaseReader()
    return _READER_CACHE


def reader_name() -> str:
    return get_reader().name


# --------------------------------------------------------------------------
# Furigana alignment
# --------------------------------------------------------------------------

_KIND_KANA = "kana"
_KIND_ANNO = "anno"  # kanji, digits, latin - anything that may need a reading
_KIND_OTHER = "other"  # punctuation, spaces


def _classify(ch: str) -> str:
    if is_kana(ch):
        return _KIND_KANA
    if is_kanji(ch) or _LATIN_DIGIT_RE.match(ch):
        return _KIND_ANNO
    return _KIND_OTHER


def _runs(text: str) -> List[Tuple[str, str]]:
    """Group ``text`` into consecutive runs of the same character class."""
    runs: List[Tuple[str, str]] = []
    for ch in text:
        kind = _classify(ch)
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


def align_reading(surface: str, reading: str) -> List[RubySegment]:
    """Split ``surface`` so that kana stays bare and kanji carries the reading.

    ``食べます`` + ``タベマス`` -> ``[食/た, べます]``.
    Falls back to a single annotated segment when the reading cannot be
    matched against the surface (irregular readings such as 大人/おとな).
    """
    surface = surface or ""
    if not surface:
        return []

    reading_hira = to_hiragana(reading or "")
    if not reading_hira or not any(_classify(c) == _KIND_ANNO for c in surface):
        # Nothing to annotate: pure kana, punctuation, or no reading available.
        return [RubySegment(surface)]

    if to_hiragana(surface) == reading_hira:
        return [RubySegment(surface)]

    runs = _runs(surface)
    segments: List[RubySegment] = []
    pos = 0
    ok = True

    for index, (kind, chunk) in enumerate(runs):
        if kind == _KIND_OTHER:
            segments.append(RubySegment(chunk))
            continue

        if kind == _KIND_KANA:
            target = to_hiragana(chunk)
            found = reading_hira.find(target, pos)
            if found < 0:
                ok = False
                break
            pos = found + len(target)
            segments.append(RubySegment(chunk))
            continue

        # Annotatable run: it owns the reading up to the next kana run.
        next_kana = ""
        for later_kind, later_chunk in runs[index + 1:]:
            if later_kind == _KIND_KANA:
                next_kana = to_hiragana(later_chunk)
                break
            if later_kind == _KIND_ANNO:
                break
        if next_kana:
            # Every annotatable character needs at least one mora.
            found = reading_hira.find(next_kana, pos + len(chunk))
            end = found if found >= 0 else len(reading_hira)
        else:
            end = len(reading_hira)
        if end <= pos:
            ok = False
            break
        segments.append(RubySegment(chunk, reading_hira[pos:end]))
        pos = end

    if not ok or pos < len(reading_hira) and not segments:
        return [RubySegment(surface, reading_hira)]
    if not ok:
        return [RubySegment(surface, reading_hira)]
    return segments


def _apply_overrides(tokens: Sequence[Token]) -> List[Token]:
    """Merge tokens that form a word with a known correct reading."""
    tokens = list(tokens)
    if not READING_OVERRIDES:
        return tokens
    max_span = 3
    result: List[Token] = []
    i = 0
    while i < len(tokens):
        matched = False
        for span in range(min(max_span, len(tokens) - i), 0, -1):
            surface = "".join(t.surface for t in tokens[i: i + span])
            override = READING_OVERRIDES.get(surface)
            if override and (span > 1 or to_hiragana(tokens[i].reading) != override):
                result.append(Token(surface, to_katakana(override)))
                i += span
                matched = True
                break
        if not matched:
            result.append(tokens[i])
            i += 1
    return result


def annotate(text: str, reader: Optional[_BaseReader] = None) -> List[RubySegment]:
    """Return ruby segments covering every character of ``text``."""
    text = text or ""
    if not text.strip():
        return [RubySegment(text)] if text else []

    reader = reader or get_reader()
    tokens = _apply_overrides(reader.tokenize(text))

    segments: List[RubySegment] = []
    cursor = 0
    for token in tokens:
        if not token.surface:
            continue
        found = text.find(token.surface, cursor)
        if found < 0:
            # Analyser normalised the surface (e.g. full-width digits); give up
            # on precise alignment for this token but keep the text intact.
            continue
        if found > cursor:
            segments.append(RubySegment(text[cursor:found]))
        segments.extend(align_reading(token.surface, token.reading))
        cursor = found + len(token.surface)

    if cursor < len(text):
        segments.append(RubySegment(text[cursor:]))

    return _merge_plain(segments) or [RubySegment(text)]


def _merge_plain(segments: Sequence[RubySegment]) -> List[RubySegment]:
    """Join neighbouring un-annotated segments to keep the display compact."""
    merged: List[RubySegment] = []
    for seg in segments:
        if not seg.text:
            continue
        if merged and not seg.needs_ruby and not merged[-1].needs_ruby:
            merged[-1] = RubySegment(merged[-1].text + seg.text)
        else:
            merged.append(seg)
    return merged


def kana_of(text: str, segments: Optional[Sequence[RubySegment]] = None) -> str:
    """All-kana version of ``text`` (katakana loanwords keep their script)."""
    segments = segments if segments is not None else annotate(text)
    out: List[str] = []
    for seg in segments:
        out.append(seg.reading if seg.needs_ruby else seg.text)
    return "".join(out)


def furigana_inline(text: str) -> str:
    """``漢字(かんじ)`` rendering of ``text``."""
    parts: List[str] = []
    for seg in annotate(text):
        parts.append(f"{seg.text}({seg.reading})" if seg.needs_ruby else seg.text)
    return "".join(parts)


def normalise(text: str) -> str:
    """Tidy text coming from the web before it is read aloud."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("　", " ")
    text = re.sub(r"\[[0-9]+\]", "", text)  # wiki footnote markers
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
