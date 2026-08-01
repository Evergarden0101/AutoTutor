"""Furigana alignment and kana readings."""

from __future__ import annotations

import pytest

from autotutor.reading import (
    align_reading,
    annotate,
    furigana_inline,
    has_kanji,
    kana_of,
    normalise,
    to_hiragana,
    to_katakana,
)


class TestKanaConversion:
    def test_katakana_to_hiragana(self):
        assert to_hiragana("キョウ") == "きょう"
        assert to_hiragana("ニホンゴ") == "にほんご"

    def test_prolonged_mark_is_preserved(self):
        assert to_hiragana("コーヒー") == "こーひー"

    def test_hiragana_to_katakana(self):
        assert to_katakana("たべます") == "タベマス"

    def test_non_kana_passes_through(self):
        assert to_hiragana("漢字ABC123") == "漢字ABC123"


class TestAlignReading:
    def test_okurigana_stays_outside_the_ruby(self):
        segments = align_reading("食べます", "タベマス")
        assert [(s.text, s.reading) for s in segments] == [("食", "た"), ("べます", "")]

    def test_leading_kana(self):
        segments = align_reading("お茶", "オチャ")
        assert [(s.text, s.reading) for s in segments] == [("お", ""), ("茶", "ちゃ")]

    def test_kanji_in_the_middle(self):
        segments = align_reading("話し合い", "ハナシアイ")
        assert [(s.text, s.reading) for s in segments] == [
            ("話", "はな"), ("し", ""), ("合", "あ"), ("い", ""),
        ]

    def test_whole_word_reading_when_unalignable(self):
        # 大人 = おとな cannot be split per character.
        segments = align_reading("大人", "オトナ")
        assert len(segments) == 1
        assert segments[0].text == "大人"
        assert segments[0].reading == "おとな"

    def test_pure_kana_needs_no_ruby(self):
        segments = align_reading("こんにちは", "コンニチハ")
        assert len(segments) == 1
        assert not segments[0].needs_ruby

    def test_segments_always_cover_the_input(self):
        for surface, reading in [
            ("食べます", "タベマス"), ("お茶", "オチャ"), ("話し合い", "ハナシアイ"),
            ("大人", "オトナ"), ("東京都", "トウキョウト"), ("", ""),
        ]:
            segments = align_reading(surface, reading)
            assert "".join(s.text for s in segments) == surface


class TestAnnotate:
    @pytest.mark.parametrize(
        "text",
        [
            "今日は日本語の勉強をします。",
            "毎朝七時に起きて、コーヒーを飲みます。",
            "彼はプログラミングが上手で、ゲームを作っています。",
            "Hello world 123",
            "。、！？",
            "",
        ],
    )
    def test_annotation_is_lossless(self, text):
        assert "".join(s.text for s in annotate(text)) == text

    def test_common_compounds_read_correctly(self):
        inline = furigana_inline("今日は日本語を勉強します。")
        assert "今日(きょう)" in inline
        assert "日本語(にほんご)" in inline

    def test_kana_view_keeps_katakana_loanwords(self):
        kana = kana_of("コーヒーを飲みます。")
        assert kana.startswith("コーヒー")
        assert "のみます" in kana

    def test_kana_view_has_no_kanji(self):
        assert not has_kanji(kana_of("病院で薬をもらいました。"))

    def test_no_ruby_on_plain_kana(self):
        segments = annotate("ひらがなだけ")
        assert not any(s.needs_ruby for s in segments)


class TestNormalise:
    def test_strips_wiki_footnotes(self):
        assert normalise("これは例です[1]。") == "これは例です。"

    def test_collapses_whitespace(self):
        assert normalise("  a　 b  ") == "a b"
