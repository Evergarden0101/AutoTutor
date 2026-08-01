"""Audio containers, MP3 encoding and playback.

Clips come in two flavours because the engines are different in kind:

* :class:`PcmClip` - raw 16-bit mono PCM, produced by the offline engines.
  Fully editable: pauses, repeats and WAV export are all trivial.
* :class:`Mp3Clip` - already-encoded MP3, produced by the online engine.
  MP3 frames of the same sample rate concatenate cleanly, which is enough to
  build a lesson with pauses; WAV export is not available for these.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

SAMPLE_WIDTH = 2  # 16-bit


class AudioError(RuntimeError):
    pass


class UnsupportedFormat(AudioError):
    pass


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------


@dataclass
class PcmClip:
    """Signed 16-bit little-endian mono PCM."""

    data: bytes
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.data) / (self.sample_rate * SAMPLE_WIDTH)

    @property
    def is_empty(self) -> bool:
        return not self.data

    @classmethod
    def silence(cls, milliseconds: int, sample_rate: int) -> "PcmClip":
        frames = max(0, int(sample_rate * milliseconds / 1000))
        return cls(b"\x00" * (frames * SAMPLE_WIDTH), sample_rate)

    def to_wav_bytes(self) -> bytes:
        import io

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(self.sample_rate)
            handle.writeframes(self.data)
        return buffer.getvalue()

    def to_mp3_bytes(self, bitrate: int = 128) -> bytes:
        return encode_mp3(self.data, self.sample_rate, bitrate)


@dataclass
class Mp3Clip:
    """A ready-made MP3 stream."""

    data: bytes
    sample_rate: int = 24000

    @property
    def duration(self) -> float:
        # Frame-accurate duration would require parsing every frame header;
        # the caller only uses this for a rough progress display.
        return 0.0

    @property
    def is_empty(self) -> bool:
        return not self.data

    def to_mp3_bytes(self, bitrate: int = 128) -> bytes:
        return self.data

    def to_wav_bytes(self) -> bytes:
        raise UnsupportedFormat(
            "在线语音（Edge TTS）直接输出 MP3，无法导出 WAV。"
            "请改用离线引擎，或直接导出 MP3。"
        )



def concat(clips: Sequence) -> Optional[object]:
    """Join clips of the same kind into a single clip."""
    clips = [c for c in clips if c is not None and not c.is_empty]
    if not clips:
        return None
    if all(isinstance(c, PcmClip) for c in clips):
        rate = clips[0].sample_rate
        if any(c.sample_rate != rate for c in clips):
            raise AudioError("无法合并采样率不同的音频片段。")
        return PcmClip(b"".join(c.data for c in clips), rate)
    if all(isinstance(c, Mp3Clip) for c in clips):
        return Mp3Clip(b"".join(c.data for c in clips), clips[0].sample_rate)
    raise AudioError("无法把 PCM 与 MP3 片段混合在一起。")


def silence_like(reference, milliseconds: int):
    """A pause in the same container format as ``reference``."""
    if isinstance(reference, Mp3Clip):
        return Mp3Clip(silent_mp3(milliseconds, reference.sample_rate), reference.sample_rate)
    rate = getattr(reference, "sample_rate", 48000)
    return PcmClip.silence(milliseconds, rate)


# --------------------------------------------------------------------------
# MP3 encoding
# --------------------------------------------------------------------------


def _encoder(sample_rate: int, bitrate: int):
    try:
        import lameenc
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        raise AudioError(
            "缺少 MP3 编码器（lameenc）。请运行 `pip install lameenc`，"
            "或改为导出 WAV 文件。"
        ) from exc
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)  # 0 = best/slowest, 9 = worst/fastest
    return encoder


def encode_mp3(pcm: bytes, sample_rate: int, bitrate: int = 128) -> bytes:
    """Encode 16-bit mono PCM to MP3."""
    encoder = _encoder(sample_rate, bitrate)
    out = bytearray(encoder.encode(pcm))
    out += encoder.flush()
    return bytes(out)


def silent_mp3(milliseconds: int, sample_rate: int = 24000, bitrate: int = 48) -> bytes:
    frames = max(0, int(sample_rate * milliseconds / 1000))
    return encode_mp3(b"\x00" * (frames * SAMPLE_WIDTH), sample_rate, bitrate)


def mp3_available() -> bool:
    try:
        import lameenc  # noqa: F401

        return True
    except ImportError:
        return False


# --------------------------------------------------------------------------
# Files and playback
# --------------------------------------------------------------------------


def write_clip(clip, path: Path, bitrate: int = 128) -> Path:
    """Write ``clip`` to ``path``; the suffix decides the format."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        path.write_bytes(clip.to_wav_bytes())
    elif suffix == ".mp3":
        path.write_bytes(clip.to_mp3_bytes(bitrate))
    else:
        raise UnsupportedFormat(f"不支持的音频格式：{suffix or '(无扩展名)'}")
    return path


class Player:
    """Plays a clip through whatever the operating system offers.

    Windows uses the MCI interface (handles both WAV and MP3 and can be
    stopped), other platforms shell out to a standard command line player.
    """

    def __init__(self) -> None:
        self._alias = "autotutor_clip"
        self._process: Optional[subprocess.Popen] = None
        self._temp: Optional[Path] = None
        self._mci_open = False

    # -- windows ----------------------------------------------------------
    def _mci(self, command: str) -> int:
        import ctypes

        return ctypes.windll.winmm.mciSendStringW(command, None, 0, None)  # type: ignore[attr-defined]

    def _play_windows(self, path: Path) -> None:
        self._stop_windows()
        self._mci(f'open "{path}" alias {self._alias}')
        self._mci_open = True
        self._mci(f"play {self._alias}")

    def _stop_windows(self) -> None:
        if self._mci_open:
            self._mci(f"stop {self._alias}")
            self._mci(f"close {self._alias}")
            self._mci_open = False

    # -- posix ------------------------------------------------------------
    _POSIX_PLAYERS = (
        ("afplay", ["afplay"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ("mpg123", ["mpg123", "-q"]),
        ("aplay", ["aplay", "-q"]),
        ("paplay", ["paplay"]),
    )

    def _play_posix(self, path: Path) -> None:
        import shutil

        for binary, argv in self._POSIX_PLAYERS:
            if shutil.which(binary):
                if binary == "aplay" and path.suffix.lower() != ".wav":
                    continue
                self._process = subprocess.Popen(
                    argv + [str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
        raise AudioError(
            "没有找到可用的播放器。请安装 ffmpeg/mpg123，或直接导出 MP3 后用其他软件播放。"
        )

    # -- api --------------------------------------------------------------
    def play(self, clip, bitrate: int = 128) -> Path:
        """Write ``clip`` to a temporary file and start playing it."""
        self.stop()
        suffix = ".wav" if isinstance(clip, PcmClip) else ".mp3"
        handle, name = tempfile.mkstemp(prefix="autotutor_", suffix=suffix)
        os.close(handle)
        path = Path(name)
        write_clip(clip, path, bitrate)
        self._temp = path
        if sys.platform.startswith("win"):
            self._play_windows(path)
        else:
            self._play_posix(path)
        return path

    def stop(self) -> None:
        if sys.platform.startswith("win"):
            self._stop_windows()
        elif self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._process.kill()
        self._process = None
        self._cleanup()

    def is_playing(self) -> bool:
        if sys.platform.startswith("win"):
            return self._mci_open
        return bool(self._process and self._process.poll() is None)

    def _cleanup(self) -> None:
        if self._temp and self._temp.exists():
            try:
                self._temp.unlink()
            except OSError:  # pragma: no cover - file still locked by the OS
                pass
        self._temp = None
