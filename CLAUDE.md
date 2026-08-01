# CLAUDE.md

Working notes for AutoTutor. The [README](README.md) explains the product; this
file covers what you need to change it safely.

## What this is

A Tkinter desktop app that generates Japanese listening practice: a graded
passage, kana readings over every kanji, a Chinese translation, and a narration
exportable as MP3. **Offline-first** — the voice model and dictionary ship
inside the executable. Online sources are opt-in and must always degrade to the
bundled corpus.

Target user reads Simplified Chinese and is learning Japanese.

## Commands

```bash
python -m autotutor                       # GUI
python -m autotutor --cli --level N5 --topic daily_life --length short
python -m pytest -q tests                 # 226 tests, ~10s
python -m pyflakes autotutor tests        # lint
python build_exe.py --clean               # build the executable
python assets/make_icon.py                # regenerate the icon
```

Headless GUI check (there is no display in most agent sandboxes):

```bash
xvfb-run -a --server-args="-screen 0 1280x900x24" python your_ui_script.py
import -window root shot.png              # ImageMagick, inside the Xvfb session
```

Requires Tk (`apt install python3-tk`). If the sandbox Python lacks Tk, look for
another interpreter that has it (`python3.12 -c "import tkinter"`) and build a
venv from that one.

## Layout

```
autotutor/
  app.py          GUI + CLI entry, argparse         console.py  stdio encoding fix
  config.py       paths, persisted Settings          models.py   Sentence/Lesson/Request
  levels.py       JLPT levels + difficulty model     topics.py   topic registry
  reading.py      furigana engines and alignment     translate.py ja→zh back-ends
  export.py       mp3/txt/html-ruby/srt/json         net.py      stdlib HTTP
  content/        corpus, offline, online, llm, service, builder
  tts/            audio (clips/mp3/playback), openjtalk, sapi, edge
  ui/             main_window, widgets, settings_dialog, theme
  data/corpus/    15 topics × N5–N1 + _common.json
launcher.py       frozen entry point (see gotcha #1)
```

Data flows: `GenerationRequest` → `content.service.generate_lesson()` → a
back-end → `content.builder.build_lesson()` (adds ruby + kana) → `Lesson` →
`tts.Narrator.narrate()` → clip + timings → `export.export_bundle()`.

## Gotchas that will bite you

These each cost real debugging time. Please keep the guarding tests.

1. **The PyInstaller entry point must be `launcher.py`, never
   `autotutor/__main__.py`.** PyInstaller runs the entry script as a top-level
   module, where `from .app import main` raises `ImportError: attempted
   relative import with no known parent package` — the exe dies instantly.
   Guarded by `TestPackaging` in `tests/test_app_and_packaging.py`.

2. **Any new entry point must call `console.configure_stdio()` before
   printing.** Windows defaults stdout to the ANSI code page (cp1252), so
   printing Japanese raises `UnicodeEncodeError`. This broke CI. Reproduce
   anywhere with `PYTHONIOENCODING=cp1252 python -m autotutor --cli ...`.
   Guarded by `TestConsoleEncoding`.

3. **Tk text widgets showing Japanese need `wrap="char"`.** With the default
   `wrap="word"`, a space-free Japanese sentence is treated as one enormous
   word and gets pushed to its own line, leaving the line above nearly empty.

4. **Ruby rendering needs line headroom.** Furigana is a text tag with a
   positive `offset`; Tk does *not* grow the line height to match, so the
   raised kana is clipped by the line above unless `spacing1`/`spacing2` are
   set. See `LessonView._configure_tags`.

5. **No emoji in ttk widgets.** The CJK UI fonts on a stock Windows or Linux
   install render them as tofu boxes. Use words.

6. **Do not use `audioop`** — removed in Python 3.13. `tts/sapi.py` does its
   WAV conversion with `array` instead.

7. **The difficulty weights in `levels.py` are fitted, not guessed.** They come
   from a least-squares fit over the labelled corpus. If you materially change
   the corpus, refit them and update the numbers quoted in the README and
   docstring. `test_score_is_monotonic_across_corpus_levels` will fail loudly
   if the model stops separating levels.

8. **Every online path must fall back to the offline corpus** and explain why
   in `lesson.warnings`. See `content/service.py`. Never let a network failure
   leave the user with nothing.

## Conventions

- **Code, comments and docstrings in English.** **User-facing strings in
  Simplified Chinese**, with Japanese for in-lesson labels (振り仮名, 単語).
  The learner reads Chinese; the content is Japanese.
- Keep the dependency list tiny. Every dependency lands in a ~150 MB
  executable, and offline operation is the core promise. `net.py` uses
  `urllib` rather than `requests` for exactly this reason.
- New optional dependencies must degrade gracefully: import inside a `try`,
  expose `available()` / `unavailable_reason()`, and keep working without it.
  See `tts/base.py` and `reading.py` for the pattern.
- Tkinter work happens on the main thread only. Background work goes through
  `AutoTutorApp._run_worker` and posts UI updates via `self._post(...)`.

## Corpus

`autotutor/data/corpus/<topic>.json`, one file per topic:

```json
{
  "id": "hospital",
  "passages": [{"level": "N5", "title_ja": "...", "title_zh": "...",
                "sentences": [{"ja": "...。", "zh": "..."}]}],
  "extras":  {"N5": [{"ja": "...。", "zh": "..."}]},
  "vocab":   {"N5": [{"word": "病院", "zh": "医院"}]}
}
```

Invariants enforced by `tests/test_corpus_and_levels.py`:

- every registered topic in `topics.py` has a corpus file, and vice versa;
- every topic covers all five levels in `passages`, `extras` and `vocab`;
- every `ja` ends with 。！？ and has a non-empty `zh`;
- no stray Latin words in the Japanese (a common drafting slip);
- passages have both titles and at least 4 sentences.

Do **not** store kana readings in the corpus — they are generated at runtime so
the printed furigana always matches the audio. Fix bad readings by adding to
`READING_OVERRIDES` in `reading.py`.

Adding a topic = add the JSON file **and** register it in `topics.py`.

## Testing

- `tests/conftest.py` points `AUTOTUTOR_HOME` at a temp directory before
  `autotutor.config` is imported, so the suite never touches real user
  settings. Keep it that way.
- Network is never touched. The online back-ends are tested by monkeypatching
  `get_json` / `get_text` / `Translator` in `content.online`
  (`TestOnlineGeneratorWithStubs`).
- Tests that need speech are `skipif`-guarded on
  `get_engine("openjtalk").available()`.
- Some sandboxes block `ja.wikipedia.org` and `www3.nhk.or.jp` at the egress
  proxy (403 on CONNECT). That is an environment policy, not a bug — do not try
  to route around it; use the stubs.

## CI

`.github/workflows/build.yml` runs the tests on Ubuntu and Windows, smoke-tests
the CLI, then builds `AutoTutor.exe` on `windows-latest` and uploads it as an
artifact. PyInstaller cannot cross-compile, so this workflow is the only way to
get a Windows exe without a Windows machine.

Deliberately, CI does **not** set `PYTHONIOENCODING` — the Windows job exercises
the real legacy-code-page path, which is what caught gotcha #2.
