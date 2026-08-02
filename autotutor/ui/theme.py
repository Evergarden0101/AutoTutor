"""Fonts, colours and ttk styling.

Everything is resolved at runtime against the fonts the machine actually has,
because the executable has to look right on a plain Windows install as well as
on a developer's Linux box.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import List, Sequence

# Ordered by preference; the first family present on the system wins.
JP_FONTS: Sequence[str] = (
    "Yu Gothic UI", "Yu Gothic", "Meiryo UI", "Meiryo", "MS Gothic",
    "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "TakaoPGothic",
    "Source Han Sans JP", "DejaVu Sans",
)

UI_FONTS: Sequence[str] = (
    "Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic UI", "Segoe UI",
    "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Noto Sans SC",
    "Ubuntu", "DejaVu Sans",
)


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_alt: str
    text: str
    muted: str
    accent: str
    accent_text: str
    border: str
    ruby: str
    warn: str
    select: str
    # The sentence being read aloud. Deliberately stronger than `select`, which
    # is the text-selection tint: sharing it made the reading indicator almost
    # invisible against the white page.
    reading: str
    reading_edge: str


LIGHT = Palette(
    bg="#f4f2ed",
    surface="#ffffff",
    surface_alt="#faf8f4",
    text="#20201d",
    muted="#6d6a63",
    accent="#b4541f",
    accent_text="#ffffff",
    border="#ddd8cd",
    ruby="#8a6d3b",
    warn="#9a6b16",
    select="#f0e2d2",
    reading="#ffe0b8",
    reading_edge="#b4541f",
)

DARK = Palette(
    bg="#17171a",
    surface="#1f1f23",
    surface_alt="#25252a",
    text="#e9e7e2",
    muted="#9b978f",
    accent="#d97742",
    accent_text="#1a1a1c",
    border="#33333a",
    ruby="#c9a46a",
    warn="#d8a350",
    select="#3a3129",
    reading="#54402c",
    reading_edge="#f09a5c",
)


def pick_font(root: tk.Misc, candidates: Sequence[str], fallback: str = "TkDefaultFont") -> str:
    available = {name.lower() for name in tkfont.families(root)}
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


@dataclass
class Fonts:
    ui: tkfont.Font
    ui_bold: tkfont.Font
    heading: tkfont.Font
    japanese: tkfont.Font
    ruby: tkfont.Font
    chinese: tkfont.Font
    small: tkfont.Font
    mono: tkfont.Font


def build_fonts(root: tk.Misc, size: int = 15) -> Fonts:
    jp = pick_font(root, JP_FONTS)
    ui = pick_font(root, UI_FONTS)
    mono = pick_font(root, ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"), "TkFixedFont")
    base = max(9, min(13, size - 3))
    return Fonts(
        ui=tkfont.Font(family=ui, size=base),
        ui_bold=tkfont.Font(family=ui, size=base, weight="bold"),
        heading=tkfont.Font(family=ui, size=base + 5, weight="bold"),
        japanese=tkfont.Font(family=jp, size=size),
        ruby=tkfont.Font(family=jp, size=max(7, int(size * 0.58))),
        chinese=tkfont.Font(family=ui, size=max(9, size - 4)),
        small=tkfont.Font(family=ui, size=max(8, base - 1)),
        mono=tkfont.Font(family=mono, size=base - 1),
    )


def apply_theme(root: tk.Tk, palette: Palette, fonts: Fonts) -> ttk.Style:
    style = ttk.Style(root)
    # 'clam' is the only built-in theme that honours background colours on
    # every platform; the native themes ignore most of what we set here.
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - very old Tk
        pass

    root.configure(background=palette.bg)

    style.configure(".", background=palette.bg, foreground=palette.text, font=fonts.ui)
    style.configure("TFrame", background=palette.bg)
    style.configure("Card.TFrame", background=palette.surface, relief="flat")
    style.configure("TLabel", background=palette.bg, foreground=palette.text)
    style.configure("Muted.TLabel", background=palette.bg, foreground=palette.muted,
                    font=fonts.small)
    style.configure("Heading.TLabel", background=palette.bg, foreground=palette.text,
                    font=fonts.heading)
    style.configure("Card.TLabel", background=palette.surface, foreground=palette.text)
    style.configure("CardMuted.TLabel", background=palette.surface,
                    foreground=palette.muted, font=fonts.small)
    style.configure("Warn.TLabel", background=palette.bg, foreground=palette.warn,
                    font=fonts.small)

    style.configure("TLabelframe", background=palette.bg, foreground=palette.muted,
                    bordercolor=palette.border, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=palette.bg, foreground=palette.muted,
                    font=fonts.small)

    style.configure(
        "TButton",
        background=palette.surface,
        foreground=palette.text,
        bordercolor=palette.border,
        focuscolor=palette.accent,
        padding=(12, 7),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", palette.select), ("disabled", palette.surface_alt)],
        foreground=[("disabled", palette.muted)],
    )

    style.configure(
        "Accent.TButton",
        background=palette.accent,
        foreground=palette.accent_text,
        padding=(16, 9),
        font=fonts.ui_bold,
        relief="flat",
        bordercolor=palette.accent,
    )
    style.map(
        "Accent.TButton",
        background=[("active", palette.accent), ("disabled", palette.border)],
        foreground=[("disabled", palette.muted)],
    )

    style.configure(
        "TRadiobutton", background=palette.bg, foreground=palette.text, padding=(2, 3)
    )
    style.map("TRadiobutton", background=[("active", palette.bg)])
    style.configure("TCheckbutton", background=palette.bg, foreground=palette.text)
    style.map("TCheckbutton", background=[("active", palette.bg)])

    style.configure(
        "Toolbutton",
        background=palette.surface,
        foreground=palette.muted,
        bordercolor=palette.border,
        padding=(13, 6),
        relief="flat",
        font=fonts.ui,
    )
    style.map(
        "Toolbutton",
        background=[("selected", palette.accent), ("active", palette.select)],
        foreground=[("selected", palette.accent_text)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=palette.surface,
        background=palette.surface,
        foreground=palette.text,
        bordercolor=palette.border,
        arrowcolor=palette.muted,
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette.surface)],
        selectbackground=[("readonly", palette.surface)],
        selectforeground=[("readonly", palette.text)],
    )
    root.option_add("*TCombobox*Listbox.background", palette.surface)
    root.option_add("*TCombobox*Listbox.foreground", palette.text)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", palette.accent_text)
    root.option_add("*TCombobox*Listbox.font", fonts.ui)

    style.configure(
        "TEntry",
        fieldbackground=palette.surface,
        foreground=palette.text,
        bordercolor=palette.border,
        insertcolor=palette.text,
        padding=5,
    )
    style.configure("TSpinbox", fieldbackground=palette.surface, foreground=palette.text,
                    bordercolor=palette.border, arrowcolor=palette.muted, padding=4)

    style.configure("TNotebook", background=palette.bg, bordercolor=palette.border,
                    tabmargins=(2, 6, 2, 0))
    style.configure(
        "TNotebook.Tab",
        background=palette.bg,
        foreground=palette.muted,
        padding=(16, 8),
        font=fonts.ui,
        bordercolor=palette.border,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", palette.surface)],
        foreground=[("selected", palette.text)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    style.configure(
        "Horizontal.TProgressbar",
        background=palette.accent,
        troughcolor=palette.surface_alt,
        bordercolor=palette.border,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        thickness=6,
    )
    style.configure("TScale", background=palette.bg, troughcolor=palette.surface_alt)
    style.configure("TSeparator", background=palette.border)
    style.configure(
        "Vertical.TScrollbar",
        background=palette.surface_alt,
        troughcolor=palette.bg,
        bordercolor=palette.bg,
        arrowcolor=palette.muted,
        relief="flat",
    )
    style.map("Vertical.TScrollbar", background=[("active", palette.border)])

    install_check_indicator(root, style, palette)
    return style


def palette_for(name: str) -> Palette:
    return DARK if (name or "").lower() == "dark" else LIGHT


# --------------------------------------------------------------------------
# Check marks
# --------------------------------------------------------------------------

# The clam theme draws a diagonal cross in a ticked checkbox, which reads as
# "wrong" rather than "on". These images replace it with a real check mark.
# Tk garbage-collects PhotoImage objects that nothing references, so the ones
# in use are kept alive here.
_CHECK_IMAGES: List[tk.PhotoImage] = []
_CHECK_SERIAL = [0]


def _draw_box(image: tk.PhotoImage, size: int, palette: Palette, fill: str) -> None:
    """Rounded-ish square: a filled box with the corner pixels left blank."""
    image.put(palette.bg, to=(0, 0, size, size))
    image.put(fill, to=(1, 1, size - 1, size - 1))
    for x, y in ((1, 1), (size - 2, 1), (1, size - 2), (size - 2, size - 2)):
        image.put(palette.bg, to=(x, y, x + 1, y + 1))


def _draw_check(image: tk.PhotoImage, size: int, colour: str) -> None:
    """A two-stroke check mark scaled to ``size``."""
    scale = size / 16.0

    def stroke(x0: float, y0: float, x1: float, y1: float) -> None:
        steps = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) * 3))
        for step in range(steps + 1):
            t = step / steps
            x = int(round(x0 + (x1 - x0) * t))
            y = int(round(y0 + (y1 - y0) * t))
            # 2px pen so the mark stays visible at small sizes.
            for dx in (0, 1):
                for dy in (0, 1):
                    px, py = x + dx, y + dy
                    if 1 <= px < size - 1 and 1 <= py < size - 1:
                        image.put(colour, to=(px, py, px + 1, py + 1))

    stroke(3.5 * scale, 8.0 * scale, 6.5 * scale, 11.0 * scale)
    stroke(6.5 * scale, 11.0 * scale, 12.0 * scale, 4.5 * scale)


def install_check_indicator(root: tk.Misc, style: ttk.Style, palette: Palette,
                            size: int = 16) -> None:
    """Point ttk check buttons at a drawn check mark instead of clam's cross."""
    try:
        unchecked = tk.PhotoImage(master=root, width=size, height=size)
        checked = tk.PhotoImage(master=root, width=size, height=size)
    except tk.TclError:  # pragma: no cover - no display
        return

    _draw_box(unchecked, size, palette, palette.surface)
    _draw_box(checked, size, palette, palette.accent)
    _draw_check(checked, size, palette.accent_text)

    _CHECK_IMAGES.clear()
    _CHECK_IMAGES.extend([unchecked, checked])

    # An element name can only be created once per interpreter, so each theme
    # change registers a fresh one.
    _CHECK_SERIAL[0] += 1
    element = f"AutoTutorCheck{_CHECK_SERIAL[0]}.indicator"
    try:
        style.element_create(
            element, "image", unchecked,
            ("selected", checked),
            ("alternate", checked),
            sticky="", padding=1,
        )
    except tk.TclError:  # pragma: no cover - element already present
        return

    style.layout(
        "TCheckbutton",
        [
            ("Checkbutton.padding", {
                "sticky": "nswe",
                "children": [
                    (element, {"side": "left", "sticky": ""}),
                    ("Checkbutton.focus", {
                        "side": "left", "sticky": "w",
                        "children": [("Checkbutton.label", {"sticky": "nswe"})],
                    }),
                ],
            }),
        ],
    )
