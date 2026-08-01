"""Lesson generation: offline composer, dispatch rules and online parsing."""

from __future__ import annotations

import pytest

from autotutor.config import Settings
from autotutor.content import generate_lesson
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
        assert not lesson.warnings

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

    def test_spoken_above_n3_falls_back_and_says_so(self, offline_settings):
        lesson = self._lesson(offline_settings, REGISTER_SPOKEN, level="N1")
        assert any("口语" in w for w in lesson.warnings)

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
        assert colloquial_score(FORMAL_TEXT) < 0.3

    def test_polite_textbook_text_sits_in_the_middle(self):
        score = colloquial_score("私は毎朝六時に起きます。朝ご飯はパンです。")
        assert 0.3 <= score <= 0.7

    def test_empty_text(self):
        assert colloquial_score("") == 0.0


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

    def _request(self, **kwargs):
        base = dict(level="N3", topic="programming", length="short", source="online")
        base.update(kwargs)
        return GenerationRequest(**base)

    def test_written_request_uses_a_written_source(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            self._request(register=REGISTER_WRITTEN)
        )
        assert "ウィキペディア" in lesson.source_label
        assert lesson.source_url.startswith("https://ja.wikipedia.org/")
        assert all(s.zh for s in lesson.sentences)

    def test_spoken_request_uses_a_spoken_source(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            self._request(register=REGISTER_SPOKEN)
        )
        assert lesson.source_label == "テスト番組"
        assert colloquial_score("".join(s.ja for s in lesson.sentences)) > 0.5

    def test_disabled_sources_are_not_asked(self, stubbed):
        """Only the podcast is enabled, so a written request still gets it."""
        settings = self._settings(sources="podcast")
        lesson = stubbed.OnlineGenerator(settings).generate(
            self._request(register=REGISTER_WRITTEN)
        )
        assert lesson.source_label == "テスト番組"
        assert any("语体" in w for w in lesson.warnings)

    def test_failing_sources_are_reported_but_not_fatal(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(self._request())
        assert any("来源" in w for w in lesson.warnings)
        assert lesson.sentences

    def test_all_sources_failing_raises(self, stubbed):
        settings = self._settings(sources="nhk_easy,nhk_news")
        with pytest.raises(stubbed.OnlineError):
            stubbed.OnlineGenerator(settings).generate(self._request())

    def test_difficulty_gap_produces_a_warning(self, stubbed):
        """Encyclopedic prose offered to an N5 learner has to say so."""
        settings = self._settings(sources="wikipedia")
        lesson = stubbed.OnlineGenerator(settings).generate(
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
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            self._request(level="N3", register=REGISTER_SPOKEN)
        )
        assert lesson.source_label == "テスト番組"

    def test_a_large_level_gap_still_wins(self, stubbed):
        """Register is a preference, not a licence to hand N5 material to N1."""
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            self._request(level="N2", register=REGISTER_SPOKEN)
        )
        assert "ウィキペディア" in lesson.source_label
        assert any("语体" in w for w in lesson.warnings)

    def test_web_text_is_annotated(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(self._request())
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
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            self._request(topic=CUSTOM_TOPIC,
                          custom_topic="https://youtu.be/dQw4w9WgXcQ")
        )
        assert lesson.source_label == "YouTube 字幕"
        assert lesson.source_url.endswith("dQw4w9WgXcQ")
