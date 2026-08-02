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
python -m autotutor --cli --level N3 --topic technology --length xlong   # ~8 min
python -m pytest -q tests                 # 498 tests, ~29s
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
  content/        corpus, offline, online, sources, llm, service, builder
  tts/            audio (clips/mp3/playback), openjtalk, sapi, edge
  ui/             main_window, widgets, settings_dialog, theme
  data/corpus/    15 topics × N5–N1 × 2 registers (1012 sentences) + _common.json
launcher.py       frozen entry point (see gotcha #1)
```

Data flows: `GenerationRequest` → `content.service.generate_lesson()` → a
back-end → `content.builder.build_lesson()` (adds ruby + kana) → `Lesson` →
`tts.Narrator.narrate()` → clip + timings → `export.export_bundle()`.

**Length is a duration, not a sentence count.** `models.LENGTH_PRESETS` maps
short/medium/long/xlong onto ~60/130/240/450 seconds of narration, and
`models.estimate_seconds()` converts text to seconds using constants measured
against the bundled voice (6.9 kana/s, 1.28 kana per written character). The
offline composer keeps pulling blocks until the budget is met, widening from
"more passages on this topic" to "neighbouring levels" to "another topic" to
"loose example sentences", and records each widening in `lesson.warnings`.

**That widening order is load-bearing.** Whole passages always come before
`extras`, which are single facts glued together with それから and read as a
list rather than a paragraph; they used to be taken second and made a medium
lesson listy long before it had to be. Widening also only triggers below
`_CLOSE_ENOUGH` of the budget - reaching to another level for the last three
seconds costs a warning and buys material that `_trim_to_budget` throws away.

**Register (语体) is a preference, not a filter.** `GenerationRequest.register`
is auto/spoken/written. Conversational passages exist at N5–N3 only, so a
spoken request above that falls back to the neutral です・ます passages and
warns. When it needs more material it looks for the *same register one level
away* before taking another register at the requested level - a casual talk
that switches to です・ます halfway sounds like two speakers, and one level off
with a consistent voice does not. `corpus.passages_for(level, register)` returns
`wanted + neutral`, and `offline._effective_register()` decides from what was
*actually* collected which opener/closer to use — a casual talk that opens with
みなさん、こんにちは sounds like two speakers spliced together.

**Online sources live in `content/sources.py`.** Each `Source` is a small
independent fetcher with an `id`, a register and a list of levels it suits;
`sources_for(register, level)` orders them and `online.OnlineGenerator._collect`
walks that order, skipping ones the user disabled and collecting failures rather
than aborting. Adding a source = add the fetch function and one `Source(...)`
entry; the settings dialog builds its checkboxes from the registry. YouTube is
in `SOURCES` but not `AUTO_SOURCE_IDS` — it needs a URL, it is never searched.

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
   if the model stops separating levels. This has happened twice: the corpus
   rewrite to longer passages moved every feature, and adding conversational
   passages broke the written-style feature outright (see #11). Drop any
   feature whose fitted coefficient comes out negative — that is collinearity,
   not signal — and any feature that inverts the level ordering, which is how
   `compound_ratio` came out of the model in the second refit.

8. **Every online path must fall back to the offline corpus** and explain why
   in `lesson.warnings`. See `content/service.py`. Never let a network failure
   leave the user with nothing.

9. **A ttk element name can only be created once per interpreter.** The check
   mark that replaces clam's ✕ is an image element; switching theme has to
   register a *new* element name (`theme._CHECK_SERIAL`) rather than redefining
   the old one. Keep a Python reference to every `PhotoImage` too, or Tk
   garbage-collects it and the indicator silently disappears.

10. **The playback cursor glyph is always in the text, only its colour
    changes.** `LessonView` tracks per-sentence index ranges; inserting or
    deleting a ▶ during playback would invalidate them, so the glyph is written
    once in the widget background colour and re-tagged in the accent colour
    when that sentence is being read.

11. **常体 is not 書き言葉.** The written-style feature used to be
    `1 - polite_ratio`, which only worked because the corpus was polite at
    N5–N3 and plain at N2–N1. Conversational passages are plain *and* easy, so
    that proxy scored 過ごしてるよ as harder than 過ごしています. Colloquial
    endings (`_CASUAL_END_RE`, `_CASUAL_INLINE_RE` in `levels.py`) are now
    subtracted out. Match colloquial markers **at the end of a sentence** — a
    substring search finds かな inside 追いつかない and んだ inside 進んだ, and
    labels an editorial as casual. Guarded by
    `TestRegister::test_written_markers_beat_lookalike_substrings` and
    `test_plain_casual_scores_easier_than_plain_formal`.

12. **The anti-repeat history defeats a seed.** `OfflineGenerator._recent`
    spans calls, so the same seed gave a different lesson the second time round
    once the corpus had more than one passage per level to choose from.
    `_collect_blocks(avoid_repeats=request.seed is None)` keeps both promises.
    Guarded by `test_seed_makes_it_repeatable` and `test_repeated_calls_vary`.

13. **Ranking online candidates on level alone throws the register away.** An
    encyclopedia article is almost always a closer level match than a podcast
    note, so `online.generate` adds `_article_register_cost` (worth about one
    JLPT level) to the sort key. `_coverage_cost` is worth the same and pulls
    the other way that matters: a source long enough to carry the whole lesson
    beats a closer-level fragment, because stitching four snippets together to
    hit the duration is not listening practice. Guarded by
    `test_register_beats_a_closer_level_match`, `test_a_large_level_gap_still_wins`
    and `test_a_source_that_can_carry_the_lesson_wins`.

14. **Do not hardcode the Edge voice catalogue.** Edge's free read-aloud
    endpoint is not Azure Speech and serves far fewer voices.
    ja-JP-Aoi/Daichi/Mayu/Naoki/Shiori exist in Azure, are all over the
    tutorials, and make edge-tts fail with "No audio was received. Please
    verify that your parameters are correct." Only Nanami and Keita are
    shipped; `EdgeEngine.refresh()` asks the service for the real list in the
    background. `voices()` must never block on the network - it is called
    while building the UI. Guarded by `TestEdgeVoices`.

15. **Every topic has a conversational retelling of its polite passage, and
    playing both says the same thing twice.** `offline._collect_blocks` skips a
    candidate whose multi-kanji vocabulary overlaps an already-taken block by
    more than `_OVERLAP_LIMIT`. That threshold is measured, not guessed: across
    the corpus, unrelated passages sit at a median of 0.00 and p90 of 0.15,
    while a casual retelling overlaps its polite twin at 0.32-0.39. Guarded by
    `test_the_same_story_is_not_told_twice`.

16. **A descriptive User-Agent gets 401 from Japanese news CDNs.** NHK News Web
    Easy's article list is public and unauthenticated but sits behind a WAF
    that rejects anything non-browser, which killed the one source beginners
    most need. `net.USER_AGENT` identifies as a browser and sends Accept /
    Accept-Language. `NHK_EASY_LISTS` also holds every known URL for that file
    because NHK has moved it before.

17. **Determinism is the enemy of the online mode.** A ranked pipeline returns
    the same lesson for a topic forever, which is what users notice first.
    Every pick is sampled instead: `sources._sample` for feeds, entries and
    search hits, `sources_for` shuffling within each register/level tier, and
    `online.generate` choosing at random among candidates within `_TIE_BAND` of
    the best score. Keep new selection code sampling. Guarded by
    `test_ordering_varies_between_calls`,
    `test_the_lead_entry_varies_between_calls` and
    `test_repeated_searches_do_not_return_the_same_article`; the stub tests
    pass a seeded `rng` so they stay reproducible.

18. **Register needs positive evidence on both sides.** `written_ratio`
    ("plain and not colloquial") is a fine *difficulty* feature but a bad
    register test: real speech is full of unmarked plain sentences, so
    減らすくらいならできそう scored as an essay. `colloquial_score` uses
    `literary_ratio` (`_LITERARY_RE`: である, における, とされる …) instead.
    Measured over the corpus the bands do not overlap - spoken 0.57-1.00,
    neutral ~0.50, written 0.07-0.43 - and
    `test_the_corpus_registers_land_in_their_own_bands` fails if they start to.

19. **Seeking is by byte offset, not by seconds.** `concat()` joins clips byte
    for byte, so `offset_bytes` is exact for both containers, which is what
    lets "play from this sentence" work on the online engine too.
    `play(from_index)` then rewinds `_play_started` by `starts_at(index)` so
    the cursor tracking needs no other change. Guarded by `TestSeeking`.

20. **`Mp3Clip.duration` reads the frame headers.** It used to return 0.0 with
    a comment about needing a decoder, which quietly disabled the whole
    playback cursor on the online engine: no duration meant `has_timings` was
    False meant the highlight never moved, with nothing said about it. Walking
    the headers needs no decoder and is exact for VBR too. Memoise it - the
    cursor asks eight times a second - and keep `_duration` `init=False` so
    `dataclasses.replace` recomputes for the new bytes. Guarded by
    `TestMp3Duration` and `test_the_cursor_follows_an_mp3_narration_too`.

21. **Never split a sentence through a quote.** Japanese punctuates *inside*
    「」, so splitting on 。 alone turns 「行かへん？」と誘われた into a fragment
    plus an orphan starting with 」 — which is what the online sources were
    serving. `levels.split_sentences` tracks bracket depth. Guarded by
    `TestSentenceSplitting`.

22. **A smooth ranking cost picks the same winner forever.** Podcast feeds are
    piles of similar-length episode notes; with a continuous `_coverage_cost`
    whichever was a few sentences longer won every search, so users saw one
    episode number again and again. The cost is banded so near-equal sources
    tie and `_TIE_BAND` sampling decides. Guarded by
    `test_similar_length_episodes_all_get_a_turn`.

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
  "passages": [{"level": "N5", "register": "spoken",
                "title_ja": "...", "title_zh": "...",
                "sentences": [{"ja": "...。", "zh": "..."}]}],
  "extras":  {"N5": [{"ja": "...。", "zh": "..."}]},
  "vocab":   {"N5": [{"word": "病院", "zh": "医院"}]}
}
```

`register` is optional; without it `corpus.infer_register()` derives one from
the text. Explicit tags exist so the conversational passages cannot be lost by
a change to the heuristic — and `test_tags_agree_with_inference` fails if a tag
stops matching what the passage actually reads like, so the two can never drift
apart.

Invariants enforced by `tests/test_corpus_and_levels.py`:

- every registered topic in `topics.py` has a corpus file, and vice versa;
- every topic covers all five levels in `passages`, `extras` and `vocab`, and
  has conversational passages at N5, N4 and N3;
- every `ja` ends with 。！？ and has a non-empty `zh`;
- one record holds exactly one sentence (the playback cursor highlights whole
  records) — quoted speech may keep an inner 。;
- no stray Latin words in the Japanese (a common drafting slip);
- passages have both titles and at least 6 sentences (5 when conversational);
- passage sentences average enough characters per level that the result reads
  as a paragraph rather than a list of one-line facts, with a lower floor for
  conversational passages because speech really is made of shorter turns
  (`test_passages_read_as_paragraphs`).

Passages are written as developed paragraphs with an arc - situation,
development, complication, reflection - because the composer now plays whole
passages rather than padding with unrelated sentences. Longer sentences at low
levels come from coordination (て-form chains, から/ので), not from grammar
above the level.

Conversational passages use 常体 with 終助詞 (ね・よ・んだ・けど), contractions
(てる, ちゃう) and a first-person voice. Keep them within the grammar of their
level: casual is a register, not a difficulty. `_common.json` carries matching
casual openers, transitions and closers, tagged `"register": "spoken"`.

Do **not** store kana readings in the corpus — they are generated at runtime so
the printed furigana always matches the audio. Fix bad readings by adding to
`READING_OVERRIDES` in `reading.py`.

Adding a topic = add the JSON file **and** register it in `topics.py`.

## Testing

- `tests/conftest.py` points `AUTOTUTOR_HOME` at a temp directory before
  `autotutor.config` is imported, so the suite never touches real user
  settings. Keep it that way.
- Network is never touched. `TestOnlineGeneratorWithStubs` replaces
  `sources.SOURCES` with fetchers that return canned `Article`s, so the tests
  check *which source is chosen and how its text is cut down* rather than
  re-testing ElementTree. Parsing has its own fixture-driven tests
  (`TestFeedParsing`, `TestYouTubeCaptions`) that never construct a generator.
- Tests that need speech are `skipif`-guarded on
  `get_engine("openjtalk").available()`.
- Some sandboxes block every Japanese content host at the egress proxy
  (`ja.wikipedia.org`, `www3.nhk.or.jp`, `www.nhk.or.jp`, `ja.wikinews.org`,
  `www.youtube.com`, the podcast CDNs) with a 403 on CONNECT. That is an
  environment policy, not a bug — do not try to route around it; use the
  fixtures, and say plainly that the live paths were not exercised.

## CI

`.github/workflows/build.yml` runs the tests on Ubuntu and Windows, smoke-tests
the CLI, then builds `AutoTutor.exe` on `windows-latest` and uploads it as an
artifact. PyInstaller cannot cross-compile, so this workflow is the only way to
get a Windows exe without a Windows machine.

Deliberately, CI does **not** set `PYTHONIOENCODING` — the Windows job exercises
the real legacy-code-page path, which is what caught gotcha #2.
