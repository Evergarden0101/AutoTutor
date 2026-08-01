"""The bundled corpus and the difficulty estimator."""

from __future__ import annotations

import json
import re

import pytest

from autotutor.content.corpus import (
    available_topic_ids,
    corpus_dir,
    corpus_stats,
    frame_sentences,
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

    def test_stats(self):
        stats = corpus_stats()
        assert len(stats) == len(TOPIC_IDS)
        assert sum(stats.values()) > 400


class TestCommonFrames:
    @pytest.mark.parametrize("level", LEVEL_CODES)
    @pytest.mark.parametrize("kind", ["openers", "closers"])
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


class TestSentenceSplitting:
    def test_splits_on_japanese_punctuation(self):
        assert split_sentences("あ。い！う？") == ["あ。", "い！", "う？"]

    def test_ignores_empty(self):
        assert split_sentences("") == []
        assert split_sentences("　") == []
