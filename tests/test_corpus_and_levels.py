"""The bundled corpus and the difficulty estimator."""

from __future__ import annotations

import json
import re

import pytest

from autotutor.content.corpus import (
    REGISTER_NEUTRAL,
    REGISTER_SPOKEN,
    REGISTER_WRITTEN,
    available_topic_ids,
    corpus_dir,
    corpus_stats,
    frame_sentences,
    infer_register,
    load_common,
    load_topic,
    match_topic,
    nearest_levels,
)
from autotutor.levels import (
    LEVEL_CODES,
    analyse,
    estimate_level,
    get_level,
    score_text,
    split_sentences,
)
from autotutor.topics import TOPIC_IDS

ALL_LEVELS = {"N5", "N4", "N3", "N2", "N1"}


class TestCorpusIntegrity:
    def test_every_registered_topic_has_a_corpus_file(self):
        available = set(available_topic_ids())
        assert set(TOPIC_IDS) == available

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_topic_covers_every_level(self, topic_id):
        corpus = load_topic(topic_id)
        assert corpus is not None
        assert {p.level for p in corpus.passages} == ALL_LEVELS
        assert set(corpus.extras) == ALL_LEVELS
        assert set(corpus.vocab) == ALL_LEVELS

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_sentences_are_well_formed(self, topic_id):
        corpus = load_topic(topic_id)
        sentences = [s for p in corpus.passages for s in p.sentences]
        sentences += [s for items in corpus.extras.values() for s in items]
        assert len(sentences) >= 20
        for sentence in sentences:
            assert sentence.ja.endswith(("。", "！", "？")), sentence.ja
            assert sentence.zh, f"missing translation: {sentence.ja}"
            # Latin words in the Japanese text usually mean a drafting slip.
            leftovers = [w for w in re.findall(r"[A-Za-z]{2,}", sentence.ja)
                         if w not in {"Wi", "Fi", "Python", "AI"}]
            assert not leftovers, f"latin text in {sentence.ja}: {leftovers}"

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_passages_have_titles(self, topic_id):
        for passage in load_topic(topic_id).passages:
            assert passage.title_ja and passage.title_zh
            assert len(passage.sentences) >= 4

    def test_corpus_files_are_valid_json(self):
        for path in corpus_dir().glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_sentences_are_not_double_sentences(self):
        """One record, one sentence - the cursor highlights whole records."""
        for topic_id in TOPIC_IDS:
            corpus = load_topic(topic_id)
            records = [s for p in corpus.passages for s in p.sentences]
            records += [s for items in corpus.extras.values() for s in items]
            for record in records:
                if "「" in record.ja:  # quoted speech keeps its inner 。
                    continue
                assert not any(c in record.ja[:-1] for c in "。！？"), record.ja

    def test_stats(self):
        stats = corpus_stats()
        assert len(stats) == len(TOPIC_IDS)
        assert sum(stats.values()) >= 700

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_passages_read_as_paragraphs(self, topic_id):
        """Sentences should be developed, not one-line facts.

        Conversational passages get their own, lower floors: speech really is
        made of shorter turns, and holding 今日は本当に暑いね to the length of
        an editorial sentence would only produce unnatural Japanese.
        """
        # Floors sit below the observed minimum per level but well above the
        # short, choppy sentences this corpus replaced (N5 18 -> 27 chars).
        written = {"N5": 22, "N4": 27, "N3": 32, "N2": 34, "N1": 35}
        spoken = {"N5": 13, "N4": 19, "N3": 22, "N2": 24, "N1": 26}
        for passage in load_topic(topic_id).passages:
            lengths = [len(s.ja) for s in passage.sentences]
            average = sum(lengths) / len(lengths)
            casual = passage.register == REGISTER_SPOKEN
            floor = (spoken if casual else written)[passage.level]
            assert average >= floor, (
                f"{topic_id}/{passage.level} ({passage.register}): "
                f"mean {average:.0f} chars, want >= {floor}"
            )
            assert len(passage.sentences) >= (5 if casual else 6)


class TestSentenceSplitting:
    """Japanese punctuates inside 「」, so a naive split breaks quotes apart."""

    def test_a_quote_is_never_split_through(self):
        text = "先輩に「今週の金曜、王将行かへん？」と誘われたんです。そんなの最高やん。"
        assert split_sentences(text) == [
            "先輩に「今週の金曜、王将行かへん？」と誘われたんです。",
            "そんなの最高やん。",
        ]

    def test_no_sentence_starts_with_a_stray_closing_quote(self):
        text = "彼は「わかった。行くよ。」と言った。それから準備を始めた。"
        assert not any(s.startswith(("」", "』", "）")) for s in split_sentences(text))

    def test_nested_quotes(self):
        text = "入れ子の「引用「内側」もある」場合。次の文。"
        assert split_sentences(text) == ["入れ子の「引用「内側」もある」場合。", "次の文。"]

    def test_parentheses_are_not_split_through(self):
        assert split_sentences("括弧（これは注釈です。）のあと。次へ。") == [
            "括弧（これは注釈です。）のあと。", "次へ。",
        ]

    def test_an_unclosed_quote_does_not_swallow_everything(self):
        text = "閉じ忘れた「引用のまま終わる。"
        assert split_sentences(text) == [text]

    def test_ordinary_text_is_unaffected(self):
        assert split_sentences("普通の文です。もう一つの文です。") == [
            "普通の文です。", "もう一つの文です。",
        ]

    def test_trailing_punctuation_stays_with_its_sentence(self):
        assert split_sentences("本当？！すごい。") == ["本当？！", "すごい。"]

    def test_empty_and_blank(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_text_with_no_terminator(self):
        assert split_sentences("終わりの句点がない") == ["終わりの句点がない"]

    def test_the_split_is_lossless(self):
        text = "彼は「行く。」と言った。次の文です。最後。"
        assert "".join(split_sentences(text)) == text


class TestRegister:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("毎朝六時に起きます。", REGISTER_NEUTRAL),
            ("土日は休みだから、家族と過ごしてるよ。", REGISTER_SPOKEN),
            ("安いからってつい買っちゃうんだよね。", REGISTER_SPOKEN),
            ("まだ間に合うかな。", REGISTER_SPOKEN),
            ("観光客の増加は、地域経済に大きな恩恵をもたらしてきた。", REGISTER_WRITTEN),
            ("この点は今後の課題であると言えるだろう。", REGISTER_WRITTEN),
        ],
    )
    def test_infer_register(self, text, expected):
        assert infer_register([text]) == expected

    def test_written_markers_beat_lookalike_substrings(self):
        """かな in 追いつかない and んだ in 進んだ are not colloquial."""
        text = (
            "処理が追いつかないごみの増加は、その典型的な例だと言えるだろう。"
            "整備は着実に進んだものの、課題は依然として残されている。"
        )
        assert infer_register(split_sentences(text)) == REGISTER_WRITTEN

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_tags_agree_with_inference(self, topic_id):
        """A hand-tagged register that the text does not support is a bug."""
        for passage in load_topic(topic_id).passages:
            inferred = infer_register([s.ja for s in passage.sentences])
            assert inferred == passage.register, (
                f"{topic_id}/{passage.level} tagged {passage.register}, "
                f"reads as {inferred}: {passage.title_ja}"
            )

    @pytest.mark.parametrize("topic_id", TOPIC_IDS)
    def test_every_topic_offers_conversational_material(self, topic_id):
        levels = {p.level for p in load_topic(topic_id).passages
                  if p.register == REGISTER_SPOKEN}
        assert {"N5", "N4", "N3"} <= levels

    def test_passages_for_falls_back_to_neutral(self):
        corpus = load_topic("daily_life")
        spoken = corpus.passages_for("N5", REGISTER_SPOKEN)
        assert spoken and spoken[0].register == REGISTER_SPOKEN
        # N1 has no conversational passage; the polite/neutral ones stand in
        # rather than the request returning nothing.
        assert corpus.passages_for("N1", REGISTER_SPOKEN) is not None

    def test_plain_casual_scores_easier_than_plain_formal(self):
        """常体 is not 書き言葉: a casual sentence must not read as advanced."""
        casual = score_text("土日は休みだから、家族と過ごしてるよ。")
        formal = score_text("休日は家族と過ごすことが多いとされている。")
        assert casual < formal
        assert estimate_level("今日は本当に暑いね。") in {"N5", "N4"}


class TestCommonFrames:
    @pytest.mark.parametrize("level", LEVEL_CODES)
    @pytest.mark.parametrize("kind", ["openers", "closers", "transitions"])
    def test_frames_exist_for_every_level(self, kind, level):
        items = frame_sentences(kind, level)
        assert items
        for item in items:
            assert item.get("ja") and item.get("zh")

    def test_openers_use_the_topic_placeholder(self):
        openers = load_common()["openers"]["N5"]
        assert any("{topic}" in o["ja"] for o in openers)


class TestTopicMatching:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("医院", "hospital"),
            ("病院", "hospital"),
            ("programming", "programming"),
            ("动画", "anime"),
            ("游戏", "game"),
        ],
    )
    def test_matches_known_topics(self, query, expected):
        assert match_topic(query, list(available_topic_ids())) == expected

    def test_unknown_topic_returns_none(self):
        assert match_topic("量子色力学", list(available_topic_ids())) is None

    def test_empty_query(self):
        assert match_topic("", list(available_topic_ids())) is None


class TestLevels:
    def test_nearest_levels_starts_with_itself(self):
        assert nearest_levels("N3")[0] == "N3"
        assert nearest_levels("N5")[:2] == ["N5", "N4"]

    def test_score_is_monotonic_across_corpus_levels(self):
        means = []
        for level in ["N5", "N4", "N3", "N2", "N1"]:
            texts = []
            for topic_id in TOPIC_IDS:
                corpus = load_topic(topic_id)
                for passage in corpus.passages_for(level):
                    texts.extend(s.ja for s in passage.sentences)
            means.append(sum(score_text(t) for t in texts) / len(texts))
        assert means == sorted(means), means
        assert means[0] < 2.0, "N5 material should score near 1"
        assert means[-1] > 4.0, "N1 material should score near 5"

    def test_beginner_text_estimates_low(self):
        assert estimate_level("私は毎朝六時に起きます。") in {"N5", "N4"}

    def test_encyclopedic_text_estimates_high(self):
        text = (
            "プログラミングとは、コンピュータプログラムを作成することであり、"
            "その作業は一般に設計、コーディング、テストの各工程に分けられる。"
        )
        assert estimate_level(text) in {"N2", "N1"}

    def test_empty_text_is_easiest(self):
        assert score_text("") == 1.0

    def test_score_is_bounded(self):
        for text in ["", "あ", "私は学生です。", "無" * 500]:
            assert 1.0 <= score_text(text) <= 5.0

    def test_get_level_falls_back(self):
        assert get_level("nonsense").code == "N4"
        assert get_level("n5").code == "N5"

    def test_stats_polite_ratio(self):
        assert analyse("私は学生です。").polite_ratio == 1.0
        assert analyse("これは本である。").polite_ratio == 0.0


class TestSentenceSplittingBasics:
    def test_splits_on_japanese_punctuation(self):
        assert split_sentences("あ。い！う？") == ["あ。", "い！", "う？"]

    def test_ignores_empty(self):
        assert split_sentences("") == []
        assert split_sentences("　") == []
