"""Windows built-in speech (SAPI 5 / System.Speech).

Driven through PowerShell's ``-EncodedCommand`` so that no COM bindings need
to be installed and so Japanese text never has to survive a code-page round
trip on the command line.  Requires a Japanese voice to be installed in
Windows (Settings -> Time & language -> Speech).
"""

from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from .audio import PcmClip
from .base import TTSEngine, TTSError, Voice

_CREATE_NO_WINDOW = 0x08000000


def _run_powershell(script: str, timeout: int = 120) -> str:
    """Run ``script`` and return its stdout."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    argv = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
    ]
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise TTSError("找不到 PowerShell，无法使用 Windows 内置语音。") from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSError("Windows 语音合成超时。") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise TTSError(f"Windows 语音合成失败：{detail[:300] or result.returncode}")
    return result.stdout.decode("utf-8", errors="replace")


def _ps_quote(text: str) -> str:
    """Quote a string for a PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


class SapiEngine(TTSEngine):
    id = "sapi5"
    name = "Windows 内置语音（离线）"
    description = "使用 Windows 自带的日语语音（需在系统中安装日语语音包），完全离线。"
    requires_network = False

    def __init__(self) -> None:
        self._voices: Optional[List[Voice]] = None

    def available(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            return bool(self.voices())
        except TTSError:
            return False

    def unavailable_reason(self) -> str:
        if not sys.platform.startswith("win"):
            return "Windows 内置语音仅在 Windows 上可用。"
        return (
            "系统中没有找到日语语音包。请在「设置 → 时间和语言 → 语音」中"
            "添加日语（日本）语音后重试。"
        )

    def voices(self) -> List[Voice]:
        if self._voices is not None:
            return self._voices
        if not sys.platform.startswith("win"):
            self._voices = []
            return self._voices

        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$s.GetInstalledVoices() | ForEach-Object {"
            "  $i = $_.VoiceInfo;"
            "  Write-Output ($i.Name + '|' + $i.Culture.Name + '|' + $i.Gender)"
            "};"
            "$s.Dispose()"
        )
        try:
            output = _run_powershell(script, timeout=60)
        except TTSError:
            self._voices = []
            return self._voices

        voices: List[Voice] = []
        for line in output.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2 or not parts[0]:
                continue
            name, culture = parts[0], parts[1]
            gender = parts[2] if len(parts) > 2 else ""
            if not culture.lower().startswith("ja"):
                continue
            voices.append(Voice(name, name, gender))
        self._voices = voices
        return voices

    def synthesize(self, text: str, rate: float = 1.0, voice: str = "") -> PcmClip:
        text = (text or "").strip()
        if not text:
            return PcmClip(b"", 22050)
        if not sys.platform.startswith("win"):
            raise TTSError(self.unavailable_reason())

        available = self.voices()
        if not available:
            raise TTSError(self.unavailable_reason())
        chosen = voice if any(v.id == voice for v in available) else available[0].id

        # SAPI rate is an integer from -10 (slow) to 10 (fast).
        sapi_rate = int(round((max(0.5, min(2.0, rate)) - 1.0) * 8))

        with tempfile.TemporaryDirectory(prefix="autotutor_sapi_") as tmp:
            wav_path = Path(tmp) / "speech.wav"
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.SelectVoice({_ps_quote(chosen)});"
                f"$s.Rate = {sapi_rate};"
                f"$s.SetOutputToWaveFile({_ps_quote(str(wav_path))});"
                f"$s.Speak({_ps_quote(text)});"
                "$s.Dispose()"
            )
            _run_powershell(script)
            if not wav_path.exists() or wav_path.stat().st_size == 0:
                raise TTSError("Windows 语音没有生成音频文件。")
            return _read_wav(wav_path)


def _read_wav(path: Path) -> PcmClip:
    """Read a WAV file into 16-bit mono PCM.

    Written against :mod:`array` rather than :mod:`audioop` because the latter
    was removed in Python 3.13.
    """
    import array
    import wave

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if width == 1:  # unsigned 8-bit
        samples = array.array("h", ((b - 128) * 256 for b in frames))
    elif width == 2:
        samples = array.array("h")
        samples.frombytes(frames)
        if sys.byteorder == "big":
            samples.byteswap()
    elif width == 4:
        wide = array.array("i")
        wide.frombytes(frames)
        if sys.byteorder == "big":
            wide.byteswap()
        samples = array.array("h", (v >> 16 for v in wide))
    else:
        raise TTSError(f"不支持的 WAV 位深：{width * 8} bit")

    if channels > 1:
        mixed = array.array("h")
        for index in range(0, len(samples) - channels + 1, channels):
            total = sum(samples[index: index + channels])
            mixed.append(max(-32768, min(32767, total // channels)))
        samples = mixed

    if sys.byteorder == "big":
        samples.byteswap()
    return PcmClip(samples.tobytes(), rate)
