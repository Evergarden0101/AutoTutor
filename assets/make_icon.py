#!/usr/bin/env python3
"""Generate ``assets/autotutor.ico`` without any imaging dependency.

The icon is drawn programmatically (a rounded terracotta tile with three
sound-wave bars and a playhead dot) and written as a multi-resolution ICO
containing embedded PNGs.  Keeping this in-repo means the build script can
regenerate the icon on any machine with nothing but the standard library.

    python assets/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Sequence, Tuple

RGBA = Tuple[int, int, int, int]

BG: RGBA = (180, 84, 31, 255)  # terracotta, matches the UI accent
BG_DARK: RGBA = (150, 66, 22, 255)
INK: RGBA = (255, 246, 238, 255)
DOT: RGBA = (255, 205, 138, 255)
CLEAR: RGBA = (0, 0, 0, 0)

SIZES: Sequence[int] = (16, 24, 32, 48, 64, 128, 256)


# --------------------------------------------------------------------------
# Tiny PNG writer
# --------------------------------------------------------------------------


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(pixels: List[List[RGBA]]) -> bytes:
    height = len(pixels)
    width = len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0 (None)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------


def _blend(dst: RGBA, src: RGBA, alpha: float) -> RGBA:
    alpha = max(0.0, min(1.0, alpha))
    if alpha <= 0:
        return dst
    out = []
    for index in range(3):
        out.append(int(round(dst[index] * (1 - alpha) + src[index] * alpha)))
    out.append(int(round(dst[3] * (1 - alpha) + src[3] * alpha)))
    return (out[0], out[1], out[2], out[3])


def _rounded_rect_coverage(x: float, y: float, w: float, h: float, radius: float,
                           px: float, py: float) -> float:
    """Approximate pixel coverage of a rounded rectangle (1 = inside)."""
    cx = min(max(px, x + radius), x + w - radius)
    cy = min(max(py, y + radius), y + h - radius)
    dx, dy = px - cx, py - cy
    distance = (dx * dx + dy * dy) ** 0.5
    if px < x or px > x + w or py < y or py > y + h:
        return 0.0
    # 1px feather for a smooth edge at every size.
    return max(0.0, min(1.0, radius - distance + 0.5)) if distance > 0 else 1.0


def draw_icon(size: int) -> List[List[RGBA]]:
    scale = size / 256.0
    pixels = [[CLEAR for _ in range(size)] for _ in range(size)]

    # Background tile with a soft vertical gradient.
    radius = 56 * scale
    for y in range(size):
        ratio = y / max(1, size - 1)
        base = _blend(BG, BG_DARK, ratio * 0.55)
        for x in range(size):
            coverage = _rounded_rect_coverage(
                0.0, 0.0, float(size), float(size), radius, x + 0.5, y + 0.5
            )
            if coverage > 0:
                pixels[y][x] = _blend(pixels[y][x], base, coverage)

    # Three sound-wave bars of different lengths, plus a playhead dot.
    bars = ((72, 60, 112), (108, 60, 152), (144, 60, 96))
    bar_h = 20 * scale
    for cy, x0, length in bars:
        top = cy * scale
        left = x0 * scale
        width = length * scale
        r = bar_h / 2
        for y in range(size):
            for x in range(size):
                coverage = _rounded_rect_coverage(
                    left, top, width, bar_h, r, x + 0.5, y + 0.5
                )
                if coverage > 0:
                    pixels[y][x] = _blend(pixels[y][x], INK, coverage)

    dot_cx, dot_cy, dot_r = 186 * scale, 154 * scale, 15 * scale
    for y in range(size):
        for x in range(size):
            distance = (((x + 0.5) - dot_cx) ** 2 + ((y + 0.5) - dot_cy) ** 2) ** 0.5
            coverage = max(0.0, min(1.0, dot_r - distance + 0.5))
            if coverage > 0:
                pixels[y][x] = _blend(pixels[y][x], DOT, coverage)

    return pixels


# --------------------------------------------------------------------------
# ICO container
# --------------------------------------------------------------------------


def encode_ico(images: Sequence[Tuple[int, bytes]]) -> bytes:
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    return header + bytes(entries) + bytes(payload)


def write_icon(path: Path = None) -> Path:
    path = Path(path) if path else Path(__file__).resolve().parent / "autotutor.ico"
    images = [(size, encode_png(draw_icon(size))) for size in SIZES]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_ico(images))
    return path


def write_png(size: int = 256, path: Path = None) -> Path:
    path = Path(path) if path else Path(__file__).resolve().parent / f"autotutor-{size}.png"
    path.write_bytes(encode_png(draw_icon(size)))
    return path


if __name__ == "__main__":
    icon = write_icon()
    print(f"wrote {icon} ({icon.stat().st_size} bytes)")
    png = write_png()
    print(f"wrote {png} ({png.stat().st_size} bytes)")
