"""The speech-engine interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    gender: str = ""

    @property
    def display(self) -> str:
        return f"{self.name}（{self.gender}）" if self.gender else self.name


class TTSEngine:
    """Synthesises Japanese speech.

    Implementations return either a :class:`~autotutor.tts.audio.PcmClip` or an
    :class:`~autotutor.tts.audio.Mp3Clip`; callers treat them through the
    small shared protocol in :mod:`autotutor.tts.audio`.
    """

    id = "base"
    name = "Base"
    description = ""
    requires_network = False

    def available(self) -> bool:
        raise NotImplementedError

    def unavailable_reason(self) -> str:
        return ""

    def voices(self) -> List[Voice]:
        return []

    def synthesize(self, text: str, rate: float = 1.0, voice: str = ""):
        """Render ``text`` to a single clip.

        ``rate`` is a multiplier where 1.0 is the engine's natural speed.
        """
        raise NotImplementedError
