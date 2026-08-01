"""Settings persistence and the shared data structures."""

from __future__ import annotations

import json

import pytest

from autotutor.config import Settings, data_dir, resource_root
from autotutor.models import (
    GenerationRequest,
    Lesson,
    RubySegment,
    Sentence,
    VocabEntry,
    target_sentence_count,
)


class TestSettings:
    def test_defaults_are_offline_first(self):
        settings = Settings()
        assert settings.allow_online is False
        assert settings.tts_engine == "auto"
        assert settings.output_dir

    def test_round_trip(self, tmp_path):
        path = tmp_path / "settings.json"
        settings = Settings()
        settings.level = "N2"
        settings.speech_rate = 1.25
        settings.allow_online = True
        settings.save(path)

        loaded = Settings.load(path)
        assert loaded.level == "N2"
        assert loaded.speech_rate == 1.25
        assert loaded.allow_online is True

    def test_missing_file_gives_defaults(self, tmp_path):
        assert Settings.load(tmp_path / "nope.json").level == "N4"

    def test_corrupt_file_gives_defaults(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{not json", encoding="utf-8")
        assert Settings.load(path).level == "N4"

    def test_unknown_keys_are_ignored(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"level": "N1", "bogus": 1}), encoding="utf-8")
        loaded = Settings.load(path)
        assert loaded.level == "N1"
        assert not hasattr(loaded, "bogus")

    def test_type_coercion(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps({"font_size": "18", "speech_rate": "0.9", "allow_online": 1}),
            encoding="utf-8",
        )
        loaded = Settings.load(path)
        assert loaded.font_size == 18
        assert loaded.speech_rate == 0.9
        assert loaded.allow_online is True

    def test_ensure_output_dir_creates_it(self, tmp_path):
        settings = Settings()
        settings.output_dir = str(tmp_path / "deep" / "nested")
        assert settings.ensure_output_dir().is_dir()


class TestPaths:
    def test_corpus_ships_with_the_package(self):
        assert (data_dir() / "corpus").is_dir()
        assert (data_dir() / "corpus" / "_common.json").is_file()

    def test_resource_root_contains_the_package(self):
        assert (resource_root() / "autotutor").is_dir()


class TestSentence:
    def test_inline_furigana(self):
        sentence = Sentence(
            ja="食べます",
            ruby=[RubySegment("食", "た"), RubySegment("べます")],
        )
        assert sentence.furigana_inline == "食(た)べます"

    def test_html_ruby(self):
        sentence = Sentence(ja="食", ruby=[RubySegment("食", "た")])
        assert sentence.html_ruby == "<ruby>食<rp>(</rp><rt>た</rt><rp>)</rp></ruby>"

    def test_no_ruby_falls_back_to_plain(self):
        assert Sentence(ja="あ").furigana_inline == "あ"
        assert Sentence(ja="あ").html_ruby == "あ"

    def test_segment_needs_ruby(self):
        assert RubySegment("食", "た").needs_ruby
        assert not RubySegment("べ").needs_ruby
        assert not RubySegment("あ", "あ").needs_ruby

    def test_round_trip(self):
        sentence = Sentence(ja="食べる", kana="たべる", zh="吃",
                            ruby=[RubySegment("食", "た"), RubySegment("べる")])
        restored = Sentence.from_dict(sentence.to_dict())
        assert restored == sentence


class TestLesson:
    def _lesson(self):
        return Lesson(
            title_ja="題", title_zh="题", level="N4", topic="daily_life",
            sentences=[
                Sentence(ja="あ。", kana="あ。", zh="啊。"),
                Sentence(ja="い。", kana="い。", zh="咦。"),
            ],
            vocab=[VocabEntry("語", "ご", "词")],
        )

    def test_views(self):
        lesson = self._lesson()
        assert lesson.plain_text == "あ。い。"
        assert lesson.kana_text == "あ。い。"
        assert lesson.chinese_text == "啊。咦。"
        assert lesson.char_count == 4

    def test_estimated_seconds_scales_with_content(self):
        short = self._lesson()
        long = self._lesson()
        long.sentences = long.sentences * 5
        assert long.estimated_seconds > short.estimated_seconds

    def test_round_trip(self):
        lesson = self._lesson()
        restored = Lesson.from_dict(lesson.to_dict())
        assert restored.title_ja == lesson.title_ja
        assert restored.plain_text == lesson.plain_text
        assert restored.vocab == lesson.vocab

    def test_dict_is_json_serialisable(self):
        json.dumps(self._lesson().to_dict(), ensure_ascii=False)


class TestRequest:
    def test_topic_query_is_trimmed(self):
        assert GenerationRequest(custom_topic="  料理 ").topic_query == "料理"

    @pytest.mark.parametrize(
        "length,expected", [("short", 4), ("medium", 8), ("long", 14), ("odd", 8)]
    )
    def test_target_sentence_count(self, length, expected):
        assert target_sentence_count(length) == expected
