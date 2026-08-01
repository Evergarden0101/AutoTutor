"""Reusable widgets: the lesson viewer and a few small helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence

from ..models import Lesson
from .theme import Fonts, Palette

# Display modes for the Japanese text.
MODE_RUBY = "ruby"
MODE_PAREN = "paren"
MODE_PLAIN = "plain"
MODE_KANA = "kana"

MODE_LABELS = (
    (MODE_RUBY, "振り仮名"),
    (MODE_PAREN, "漢字(かな)"),
    (MODE_PLAIN, "漢字のみ"),
    (MODE_KANA, "かな のみ"),
)


class ScrollingText(ttk.Frame):
    """A read-only Text widget with a themed scrollbar."""

    def __init__(
        self, master: tk.Misc, palette: Palette, fonts: Fonts, wrap: str = "word", **kwargs
    ) -> None:
        super().__init__(master)
        self.palette = palette
        self.fonts = fonts
        self.text = tk.Text(
            self,
            wrap=wrap,
            padx=22,
            pady=18,
            borderwidth=0,
            highlightthickness=0,
            background=palette.surface,
            foreground=palette.text,
            insertbackground=palette.text,
            selectbackground=palette.select,
            selectforeground=palette.text,
            spacing1=2,
            spacing3=6,
            cursor="arrow",
            font=fonts.japanese,
            **kwargs,
        )
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._read_only = True
        self.text.bind("<Key>", self._on_key)

    def _on_key(self, event: tk.Event):
        if not self._read_only:
            return None
        # Allow navigation and copying, block editing.
        allowed = {
            "Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End",
            "Control_L", "Control_R", "Shift_L", "Shift_R",
        }
        if event.keysym in allowed:
            return None
        if event.state & 0x4 and event.keysym.lower() in {"c", "a"}:
            return None
        return "break"

    def set_editable(self, editable: bool) -> None:
        self._read_only = not editable
        self.text.configure(cursor="xterm" if editable else "arrow")

    def clear(self) -> None:
        self.text.delete("1.0", "end")

    def write(self, content: str, *tags: str) -> None:
        self.text.insert("end", content, tags if tags else ())


class LessonView(ScrollingText):
    """Renders a lesson with furigana, translation and per-sentence highlight."""

    def __init__(self, master: tk.Misc, palette: Palette, fonts: Fonts) -> None:
        # Japanese has no inter-word spaces, so "word" wrapping would push a
        # whole sentence onto the next line instead of breaking it.
        super().__init__(master, palette, fonts, wrap="char")
        self.mode = MODE_RUBY
        self.show_translation = True
        self._sentence_ranges: Dict[int, tuple] = {}
        self._highlighted: Optional[int] = None
        self._configure_tags()

    def _configure_tags(self) -> None:
        palette, fonts = self.palette, self.fonts
        # Raise the reading towards the top of the kanji it belongs to, and
        # give every line enough headroom that the raised kana is not clipped
        # by the line above (Tk does not grow line height for `offset`).
        offset = max(4, int(fonts.japanese.metrics("ascent") * 0.5))
        headroom = max(4, int(fonts.ruby.metrics("linespace") * 0.7))
        self.text.tag_configure(
            "ja", font=fonts.japanese, foreground=palette.text,
            spacing1=headroom, spacing2=headroom,
        )
        self.text.tag_configure(
            "ruby", font=fonts.ruby, foreground=palette.ruby, offset=offset,
            spacing1=headroom, spacing2=headroom,
        )
        self.text.tag_configure(
            "paren", font=fonts.ruby, foreground=palette.ruby,
            spacing1=headroom, spacing2=headroom,
        )
        self.text.tag_configure(
            "zh", font=fonts.chinese, foreground=palette.muted,
            lmargin1=30, lmargin2=30, spacing1=2, spacing3=10,
        )
        self.text.tag_configure(
            "num", font=fonts.small, foreground=palette.muted,
            spacing1=headroom,
        )
        self.text.tag_configure(
            "title", font=fonts.heading, foreground=palette.text, spacing3=4,
        )
        self.text.tag_configure(
            "subtitle", font=fonts.ui, foreground=palette.muted, spacing3=16,
        )
        self.text.tag_configure(
            "meta", font=fonts.small, foreground=palette.muted, spacing3=14,
        )
        self.text.tag_configure(
            "warn", font=fonts.small, foreground=palette.warn,
            lmargin1=8, lmargin2=20, spacing3=4,
        )
        self.text.tag_configure(
            "vocab", font=fonts.ui, foreground=palette.text, lmargin1=10, lmargin2=10,
        )
        self.text.tag_configure("sep", font=fonts.small, foreground=palette.border)
        self.text.tag_configure(
            "highlight", background=palette.select,
        )
        self.text.tag_raise("highlight")
        self.text.tag_raise("ruby")

    def refresh_style(self, palette: Palette, fonts: Fonts) -> None:
        self.palette, self.fonts = palette, fonts
        self.text.configure(
            background=palette.surface, foreground=palette.text,
            selectbackground=palette.select, font=fonts.japanese,
        )
        self._configure_tags()

    # -- rendering ---------------------------------------------------------
    def show_placeholder(self, message: str) -> None:
        self.clear()
        self._sentence_ranges.clear()
        self.write(message, "meta")

    def show_lesson(self, lesson: Optional[Lesson]) -> None:
        self.clear()
        self._sentence_ranges.clear()
        self._highlighted = None
        if lesson is None:
            return

        if lesson.title_ja:
            self.write(lesson.title_ja + "\n", "title")
        if lesson.title_zh:
            self.write(lesson.title_zh + "\n", "subtitle")

        meta = [f"JLPT {lesson.level}"]
        if lesson.topic_label:
            meta.append(lesson.topic_label)
        if lesson.source_label:
            meta.append(lesson.source_label)
        meta.append(f"{len(lesson.sentences)} 句 / 约 {lesson.estimated_seconds} 秒")
        self.write("　·　".join(meta) + "\n", "meta")

        for index, sentence in enumerate(lesson.sentences):
            start = self.text.index("end-1c")
            self.write(f"{index + 1:>2}. ", "num")
            self._write_sentence(sentence)
            self.write("\n")
            if self.show_translation and sentence.zh:
                self.write(sentence.zh + "\n", "zh")
            else:
                self.write("\n", "zh")
            self._sentence_ranges[index] = (start, self.text.index("end-1c"))

        if lesson.vocab:
            self.write("\n単語 / 生词\n", "subtitle")
            for entry in lesson.vocab:
                kana = f"（{entry.kana}）" if entry.kana and entry.kana != entry.word else ""
                self.write(f"　{entry.word}{kana}", "vocab")
                self.write(f"　—　{entry.zh}\n", "zh")

        if lesson.warnings:
            self.write("\n")
            for warning in lesson.warnings:
                self.write(f"⚠ {warning}\n", "warn")

        if lesson.source_url:
            self.write(f"\n{lesson.source_url}\n", "meta")

        self.text.see("1.0")

    def _write_sentence(self, sentence) -> None:
        if self.mode == MODE_KANA:
            self.write(sentence.kana or sentence.ja, "ja")
            return
        if self.mode == MODE_PLAIN or not sentence.ruby:
            self.write(sentence.ja, "ja")
            return
        for segment in sentence.ruby:
            if not segment.needs_ruby:
                self.write(segment.text, "ja")
                continue
            if self.mode == MODE_PAREN:
                self.write(segment.text, "ja")
                self.write(f"({segment.reading})", "paren")
            else:
                self.write(segment.text, "ja")
                self.write(segment.reading, "ruby")

    # -- playback highlight ------------------------------------------------
    def highlight(self, index: Optional[int]) -> None:
        if index == self._highlighted:
            return
        self.text.tag_remove("highlight", "1.0", "end")
        self._highlighted = index
        if index is None:
            return
        span = self._sentence_ranges.get(index)
        if not span:
            return
        self.text.tag_add("highlight", span[0], span[1])
        self.text.see(span[0])


class StatusBar(ttk.Frame):
    """Message on the left, progress bar on the right."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.message = tk.StringVar(value="")
        self.label = ttk.Label(self, textvariable=self.message, style="Muted.TLabel")
        self.label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(self, mode="determinate", length=190)
        self.progress.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.progress.grid_remove()
        self.columnconfigure(1, weight=1)

    def set(self, message: str) -> None:
        self.message.set(message)

    def start(self, message: str = "", determinate: bool = False) -> None:
        if message:
            self.message.set(message)
        self.progress.grid()
        if determinate:
            self.progress.configure(mode="determinate", value=0)
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(14)

    def step(self, value: float) -> None:
        self.progress.configure(mode="determinate", value=max(0.0, min(100.0, value)))

    def stop(self, message: str = "") -> None:
        try:
            self.progress.stop()
        except tk.TclError:  # pragma: no cover - widget already destroyed
            pass
        self.progress.grid_remove()
        if message:
            self.message.set(message)


class SegmentedControl(ttk.Frame):
    """A row of mutually exclusive toggle buttons."""

    def __init__(
        self,
        master: tk.Misc,
        options: Sequence,
        variable: tk.StringVar,
        command: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master)
        self.variable = variable
        self._command = command
        self._buttons: List[ttk.Radiobutton] = []
        for column, (value, label) in enumerate(options):
            button = ttk.Radiobutton(
                self,
                text=label,
                value=value,
                variable=variable,
                style="Toolbutton",
                command=self._on_click,
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self._buttons.append(button)

    def _on_click(self) -> None:
        if self._command:
            self._command(self.variable.get())

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons:
            button.configure(state=state)
