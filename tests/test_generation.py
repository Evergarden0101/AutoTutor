"""Lesson generation: offline composer, dispatch rules and online parsing."""

from __future__ import annotations

import random
import re

import pytest

from autotutor.config import Settings
from autotutor.content import generate_lesson
from autotutor.content.corpus import frame_sentences, load_topic
from autotutor.content.offline import OfflineGenerator
from autotutor.content.online import (
    best_window,
    clean_sentences,
    colloquial_score,
    strip_html,
)
from autotutor.content.sources import (
    AUTO_SOURCE_IDS,
    SOURCES,
    SOURCES_BY_ID,
    Article,
    SourceError,
    extract_nhk_body,
    parse_caption_xml,
    parse_feed,
    pick_japanese_track,
    sources_for,
    youtube_video_id,
)
from autotutor.models import (
    LENGTH_IDS,
    REGISTER_SPOKEN,
    REGISTER_WRITTEN,
    GenerationRequest,
    target_seconds,
)
from autotutor.levels import LEVEL_CODES
from autotutor.topics import CUSTOM_TOPIC, RANDOM_TOPIC, TOPIC_IDS


@pytest.fixture
def offline_settings():
    settings = Settings()
    settings.allow_online = False
    return settings


class TestOfflineGeneration:
    @pytest.mark.parametrize("level", ["N5", "N4", "N3", "N2", "N1"])
    @pytest.mark.parametrize("length", LENGTH_IDS)
    def test_duration_is_close_to_the_requested_length(
        self, offline_settings, level, length
    ):
        request = GenerationRequest(level=level, topic="daily_life", length=length, seed=1)
        lesson = generate_lesson(request, offline_settings)
        target = target_seconds(length)
        assert 0.7 * target <= lesson.estimated_seconds <= 1.35 * target, (
            f"{level}/{length}: {lesson.estimated_seconds}s vs target {target}s"
        )

    def test_longer_presets_produce_longer_lessons(self, offline_settings):
        durations = [
            generate_lesson(
                GenerationRequest(level="N4", topic="travel", length=length, seed=9),
                offline_settings,
            ).estimated_seconds
            for length in LENGTH_IDS
        ]
        assert durations == sorted(durations), durations

    def test_can_reach_eight_minutes(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N3", topic="anime", length="xlong", seed=2),
            offline_settings,
        )
        assert lesson.estimated_seconds >= 300, lesson.estimated_seconds

    def test_sentences_are_substantial_not_choppy(self, offline_settings):
        """The corpus should read as paragraphs, not one-line facts."""
        lesson = generate_lesson(
            GenerationRequest(level="N3", topic="programming", length="medium", seed=1),
            offline_settings,
        )
        lengths = [len(s.ja) for s in lesson.sentences]
        assert sum(lengths) / len(lengths) >= 28, lengths

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_every_topic_generates(self, offline_settings, topic_id):
        request = GenerationRequest(level="N4", topic=topic_id, length="medium", seed=2)
        lesson = generate_lesson(request, offline_settings)
        assert lesson.topic == topic_id
        assert lesson.sentences
        assert lesson.title_ja and lesson.title_zh
        assert all(s.zh for s in lesson.sentences), "offline lessons ship translations"
        assert all(s.kana for s in lesson.sentences)
        assert lesson.vocab

    def test_annotation_is_attached(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N5", topic="hospital", length="short", seed=3),
            offline_settings,
        )
        assert any(seg.needs_ruby for s in lesson.sentences for seg in s.ruby)
        for sentence in lesson.sentences:
            assert "".join(seg.text for seg in sentence.ruby) == sentence.ja

    def test_seed_makes_it_repeatable(self, offline_settings):
        request = GenerationRequest(level="N3", topic="game", length="medium", seed=42)
        first = generate_lesson(request, offline_settings).plain_text
        second = generate_lesson(request, offline_settings).plain_text
        assert first == second

    def test_random_topic_resolves(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N4", topic=RANDOM_TOPIC, length="short"),
            offline_settings,
        )
        assert lesson.topic in TOPIC_IDS

    def test_known_custom_topic_maps_onto_a_corpus_topic(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(
                level="N4", topic=CUSTOM_TOPIC, custom_topic="医院", length="short"
            ),
            offline_settings,
        )
        assert lesson.topic == "hospital"
        # An exact topic match must not claim it substituted something. Other
        # warnings (level widening, for instance) are a separate concern.
        assert not any("已改用最接近的内置主题" in w for w in lesson.warnings), (
            lesson.warnings
        )

    def test_unknown_custom_topic_warns_and_falls_back(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(
                level="N4", topic=CUSTOM_TOPIC, custom_topic="量子色力学", length="short"
            ),
            offline_settings,
        )
        assert lesson.topic in TOPIC_IDS
        assert lesson.warnings and "量子色力学" in lesson.warnings[0]

    def test_repeated_calls_vary(self, offline_settings):
        generator = OfflineGenerator()
        request = GenerationRequest(level="N4", topic="anime", length="medium")
        texts = {generator.generate(request).plain_text for _ in range(6)}
        assert len(texts) > 1, "the composer should not repeat itself every time"

    def test_estimated_duration_is_positive(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N4", topic="food", length="medium", seed=5),
            offline_settings,
        )
        assert lesson.estimated_seconds > 5


class TestPassageCoherence:
    """A lesson should read as connected prose, not a list of stray facts."""

    def _blocks(self, level="N4", topic="daily_life", budget=240.0, register="auto"):
        generator = OfflineGenerator()
        warnings = []
        blocks = generator._collect_blocks(
            load_topic(topic), level, budget, warnings, register,
        )
        return blocks, warnings

    def test_whole_passages_are_used_before_loose_sentences(self):
        """`extras` are single facts glued with それから - a last resort.

        They used to be taken second, ahead of real passages from neighbouring
        levels, so a medium lesson turned listy long before it had to.
        """
        blocks, _ = self._blocks(budget=240.0)
        titled = [i for i, b in enumerate(blocks) if b.title_ja]
        loose = [i for i, b in enumerate(blocks) if not b.title_ja]
        assert titled, "expected at least one whole passage"
        if loose:
            assert min(loose) > max(titled), (
                "loose sentences must come after every whole passage"
            )

    def test_a_short_lesson_is_whole_passages_only(self):
        """At the short preset there is no excuse for stray sentences."""
        blocks, warnings = self._blocks(budget=60.0)
        assert blocks and all(b.title_ja for b in blocks)
        assert not warnings

    def test_padding_with_loose_sentences_is_disclosed(self):
        """Whatever the composer had to do to fill the time, it says so."""
        blocks, warnings = self._blocks(budget=100000.0)
        if any(not b.title_ja for b in blocks):
            assert any("例句" in w for w in warnings), warnings

    def test_the_same_story_is_not_told_twice(self):
        """Every topic has a casual retelling of its polite passage.

        Playing both means the learner hears about だし twice in one lesson -
        once in です・ます and once in 常体 - which is padding, not development.
        """
        from autotutor.content.offline import _overlap, _topic_terms

        blocks, _ = self._blocks(level="N3", topic="food", budget=240.0)
        terms = [_topic_terms(b.pairs) for b in blocks]
        for i in range(len(terms)):
            for j in range(i + 1, len(terms)):
                assert _overlap(terms[i], terms[j]) <= 0.25, (
                    f"blocks {i} and {j} cover the same ground: "
                    f"{blocks[i].title_ja} / {blocks[j].title_ja}"
                )

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_no_topic_repeats_itself_at_any_length(self, offline_settings, topic_id):
        from autotutor.content.offline import _overlap, _topic_terms

        blocks, _ = self._blocks(level="N4", topic=topic_id, budget=450.0)
        terms = [_topic_terms(b.pairs) for b in blocks]
        worst = max(
            (_overlap(terms[i], terms[j])
             for i in range(len(terms)) for j in range(i + 1, len(terms))),
            default=0.0,
        )
        assert worst <= 0.25, f"{topic_id}: {worst:.2f}"

    def test_overlap_is_symmetric_and_bounded(self):
        from autotutor.content.offline import _overlap

        assert _overlap(set(), set()) == 0.0
        assert _overlap({"料理"}, {"料理"}) == 1.0
        assert _overlap({"料理"}, {"天気"}) == 0.0
        assert _overlap({"a", "b"}, {"b", "c"}) == _overlap({"b", "c"}, {"a", "b"})

    @pytest.mark.parametrize("length", LENGTH_IDS)
    def test_a_lesson_never_ends_on_a_stray_fragment(self, offline_settings, length):
        lesson = generate_lesson(
            GenerationRequest(level="N4", topic="travel", length=length, seed=4),
            offline_settings,
        )
        assert lesson.sentences[-1].ja.endswith(("。", "！", "？"))

    @pytest.mark.parametrize("topic_id", ["food", "travel", "anime"])
    def test_passage_content_is_never_replayed(self, offline_settings, topic_id):
        """Widening must not serve a passage the lesson already used.

        Transitions are drawn from a small shared pool and may recur in a very
        long lesson; the material itself may not.
        """
        lesson = generate_lesson(
            GenerationRequest(level="N3", topic=topic_id, length="xlong", seed=8),
            offline_settings,
        )
        transitions = {
            item["ja"]
            for level in LEVEL_CODES
            for item in frame_sentences("transitions", level)
            + frame_sentences("transitions", level, "spoken")
        }
        body = [s.ja for s in lesson.sentences if s.ja not in transitions]
        duplicates = {s for s in body if body.count(s) > 1}
        assert not duplicates, duplicates

    def test_a_transition_never_follows_itself(self, offline_settings):
        """Back-to-back repeats are what a listener actually notices."""
        lesson = generate_lesson(
            GenerationRequest(level="N3", topic="food", length="xlong", seed=8),
            offline_settings,
        )
        japanese = [s.ja for s in lesson.sentences]
        assert all(a != b for a, b in zip(japanese, japanese[1:]))

    def test_the_transition_pool_is_bigger_than_a_handful(self):
        """Three lines across ten seams guarantees hearing one three times."""
        for level in LEVEL_CODES:
            assert len(frame_sentences("transitions", level)) >= 5, level


class TestOfflineRegister:
    def _lesson(self, settings, register, level="N4", topic="daily_life"):
        return generate_lesson(
            GenerationRequest(level=level, topic=topic, length="short",
                              register=register, seed=11),
            settings,
        )

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_spoken_beats_written_for_every_topic(self, offline_settings, topic_id):
        """The contract is relative: below N2 the formal fallback is です・ます.

        Polite textbook Japanese scores exactly 0.5 - neither speech nor
        literary prose - so an absolute threshold would only measure how much
        neutral filler happened to be pulled in to fill the time.
        """
        spoken = self._lesson(offline_settings, REGISTER_SPOKEN, topic=topic_id)
        written = self._lesson(offline_settings, REGISTER_WRITTEN, topic=topic_id)
        assert colloquial_score(spoken.plain_text) > colloquial_score(written.plain_text)

    @pytest.mark.parametrize("level", ["N5", "N4", "N3"])
    def test_spoken_request_reads_as_speech(self, offline_settings, level):
        lesson = self._lesson(offline_settings, REGISTER_SPOKEN, level=level)
        assert colloquial_score(lesson.plain_text) > 0.5, lesson.plain_text[:80]

    @pytest.mark.parametrize("level", ["N2", "N1"])
    def test_written_request_reads_as_prose(self, offline_settings, level):
        lesson = self._lesson(offline_settings, REGISTER_WRITTEN, level=level)
        assert colloquial_score(lesson.plain_text) < 0.5, lesson.plain_text[:80]

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_a_spoken_request_never_gets_literary_prose(
        self, offline_settings, topic_id
    ):
        """Filling the time may mix in です・ます, but never 書き言葉.

        A short lesson can need more than the one conversational passage a
        topic has at a given level, and the polite passages stand in. That is
        a documented fallback; dropping to editorial prose would not be. The
        floor sits between the two: an N1 essay scores under 0.15, while the
        most polite-heavy conversational lesson the corpus produces is 0.46.
        """
        for level in ("N5", "N4", "N3"):
            lesson = self._lesson(
                offline_settings, REGISTER_SPOKEN, level=level, topic=topic_id
            )
            assert colloquial_score(lesson.plain_text) > 0.35, (
                f"{topic_id}/{level}: {lesson.plain_text[:80]}"
            )

    def test_frames_match_the_body(self, offline_settings):
        """A casual talk must not open with みなさん、こんにちは."""
        lesson = self._lesson(offline_settings, REGISTER_SPOKEN)
        opener = lesson.sentences[0].ja
        assert not opener.startswith("みなさん")
        assert colloquial_score(opener) >= 0.5

    @pytest.mark.parametrize("level", ["N2", "N1"])
    def test_spoken_above_n3_either_delivers_or_explains(self, offline_settings, level):
        """No silent substitution: conversational material, or a reason.

        There are no conversational passages above N3, so the composer either
        widens to a neighbouring level that has some - which is a level warning,
        not a register one - or falls back to polite Japanese and says so.
        """
        lesson = self._lesson(offline_settings, REGISTER_SPOKEN, level=level)
        delivered = colloquial_score(lesson.plain_text) > 0.5
        assert delivered or lesson.warnings, lesson.plain_text[:80]
        if not delivered:
            assert any("口语" in w for w in lesson.warnings), lesson.warnings

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_a_casual_lesson_keeps_one_voice(self, offline_settings, topic_id):
        """Switching to です・ます halfway sounds like a second speaker.

        Below N4 a single conversational passage rarely fills the time, so the
        composer reaches one level sideways for more of the same voice before
        it falls back to the polite passages.
        """
        for level in ("N5", "N4", "N3"):
            lesson = self._lesson(
                offline_settings, REGISTER_SPOKEN, level=level, topic=topic_id
            )
            assert colloquial_score(lesson.plain_text) > 0.6, (
                f"{topic_id}/{level}: {lesson.plain_text[:70]}"
            )

    def test_modern_speech_markers_are_present(self):
        """The conversational corpus should read as people actually talk."""
        modern = ("まじで", "めっちゃ", "ぶっちゃけ", "てか", "やばい", "じゃん",
                  "っていうか", "スマホ", "ネット", "普通に")
        hits = 0
        for topic_id in TOPIC_IDS:
            for passage in load_topic(topic_id).passages:
                if passage.register != REGISTER_SPOKEN:
                    continue
                text = "".join(s.ja for s in passage.sentences)
                if any(marker in text for marker in modern):
                    hits += 1
        assert hits >= 20, f"only {hits} conversational passages sound contemporary"

    def test_neutral_filler_does_not_out_vote_the_passages(self):
        """です・ます extras suit either request, so they abstain from the vote."""
        from autotutor.content.offline import _Block, _effective_register

        casual = _Block(pairs=[("今日は寝坊しちゃった。", "")], register="spoken")
        filler = _Block(pairs=[("私は毎朝六時に起きます。", "")] * 5, register="neutral")
        assert _effective_register([casual, filler]) == "spoken"
        assert _effective_register([filler]) == "neutral"
        assert _effective_register([]) == "neutral"

    def test_auto_does_not_warn(self, offline_settings):
        lesson = self._lesson(offline_settings, "auto", level="N1")
        assert not any("口语" in w for w in lesson.warnings)

    def test_register_changes_the_text(self, offline_settings):
        spoken = self._lesson(offline_settings, REGISTER_SPOKEN).plain_text
        written = self._lesson(offline_settings, REGISTER_WRITTEN).plain_text
        assert spoken != written


class TestDispatchRules:
    def test_online_request_falls_back_when_network_is_disabled(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N4", topic="travel", length="short", source="online"),
            offline_settings,
        )
        assert lesson.source == "offline"
        assert any("联网" in w for w in lesson.warnings)

    def test_llm_request_falls_back_when_network_is_disabled(self, offline_settings):
        lesson = generate_lesson(
            GenerationRequest(level="N4", topic="travel", length="short", source="llm"),
            offline_settings,
        )
        assert lesson.source == "offline"

    def test_custom_text_is_split_and_annotated(self, offline_settings):
        request = GenerationRequest(
            level="N4",
            source="custom",
            custom_text="今日は良い天気ですね。公園で散歩しました。",
        )
        lesson = generate_lesson(request, offline_settings)
        assert lesson.source == "custom"
        assert len(lesson.sentences) == 2
        assert lesson.sentences[0].kana.startswith("きょう")

    def test_empty_custom_text_raises(self, offline_settings):
        from autotutor.content.service import GenerationError

        with pytest.raises(GenerationError):
            generate_lesson(
                GenerationRequest(source="custom", custom_text="   "), offline_settings
            )


class TestOnlineParsing:
    def test_strip_html_removes_ruby_annotations(self):
        markup = "<p><ruby>東京<rt>とうきょう</rt></ruby>です。</p>"
        assert strip_html(markup).strip() == "東京です。"

    def test_strip_html_drops_scripts(self):
        assert "alert" not in strip_html("<script>alert(1)</script><p>本文</p>")

    def test_extract_nhk_body(self):
        markup = '<div id="js-article-body"><p>雪が降りました。</p></div>'
        assert "雪が降りました。" in extract_nhk_body(markup)

    def test_extract_nhk_body_missing_returns_empty(self):
        assert extract_nhk_body("<html><body>nothing</body></html>") == ""

    def test_clean_sentences_filters_junk(self):
        text = (
            "これは十分に長い普通の文章です。\n"
            "短い。\n"
            "== 見出し ==\n"
            "https://example.com のような行は読み上げに向きません。\n"
            "これも十分な長さのある文章になっています。"
        )
        sentences = clean_sentences(text)
        assert "これは十分に長い普通の文章です。" in sentences
        assert all("http" not in s for s in sentences)
        assert all(len(s) >= 8 for s in sentences)

    def test_best_window_prefers_matching_difficulty(self):
        easy = ["私は毎朝走ります。", "水をたくさん飲みます。", "とても元気です。"]
        hard = [
            "当該制度の運用については、関係機関との協議を要するものとされている。",
            "経済的合理性の観点から見れば、その判断は妥当であったと評価されうる。",
            "以上の分析に基づき、今後の政策的含意を整理しておきたい。",
        ]
        window, distance = best_window(easy + hard, "N5", 3)
        assert window == easy
        assert distance < 1.5

    def test_best_window_handles_short_input(self):
        window, _ = best_window(["一文だけです。"], "N4", 8)
        assert window == ["一文だけです。"]

    def test_best_window_empty(self):
        window, distance = best_window([], "N4", 4)
        assert window == [] and distance > 5


FORMAL_TEXT = (
    "プログラミングとは、コンピュータプログラムを作成することである。"
    "その作業は一般に設計、コーディング、テストの各工程に分けられる。"
    "プログラミング言語には多数の種類が存在し、目的に応じて使い分けられている。"
    "近年は統合開発環境の普及により、記述と検証の効率が大きく向上した。"
)
CASUAL_TEXT = (
    "今日はプログラミングの話をするね。\n"
    "最初はエラーばっかりで、正直やめようかと思ったんだよね。\n"
    "でも動いたときは、めちゃくちゃうれしかったよ。\n"
    "分からないところは調べながらでいいと思ってる。\n"
    "毎日ちょっとずつ書いてると、だんだん慣れてくるんだ。"
)


class TestSourceRegistry:
    def test_every_source_has_a_label_and_note(self):
        for source in SOURCES:
            assert source.id and source.label_zh and source.note_zh
            assert source.register in {"spoken", "written", "any"}

    def test_youtube_is_not_searched_automatically(self):
        """It reads a URL the learner pasted; there is nothing to search."""
        assert "youtube" not in AUTO_SOURCE_IDS
        assert "youtube" in SOURCES_BY_ID

    def test_spoken_request_puts_spoken_sources_first(self):
        ordered = sources_for(REGISTER_SPOKEN, "N3")
        assert ordered[0].register == "spoken"

    def test_written_request_puts_written_sources_first(self):
        ordered = sources_for(REGISTER_WRITTEN, "N3")
        assert ordered[0].register == "written"

    def test_beginner_level_prefers_nhk_easy(self):
        ordered = sources_for(REGISTER_WRITTEN, "N5")
        assert ordered[0].id == "nhk_easy"

    def test_advanced_level_demotes_nhk_easy(self):
        ordered = sources_for(REGISTER_WRITTEN, "N1")
        assert ordered[0].id != "nhk_easy"

    def test_ordering_covers_every_searchable_source(self):
        ids = {s.id for s in sources_for("auto", "N3")}
        assert ids == set(AUTO_SOURCE_IDS)

    def test_ordering_varies_between_calls(self):
        """A fixed order meant N2/N1 got Wikipedia forever."""
        leads = {sources_for("written", "N1")[0].id for _ in range(30)}
        assert len(leads) > 1, leads

    def test_variety_never_breaks_the_register_tiers(self):
        for _ in range(30):
            ordered = sources_for(REGISTER_SPOKEN, "N3")
            spoken = [i for i, s in enumerate(ordered) if s.register == "spoken"]
            written = [i for i, s in enumerate(ordered) if s.register == "written"]
            assert max(spoken) < min(written), [s.id for s in ordered]

    def test_advanced_levels_have_more_than_one_good_option(self):
        """One source for a level is the same lesson every time by definition."""
        for level in ("N2", "N1"):
            suited = [
                s for s in sources_for(REGISTER_WRITTEN, level)
                if not s.best_levels or level in s.best_levels
            ]
            assert len(suited) >= 3, (level, [s.id for s in suited])

    def test_every_register_has_several_sources(self):
        for register in (REGISTER_SPOKEN, REGISTER_WRITTEN):
            matching = [s for s in SOURCES if s.register == register]
            assert len(matching) >= 3, register

    def test_feeds_are_https_and_unique(self):
        from autotutor.content.sources import (
            BLOG_FEEDS, NEWS_FEEDS, PODCAST_FEEDS, TECH_FEEDS,
        )

        urls = []
        for group in (PODCAST_FEEDS, NEWS_FEEDS, TECH_FEEDS, BLOG_FEEDS):
            for feed in group:
                assert feed["name"] and feed["url"].startswith("https://"), feed
                urls.append(feed["url"])
        assert len(urls) == len(set(urls)), "duplicate feed"


class TestFeedParsing:
    RSS = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>テスト番組</title>
      <item>
        <title>第10回 コンビニの話</title>
        <description>&lt;p&gt;今日はコンビニの話をするね。&lt;/p&gt;</description>
        <link>https://example.com/10</link>
      </item>
      <item>
        <title>第11回 電車の話</title>
        <description>電車で通勤してる人の話だよ。</description>
        <link>https://example.com/11</link>
      </item>
    </channel></rss>"""

    ATOM = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>雪のニュース</title>
        <summary>東京で雪が降りました。</summary>
        <link href="https://example.com/a"/>
      </entry>
    </feed>"""

    def test_parses_rss(self):
        entries = parse_feed(self.RSS)
        assert len(entries) == 2
        assert entries[0]["title"] == "第10回 コンビニの話"
        assert entries[0]["summary"] == "今日はコンビニの話をするね。"
        assert entries[0]["link"] == "https://example.com/10"

    def test_parses_atom_with_namespaces_and_link_attribute(self):
        entries = parse_feed(self.ATOM)
        assert entries[0]["title"] == "雪のニュース"
        assert entries[0]["link"] == "https://example.com/a"

    def test_respects_the_limit(self):
        assert len(parse_feed(self.RSS, limit=1)) == 1

    def test_substantial_entries_are_offered_first(self):
        """A one-line blurb cannot carry a lesson; a real episode note can."""
        from autotutor.content.sources import _substantial_first

        short = {"title": "短い", "summary": "ひとこと。"}
        long = {"title": "長い", "summary": "あ" * 300}
        assert _substantial_first([short, long]) == [long, short]

    def test_every_entry_survives_the_reordering(self):
        from autotutor.content.sources import _substantial_first

        a = {"title": "A", "summary": "あ" * 300}
        b = {"title": "B", "summary": "い" * 300}
        c = {"title": "C", "summary": "短い。"}
        assert sorted(
            _substantial_first([a, b, c]), key=lambda e: e["title"]
        ) == [a, b, c]

    def test_a_feed_of_short_notes_is_degraded_not_rejected(self):
        from autotutor.content.sources import _substantial_first

        entries = [{"title": "A", "summary": "短い。"}, {"title": "B", "summary": "短い。"}]
        assert sorted(_substantial_first(entries), key=lambda e: e["title"]) == entries

    def test_the_lead_entry_varies_between_calls(self):
        """Same feed, different lesson - otherwise the app repeats itself."""
        from autotutor.content.sources import _substantial_first

        entries = [{"title": chr(65 + n), "summary": "あ" * 300} for n in range(8)]
        leads = {_substantial_first(entries)[0]["title"] for _ in range(20)}
        assert len(leads) > 1, leads

    def test_malformed_feed_raises_source_error(self):
        with pytest.raises(SourceError):
            parse_feed("<rss><channel>unclosed")

    def test_empty_feed_raises_source_error(self):
        with pytest.raises(SourceError):
            parse_feed("<rss version='2.0'><channel></channel></rss>")


class TestYouTubeCaptions:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
            "dQw4w9WgXcQ",
        ],
    )
    def test_recognises_url_forms(self, url):
        assert youtube_video_id(url) == "dQw4w9WgXcQ"

    @pytest.mark.parametrize("url", ["", "https://example.com/video", "毎日勉強します。"])
    def test_rejects_non_youtube_input(self, url):
        assert youtube_video_id(url) is None

    def test_prefers_human_written_captions(self):
        tracks = [
            {"languageCode": "en", "baseUrl": "e"},
            {"languageCode": "ja", "kind": "asr", "baseUrl": "auto"},
            {"languageCode": "ja", "baseUrl": "manual"},
        ]
        assert pick_japanese_track(tracks)["baseUrl"] == "manual"

    def test_falls_back_to_auto_captions(self):
        tracks = [{"languageCode": "ja", "kind": "asr", "baseUrl": "auto"}]
        assert pick_japanese_track(tracks)["baseUrl"] == "auto"

    def test_no_japanese_track(self):
        assert pick_japanese_track([{"languageCode": "en", "baseUrl": "e"}]) is None

    def test_caption_xml_joins_broken_lines(self):
        xml = (
            '<transcript><text start="0">今日は</text>'
            '<text start="1">コンビニの話をします。</text>'
            '<text start="3">よろしくね。</text></transcript>'
        )
        text = parse_caption_xml(xml)
        assert "今日はコンビニの話をします。" in text
        assert "よろしくね。" in text

    def test_empty_captions_raise(self):
        with pytest.raises(SourceError):
            parse_caption_xml("<transcript></transcript>")


class TestColloquialScore:
    def test_conversational_text_scores_high(self):
        assert colloquial_score(CASUAL_TEXT) > 0.7

    def test_written_text_scores_low(self):
        assert colloquial_score(FORMAL_TEXT) < 0.45

    def test_polite_textbook_text_sits_in_the_middle(self):
        score = colloquial_score("私は毎朝六時に起きます。朝ご飯はパンです。")
        assert score == pytest.approx(0.5)

    def test_the_three_registers_are_ordered(self):
        polite = colloquial_score("私は毎朝六時に起きます。朝ご飯はパンです。")
        assert colloquial_score(FORMAL_TEXT) < polite < colloquial_score(CASUAL_TEXT)

    def test_unmarked_plain_speech_is_not_treated_as_an_essay(self):
        """Real speech is full of plain sentences with no 終助詞 at all.

        Counting those as literary is what made a conversational passage
        score as formal once the corpus started sounding natural.
        """
        assert colloquial_score("完全にやめるのは無理だけど、減らすくらいならできそう。") >= 0.5

    def test_a_literary_marker_is_what_makes_it_written(self):
        assert colloquial_score("それは今後の課題であると言えるだろう。") < 0.5

    def test_empty_text(self):
        assert colloquial_score("") == 0.0

    @pytest.mark.parametrize("register", ["spoken", "neutral", "written"])
    def test_the_corpus_registers_land_in_their_own_bands(self, register):
        """Measured over the bundled corpus, the three bands do not overlap."""
        bands = {"spoken": (0.55, 1.0), "neutral": (0.35, 0.6), "written": (0.0, 0.45)}
        low, high = bands[register]
        for topic_id in TOPIC_IDS:
            for passage in load_topic(topic_id).passages:
                if passage.register != register:
                    continue
                score = colloquial_score("".join(s.ja for s in passage.sentences))
                assert low <= score <= high, (
                    f"{topic_id}/{passage.level}: {score:.2f} outside {low}-{high}"
                )


class TestOnlineGeneratorWithStubs:
    """Exercise the online path without touching the network.

    The sources are replaced wholesale rather than the HTTP layer: what this
    needs to check is which source gets asked and how its text is cut down,
    not whether ElementTree can parse an RSS feed (:class:`TestFeedParsing`
    covers that).
    """

    @pytest.fixture
    def stubbed(self, monkeypatch):
        import autotutor.content.online as online
        from autotutor.content import sources as sources_module

        def formal(query="", timeout=20, limit=4):
            return [Article(title="プログラミング", text=FORMAL_TEXT,
                            url="https://ja.wikipedia.org/wiki/プログラミング",
                            source_label="ウィキペディア（日本語版）",
                            source_id="wikipedia", register=REGISTER_WRITTEN)]

        def casual(query="", timeout=20, limit=4):
            return [Article(title="第10回 プログラミングの話", text=CASUAL_TEXT,
                            url="https://example.com/10",
                            source_label="テスト番組",
                            source_id="podcast", register=REGISTER_SPOKEN)]

        def dead(query="", timeout=20, limit=4):
            raise SourceError("测试用来源不可用")

        stubs = {"podcast": casual, "wikipedia": formal}
        patched = [
            sources_module.Source(
                s.id, s.label_zh, s.register,
                stubs.get(s.id, dead), s.best_levels, s.note_zh,
            )
            for s in sources_module.SOURCES
        ]
        monkeypatch.setattr(sources_module, "SOURCES", patched)
        monkeypatch.setattr(
            sources_module, "SOURCES_BY_ID", {s.id: s for s in patched}
        )

        class StubTranslator:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, texts):
                return [f"[译]{t[:6]}" for t in texts], None

        monkeypatch.setattr(online, "to_japanese", lambda t, timeout=15: t)
        monkeypatch.setattr(online, "Translator", StubTranslator)
        return online

    def _settings(self, **kwargs):
        settings = Settings()
        settings.allow_online = True
        for key, value in kwargs.items():
            setattr(settings, key, value)
        return settings

    def _generator(self, stubbed, settings=None, seed=7):
        """Seeded: near-tied candidates are picked at random in production."""
        return stubbed.OnlineGenerator(settings or self._settings(),
                                       rng=random.Random(seed))

    def _request(self, **kwargs):
        base = dict(level="N3", topic="programming", length="short", source="online")
        base.update(kwargs)
        return GenerationRequest(**base)

    def test_written_request_uses_a_written_source(self, stubbed):
        lesson = self._generator(stubbed).generate(
            self._request(register=REGISTER_WRITTEN)
        )
        assert "ウィキペディア" in lesson.source_label
        assert lesson.source_url.startswith("https://ja.wikipedia.org/")
        assert all(s.zh for s in lesson.sentences)

    def test_spoken_request_uses_a_spoken_source(self, stubbed):
        lesson = self._generator(stubbed).generate(
            self._request(register=REGISTER_SPOKEN)
        )
        assert lesson.source_label == "テスト番組"
        assert colloquial_score("".join(s.ja for s in lesson.sentences)) > 0.5

    def test_disabled_sources_are_not_asked(self, stubbed):
        """Only the podcast is enabled, so a written request still gets it."""
        settings = self._settings(sources="podcast")
        lesson = self._generator(stubbed, settings).generate(
            self._request(register=REGISTER_WRITTEN)
        )
        assert lesson.source_label == "テスト番組"
        assert any("语体" in w for w in lesson.warnings)

    def test_failing_sources_are_reported_but_not_fatal(self, stubbed):
        lesson = self._generator(stubbed).generate(self._request())
        assert any("来源" in w for w in lesson.warnings)
        assert lesson.sentences

    def test_all_sources_failing_raises(self, stubbed):
        settings = self._settings(sources="nhk_easy,nhk_news")
        with pytest.raises(stubbed.OnlineError):
            self._generator(stubbed, settings).generate(self._request())

    def test_difficulty_gap_produces_a_warning(self, stubbed):
        """Encyclopedic prose offered to an N5 learner has to say so."""
        settings = self._settings(sources="wikipedia")
        lesson = self._generator(stubbed, settings).generate(
            self._request(level="N5", register=REGISTER_WRITTEN)
        )
        assert any("难度" in w for w in lesson.warnings)

    def test_register_beats_a_closer_level_match(self, stubbed):
        """A conversational request must not be answered with an encyclopedia.

        At N3 the Wikipedia stub is the closer level match (0.7 vs 1.5), so
        ranking on level alone would pick it and quietly ignore the register.
        A register miss is worth about one level, so the podcast wins here -
        but not at N2, where the gap grows to two levels.
        """
        lesson = self._generator(stubbed).generate(
            self._request(level="N3", register=REGISTER_SPOKEN)
        )
        assert lesson.source_label == "テスト番組"

    def test_a_large_level_gap_still_wins(self, stubbed):
        """Register is a preference, not a licence to hand N5 material to N1."""
        lesson = self._generator(stubbed).generate(
            self._request(level="N2", register=REGISTER_SPOKEN)
        )
        assert "ウィキペディア" in lesson.source_label
        assert any("语体" in w for w in lesson.warnings)

    def test_a_source_that_can_carry_the_lesson_wins(self, stubbed, monkeypatch):
        """One continuous text beats a closer-level fragment.

        Listening practice is a continuous piece of speech; four unrelated
        snippets stitched to hit the duration is not what anyone asked for.
        """
        from autotutor.content import sources as sources_module

        long_text = "".join(
            f"日本語の勉強を続けるのは、思っているよりも大変なことだと思います{n}。"
            for n in range(40)
        )
        def short(query="", timeout=20, limit=4):
            return [Article(title="短い記事", text=FORMAL_TEXT, url="u1",
                            source_label="短", source_id="wikipedia",
                            register=REGISTER_WRITTEN)]

        def long(query="", timeout=20, limit=4):
            return [Article(title="長い記事", text=long_text, url="u2",
                            source_label="長", source_id="nhk_news",
                            register=REGISTER_WRITTEN)]

        patched = [
            sources_module.Source(
                s.id, s.label_zh, s.register,
                {"wikipedia": short, "nhk_news": long}.get(
                    s.id, lambda *a, **k: (_ for _ in ()).throw(SourceError("x"))
                ),
                s.best_levels, s.note_zh,
            )
            for s in sources_module.SOURCES
        ]
        monkeypatch.setattr(sources_module, "SOURCES", patched)

        lesson = self._generator(stubbed).generate(
            self._request(level="N3", length="long", register=REGISTER_WRITTEN)
        )
        assert lesson.source_label == "長"
        assert not any("合并" in w for w in lesson.warnings)

    def test_merging_prefers_the_same_source(self, stubbed, monkeypatch):
        """Extend a podcast with the same programme, not with a news feed."""
        from autotutor.content import sources as sources_module

        def two_episodes(query="", timeout=20, limit=4):
            return [
                Article(title="第10回", text=CASUAL_TEXT, url="a",
                        source_label="テスト番組", source_id="podcast",
                        register=REGISTER_SPOKEN),
                Article(title="第11回", text=CASUAL_TEXT, url="b",
                        source_label="テスト番組", source_id="podcast",
                        register=REGISTER_SPOKEN),
            ]

        def news(query="", timeout=20, limit=4):
            return [Article(title="ニュース", text=FORMAL_TEXT, url="c",
                            source_label="NHK", source_id="nhk_news",
                            register=REGISTER_WRITTEN)]

        patched = [
            sources_module.Source(
                s.id, s.label_zh, s.register,
                {"podcast": two_episodes, "nhk_news": news}.get(
                    s.id, lambda *a, **k: (_ for _ in ()).throw(SourceError("x"))
                ),
                s.best_levels, s.note_zh,
            )
            for s in sources_module.SOURCES
        ]
        monkeypatch.setattr(sources_module, "SOURCES", patched)

        lesson = self._generator(stubbed).generate(
            self._request(length="long", register=REGISTER_SPOKEN)
        )
        assert lesson.source_label == "テスト番組"
        merged = [w for w in lesson.warnings if "接续" in w or "合并" in w]
        assert merged, lesson.warnings
        # Whichever episode led, the other one is used up before reaching for
        # an unrelated news item.
        episode = re.search(r"第\d+回", merged[0])
        assert episode, merged[0]
        assert episode.start() < merged[0].index("ニュース"), merged[0]

    def test_merging_within_one_source_says_so(self, stubbed, monkeypatch):
        from autotutor.content import sources as sources_module

        def two_episodes(query="", timeout=20, limit=4):
            return [
                Article(title="第10回", text=CASUAL_TEXT, url="a",
                        source_label="テスト番組", source_id="podcast",
                        register=REGISTER_SPOKEN),
                Article(title="第11回", text=CASUAL_TEXT, url="b",
                        source_label="テスト番組", source_id="podcast",
                        register=REGISTER_SPOKEN),
            ]

        patched = [
            sources_module.Source(
                s.id, s.label_zh, s.register,
                two_episodes if s.id == "podcast"
                else (lambda *a, **k: (_ for _ in ()).throw(SourceError("x"))),
                s.best_levels, s.note_zh,
            )
            for s in sources_module.SOURCES
        ]
        monkeypatch.setattr(sources_module, "SOURCES", patched)

        lesson = self._generator(stubbed).generate(
            self._request(length="long", register=REGISTER_SPOKEN)
        )
        merged = [w for w in lesson.warnings if "接续" in w]
        assert merged and "同一来源" in merged[0], lesson.warnings

    def test_repeated_searches_do_not_return_the_same_article(
        self, stubbed, monkeypatch
    ):
        """The loudest complaint about online mode: it repeats itself."""
        from autotutor.content import sources as sources_module

        def many(query="", timeout=20, limit=4):
            return [
                Article(title=f"記事{n}", text=FORMAL_TEXT.replace("。", f"{n}。"),
                        url=f"u{n}", source_label=f"来源{n}",
                        source_id="wikipedia", register=REGISTER_WRITTEN)
                for n in range(6)
            ]

        patched = [
            sources_module.Source(
                s.id, s.label_zh, s.register,
                many if s.id == "wikipedia"
                else (lambda *a, **k: (_ for _ in ()).throw(SourceError("x"))),
                s.best_levels, s.note_zh,
            )
            for s in sources_module.SOURCES
        ]
        monkeypatch.setattr(sources_module, "SOURCES", patched)

        settings = self._settings(sources="wikipedia")
        titles = {
            stubbed.OnlineGenerator(settings).generate(
                self._request(register=REGISTER_WRITTEN)
            ).title_ja
            for _ in range(12)
        }
        assert len(titles) > 1, titles

    def test_web_text_is_annotated(self, stubbed):
        lesson = self._generator(stubbed).generate(self._request())
        for sentence in lesson.sentences:
            assert "".join(seg.text for seg in sentence.ruby) == sentence.ja
            assert sentence.kana

    def test_pasted_video_url_reads_its_captions(self, stubbed, monkeypatch):
        from autotutor.content import sources as sources_module

        def fake_captions(url, timeout):
            return Article(title="日本語ポッドキャスト", text=CASUAL_TEXT,
                           url=url, source_label="YouTube 字幕",
                           source_id="youtube", register=REGISTER_SPOKEN)

        monkeypatch.setattr(sources_module, "fetch_youtube_captions", fake_captions)
        monkeypatch.setattr(stubbed, "fetch_youtube_captions", fake_captions)
        lesson = self._generator(stubbed).generate(
            self._request(topic=CUSTOM_TOPIC,
                          custom_topic="https://youtu.be/dQw4w9WgXcQ")
        )
        assert lesson.source_label == "YouTube 字幕"
        assert lesson.source_url.endswith("dQw4w9WgXcQ")
