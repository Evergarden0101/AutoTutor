"""Make the standard streams able to carry Japanese and Chinese.

Windows still defaults ``sys.stdout`` to the legacy ANSI code page (cp1252 on
a Western install, cp936 on a Chinese one), so a bare ``print()`` of Japanese
text raises ``UnicodeEncodeError`` and takes the whole program down.  Since
essentially everything this application prints is CJK, the entry points call
:func:`configure_stdio` before writing anything.

Streams that can already encode CJK are left alone, so a console deliberately
set to cp932 or UTF-8 keeps working exactly as configured.
"""

from __future__ import annotations

import sys
from typing import Iterable

# Characters the application routinely prints: kanji, kana and Simplified
# Chinese.  If a stream can encode these it needs no help.
_PROBE = "日本語・中文・かな"

_UTF8_CODE_PAGE = 65001


def _needs_utf8(stream: object) -> bool:
    """True when ``stream`` cannot represent the text we are going to print."""
    if stream is None:
        return False
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True
    try:
        _PROBE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return True
    return False


def _set_windows_console_code_page() -> None:
    """Ask the attached console to interpret our output as UTF-8.

    Only the *output* code page is touched. Changing the input code page as
    well would affect anything else sharing the console without buying us
    anything - this application never reads CJK from stdin.
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleOutputCP(_UTF8_CODE_PAGE)
    except Exception:
        # No console attached (windowed build), or the call is unavailable.
        # Reconfiguring the Python-side streams below is still worthwhile.
        pass


def configure_stdio(streams: Iterable[str] = ("stdout", "stderr")) -> None:
    """Switch stdout/stderr to UTF-8 when their current encoding cannot cope.

    Safe to call more than once, and safe in a windowed PyInstaller build
    where ``sys.stdout`` is ``None``.
    """
    targets = []
    for name in streams:
        stream = getattr(sys, name, None)
        if stream is not None and _needs_utf8(stream):
            targets.append(stream)

    if not targets:
        return

    if sys.platform.startswith("win"):
        _set_windows_console_code_page()

    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Python < 3.7 or an exotic stream replacement; nothing to do that
            # would not risk breaking whatever the host has installed.
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
