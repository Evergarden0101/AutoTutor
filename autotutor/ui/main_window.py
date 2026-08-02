"""The AutoTutor main window."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from ..config import Settings
from ..content import generate_lesson
from ..content.corpus import corpus_stats
from ..export import export_bundle
from ..levels import LEVEL_CODES, LEVELS, get_level
from ..models import (
    LENGTH_PRESETS,
    REGISTER_OPTIONS,
    REGISTER_SPOKEN,
    GenerationRequest,
    Lesson,
    get_length,
    get_register,
)
from ..reading import reader_name
from ..topics import CUSTOM_TOPIC, RANDOM_TOPIC, all_topics
from ..tts import (
    Narrator,
    NarrationResult,
    Player,
    VoiceChoice,
    apply_voice_choice,
    current_voice_choice,
    get_engine,
    list_engines,
    list_voice_choices,
)
from ..tts.audio import AudioError
from ..version import APP_TITLE, APP_NAME, __version__
from .settings_dialog import SettingsDialog
from .theme import apply_theme, build_fonts, palette_for
from .widgets import (
    MODE_LABELS,
    LessonView,
    ScrollingText,
    SegmentedControl,
    StatusBar,
)

LENGTH_OPTIONS = tuple((p.id, p.display) for p in LENGTH_PRESETS)
REGISTER_UI_OPTIONS = tuple((r.id, r.label_zh) for r in REGISTER_OPTIONS)
SOURCE_OPTIONS = (
    ("offline", "离线语料"),
    ("online", "联网搜索"),
    ("llm", "AI 生成"),
    ("custom", "自备文本"),
)


class AutoTutorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.palette = palette_for(self.settings.theme)
        self.fonts = build_fonts(self, self.settings.font_size)

        self.title(APP_TITLE)
        self.geometry("1120x900")
        self.minsize(940, 700)
        apply_theme(self, self.palette, self.fonts)

        self.lesson: Optional[Lesson] = None
        self.narration: Optional[NarrationResult] = None
        self.player = Player()
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._play_started = 0.0
        self._play_job: Optional[str] = None
        self._voices_refreshed = False
        # Which sentence a pending synthesis should start playing from.
        self._pending_start = 0

        self._build()
        self._pump()
        self._refresh_online_voices()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.lesson_view.show_placeholder(
            "选择级别和主题，然后点击「生成课文」。\n\n"
            "首次使用建议：级别 N5、主题 日常生活、来源 离线语料。\n"
            "程序会生成日文课文（汉字带假名注音）、中文翻译，并合成朗读音频。"
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = ttk.Frame(self, padding=(18, 14, 18, 12))
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self._build_header(root)
        self._build_controls(root)
        self._build_output(root)

        self.status = StatusBar(root)
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.status.set(self._ready_message())

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        titles = ttk.Frame(header)
        titles.grid(row=0, column=0, sticky="w")
        ttk.Label(titles, text="AutoTutor", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            titles,
            text="日本語リスニング練習ジェネレーター  ·  日语听力练习生成器",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w")

        buttons = ttk.Frame(header)
        buttons.grid(row=0, column=2, sticky="e")
        ttk.Button(buttons, text="设置", command=self.open_settings).grid(row=0, column=0)
        ttk.Button(buttons, text="关于", command=self.show_about).grid(
            row=0, column=1, padx=(8, 0)
        )

    def _build_controls(self, parent: ttk.Frame) -> None:
        card = ttk.Labelframe(parent, text=" 生成设置 ", padding=(16, 8, 16, 12))
        card.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        card.columnconfigure(1, weight=1)

        # -- level ---------------------------------------------------------
        ttk.Label(card, text="日语级别").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.var_level = tk.StringVar(value=self.settings.level)
        level_options = [(code, f"{code} · {LEVELS[code].label_zh}") for code in LEVEL_CODES]
        SegmentedControl(card, level_options, self.var_level, self._on_level_change).grid(
            row=0, column=1, sticky="w"
        )

        self.level_hint = ttk.Label(card, text="", style="Muted.TLabel", justify="left")
        self.level_hint.grid(row=1, column=1, sticky="w", pady=(3, 9))

        # -- audio length ----------------------------------------------------
        ttk.Label(card, text="音频长度").grid(row=2, column=0, sticky="w", padx=(0, 12))
        length_frame = ttk.Frame(card)
        length_frame.grid(row=2, column=1, sticky="w")
        self.var_length = tk.StringVar(value=self.settings.length)
        SegmentedControl(
            length_frame, LENGTH_OPTIONS, self.var_length,
            lambda _v: self._on_level_change(self.var_level.get()),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            length_frame, text="（朗读时长目标，内容会自动加长到接近这个时间）",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        # -- register ------------------------------------------------------
        ttk.Label(card, text="语体风格").grid(row=3, column=0, sticky="w", padx=(0, 12),
                                              pady=(9, 0))
        self.var_register = tk.StringVar(value=self.settings.register)
        SegmentedControl(
            card, REGISTER_UI_OPTIONS, self.var_register, self._on_register_change
        ).grid(row=3, column=1, sticky="w", pady=(9, 0))
        self.register_hint = ttk.Label(card, text="", style="Muted.TLabel",
                                       wraplength=820, justify="left")
        self.register_hint.grid(row=4, column=1, sticky="w", pady=(3, 0))

        # -- topic ---------------------------------------------------------
        ttk.Label(card, text="主题领域").grid(row=5, column=0, sticky="w", padx=(0, 12),
                                              pady=(9, 0))
        topic_frame = ttk.Frame(card)
        topic_frame.grid(row=5, column=1, sticky="ew", pady=(9, 0))
        topic_frame.columnconfigure(1, weight=1)

        self._topic_ids: List[str] = [t.id for t in all_topics()] + [RANDOM_TOPIC, CUSTOM_TOPIC]
        topic_labels = [t.display for t in all_topics()] + ["— 随机主题 —", "— 自定义主题 —"]
        self.topic_box = ttk.Combobox(
            topic_frame, state="readonly", values=topic_labels, width=24
        )
        index = (
            self._topic_ids.index(self.settings.topic)
            if self.settings.topic in self._topic_ids
            else 0
        )
        self.topic_box.current(index)
        self.topic_box.grid(row=0, column=0, sticky="w")
        self.topic_box.bind("<<ComboboxSelected>>", lambda _e: self._on_topic_change())

        self.var_custom_topic = tk.StringVar()
        self.custom_entry = ttk.Entry(topic_frame, textvariable=self.var_custom_topic)
        self.custom_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.custom_entry.bind("<Return>", lambda _e: self.generate())

        ttk.Button(topic_frame, text="随机一篇", command=self._randomise_topic).grid(
            row=0, column=2, padx=(10, 0)
        )

        # -- source --------------------------------------------------------
        ttk.Label(card, text="内容来源").grid(row=6, column=0, sticky="w", padx=(0, 12),
                                              pady=(9, 0))
        self.var_source = tk.StringVar(value=self.settings.source)
        SegmentedControl(card, SOURCE_OPTIONS, self.var_source, self._on_source_change).grid(
            row=6, column=1, sticky="w", pady=(9, 0)
        )
        self.source_hint = ttk.Label(card, text="", style="Muted.TLabel", wraplength=820,
                                     justify="left")
        self.source_hint.grid(row=7, column=1, sticky="w", pady=(3, 0))

        # -- actions -------------------------------------------------------
        actions = ttk.Frame(card)
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(4, weight=1)

        self.btn_generate = ttk.Button(
            actions, text="生成课文", style="Accent.TButton", command=self.generate
        )
        self.btn_generate.grid(row=0, column=0)
        self.btn_play = ttk.Button(actions, text="▶ 播放", command=self.play, state="disabled")
        self.btn_play.grid(row=0, column=1, padx=(10, 0))
        self.btn_stop = ttk.Button(actions, text="■ 停止", command=self.stop, state="disabled")
        self.btn_stop.grid(row=0, column=2, padx=(8, 0))
        self.btn_export = ttk.Button(
            actions, text="导出…", command=self.export, state="disabled"
        )
        self.btn_export.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(actions, text="Ctrl+G 生成　Ctrl+P 播放　Ctrl+E 导出",
                  style="Muted.TLabel").grid(row=0, column=4, sticky="e")

        self.bind_all("<Control-g>", lambda _e: self.generate())
        self.bind_all("<Control-p>", lambda _e: self.play())
        self.bind_all("<Control-e>", lambda _e: self.export())

        self._on_level_change(self.var_level.get())
        self._on_register_change(self.var_register.get())
        self._on_source_change(self.var_source.get())
        self._on_topic_change()

    def _build_output(self, parent: ttk.Frame) -> None:
        wrapper = ttk.Frame(parent)
        wrapper.grid(row=2, column=0, sticky="nsew")
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(wrapper)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(2, weight=1)

        ttk.Label(toolbar, text="注音显示").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.var_mode = tk.StringVar(value="ruby")
        SegmentedControl(toolbar, MODE_LABELS, self.var_mode, self._on_mode_change).grid(
            row=0, column=1, sticky="w"
        )
        self.var_show_zh = tk.BooleanVar(value=self.settings.show_translation)
        ttk.Checkbutton(
            toolbar, text="显示中文翻译", variable=self.var_show_zh,
            command=self._on_mode_change,
        ).grid(row=0, column=3, sticky="e")

        # -- voice, right where the text it will read is ---------------------
        voice_row = ttk.Frame(toolbar)
        voice_row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        voice_row.columnconfigure(1, weight=1)
        ttk.Label(voice_row, text="朗读语音").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.voice_box = ttk.Combobox(voice_row, state="readonly", width=34)
        self.voice_box.grid(row=0, column=1, sticky="w")
        self.voice_box.bind("<<ComboboxSelected>>", lambda _e: self._on_voice_change())
        self.voice_hint = ttk.Label(voice_row, text="", style="Muted.TLabel",
                                    wraplength=380, justify="left")
        self.voice_hint.grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Label(
            voice_row, text="点击任意一句前面的 ▶ 可以从那一句开始朗读",
            style="Muted.TLabel",
        ).grid(row=0, column=3, sticky="e", padx=(12, 0))
        self._voice_keys: List[str] = []
        self.refresh_voices()

        self.notebook = ttk.Notebook(wrapper)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.lesson_view = LessonView(self.notebook, self.palette, self.fonts)
        self.lesson_view.on_play_from = self.play_from
        self.notebook.add(self.lesson_view, text="  课文 / 課文  ")

        self.custom_view = ScrollingText(self.notebook, self.palette, self.fonts, wrap="char")
        self.custom_view.set_editable(True)
        self.custom_view.text.configure(font=self.fonts.japanese)
        self.custom_view.write(
            "在这里粘贴你自己的日语文本，然后把「内容来源」切换到「自备文本」并点击生成。\n"
            "程序会自动加上假名注音、中文翻译（需联网）和朗读音频。\n"
        )
        self.notebook.add(self.custom_view, text="  自备文本  ")

        self.log_view = ScrollingText(self.notebook, self.palette, self.fonts)
        self.log_view.text.configure(font=self.fonts.mono, spacing3=2)
        self.notebook.add(self.log_view, text="  运行日志  ")

    # ------------------------------------------------------------------
    # Small UI reactions
    # ------------------------------------------------------------------
    def _ready_message(self) -> str:
        engines = [e.name for e in list_engines() if e.available()]
        engine_text = "、".join(engines) if engines else "无（请安装 pyopenjtalk-plus）"
        mode = "离线模式" if not self.settings.allow_online else "已允许联网"
        return f"就绪　·　{mode}　·　语音引擎：{engine_text}　·　注音引擎：{reader_name()}"

    def _on_level_change(self, code: str) -> None:
        level = get_level(code)
        preset = get_length(self.var_length.get())
        minutes, seconds = divmod(preset.target_seconds, 60)
        self.level_hint.configure(
            text=f"{level.label_zh} / {level.label_en} — {level.description_zh}"
                 f"　·　目标时长约 {minutes} 分 {seconds:02d} 秒"
        )

    def _on_topic_change(self) -> None:
        topic_id = self._selected_topic()
        is_custom = topic_id == CUSTOM_TOPIC
        self.custom_entry.configure(state="normal" if is_custom else "disabled")
        if is_custom:
            self.custom_entry.focus_set()
            self.source_hint.configure(
                text="自定义主题在「联网搜索」或「AI 生成」下效果最好；"
                     "离线模式会自动匹配最接近的内置主题。"
            )
        else:
            self._on_source_change(self.var_source.get())

    def _on_source_change(self, source: str) -> None:
        hints = {
            "offline": "完全离线：使用内置的分级课文与人工校对的中文翻译，随时可用。",
            "online": "联网搜索 NHK News Web Easy 与日文维基百科，按所选级别筛选段落；"
                      "失败时会自动回退到离线语料。",
            "llm": "调用 Claude API 按级别和主题现写课文（需在设置里填写 API Key）。",
            "custom": "使用「自备文本」标签页里的日语文本，生成注音、翻译和音频。",
        }
        self.source_hint.configure(text=hints.get(source, ""))
        if source in {"online", "llm"} and not self.settings.allow_online:
            self.source_hint.configure(
                text=hints.get(source, "") + "\n⚠ 当前未允许联网，将自动改用离线语料。请在「设置 → 联网」中开启。"
            )
        if source == "custom":
            self.notebook.select(self.custom_view)

    def _on_register_change(self, register: str) -> None:
        option = get_register(register)
        text = option.description_zh if option else ""
        if register == REGISTER_SPOKEN:
            text += "　·　N5–N3 最完整。"
        self.register_hint.configure(text=text)

    def _on_mode_change(self, *_args) -> None:
        self.lesson_view.mode = self.var_mode.get()
        self.lesson_view.show_translation = bool(self.var_show_zh.get())
        self.lesson_view.show_lesson(self.lesson)

    def refresh_voices(self) -> None:
        """Rebuild the voice list; the online engines come and go with settings."""
        choices = list_voice_choices(self.settings.allow_online)
        self._voice_keys = [c.key for c in choices]
        self.voice_box.configure(values=[c.label for c in choices])
        if not choices:
            self.voice_box.set("")
            self.voice_box.configure(state="disabled")
            self.voice_hint.configure(text="没有可用的语音引擎，无法生成音频。")
            return

        self.voice_box.configure(state="readonly")
        current = current_voice_choice(self.settings)
        index = self._voice_keys.index(current) if current in self._voice_keys else 0
        self.voice_box.current(index)
        # Keep the settings and the box in step: "auto" resolves to whatever is
        # showing, so the next narration uses the voice the learner can see.
        apply_voice_choice(self.settings, self._voice_keys[index])
        self._describe_voice(choices[index])

    def _refresh_online_voices(self) -> None:
        """Replace the shipped Edge list with what the service actually serves.

        A hardcoded catalogue goes stale silently - offering a voice Microsoft
        has retired only fails later, at play time. One background round trip,
        and only if the learner has allowed networking.
        """
        if not self.settings.allow_online or self._voices_refreshed:
            return
        self._voices_refreshed = True
        engine = get_engine("edge")
        if engine is None or not engine.available():
            return

        def work() -> None:
            if engine.refresh():
                self._post(self.refresh_voices)

        self._run_worker(work)

    def _describe_voice(self, choice: "VoiceChoice") -> None:
        if choice.requires_network:
            self.voice_hint.configure(text="在线语音，生成时需要联网。")
        else:
            self.voice_hint.configure(text="离线语音，随时可用。")

    def _on_voice_change(self) -> None:
        index = self.voice_box.current()
        if not (0 <= index < len(self._voice_keys)):
            return
        key = self._voice_keys[index]
        apply_voice_choice(self.settings, key)
        self.settings.save()
        choice = next(
            (c for c in list_voice_choices(self.settings.allow_online) if c.key == key),
            None,
        )
        if choice:
            self._describe_voice(choice)
        self.log(f"朗读语音已切换为 {self.voice_box.get()}")
        # The rendered audio belongs to the old voice.
        self._invalidate_narration()

    def _invalidate_narration(self) -> None:
        if self.narration is None:
            return
        self.stop()
        self.narration = None
        self.status.set(self._ready_message())

    def _selected_topic(self) -> str:
        index = self.topic_box.current()
        if 0 <= index < len(self._topic_ids):
            return self._topic_ids[index]
        return self._topic_ids[0]

    def _randomise_topic(self) -> None:
        self.topic_box.current(self._topic_ids.index(RANDOM_TOPIC))
        self._on_topic_change()
        self.generate()

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.write(f"[{stamp}] {message}\n")
        self.log_view.text.see("end")

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                self._queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self.log(f"界面更新出错：{exc}")
        self.after(60, self._pump)

    def _post(self, callback: Callable[[], None]) -> None:
        self._queue.put(callback)

    def _run_worker(self, work: Callable[[], None]) -> None:
        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_generate.configure(state=state)
        self.btn_export.configure(
            state="normal" if (not busy and self.lesson) else "disabled"
        )
        self.btn_play.configure(
            state="normal" if (not busy and self.lesson) else "disabled"
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _current_request(self) -> GenerationRequest:
        topic_id = self._selected_topic()
        custom = self.var_custom_topic.get().strip() if topic_id == CUSTOM_TOPIC else ""
        custom_text = ""
        if self.var_source.get() == "custom":
            custom_text = self.custom_view.text.get("1.0", "end").strip()
        return GenerationRequest(
            level=self.var_level.get(),
            topic=topic_id,
            custom_topic=custom,
            length=self.var_length.get(),
            source=self.var_source.get(),
            register=self.var_register.get(),
            custom_text=custom_text,
            translate=True,
        )

    def _remember_choices(self) -> None:
        self.settings.level = self.var_level.get()
        self.settings.topic = self._selected_topic()
        self.settings.length = self.var_length.get()
        self.settings.source = self.var_source.get()
        self.settings.register = self.var_register.get()
        self.settings.show_translation = bool(self.var_show_zh.get())
        self.settings.save()

    def generate(self) -> None:
        if self._busy:
            return
        request = self._current_request()
        if request.source == "custom" and not request.custom_text:
            messagebox.showinfo(
                APP_NAME, "请先在「自备文本」标签页粘贴一段日语文本。", parent=self
            )
            self.notebook.select(self.custom_view)
            return
        if request.topic == CUSTOM_TOPIC and not request.topic_query:
            messagebox.showinfo(APP_NAME, "请输入一个自定义主题。", parent=self)
            self.custom_entry.focus_set()
            return

        self._remember_choices()
        self.stop()
        self._set_busy(True)
        self.status.start("正在生成课文…")
        self.log(
            f"生成：级别={request.level} 主题={request.topic_query or request.topic} "
            f"长度={request.length} 语体={request.register} 来源={request.source}"
        )

        def work() -> None:
            try:
                lesson = generate_lesson(
                    request,
                    self.settings,
                    progress=lambda msg: self._post(lambda m=msg: self.status.set(m)),
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._post(lambda e=exc: self._generation_failed(e))
                return
            self._post(lambda: self._generation_done(lesson))

        self._run_worker(work)

    def _generation_failed(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status.stop("生成失败")
        self.log(f"错误：{exc}")
        messagebox.showerror(APP_NAME, f"生成失败：\n{exc}", parent=self)

    def _generation_done(self, lesson: Lesson) -> None:
        self.lesson = lesson
        self.narration = None
        self.lesson_view.mode = self.var_mode.get()
        self.lesson_view.show_translation = bool(self.var_show_zh.get())
        self.lesson_view.show_lesson(lesson)
        self.notebook.select(self.lesson_view)
        self._set_busy(False)
        self.status.stop(
            f"课文已生成：{len(lesson.sentences)} 句 · 约 {lesson.estimated_seconds} 秒"
        )
        self.log(f"完成：{lesson.title_ja or lesson.topic_label}（{lesson.source_label}）")
        for warning in lesson.warnings:
            self.log(f"提示：{warning}")
        self._synthesize(auto=True)

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------
    def _synthesize(self, auto: bool = False, then_play: bool = False) -> None:
        if not self.lesson or self._busy:
            return
        self._set_busy(True)
        self._cancel.clear()
        self.status.start("正在合成语音…", determinate=True)

        lesson = self.lesson

        def work() -> None:
            def progress(done: int, total: int) -> None:
                percent = 100.0 * done / max(1, total)
                self._post(lambda p=percent, d=done, t=total: (
                    self.status.step(p),
                    self.status.set(f"正在合成语音… {d}/{t} 句"),
                ))

            try:
                result = Narrator(self.settings).narrate(
                    lesson, progress=progress, cancel=self._cancel
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._post(lambda e=exc: self._synthesis_failed(e))
                return
            self._post(lambda: self._synthesis_done(result, then_play))

        self._run_worker(work)

    def _synthesis_failed(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status.stop("语音合成失败")
        self.log(f"语音合成失败：{exc}")

    def _synthesis_done(self, result: NarrationResult, then_play: bool) -> None:
        self.narration = result
        self._set_busy(False)
        for warning in result.warnings:
            self.log(f"语音提示：{warning}")
        if result.clip is None:
            self.status.stop("没有生成音频")
            self.btn_play.configure(state="disabled")
            if result.warnings:
                messagebox.showwarning(APP_NAME, "\n".join(result.warnings), parent=self)
            return
        duration = result.duration
        suffix = f"，时长约 {duration:.0f} 秒" if duration else ""
        self.status.stop(f"音频已就绪（{result.engine_name}）{suffix}")
        self.log(f"音频完成：{result.engine_name}{suffix}")
        self.btn_play.configure(state="normal")
        if then_play:
            start, self._pending_start = self._pending_start, 0
            self.play(start)

    def play(self, from_index: int = 0) -> None:
        """Play the narration, optionally starting at sentence ``from_index``."""
        if not self.lesson:
            return
        if self.narration is None or self.narration.clip is None:
            self._pending_start = from_index
            self._synthesize(then_play=True)
            return

        from_index = max(0, min(from_index, len(self.lesson.sentences) - 1))
        clip = self.narration.clip_from(from_index) if from_index else self.narration.clip
        try:
            self.player.play(clip, self.settings.mp3_bitrate)
        except (AudioError, OSError) as exc:
            self.log(f"播放失败：{exc}")
            messagebox.showerror(APP_NAME, f"播放失败：\n{exc}", parent=self)
            return
        self.btn_stop.configure(state="normal")
        self.status.start("正在播放…", determinate=True)
        # Rewind the clock instead of offsetting every later calculation, so the
        # cursor tracking works unchanged whichever sentence we started at.
        self._play_started = time.monotonic() - self.narration.starts_at(from_index)
        self._track_playback()

    def play_from(self, index: int) -> None:
        """Start reading at one sentence - the ▶ next to it was clicked."""
        if self._busy:
            return
        self.stop()
        if self.lesson and 0 <= index < len(self.lesson.sentences):
            self.log(f"从第 {index + 1} 句开始播放。")
        self.play(index)

    def _track_playback(self) -> None:
        """Show where the narration has got to: marker, highlight and clock."""
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        if not self.narration or not self.narration.has_timings:
            return

        total = self.narration.duration
        elapsed = min(time.monotonic() - self._play_started, total)

        current = None
        spoken = 0
        for timing in self.narration.timings:
            if timing.start <= elapsed:
                spoken = timing.index + 1
            if timing.start <= elapsed < timing.end:
                current = timing.index
                break

        self.lesson_view.highlight(current)
        count = len(self.lesson.sentences) if self.lesson else len(self.narration.timings)
        self.status.step(100.0 * elapsed / total if total else 0.0)
        self.status.set(
            f"▶ 正在朗读　第 {max(1, spoken)} / {count} 句　"
            f"{_clock(elapsed)} / {_clock(total)}"
        )

        if elapsed < total and self.player.is_playing():
            self._play_job = self.after(120, self._track_playback)
        else:
            self.lesson_view.highlight(None)
            self.btn_stop.configure(state="disabled")
            self.status.stop(f"播放结束　·　共 {_clock(total)}")

    def stop(self) -> None:
        self._cancel.set()
        was_playing = self._play_job is not None
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        try:
            self.player.stop()
        except Exception as exc:  # pragma: no cover - defensive
            self.log(f"停止播放时出错：{exc}")
        self.lesson_view.highlight(None)
        self.btn_stop.configure(state="disabled")
        if was_playing:
            self.status.stop("已停止播放")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export(self) -> None:
        if not self.lesson:
            return
        if self.narration is None or self.narration.clip is None:
            if messagebox.askyesno(
                APP_NAME,
                "音频还没有生成。现在先合成语音吗？合成完成后再点一次「导出…」。",
                parent=self,
            ):
                self._synthesize()
            return

        dialog = ExportDialog(self, self.settings, self.palette, self.fonts,
                              has_timings=self.narration.has_timings,
                              is_mp3_only=not self.narration.has_timings)
        self.wait_window(dialog)
        if not dialog.confirmed:
            return

        directory = Path(dialog.directory)
        stem = self.lesson.slug()
        self.status.start("正在导出…")

        def work() -> None:
            try:
                result = export_bundle(
                    self.lesson,
                    directory,
                    clip=self.narration.clip if dialog.want_audio else None,
                    timings=self.narration.timings if dialog.want_srt else None,
                    stem=stem,
                    audio_format=dialog.audio_format,
                    bitrate=self.settings.mp3_bitrate,
                    include_text=dialog.want_text,
                    include_html=dialog.want_html,
                    include_srt=dialog.want_srt,
                    include_json=dialog.want_json,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._post(lambda e=exc: self._export_failed(e))
                return
            self._post(lambda: self._export_done(result, directory))

        self._run_worker(work)

    def _export_failed(self, exc: Exception) -> None:
        self.status.stop("导出失败")
        self.log(f"导出失败：{exc}")
        messagebox.showerror(APP_NAME, f"导出失败：\n{exc}", parent=self)

    def _export_done(self, result, directory: Path) -> None:
        self.status.stop(f"已导出 {len(result.files)} 个文件")
        for path in result.files:
            self.log(f"已导出：{path}")
        for warning in result.warnings:
            self.log(f"导出提示：{warning}")

        names = "\n".join(f"· {p.name}" for p in result.files)
        extra = ("\n\n" + "\n".join(result.warnings)) if result.warnings else ""
        if messagebox.askyesno(
            APP_NAME,
            f"已保存到：\n{directory}\n\n{names}{extra}\n\n要打开这个文件夹吗？",
            parent=self,
        ):
            self._open_folder(directory)

    def _open_folder(self, directory: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(directory)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(directory)])
            else:
                subprocess.Popen(["xdg-open", str(directory)])
        except Exception as exc:  # pragma: no cover - platform dependent
            self.log(f"无法打开文件夹：{exc}")

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def open_settings(self) -> None:
        SettingsDialog(self, self.settings, self.palette, self.fonts,
                       on_saved=self._settings_saved)

    def _settings_saved(self) -> None:
        self.palette = palette_for(self.settings.theme)
        self.fonts = build_fonts(self, self.settings.font_size)
        apply_theme(self, self.palette, self.fonts)
        self.lesson_view.refresh_style(self.palette, self.fonts)
        self.lesson_view.show_lesson(self.lesson)
        self.custom_view.text.configure(
            background=self.palette.surface, foreground=self.palette.text,
            font=self.fonts.japanese,
        )
        self.log_view.text.configure(
            background=self.palette.surface, foreground=self.palette.text,
            font=self.fonts.mono,
        )
        self.narration = None
        self.status.set(self._ready_message())
        self._on_source_change(self.var_source.get())
        # Turning networking off removes the online voices from the picker.
        self.refresh_voices()
        self._refresh_online_voices()
        self.log("设置已保存，音频将在下次播放时重新合成。")

    def show_about(self) -> None:
        stats = corpus_stats()
        engines = "\n".join(
            f"  · {e.name}：{'可用' if e.available() else '不可用'}"
            for e in list_engines()
        )
        messagebox.showinfo(
            f"关于 {APP_NAME}",
            f"{APP_NAME} {__version__}\n"
            "日语听力练习生成器\n\n"
            f"内置语料：{len(stats)} 个主题 / {sum(stats.values())} 个句子（N5–N1）\n"
            f"注音引擎：{reader_name()}\n"
            f"语音引擎：\n{engines}\n\n"
            "离线可用：内置语料 + Open JTalk 语音，无需联网。\n"
            "联网功能（可选）：文章搜索、在线翻译、Edge 在线语音、Claude API。",
            parent=self,
        )

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self._cancel.set()
        try:
            self.player.stop()
        except Exception:  # pragma: no cover - defensive
            pass
        self._remember_choices()
        self.destroy()


class ExportDialog(tk.Toplevel):
    """Asks which files to write and where."""

    def __init__(self, master: tk.Misc, settings: Settings, palette, fonts,
                 has_timings: bool = True, is_mp3_only: bool = False) -> None:
        super().__init__(master)
        self.settings = settings
        self.confirmed = False
        self.directory = settings.ensure_output_dir()
        self.audio_format = "mp3"
        self.want_audio = self.want_text = self.want_html = True
        self.want_srt = has_timings
        self.want_json = False

        self.title("导出课文与音频")
        self.configure(background=palette.bg)
        self.resizable(False, False)
        self.transient(master)

        frame = ttk.Frame(self, padding=18)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="选择要导出的内容").grid(row=0, column=0, columnspan=2,
                                                       sticky="w", pady=(0, 10))

        self.var_audio = tk.BooleanVar(value=True)
        self.var_text = tk.BooleanVar(value=True)
        self.var_html = tk.BooleanVar(value=True)
        self.var_srt = tk.BooleanVar(value=has_timings)
        self.var_json = tk.BooleanVar(value=False)

        ttk.Checkbutton(frame, text="音频（MP3）", variable=self.var_audio).grid(
            row=1, column=0, sticky="w")
        ttk.Checkbutton(frame, text="学习讲义（TXT：注音 / 假名 / 中文 / 生词）",
                        variable=self.var_text).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(frame, text="网页版（HTML：真正的振假名排版，可打印）",
                        variable=self.var_html).grid(row=3, column=0, sticky="w")
        srt_check = ttk.Checkbutton(frame, text="字幕（SRT：逐句时间轴）",
                                    variable=self.var_srt)
        srt_check.grid(row=4, column=0, sticky="w")
        if not has_timings:
            srt_check.configure(state="disabled")
            ttk.Label(frame, text="（在线语音无法计算时间轴）",
                      style="Muted.TLabel").grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(frame, text="课文数据（JSON）", variable=self.var_json).grid(
            row=6, column=0, sticky="w", pady=(0, 12))

        wav_row = ttk.Frame(frame)
        wav_row.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(wav_row, text="音频格式").grid(row=0, column=0, padx=(0, 10))
        self.var_format = tk.StringVar(value="mp3")
        formats = ["mp3"] if is_mp3_only else ["mp3", "wav"]
        ttk.Combobox(wav_row, state="readonly", width=6, values=formats,
                     textvariable=self.var_format).grid(row=0, column=1)

        ttk.Label(frame, text="保存位置").grid(row=8, column=0, sticky="w")
        self.var_dir = tk.StringVar(value=str(self.directory))
        ttk.Entry(frame, textvariable=self.var_dir, width=44).grid(
            row=9, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(frame, text="浏览…", command=self._pick).grid(
            row=9, column=1, sticky="e", padx=(8, 0), pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).grid(row=0, column=0)
        ttk.Button(buttons, text="导出", style="Accent.TButton",
                   command=self._confirm).grid(row=0, column=1, padx=(8, 0))

        self.update_idletasks()
        self.grab_set()

    def _pick(self) -> None:
        chosen = filedialog.askdirectory(
            title="选择保存位置", initialdir=self.var_dir.get() or None, parent=self
        )
        if chosen:
            self.var_dir.set(chosen)

    def _confirm(self) -> None:
        self.directory = Path(self.var_dir.get().strip() or self.settings.output_dir)
        self.audio_format = self.var_format.get()
        self.want_audio = bool(self.var_audio.get())
        self.want_text = bool(self.var_text.get())
        self.want_html = bool(self.var_html.get())
        self.want_srt = bool(self.var_srt.get())
        self.want_json = bool(self.var_json.get())
        if not any([self.want_audio, self.want_text, self.want_html,
                    self.want_srt, self.want_json]):
            messagebox.showinfo("导出", "请至少选择一项要导出的内容。", parent=self)
            return
        self.settings.output_dir = str(self.directory)
        self.settings.save()
        self.confirmed = True
        self.destroy()


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def run() -> None:
    app = AutoTutorApp()
    app.mainloop()
