"""Application paths and persisted user settings.

Everything the user can tweak lives in a single JSON file inside the per-user
application data directory, so the executable itself stays read-only and can be
placed anywhere (including a USB stick).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict

from .version import APP_NAME

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Directory that contains bundled read-only resources."""
    if is_frozen():
        # PyInstaller unpacks data files next to the temporary _MEIPASS root.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Directory holding the bundled corpus and other packaged data."""
    root = resource_root()
    candidates = [root / "autotutor" / "data", root / "data"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def user_data_dir() -> Path:
    """Writable per-user directory for settings and caches."""
    override = os.environ.get("AUTOTUTOR_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        path = Path(base) / APP_NAME.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    """Where exported audio/text goes unless the user picks somewhere else."""
    candidates = [Path.home() / "Documents", Path.home()]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate / f"{APP_NAME} Lessons"
    return user_data_dir() / "lessons"


def cache_dir() -> Path:
    path = user_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


SETTINGS_PATH = user_data_dir() / "settings.json"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@dataclass
class Settings:
    """User-adjustable options, persisted as JSON."""

    # Content
    level: str = "N4"
    topic: str = "daily_life"
    length: str = "medium"
    source: str = "offline"
    register: str = "auto"
    # Comma-separated online source ids; empty means "all of them".
    sources: str = ""
    show_furigana: bool = True
    show_translation: bool = True

    # Speech
    tts_engine: str = "auto"  # auto | openjtalk | sapi5 | edge
    speech_rate: float = 1.0
    sentence_pause_ms: int = 550
    paragraph_pause_ms: int = 900
    repeat_each_sentence: int = 1
    edge_voice: str = "ja-JP-NanamiNeural"
    sapi_voice: str = ""
    mp3_bitrate: int = 128

    # Network
    allow_online: bool = False
    online_translate: bool = True
    request_timeout: int = 20
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Files / UI
    output_dir: str = ""
    font_size: int = 15
    theme: str = "light"

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = str(default_output_dir())

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or SETTINGS_PATH
        settings = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(raw, dict):
            return settings
        known = {f for f in settings.__dataclass_fields__}  # type: ignore[attr-defined]
        for key, value in raw.items():
            if key in known:
                try:
                    setattr(settings, key, _coerce(getattr(settings, key), value))
                except (TypeError, ValueError):
                    continue
        settings.__post_init__()
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or SETTINGS_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            # Settings are a convenience; never let a read-only disk break the app.
            pass

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def enabled_source_ids(self) -> set:
        """Online sources the learner left switched on."""
        from .content.sources import AUTO_SOURCE_IDS

        chosen = {s.strip() for s in (self.sources or "").split(",") if s.strip()}
        return chosen & set(AUTO_SOURCE_IDS) or set(AUTO_SOURCE_IDS)

    def ensure_output_dir(self) -> Path:
        path = Path(self.output_dir).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = default_output_dir()
            path.mkdir(parents=True, exist_ok=True)
            self.output_dir = str(path)
        return path


def _coerce(current: Any, value: Any) -> Any:
    """Coerce a loaded JSON value to the type of the existing default."""
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, str):
        return str(value)
    return value
