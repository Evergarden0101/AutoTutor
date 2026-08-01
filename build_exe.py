#!/usr/bin/env python3
"""One-command build of the AutoTutor executable.

    python build_exe.py

Checks the toolchain, generates the application icon if it is missing, runs
PyInstaller against ``AutoTutor.spec`` and reports where the result landed.
On Windows this produces ``dist/AutoTutor.exe``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "AutoTutor.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# This script reports progress in Chinese, which the Windows default code page
# cannot encode. autotutor.console only needs the standard library, so it is
# importable even before the project dependencies are installed.
sys.path.insert(0, str(ROOT))
try:
    from autotutor.console import configure_stdio

    configure_stdio()
except Exception:  # pragma: no cover - never let this stop a build
    pass

REQUIRED = [
    ("pyopenjtalk", "pyopenjtalk-plus"),
    ("lameenc", "lameenc"),
    ("numpy", "numpy"),
]


def check_python() -> None:
    if sys.version_info < (3, 9):
        sys.exit("需要 Python 3.9 或更高版本。")
    if sys.version_info >= (3, 15):
        print("! 提示：pyopenjtalk-plus 目前提供到 CPython 3.14 的预编译包。")


def check_imports() -> None:
    missing = []
    for module, package in REQUIRED:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        missing.append("tkinter（Windows/macOS 官方 Python 自带；Linux: apt install python3-tk）")
    if missing:
        sys.exit(
            "缺少以下依赖，请先运行 `pip install -r requirements.txt`：\n  - "
            + "\n  - ".join(missing)
        )


def check_pyinstaller() -> None:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            sys.exit("未安装 PyInstaller，请运行 `pip install pyinstaller`。")


def clean() -> None:
    for path in (BUILD, DIST):
        if path.exists():
            print(f"  清理 {path}")
            shutil.rmtree(path, ignore_errors=True)


def ensure_icon() -> None:
    icon = ROOT / "assets" / "autotutor.ico"
    if icon.exists():
        return
    try:
        from assets.make_icon import write_icon
    except Exception:
        print("! 未生成图标（可选），将使用默认图标。")
        return
    try:
        icon.parent.mkdir(parents=True, exist_ok=True)
        write_icon(icon)
        print(f"  已生成图标 {icon}")
    except Exception as exc:  # pragma: no cover - cosmetic only
        print(f"! 图标生成失败（忽略）：{exc}")


def build(extra_args) -> int:
    command = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"]
    command += list(extra_args)
    print("  " + " ".join(command))
    return subprocess.call(command, cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the AutoTutor executable")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/ first")
    parser.add_argument("--onedir", action="store_true",
                        help="build a folder instead of a single file (starts faster)")
    args, extra = parser.parse_known_args()

    print("== AutoTutor build ==")
    check_python()
    check_imports()
    check_pyinstaller()
    ensure_icon()
    if args.clean:
        clean()

    if args.onedir:
        extra.append("--onedir")

    code = build(extra)
    if code != 0:
        print("\n构建失败。请查看上面的 PyInstaller 输出。")
        return code

    exe = DIST / ("AutoTutor.exe" if sys.platform.startswith("win") else "AutoTutor")
    if exe.exists():
        size = exe.stat().st_size / (1024 * 1024)
        print(f"\n完成：{exe}  ({size:.0f} MB)")
    else:
        print(f"\n完成，输出目录：{DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
