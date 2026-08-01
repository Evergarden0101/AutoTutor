"""Speech synthesis: engine registry and lesson narration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
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
    "available_engines",
    "get_engine",
    "list_engines",
]

ENGINE_ORDER = ("openjtalk", "sapi5", "edge")

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


@dataclass
class SentenceTiming:
    """Where a sentence sits inside the rendered clip, in seconds."""

    index: int
    start: float
    end: float


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

    def narrate(
        self,
        lesson: Lesson,
        progress: Optional[Callable[[int, int], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> NarrationResult:
        engine, warnings = self.resolve_engine()
        if engine is None:
            return NarrationResult(None, warnings=warnings)

        voice = self._voice_for(engine)
        # Slow the delivery down for beginners, speed it up for advanced material.
        rate = self.settings.speech_rate * get_level(lesson.level).speech_rate
        repeats = max(1, int(self.settings.repeat_each_sentence))

        sentences = [s for s in lesson.sentences if s.ja.strip()]
        total = len(sentences)
        pieces: List[object] = []
        timings: List[SentenceTiming] = []
        elapsed = 0.0

        def push(piece) -> None:
            nonlocal elapsed
            pieces.append(piece)
            elapsed += getattr(piece, "duration", 0.0)

        for index, sentence in enumerate(sentences):
            if cancel is not None and cancel.is_set():
                return NarrationResult(
                    concat(pieces), engine.id, engine.name, warnings,
                    cancelled=True, timings=timings,
                )
            try:
                clip = engine.synthesize(sentence.ja, rate=rate, voice=voice)
            except TTSError as exc:
                warnings.append(f"第 {index + 1} 句合成失败：{exc}")
                continue
            if clip is None or clip.is_empty:
                continue

            start = elapsed
            for repeat in range(repeats):
                push(clip)
                if repeat < repeats - 1:
                    push(silence_like(clip, self.settings.sentence_pause_ms // 2))
            timings.append(SentenceTiming(index, start, elapsed))

            gap = (
                self.settings.paragraph_pause_ms
                if index == total - 1
                else self.settings.sentence_pause_ms
            )
            push(silence_like(clip, gap))

            if progress:
                progress(index + 1, total)

        if not pieces:
            warnings.append("没有生成任何音频，请检查语音引擎设置。")
            return NarrationResult(None, engine.id, engine.name, warnings)

        try:
            clip = concat(pieces)
        except AudioError as exc:
            return NarrationResult(None, engine.id, engine.name, warnings + [str(exc)])
        return NarrationResult(clip, engine.id, engine.name, warnings, timings=timings)
