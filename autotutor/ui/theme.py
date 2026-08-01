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
from typing import Sequence

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
    return style


def palette_for(name: str) -> Palette:
    return DARK if (name or "").lower() == "dark" else LIGHT
