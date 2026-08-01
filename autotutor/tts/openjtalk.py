"""Offline narration with Open JTalk.

This is the engine that makes AutoTutor genuinely offline: the voice model
ships inside the executable, so no Windows language pack and no network
connection are required.
"""

from __future__ import annotations

from typing import List

from .audio import PcmClip
from .base import TTSEngine, TTSError, Voice


class OpenJTalkEngine(TTSEngine):
    id = "openjtalk"
    name = "Open JTalk（离线内置）"
    description = "完全离线的日语合成，随程序打包，无需联网或安装语音包。"
    requires_network = False

    def __init__(self) -> None:
        self._module = None
        self._numpy = None
        self._error = ""
        try:
            import numpy
            import pyopenjtalk

            self._module = pyopenjtalk
            self._numpy = numpy
        except Exception as exc:  # pragma: no cover - depends on optional dependency
            self._error = str(exc)

    def available(self) -> bool:
        return self._module is not None

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return (
            "未安装 pyopenjtalk（`pip install pyopenjtalk-plus`）。"
            + (f" 详细信息：{self._error}" if self._error else "")
        )

    def voices(self) -> List[Voice]:
        return [Voice("default", "Open JTalk 標準音声 mei", "女性")]

    def synthesize(self, text: str, rate: float = 1.0, voice: str = "") -> PcmClip:
        if self._module is None:
            raise TTSError(self.unavailable_reason())
        text = (text or "").strip()
        if not text:
            return PcmClip(b"", 48000)

        speed = max(0.5, min(2.0, float(rate)))
        try:
            waveform, sample_rate = self._module.tts(text, speed=speed)
        except Exception as exc:
            raise TTSError(f"语音合成失败：{exc}") from exc

        np = self._numpy
        # pyopenjtalk returns float64 already scaled to the int16 range.
        samples = np.asarray(waveform, dtype=np.float64)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 32767.0:
            samples = samples * (32767.0 / peak)
        pcm = np.clip(samples, -32768, 32767).astype("<i2")
        return PcmClip(pcm.tobytes(), int(sample_rate))
