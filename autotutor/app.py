"""Application entry point.

Also provides a small command line interface so lessons can be generated from
a script or a scheduled task without opening the window.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .config import Settings
from .console import configure_stdio
from .content import generate_lesson
from .export import export_bundle, lesson_to_text
from .levels import LEVEL_CODES
from .models import (
    LENGTH_IDS,
    LENGTH_PRESETS,
    REGISTER_IDS,
    REGISTER_OPTIONS,
    GenerationRequest,
)
from .topics import RANDOM_TOPIC, TOPIC_IDS
from .version import APP_NAME, __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autotutor",
        description=f"{APP_NAME} {__version__} - Japanese listening practice generator",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--cli", action="store_true",
                        help="generate one lesson without opening the window")
    parser.add_argument("--level", choices=LEVEL_CODES, default=None,
                        help="JLPT level (default: last used, or N4)")
    parser.add_argument("--topic", default=None,
                        help=f"topic id ({', '.join(TOPIC_IDS)}), 'random', or free text")
    parser.add_argument(
        "--length", choices=LENGTH_IDS, default=None,
        help="target narration length: " + ", ".join(
            f"{p.id} (~{p.minutes_zh})" for p in LENGTH_PRESETS
        ),
    )
    parser.add_argument("--source", choices=["offline", "online", "llm"], default=None)
    parser.add_argument(
        "--register", choices=REGISTER_IDS, default=None,
        help="how formal the Japanese sounds: " + ", ".join(
            f"{r.id} ({r.label_ja})" for r in REGISTER_OPTIONS
        ),
    )
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument("--no-audio", action="store_true", help="skip speech synthesis")
    parser.add_argument("--seed", type=int, default=None, help="make the result repeatable")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    settings = Settings.load()
    if args.source in {"online", "llm"}:
        settings.allow_online = True

    topic = args.topic or settings.topic
    custom = ""
    if topic in {"random", RANDOM_TOPIC}:
        topic = RANDOM_TOPIC
    elif topic not in TOPIC_IDS:
        custom, topic = topic, RANDOM_TOPIC

    request = GenerationRequest(
        level=args.level or settings.level,
        topic=topic,
        custom_topic=custom,
        length=args.length or settings.length,
        source=args.source or "offline",
        register=args.register or settings.register,
        seed=args.seed,
    )

    lesson = generate_lesson(request, settings, progress=lambda m: print(f"... {m}"))
    print()
    print(lesson_to_text(lesson))

    directory = Path(args.out) if args.out else settings.ensure_output_dir()
    clip = None
    timings: Optional[List] = None
    if not args.no_audio:
        from .tts import Narrator

        print("... 正在合成语音")
        result = Narrator(settings).narrate(lesson)
        for warning in result.warnings:
            print(f"    ! {warning}")
        clip, timings = result.clip, result.timings

    exported = export_bundle(
        lesson, directory, clip=clip, timings=timings,
        bitrate=settings.mp3_bitrate, include_json=False,
    )
    for path in exported.files:
        print(f"    -> {path}")
    for warning in exported.warnings:
        print(f"    ! {warning}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    # Before anything is printed: the lessons are Japanese and the messages are
    # Chinese, neither of which survives the Windows default code page.
    configure_stdio()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cli:
        return run_cli(args)

    try:
        from .ui import run
    except ImportError as exc:  # pragma: no cover - tkinter missing
        print(
            f"无法启动图形界面：{exc}\n"
            "请安装 Tk 支持（Windows/macOS 的官方 Python 自带；"
            "Debian/Ubuntu 请执行 `sudo apt install python3-tk`），"
            "或使用 `--cli` 在命令行下生成课文。",
            file=sys.stderr,
        )
        return 2

    run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
