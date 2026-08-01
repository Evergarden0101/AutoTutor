"""High-quality online narration through Microsoft Edge's neural voices.

Optional: the package only works with a network connection, so it is never the
default.  When it is available the audio quality is noticeably better than the
offline engines, which makes it worth offering to learners who are online
anyway (for example when they used the online article search).
"""

from __future__ import annotations

import asyncio
from typing import List

from .audio import Mp3Clip
from .base import TTSEngine, TTSError, Voice

# Shipped as a fallback list so the voice picker is populated before the first
# successful network call.
KNOWN_VOICES: List[Voice] = [
    Voice("ja-JP-NanamiNeural", "Nanami / 七海", "女性"),
    Voice("ja-JP-KeitaNeural", "Keita / 圭太", "男性"),
    Voice("ja-JP-AoiNeural", "Aoi / 葵", "女性"),
    Voice("ja-JP-DaichiNeural", "Daichi / 大地", "男性"),
    Voice("ja-JP-MayuNeural", "Mayu / 真由", "女性"),
    Voice("ja-JP-NaokiNeural", "Naoki / 直樹", "男性"),
    Voice("ja-JP-ShioriNeural", "Shiori / 詩織", "女性"),
]


def _rate_string(rate: float) -> str:
    """Convert a 0.5-2.0 multiplier into the ``+10%`` form Edge expects."""
    percent = int(round((max(0.5, min(2.0, rate)) - 1.0) * 100))
    return f"{percent:+d}%"


class EdgeEngine(TTSEngine):
    id = "edge"
    name = "Edge 在线语音（音质最佳）"
    description = "微软 Edge 神经网络语音，音质最自然，但需要联网。"
    requires_network = True

    def __init__(self) -> None:
        self._module = None
        self._error = ""
        try:
            import edge_tts

            self._module = edge_tts
        except Exception as exc:  # pragma: no cover - depends on optional dependency
            self._error = str(exc)

    def available(self) -> bool:
        return self._module is not None

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return (
            "未安装 edge-tts（`pip install edge-tts`）。"
            + (f" 详细信息：{self._error}" if self._error else "")
        )

    def voices(self) -> List[Voice]:
        return list(KNOWN_VOICES)

    def synthesize(self, text: str, rate: float = 1.0, voice: str = "") -> Mp3Clip:
        if self._module is None:
            raise TTSError(self.unavailable_reason())
        text = (text or "").strip()
        if not text:
            return Mp3Clip(b"")

        voice_id = voice or KNOWN_VOICES[0].id
        try:
            data = asyncio.run(self._stream(text, voice_id, _rate_string(rate)))
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"在线语音合成失败（请检查网络）：{exc}") from exc

        if not data:
            raise TTSError("在线语音服务没有返回音频数据。")
        return Mp3Clip(data)

    async def _stream(self, text: str, voice: str, rate: str) -> bytes:
        communicate = self._module.Communicate(text, voice, rate=rate)
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.extend(chunk["data"])
        return bytes(chunks)
