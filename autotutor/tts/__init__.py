"""Speech synthesis: engine registry and lesson narration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

from ..config import Settings
from ..levels import get_level
from ..models import Lesson
from .audio import AudioError, Mp3Clip, PcmClip, Player, concat, silence_like
from .base import TTSEngine, TTSError, Voice
from .edge import EdgeEngine
from .openjtalk import OpenJTalkEngine
from .sapi import SapiEngine

__all__ = [
    "AudioError",
    "Mp3Clip",
    "Narrator",
    "NarrationResult",
    "PcmClip",
    "Player",
    "TTSEngine",
    "TTSError",
    "Voice",
    "VoiceChoice",
    "apply_voice_choice",
    "available_engines",
    "current_voice_choice",
    "get_engine",
    "list_engines",
    "list_voice_choices",
]

ENGINE_ORDER = ("openjtalk", "sapi5", "edge")

# Stop hammering a dead engine: if the first few sentences all fail there is
# nothing to salvage, and narrate() falls back to an offline engine instead.
_GIVE_UP_AFTER = 2

_ENGINE_CACHE: Dict[str, TTSEngine] = {}


def get_engine(engine_id: str) -> Optional[TTSEngine]:
    """Return a cached engine instance (engines are cheap but stateful)."""
    if engine_id not in ENGINE_ORDER:
        return None
    if engine_id not in _ENGINE_CACHE:
        _ENGINE_CACHE[engine_id] = {
            "openjtalk": OpenJTalkEngine,
            "sapi5": SapiEngine,
            "edge": EdgeEngine,
        }[engine_id]()
    return _ENGINE_CACHE[engine_id]


def list_engines() -> List[TTSEngine]:
    return [engine for engine in (get_engine(e) for e in ENGINE_ORDER) if engine]


def available_engines(allow_online: bool = True) -> List[TTSEngine]:
    out: List[TTSEngine] = []
    for engine in list_engines():
        if engine.requires_network and not allow_online:
            continue
        if engine.available():
            out.append(engine)
    return out


# --------------------------------------------------------------------------
# Voices across engines
# --------------------------------------------------------------------------

# Which Settings field holds the chosen voice for each engine. Open JTalk has
# only its bundled voice, so it stores nothing.
_VOICE_SETTING = {"edge": "edge_voice", "sapi5": "sapi_voice"}


@dataclass(frozen=True)
class VoiceChoice:
    """One selectable voice, identified by the engine that provides it.

    The learner thinks in voices, not engines, so the picker flattens the two
    into a single list and setting one back writes both fields.
    """

    engine_id: str
    voice_id: str
    label: str
    requires_network: bool = False

    @property
    def key(self) -> str:
        return f"{self.engine_id}:{self.voice_id}"


def list_voice_choices(allow_online: bool = True) -> List[VoiceChoice]:
    """Every voice that can be used right now, offline engines first."""
    choices: List[VoiceChoice] = []
    for engine in list_engines():
        if engine.requires_network and not allow_online:
            continue
        if not engine.available():
            continue
        voices = engine.voices() or [Voice("", engine.name)]
        for voice in voices:
            suffix = "联网" if engine.requires_network else "离线"
            choices.append(VoiceChoice(
                engine_id=engine.id,
                voice_id=voice.id,
                label=f"{voice.display} · {suffix}",
                requires_network=engine.requires_network,
            ))
    return choices


def current_voice_choice(settings: Settings) -> str:
    """The :attr:`VoiceChoice.key` the settings currently describe."""
    engine_id = settings.tts_engine or "auto"
    if engine_id == "auto":
        engines = available_engines(settings.allow_online)
        engine_id = engines[0].id if engines else ENGINE_ORDER[0]
    field_name = _VOICE_SETTING.get(engine_id)
    voice_id = getattr(settings, field_name, "") if field_name else ""
    if not voice_id:
        engine = get_engine(engine_id)
        voices = engine.voices() if engine else []
        voice_id = voices[0].id if voices else ""
    return f"{engine_id}:{voice_id}"


def apply_voice_choice(settings: Settings, key: str) -> None:
    """Write a picker selection back into ``settings``."""
    engine_id, _, voice_id = (key or "").partition(":")
    if engine_id not in ENGINE_ORDER:
        return
    settings.tts_engine = engine_id
    field_name = _VOICE_SETTING.get(engine_id)
    if field_name:
        setattr(settings, field_name, voice_id)


@dataclass
class SentenceTiming:
    """Where a sentence sits inside the rendered clip.

    ``start``/``end`` are seconds and drive the playback cursor; they are only
    meaningful for PCM, because an MP3 stream cannot be measured without
    decoding it. ``offset_bytes`` is exact for both, because :func:`concat`
    joins clips byte for byte - which is what makes "play from this sentence"
    work on either engine.
    """

    index: int
    start: float
    end: float
    offset_bytes: int = 0


@dataclass
class NarrationResult:
    clip: object
    engine_id: str = ""
    engine_name: str = ""
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False
    timings: List[SentenceTiming] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return getattr(self.clip, "duration", 0.0) if self.clip else 0.0

    @property
    def has_timings(self) -> bool:
        """MP3 clips from the online engine cannot be measured without decoding."""
        return bool(self.timings) and isinstance(self.clip, PcmClip)

    @property
    def can_seek(self) -> bool:
        """Whether playback can start from an arbitrary sentence."""
        return bool(self.timings) and self.clip is not None

    def clip_from(self, index: int):
        """The narration from sentence ``index`` onwards.

        Byte offsets rather than seconds, so this is exact for MP3 too: every
        sentence begins at the start of its own stream inside the join.
        """
        if self.clip is None:
            return None
        timing = next((t for t in self.timings if t.index >= index), None)
        if timing is None or timing.offset_bytes <= 0:
            return self.clip
        data = getattr(self.clip, "data", b"")[timing.offset_bytes:]
        if not data:
            return self.clip
        return replace(self.clip, data=data)

    def starts_at(self, index: int) -> float:
        """When sentence ``index`` begins, in seconds (0.0 if unknown)."""
        timing = next((t for t in self.timings if t.index >= index), None)
        return timing.start if timing else 0.0


class Narrator:
    """Renders a whole :class:`Lesson` into one audio clip."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- engine selection --------------------------------------------------
    def resolve_engine(self) -> tuple:
        """Return ``(engine, warnings)`` honouring the user's preference."""
        warnings: List[str] = []
        allow_online = self.settings.allow_online
        preferred = self.settings.tts_engine or "auto"

        if preferred != "auto":
            engine = get_engine(preferred)
            if engine and engine.requires_network and not allow_online:
                warnings.append(
                    f"{engine.name} 需要联网，但联网功能已关闭，已自动改用离线引擎。"
                )
            elif engine and engine.available():
                return engine, warnings
            elif engine:
                warnings.append(f"{engine.name} 当前不可用：{engine.unavailable_reason()}")
            else:
                warnings.append(f"未知的语音引擎「{preferred}」，已自动选择。")

        for engine in list_engines():
            if engine.requires_network and not allow_online:
                continue
            if engine.available():
                return engine, warnings

        return None, warnings + ["没有任何可用的语音引擎，无法生成音频。"]

    # -- narration ---------------------------------------------------------
    def _voice_for(self, engine: TTSEngine) -> str:
        if engine.id == "edge":
            return self.settings.edge_voice
        if engine.id == "sapi5":
            return self.settings.sapi_voice
        return ""

    def _offline_fallback(self, failed: TTSEngine) -> Optional[TTSEngine]:
        """The best usable engine that is not ``failed`` and needs no network."""
        for engine in list_engines():
            if engine.id == failed.id or engine.requires_network:
                continue
            if engine.available():
                return engine
        return None

    def narrate(
        self,
        lesson: Lesson,
        progress: Optional[Callable[[int, int], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> NarrationResult:
        engine, warnings = self.resolve_engine()
        if engine is None:
            return NarrationResult(None, warnings=warnings)

        result = self._render(engine, lesson, warnings, progress, cancel)
        if result.clip is not None or result.cancelled:
            return result

        # A retired online voice streams nothing at all. Falling back keeps the
        # promise that a network problem never leaves the learner with silence.
        spare = self._offline_fallback(engine)
        if spare is None:
            return result
        result.warnings.append(f"{engine.name}没有生成音频，已改用{spare.name}。")
        return self._render(spare, lesson, result.warnings, progress, cancel)

    def _render(
        self,
        engine: TTSEngine,
        lesson: Lesson,
        warnings: List[str],
        progress: Optional[Callable[[int, int], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> NarrationResult:
        voice = self._voice_for(engine)
        # Slow the delivery down for beginners, speed it up for advanced material.
        rate = self.settings.speech_rate * get_level(lesson.level).speech_rate
        repeats = max(1, int(self.settings.repeat_each_sentence))

        sentences = [s for s in lesson.sentences if s.ja.strip()]
        total = len(sentences)
        pieces: List[object] = []
        timings: List[SentenceTiming] = []
        elapsed = 0.0
        written = 0

        def push(piece) -> None:
            nonlocal elapsed, written
            pieces.append(piece)
            elapsed += getattr(piece, "duration", 0.0)
            written += len(getattr(piece, "data", b""))

        for index, sentence in enumerate(sentences):
            if cancel is not None and cancel.is_set():
                return NarrationResult(
                    concat(pieces), engine.id, engine.name, warnings,
                    cancelled=True, timings=timings,
                )
            try:
                clip = engine.synthesize(sentence.ja, rate=rate, voice=voice)
            except TTSError as exc:
                # A broken voice fails identically on every sentence; saying so
                # once is information, saying it 40 times is noise.
                reason = f"{engine.name}合成失败：{exc}"
                if reason not in warnings:
                    warnings.append(reason)
                if not pieces and index >= _GIVE_UP_AFTER:
                    break
                continue
            if clip is None or clip.is_empty:
                continue

            start, offset = elapsed, written
            for repeat in range(repeats):
                push(clip)
                if repeat < repeats - 1:
                    push(silence_like(clip, self.settings.sentence_pause_ms // 2))
            timings.append(SentenceTiming(index, start, elapsed, offset))

            gap = (
                self.settings.paragraph_pause_ms
                if index == total - 1
                else self.settings.sentence_pause_ms
            )
            push(silence_like(clip, gap))

            if progress:
                progress(index + 1, total)

        if not pieces:
            # Only add the generic line when nothing more specific was recorded;
            # "check your engine settings" is useless next to a real reason.
            if not warnings:
                warnings.append("没有生成任何音频，请检查语音引擎设置。")
            return NarrationResult(None, engine.id, engine.name, warnings)

        try:
            clip = concat(pieces)
        except AudioError as exc:
            return NarrationResult(None, engine.id, engine.name, warnings + [str(exc)])
        return NarrationResult(clip, engine.id, engine.name, warnings, timings=timings)
