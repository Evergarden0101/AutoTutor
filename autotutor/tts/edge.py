"""High-quality online narration through Microsoft Edge's neural voices.

Optional: the package only works with a network connection, so it is never the
default.  When it is available the audio quality is noticeably better than the
offline engines, which makes it worth offering to learners who are online
anyway (for example when they used the online article search).
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from .audio import Mp3Clip
from .base import TTSEngine, TTSError, Voice

# Shipped as a fallback so the voice picker is populated before the first
# successful network call - and *only* these two.
#
# Edge's free read-aloud endpoint is not Azure Speech: it serves a much smaller
# catalogue. ja-JP-Aoi/Daichi/Mayu/Naoki/Shiori exist in Azure and are widely
# quoted in tutorials, but this endpoint answers them with an empty stream, and
# edge-tts turns that into "No audio was received. Please verify that your
# parameters are correct." Do not add a voice here without hearing it play;
# prefer letting refresh() ask the service what it actually has.
KNOWN_VOICES: List[Voice] = [
    Voice("ja-JP-NanamiNeural", "Nanami / 七海", "女性"),
    Voice("ja-JP-KeitaNeural", "Keita / 圭太", "男性"),
]

_GENDER_ZH = {"Female": "女性", "Male": "男性"}


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
        self._live_voices: Optional[List[Voice]] = None
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
        """Never blocks: the live list is only used once :meth:`refresh` has run."""
        return list(self._live_voices or KNOWN_VOICES)

    def refresh(self) -> bool:
        """Ask the service which Japanese voices it actually serves.

        Costs a network round trip, so the UI calls it from a worker thread and
        only when the learner has allowed networking. A hardcoded list goes
        stale silently - this is what keeps the picker honest when Microsoft
        adds or retires a voice.
        """
        if self._module is None:
            return False
        try:
            entries = asyncio.run(self._module.list_voices())
        except Exception:
            return False

        found = [self._to_voice(e) for e in entries or []
                 if str(e.get("Locale", "")).lower().startswith("ja")]
        found = [v for v in found if v is not None]
        if not found:
            return False
        self._live_voices = found
        return True

    @staticmethod
    def _to_voice(entry: Dict[str, str]) -> Optional[Voice]:
        short = entry.get("ShortName") or ""
        if not short:
            return None
        # "Microsoft Nanami Online (Natural) - Japanese (Japan)" -> "Nanami"
        friendly = (entry.get("FriendlyName") or "").replace("Microsoft ", "")
        label = friendly.split(" Online")[0].strip() or short
        return Voice(short, label, _GENDER_ZH.get(entry.get("Gender", ""), ""))

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
            raise TTSError(self._explain(voice_id, exc)) from exc

        if not data:
            # The service accepted the request and streamed nothing, which is
            # what it does for a voice it does not serve.
            raise TTSError(
                f"在线语音「{voice_id}」没有返回音频，这个音色可能已经停用。"
                "请在课文上方的「朗读语音」里换一个音色。"
            )
        return Mp3Clip(data)

    @staticmethod
    def _explain(voice_id: str, exc: Exception) -> str:
        """Turn edge-tts's two failure modes into advice the learner can act on."""
        detail = str(exc)
        if "No audio was received" in detail or "NoAudioReceived" in type(exc).__name__:
            return (
                f"在线语音「{voice_id}」不可用，这个音色可能已经停用。"
                "请在课文上方的「朗读语音」里换一个音色，或改用离线语音。"
            )
        return f"在线语音合成失败（请检查网络）：{detail}"

    async def _stream(self, text: str, voice: str, rate: str) -> bytes:
        communicate = self._module.Communicate(text, voice, rate=rate)
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.extend(chunk["data"])
        return bytes(chunks)
