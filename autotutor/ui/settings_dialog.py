"""The settings window."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Optional

from ..config import Settings
from ..content.sources import AUTO_SOURCE_IDS, SOURCES, SOURCES_BY_ID
from ..tts import list_engines
from ..tts.edge import KNOWN_VOICES
from .theme import Fonts, Palette


class SettingsDialog(tk.Toplevel):
    """Modal settings editor; writes back into the shared Settings object."""

    def __init__(
        self,
        master: tk.Misc,
        settings: Settings,
        palette: Palette,
        fonts: Fonts,
        on_saved: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self.settings = settings
        self.palette = palette
        self.fonts = fonts
        self.on_saved = on_saved

        self.title("设置 / Settings")
        self.configure(background=palette.bg)
        self.resizable(False, False)
        self.transient(master)

        self._build()
        self.update_idletasks()
        self._centre_on(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _centre_on(self, master: tk.Misc) -> None:
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:  # pragma: no cover - master not mapped yet
            pass

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        s = self.settings
        outer = ttk.Frame(self, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")

        notebook = ttk.Notebook(outer)
        notebook.grid(row=0, column=0, sticky="nsew")
        speech = ttk.Frame(notebook, padding=16)
        network = ttk.Frame(notebook, padding=16)
        sources = ttk.Frame(notebook, padding=16)
        files = ttk.Frame(notebook, padding=16)
        notebook.add(speech, text="  语音  ")
        notebook.add(network, text="  联网  ")
        notebook.add(sources, text="  搜索来源  ")
        notebook.add(files, text="  文件与界面  ")

        # ---- speech ------------------------------------------------------
        self.var_engine = tk.StringVar(value=s.tts_engine)
        ttk.Label(speech, text="语音引擎").grid(row=0, column=0, sticky="w", pady=(0, 4))
        engines = [("auto", "自动选择（推荐）")]
        for engine in list_engines():
            suffix = "" if engine.available() else "（不可用）"
            engines.append((engine.id, engine.name + suffix))
        self.engine_box = ttk.Combobox(
            speech, state="readonly", width=34,
            values=[label for _, label in engines],
        )
        self._engine_ids = [eid for eid, _ in engines]
        current = self._engine_ids.index(s.tts_engine) if s.tts_engine in self._engine_ids else 0
        self.engine_box.current(current)
        self.engine_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.engine_box.bind("<<ComboboxSelected>>", lambda _e: self._update_engine_hint())

        self.engine_hint = ttk.Label(speech, text="", style="Muted.TLabel", wraplength=380,
                                     justify="left")
        self.engine_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 14))

        ttk.Label(speech, text="Edge 在线音色").grid(row=3, column=0, sticky="w")
        self.var_voice = tk.StringVar(value=s.edge_voice)
        voice_box = ttk.Combobox(
            speech, state="readonly", width=34,
            values=[f"{v.display}  ({v.id})" for v in KNOWN_VOICES],
        )
        ids = [v.id for v in KNOWN_VOICES]
        voice_box.current(ids.index(s.edge_voice) if s.edge_voice in ids else 0)
        voice_box.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 14))
        self._voice_box = voice_box
        self._voice_ids = ids

        self.var_rate = tk.DoubleVar(value=s.speech_rate)
        self._slider(speech, 5, "语速", self.var_rate, 0.6, 1.6, "{:.2f}×")

        self.var_pause = tk.IntVar(value=s.sentence_pause_ms)
        self._slider(speech, 7, "句间停顿", self.var_pause, 0, 2000, "{:.0f} ms")

        self.var_repeat = tk.IntVar(value=s.repeat_each_sentence)
        ttk.Label(speech, text="每句重复次数").grid(row=9, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(speech, from_=1, to=5, width=6, textvariable=self.var_repeat).grid(
            row=9, column=1, sticky="e", pady=(10, 0)
        )

        self.var_bitrate = tk.IntVar(value=s.mp3_bitrate)
        ttk.Label(speech, text="MP3 码率 (kbps)").grid(row=10, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            speech, state="readonly", width=6, textvariable=self.var_bitrate,
            values=["64", "96", "128", "160", "192"],
        ).grid(row=10, column=1, sticky="e", pady=(8, 0))
        speech.columnconfigure(0, weight=1)

        # ---- network -----------------------------------------------------
        self.var_online = tk.BooleanVar(value=s.allow_online)
        ttk.Checkbutton(
            network, text="允许联网（搜索文章、在线语音、在线翻译）",
            variable=self.var_online,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            network,
            text="关闭后，程序完全离线运行：只使用内置语料库和内置语音引擎。",
            style="Muted.TLabel", wraplength=400, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        self.var_translate = tk.BooleanVar(value=s.online_translate)
        ttk.Checkbutton(
            network, text="为联网获取的文本自动生成中文翻译",
            variable=self.var_translate,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 14))

        ttk.Label(network, text="网络超时（秒）").grid(row=3, column=0, sticky="w")
        self.var_timeout = tk.IntVar(value=s.request_timeout)
        ttk.Spinbox(network, from_=5, to=120, width=6, textvariable=self.var_timeout).grid(
            row=3, column=1, sticky="e"
        )

        ttk.Separator(network, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=16
        )
        ttk.Label(network, text="Anthropic API Key（可选）").grid(row=7, column=0,
                                                                 columnspan=2, sticky="w")
        ttk.Label(
            network,
            text="填写后可使用「AI 生成」模式，按任意主题现写课文，翻译质量也更好。"
                 "密钥只保存在本机的设置文件里。",
            style="Muted.TLabel", wraplength=400, justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(2, 6))
        self.var_key = tk.StringVar(value=s.anthropic_api_key)
        self.key_entry = ttk.Entry(network, textvariable=self.var_key, show="•", width=42)
        self.key_entry.grid(row=9, column=0, sticky="ew")
        self.var_show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            network, text="显示", variable=self.var_show_key, command=self._toggle_key,
        ).grid(row=9, column=1, sticky="e", padx=(8, 0))

        ttk.Label(network, text="模型").grid(row=10, column=0, sticky="w", pady=(10, 0))
        self.var_model = tk.StringVar(value=s.anthropic_model)
        ttk.Combobox(
            network, textvariable=self.var_model, width=24,
            values=["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
        ).grid(row=10, column=1, sticky="e", pady=(10, 0))
        network.columnconfigure(0, weight=1)

        # ---- search sources ----------------------------------------------
        self._build_sources(sources)
        sources.columnconfigure(0, weight=1)

        # ---- files / ui --------------------------------------------------
        ttk.Label(files, text="导出目录").grid(row=0, column=0, columnspan=2, sticky="w")
        self.var_output = tk.StringVar(value=s.output_dir)
        entry = ttk.Entry(files, textvariable=self.var_output, width=40)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 14))
        ttk.Button(files, text="浏览…", command=self._pick_dir).grid(
            row=1, column=1, sticky="e", padx=(8, 0), pady=(4, 14)
        )

        ttk.Label(files, text="正文字号").grid(row=2, column=0, sticky="w")
        self.var_font = tk.IntVar(value=s.font_size)
        ttk.Spinbox(files, from_=11, to=28, width=6, textvariable=self.var_font).grid(
            row=2, column=1, sticky="e"
        )

        ttk.Label(files, text="界面主题").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.var_theme = tk.StringVar(value=s.theme)
        ttk.Combobox(
            files, state="readonly", width=10, textvariable=self.var_theme,
            values=["light", "dark"],
        ).grid(row=3, column=1, sticky="e", pady=(10, 0))
        ttk.Label(
            files, text="修改字号或主题后需要重新生成一次课文才会完全应用。",
            style="Muted.TLabel", wraplength=400, justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        files.columnconfigure(0, weight=1)

        # ---- buttons -----------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.grid(row=1, column=0, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).grid(row=0, column=0)
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=self._save).grid(
            row=0, column=1, padx=(8, 0)
        )

        self._update_engine_hint()

    def _build_sources(self, parent: ttk.Frame) -> None:
        """Per-source toggles for the online search.

        Unticking everything is the same as ticking everything - a search with
        no sources would simply always fail, which is never what anyone wants.
        """
        ttk.Label(
            parent,
            text="「联网搜索」会按顺序尝试下面勾选的来源，任何一个失败都会自动跳到下一个。",
            style="Muted.TLabel", wraplength=420, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        enabled = self.settings.enabled_source_ids()
        self.var_sources = {}
        row = 1
        for source in SOURCES:
            if source.id not in AUTO_SOURCE_IDS:
                continue
            var = tk.BooleanVar(value=source.id in enabled)
            self.var_sources[source.id] = var
            tag = "口语" if source.register == "spoken" else "书面"
            ttk.Checkbutton(
                parent, text=f"{source.label_zh}　·　{tag}", variable=var,
            ).grid(row=row, column=0, sticky="w")
            ttk.Label(
                parent, text=source.note_zh, style="Muted.TLabel",
                wraplength=400, justify="left",
            ).grid(row=row + 1, column=0, sticky="w", padx=(24, 0), pady=(0, 6))
            row += 2

        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, sticky="ew", pady=10
        )
        youtube = SOURCES_BY_ID.get("youtube")
        if youtube:
            ttk.Label(parent, text=f"{youtube.label_zh}　·　口语").grid(
                row=row + 1, column=0, sticky="w"
            )
            ttk.Label(
                parent,
                text=youtube.note_zh + "在「主题领域」选「自定义主题」并粘贴链接即可，不需要勾选。",
                style="Muted.TLabel", wraplength=400, justify="left",
            ).grid(row=row + 2, column=0, sticky="w", padx=(24, 0), pady=(0, 6))

    def _slider(self, parent, row, label, variable, lo, hi, fmt) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(10, 0))
        value_label = ttk.Label(parent, text=fmt.format(variable.get()), style="Muted.TLabel")
        value_label.grid(row=row, column=1, sticky="e", pady=(10, 0))

        def on_change(raw: str) -> None:
            value = float(raw)
            if isinstance(variable, tk.IntVar):
                value = int(round(value / 50) * 50)
                variable.set(value)
            value_label.configure(text=fmt.format(value))

        scale = ttk.Scale(parent, from_=lo, to=hi, variable=variable, command=on_change)
        scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew")

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.var_show_key.get() else "•")

    def _pick_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="选择导出目录", initialdir=self.var_output.get() or None, parent=self
        )
        if chosen:
            self.var_output.set(chosen)

    def _update_engine_hint(self) -> None:
        index = self.engine_box.current()
        engine_id = self._engine_ids[index] if 0 <= index < len(self._engine_ids) else "auto"
        if engine_id == "auto":
            self.engine_hint.configure(
                text="优先使用内置的离线引擎；只有在离线引擎不可用时才会考虑在线语音。"
            )
            return
        for engine in list_engines():
            if engine.id == engine_id:
                text = engine.description
                if not engine.available():
                    text += "\n⚠ " + engine.unavailable_reason()
                self.engine_hint.configure(text=text)
                return

    # -- saving ------------------------------------------------------------
    def _save(self) -> None:
        s = self.settings
        index = self.engine_box.current()
        s.tts_engine = self._engine_ids[index] if 0 <= index < len(self._engine_ids) else "auto"
        voice_index = self._voice_box.current()
        if 0 <= voice_index < len(self._voice_ids):
            s.edge_voice = self._voice_ids[voice_index]

        s.speech_rate = round(float(self.var_rate.get()), 2)
        s.sentence_pause_ms = int(self.var_pause.get())
        s.paragraph_pause_ms = max(s.sentence_pause_ms, int(s.sentence_pause_ms * 1.6))
        s.repeat_each_sentence = int(self.var_repeat.get())
        try:
            s.mp3_bitrate = int(self.var_bitrate.get())
        except (ValueError, tk.TclError):
            s.mp3_bitrate = 128

        s.allow_online = bool(self.var_online.get())
        s.online_translate = bool(self.var_translate.get())
        s.request_timeout = int(self.var_timeout.get())
        chosen = [sid for sid, var in self.var_sources.items() if var.get()]
        # All ticked is stored as "no preference", so a source added in a later
        # version is enabled rather than silently missing.
        s.sources = "" if len(chosen) == len(self.var_sources) else ",".join(chosen)
        s.anthropic_api_key = self.var_key.get().strip()
        s.anthropic_model = self.var_model.get().strip() or "claude-sonnet-5"

        s.output_dir = self.var_output.get().strip() or s.output_dir
        s.font_size = int(self.var_font.get())
        s.theme = self.var_theme.get()

        s.save()
        if self.on_saved:
            self.on_saved()
        self.destroy()
