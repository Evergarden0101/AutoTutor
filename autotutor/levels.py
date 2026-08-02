"""JLPT level definitions and a heuristic difficulty estimator.

The estimator is used in two places:

* to rank/filter passages that were downloaded from the web so that only
  material close to the learner's level is offered, and
* to warn the learner when a custom text they pasted is far from the level
  they selected.

The score is a small linear model over three surface features (kanji
sophistication, sentence length, and how much of the text is plain *written*
style - neither です/ます nor colloquial).  Its weights were fitted by least
squares against the level-labelled sentences in the bundled corpus; on that
data it explains 73% of the variance and lands within one JLPT level 85% of
the time.

Note the shape of the written-style feature.  An earlier version used simply
"not です/ます", which worked only because the corpus was polite at N5-N3 and
plain at N2-N1.  Once conversational passages were added, that proxy scored
過ごしてるよ as harder than 過ごしています, which is backwards: 常体 is not the
same thing as 書き言葉.  The feature now counts the plain sentences that are
*not* colloquial either, and 終助詞 endings are subtracted out.

Two caveats worth knowing: the kanji bands below follow the widely circulated
JLPT kanji lists rather than an official specification, and surface statistics
barely separate N2 from N1 - both land around 4.2.  Treat the output as a
ranking signal, not a verdict.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

# --------------------------------------------------------------------------
# Kanji bands (approximate, used for heuristic scoring only)
# --------------------------------------------------------------------------

_N5_KANJI = (
    "日一国人年大十二本中長出三時行見月分後前生五間上東四今金九入学高円子外八六下"
    "来気小七山話女北午百書先名川千水半男西電校語土木聞食車何南万毎白天母火右読友左休父雨"
)

_N4_KANJI = (
    "会同事自社発者地業方新場員立開手力問代明動京目通言理体田主題意不作用度強公持野以思家世多正安"
    "院心界教文元重近考画海売知道集別物使品計死特私始朝運終台広住真有口少町料工建空急止送切転研足"
    "究楽起着店病質待試族銀早映親験英医仕去味写字答夜音帰古歌買悪図週室歩風紙黒花春赤青館屋色走秋"
    "夏習駅洋旅服夕借曜飲肉貸堂鳥飯勉冬昼茶弟牛魚兄犬妹姉漢"
)

_N3_KANJI = (
    "政議民連対部合市内相定回選米実関決全表戦経最現調化当約首法性的要制治務成期取都和機平加受続進"
    "数記初指権支産点報済活原共得解昨情面郎素法営転流論議料集当対相定改期政研済想側他協統減任経信"
    "存最原求関育観確際件備価再連解制向条応認況設落格施個断営利厳求務総資規制導域展性談容認組質務"
    "存在受験増加減少変化必要重要関係影響結果原因方法問題解決経験練習準備説明報告連絡相談確認注意"
    "希望興味賛成反対性格能力態度状況状態環境社会文化歴史経済政治法律科学技術情報資料条件目的理由"
    "予定予約約束招待紹介案内訪問参加出席欠席遅刻残念失敗成功努力我慢心配安心満足不満緊張感動感謝"
)

_KANJI_RE = re.compile(r"[一-鿿㐀-䶿]")
_KANJI_RUN_RE = re.compile(r"[一-鿿㐀-䶿]+")
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿー]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*")
# Sentence endings that mark learner-facing polite style.
_POLITE_END_RE = re.compile(r"(ます|ました|ません|でしょう|です|でした|ください)[。！？!?]?$")
# Sentence endings that mark casual speech. Plain form on its own does not mean
# literary Japanese - 過ごしてるよ is 常体 but easier than です/ます, not harder -
# so these are subtracted from the written-style feature.
_CASUAL_END_RE = re.compile(
    r"("
    r"だよ|だね|だな|よね|よな|かな|かなあ|かも|けど|けどね|でしょ|だろ|じゃない|じゃん"
    r"|んだ|んだよ|んだね|もん|のに|って|っけ"
    # Contracted ている/ておく: 待ってた and 見てる are speech, never an essay.
    r"|[てで](る|た|ない|なかった)"
    r"|と(く|いた)"
    r"|[^、。！？!?\s]([よねさわぞ]|っけ|かい)"
    r")[。！？!?]?$"
)
# Contractions and fillers that only occur in speech, wherever they appear.
_CASUAL_INLINE_RE = re.compile(
    r"ちゃう|ちゃっ|じゃっ|なきゃ|なくちゃ|なんか|やっぱ|だってさ|ってさ|みたいな感じ"
)

# Least-squares weights fitted against the bundled level-labelled corpus.
# Refit after conversational passages were added and the written-style feature
# was redefined. compound_ratio was dropped in this refit: its coefficient is
# positive but including it inverts the N2/N1 ordering, and the two levels are
# barely separable by surface statistics anyway.
_W_KANJI_BAND = 0.550
_W_SENTENCE_LEN = 0.803
_W_WRITTEN_STYLE = 1.141
_W_INTERCEPT = -1.551


@dataclass(frozen=True)
class Level:
    """A JLPT level and the writing style that goes with it."""

    code: str
    rank: int  # 1 = N5 (easiest) ... 5 = N1 (hardest)
    label_en: str
    label_zh: str
    label_ja: str
    description_en: str
    description_zh: str
    target_sentence_chars: Tuple[int, int]
    style_hint: str
    speech_rate: float = 1.0  # narration speed multiplier suited to the level

    @property
    def display(self) -> str:
        return f"{self.code} - {self.label_zh} / {self.label_en}"


LEVELS: Dict[str, Level] = {
    "N5": Level(
        code="N5",
        rank=1,
        label_en="Beginner",
        label_zh="入门",
        label_ja="初級",
        description_en="Short polite sentences, ~100 basic kanji, present/past forms.",
        description_zh="简短的礼貌体句子，约100个基础汉字，现在式与过去式。",
        target_sentence_chars=(8, 22),
        style_hint=(
            "very short polite sentences (です/ます), only the most basic kanji, "
            "no subordinate clauses"
        ),
        speech_rate=0.85,
    ),
    "N4": Level(
        code="N4",
        rank=2,
        label_en="Elementary",
        label_zh="初级",
        label_ja="初中級",
        description_en="Everyday sentences with て-form, plain form and common conjunctions.",
        description_zh="日常句子，含て形、简体形与常见连接词。",
        target_sentence_chars=(12, 30),
        style_hint=(
            "everyday polite sentences with て-form, から/ので, simple comparisons, "
            "about 300 common kanji"
        ),
        speech_rate=0.92,
    ),
    "N3": Level(
        code="N3",
        rank=3,
        label_en="Intermediate",
        label_zh="中级",
        label_ja="中級",
        description_en="Connected paragraphs, passive/causative, opinions and reasons.",
        description_zh="连贯段落，被动/使役，表达观点与理由。",
        target_sentence_chars=(18, 42),
        style_hint=(
            "connected explanatory sentences, passive and causative forms, "
            "expressing opinions and reasons"
        ),
        speech_rate=1.0,
    ),
    "N2": Level(
        code="N2",
        rank=4,
        label_en="Upper-intermediate",
        label_zh="中高级",
        label_ja="中上級",
        description_en="Newspaper-like prose, abstract nouns, formal conjunctions.",
        description_zh="接近报刊的文体，抽象名词与书面连接词。",
        target_sentence_chars=(25, 60),
        style_hint=(
            "semi-formal written style, abstract vocabulary, conjunctions such as "
            "一方で / に対して / といった"
        ),
        speech_rate=1.05,
    ),
    "N1": Level(
        code="N1",
        rank=5,
        label_en="Advanced",
        label_zh="高级",
        label_ja="上級",
        description_en="Editorial or technical prose with nuanced, literary expressions.",
        description_zh="社论或专业文体，含细腻、书面化的表达。",
        target_sentence_chars=(30, 80),
        style_hint=(
            "dense editorial or technical prose, nominalisation, literary connectives "
            "such に他ならない / を余儀なくされる"
        ),
        speech_rate=1.1,
    ),
}

LEVEL_CODES: List[str] = ["N5", "N4", "N3", "N2", "N1"]


def get_level(code: str) -> Level:
    """Return the :class:`Level` for ``code``, defaulting to N4."""
    return LEVELS.get((code or "").strip().upper(), LEVELS["N4"])


# --------------------------------------------------------------------------
# Difficulty scoring
# --------------------------------------------------------------------------


def _kanji_band(char: str) -> int:
    """Return 1..5 for how advanced a single kanji is."""
    if char in _N5_KANJI:
        return 1
    if char in _N4_KANJI:
        return 2
    if char in _N3_KANJI:
        return 3
    return 4  # unlisted kanji: treat as upper-intermediate or above


@dataclass
class TextStats:
    """Cheap readability statistics for a Japanese text."""

    chars: int = 0
    kanji: int = 0
    kana: int = 0
    sentences: int = 0
    avg_sentence_chars: float = 0.0
    kanji_ratio: float = 0.0
    rare_kanji_ratio: float = 0.0
    mean_kanji_band: float = 1.0
    compound_ratio: float = 0.0
    polite_ratio: float = 1.0
    casual_ratio: float = 0.0
    written_ratio: float = 0.0
    unique_kanji: List[str] = field(default_factory=list)


def split_sentences(text: str) -> List[str]:
    """Split Japanese text into sentences on 。！？ (and ASCII equivalents)."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text or "")]
    return [p for p in parts if p]


def analyse(text: str) -> TextStats:
    """Compute :class:`TextStats` for ``text``."""
    text = unicodedata.normalize("NFKC", text or "")
    stats = TextStats()
    if not text:
        return stats

    kanji_chars = _KANJI_RE.findall(text)
    stats.chars = len(text)
    stats.kanji = len(kanji_chars)
    stats.kana = len(_KANA_RE.findall(text))
    stats.unique_kanji = sorted(set(kanji_chars))

    sentences = split_sentences(text)
    stats.sentences = max(1, len(sentences))
    stats.avg_sentence_chars = stats.chars / stats.sentences

    letters = stats.kanji + stats.kana
    stats.kanji_ratio = stats.kanji / letters if letters else 0.0

    if kanji_chars:
        bands = [_kanji_band(c) for c in kanji_chars]
        stats.mean_kanji_band = sum(bands) / len(bands)
        stats.rare_kanji_ratio = sum(1 for b in bands if b >= 4) / len(bands)

    runs = _KANJI_RUN_RE.findall(text)
    if runs:
        # Multi-kanji runs are Sino-Japanese compounds - the vocabulary that
        # separates a news article from a textbook dialogue.
        stats.compound_ratio = sum(1 for r in runs if len(r) >= 2) / len(runs)

    counted = sentences or [text]
    polite = sum(1 for s in counted if _POLITE_END_RE.search(s))
    casual = sum(1 for s in counted
                 if not _POLITE_END_RE.search(s)
                 and (_CASUAL_END_RE.search(s) or _CASUAL_INLINE_RE.search(s)))
    stats.polite_ratio = polite / len(counted)
    stats.casual_ratio = casual / len(counted)
    # What is left is plain form that is neither polite nor colloquial: the
    # 常体 of newspapers, essays and encyclopedias.
    stats.written_ratio = max(0.0, 1.0 - stats.polite_ratio - stats.casual_ratio)
    return stats


def score_text(text: str) -> float:
    """Return a continuous difficulty score: 1.0 (N5) .. 5.0 (N1)."""
    stats = analyse(text)
    if not stats.chars:
        return 1.0

    score = (
        _W_KANJI_BAND * stats.mean_kanji_band
        + _W_SENTENCE_LEN * min(6.0, stats.avg_sentence_chars / 10.0)
        + _W_WRITTEN_STYLE * stats.written_ratio
        + _W_INTERCEPT
    )
    return round(min(5.0, max(1.0, score)), 2)


def estimate_level(text: str) -> str:
    """Return the JLPT code that best matches ``text``."""
    score = score_text(text)
    index = min(4, max(0, int(round(score)) - 1))
    return LEVEL_CODES[index]


def level_distance(text: str, target: str) -> float:
    """Absolute distance in level ranks between ``text`` and ``target``."""
    return abs(score_text(text) - float(get_level(target).rank))


def fits_level(text: str, target: str, tolerance: float = 1.0) -> bool:
    """True when ``text`` is within ``tolerance`` ranks of the target level."""
    return level_distance(text, target) <= tolerance


def rank_by_level(texts: Sequence[str], target: str) -> List[Tuple[str, float]]:
    """Sort ``texts`` by how close they are to ``target`` (closest first)."""
    scored = [(t, level_distance(t, target)) for t in texts]
    scored.sort(key=lambda item: item[1])
    return scored
