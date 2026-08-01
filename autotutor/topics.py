"""The topic registry.

Each topic maps to a bundled corpus file (``autotutor/data/corpus/<id>.json``)
and to a set of search keywords used by the online generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

RANDOM_TOPIC = "__random__"
CUSTOM_TOPIC = "__custom__"


@dataclass(frozen=True)
class Topic:
    id: str
    name_ja: str
    name_zh: str
    name_en: str
    icon: str = ""
    search_terms: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        # No emoji here on purpose: the CJK UI fonts on a plain Windows or
        # Linux install render them as tofu boxes inside a ttk Combobox.
        return f"{self.name_zh} / {self.name_ja}"

    @property
    def label(self) -> str:
        return f"{self.name_zh}({self.name_ja})"


_TOPIC_LIST: List[Topic] = [
    Topic(
        "daily_life",
        "日常生活",
        "日常生活",
        "Daily life",
        "🏠",
        ["日常生活", "生活習慣", "家事"],
    ),
    Topic("food", "食べ物と料理", "饮食与料理", "Food & cooking", "🍜",
          ["日本料理", "和食", "家庭料理"]),
    Topic("travel", "旅行", "旅行", "Travel", "🧳",
          ["観光", "旅行", "新幹線"]),
    Topic("school", "学校と勉強", "学校与学习", "School & study", "🎒",
          ["学校教育", "大学", "受験"]),
    Topic("work", "仕事とビジネス", "工作与职场", "Work & business", "💼",
          ["会社", "働き方", "ビジネスマナー"]),
    Topic("shopping", "買い物", "购物", "Shopping", "🛒",
          ["買い物", "コンビニエンスストア", "通信販売"]),
    Topic("hospital", "病院と健康", "医院与健康", "Hospital & health", "🏥",
          ["病院", "健康", "医療"]),
    Topic("game", "ゲーム", "电子游戏", "Video games", "🎮",
          ["コンピュータゲーム", "テレビゲーム", "eスポーツ"]),
    Topic("anime", "アニメと漫画", "动画与漫画", "Anime & manga", "🎬",
          ["日本のアニメーション", "漫画", "声優"]),
    Topic("programming", "プログラミング", "编程开发", "Programming", "💻",
          ["プログラミング", "ソフトウェア開発", "プログラミング言語"]),
    Topic("technology", "テクノロジー", "科技", "Technology", "🔬",
          ["人工知能", "ロボット", "情報技術"]),
    Topic("sports", "スポーツ", "体育运动", "Sports", "⚽",
          ["スポーツ", "野球", "サッカー"]),
    Topic("music", "音楽", "音乐", "Music", "🎵",
          ["音楽", "ポピュラー音楽", "楽器"]),
    Topic("weather", "天気と季節", "天气与季节", "Weather & seasons", "🌤",
          ["気象", "四季", "台風"]),
    Topic("culture", "日本文化", "日本文化", "Japanese culture", "⛩",
          ["日本の文化", "年中行事", "伝統芸能"]),
]

TOPICS: Dict[str, Topic] = {t.id: t for t in _TOPIC_LIST}
TOPIC_IDS: List[str] = [t.id for t in _TOPIC_LIST]


def get_topic(topic_id: str) -> Optional[Topic]:
    return TOPICS.get((topic_id or "").strip())


def topic_label(topic_id: str, fallback: str = "") -> str:
    topic = get_topic(topic_id)
    if topic:
        return topic.label
    return fallback or topic_id


def all_topics() -> List[Topic]:
    return list(_TOPIC_LIST)


def search_terms_for(topic_id: str, custom: str = "") -> List[str]:
    """Search keywords for the online generator."""
    if custom.strip():
        return [custom.strip()]
    topic = get_topic(topic_id)
    if topic:
        return list(topic.search_terms) or [topic.name_ja]
    return [topic_id] if topic_id else []
