"""Lesson generation: offline composer, dispatch rules and online parsing."""

from __future__ import annotations

import json

import pytest

from autotutor.config import Settings
from autotutor.content import generate_lesson
from autotutor.content.offline import OfflineGenerator
from autotutor.content.online import (
    best_window,
    clean_sentences,
    extract_nhk_body,
    strip_html,
)
from autotutor.models import LENGTH_IDS, GenerationRequest, target_seconds
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


class TestOnlineGeneratorWithStubs:
    """Exercise the online path without touching the network."""

    WIKI = (
        "プログラミングとは、コンピュータプログラムを作成することである。"
        "その作業は一般に設計、コーディング、テストの各工程に分けられる。"
        "プログラミング言語には多数の種類が存在し、目的に応じて使い分けられている。"
        "近年は統合開発環境の普及により、記述と検証の効率が大きく向上した。"
    )
    NHK = (
        '<div id="js-article-body">'
        "<p><ruby>東京<rt>とうきょう</rt></ruby>では<ruby>雪<rt>ゆき</rt></ruby>が降りました。</p>"
        "<p>でんしゃが止まって、たくさんの人が困りました。</p>"
        "<p>きょうは天気がよくなると言っています。</p></div>"
    )

    @pytest.fixture
    def stubbed(self, monkeypatch):
        import autotutor.content.online as online

        def fake_json(url, params=None, timeout=20, headers=None):
            if params and params.get("list") == "search":
                return {"query": {"search": [{"title": "プログラミング"}]}}
            return {"query": {"pages": {"1": {"title": "プログラミング",
                                              "extract": self.WIKI}}}}

        def fake_text(url, params=None, timeout=20, headers=None, encoding="utf-8"):
            if url.endswith("news-list.json"):
                return json.dumps([{"2026-08-01": [{"news_id": "k1", "title": "東京に雪"}]}])
            return self.NHK

        class StubTranslator:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, texts):
                return [f"[译]{t[:6]}" for t in texts], None

        monkeypatch.setattr(online, "get_json", fake_json)
        monkeypatch.setattr(online, "get_text", fake_text)
        monkeypatch.setattr(online, "to_japanese", lambda t, timeout=15: t)
        monkeypatch.setattr(online, "Translator", StubTranslator)
        return online

    def _settings(self):
        settings = Settings()
        settings.allow_online = True
        return settings

    def test_beginner_level_prefers_nhk_easy(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            GenerationRequest(level="N5", topic="programming", length="short",
                              source="online")
        )
        assert "NHK" in lesson.source_label
        assert lesson.source_url.startswith("https://www3.nhk.or.jp/")

    def test_intermediate_level_uses_wikipedia(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            GenerationRequest(level="N3", topic="programming", length="short",
                              source="online")
        )
        assert "ウィキペディア" in lesson.source_label
        assert lesson.source_url.startswith("https://ja.wikipedia.org/")
        assert all(s.zh for s in lesson.sentences)

    def test_difficulty_gap_produces_a_warning(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            GenerationRequest(level="N3", topic="programming", length="short",
                              source="online")
        )
        assert any("难度" in w for w in lesson.warnings)

    def test_web_text_is_annotated(self, stubbed):
        lesson = stubbed.OnlineGenerator(self._settings()).generate(
            GenerationRequest(level="N3", topic="programming", length="short",
                              source="online")
        )
        for sentence in lesson.sentences:
            assert "".join(seg.text for seg in sentence.ruby) == sentence.ja
            assert sentence.kana
