"""Search the web for Japanese reading material.

Two sources are used, both free and key-less:

* **NHK News Web Easy** - news rewritten for learners, ideal for N5/N4.
* **Japanese Wikipedia** - broad topic coverage for N3 and above.

Fetched text is split into sentences and a sliding window picks the passage
whose estimated difficulty is closest to the level the learner selected.
"""

from __future__ import annotations

import html
import random
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..config import Settings
from ..levels import LEVELS, get_level, score_text, split_sentences
from ..models import GenerationRequest, Lesson, target_sentence_count
from ..net import NetworkError, get_json, get_text
from ..reading import normalise
from ..topics import RANDOM_TOPIC, TOPICS, get_topic, search_terms_for
from ..translate import Translator, to_japanese
from .builder import build_lesson
from .corpus import available_topic_ids

WIKIPEDIA_API = "https://ja.wikipedia.org/w/api.php"
NHK_LIST = "https://www3.nhk.or.jp/news/easy/news-list.json"
NHK_ARTICLE = "https://www3.nhk.or.jp/news/easy/{news_id}/{news_id}.html"
NHK_PAGE = "https://www3.nhk.or.jp/news/easy/{news_id}/{news_id}.html"


@dataclass
class Article:
    title: str
    text: str
    url: str = ""
    source_label: str = ""
    sentences: List[str] = field(default_factory=list)


class OnlineError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTML helpers (no external parser needed)
# --------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_RUBY_ANNOTATION_RE = re.compile(r"<(rt|rp)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{2,}")


def strip_html(markup: str) -> str:
    """Plain text from HTML, discarding ruby annotations (we regenerate them)."""
    text = _SCRIPT_RE.sub(" ", markup or "")
    text = _RUBY_ANNOTATION_RE.sub("", text)
    text = re.sub(r"</(p|div|br|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _BLANK_RE.sub("\n", text)
    return text.strip()


def clean_sentences(text: str) -> List[str]:
    """Split into sentences and drop anything that will not read aloud well."""
    text = normalise(text)
    text = re.sub(r"[（(][^）)]{0,80}[）)]", "", text)  # inline glosses / readings
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    out: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("="):
            continue
        for sentence in split_sentences(line):
            sentence = sentence.strip()
            if len(sentence) < 8 or len(sentence) > 140:
                continue
            if not sentence.endswith(("。", "！", "？")):
                continue
            if re.search(r"[a-zA-Z]{12,}|https?://|\|", sentence):
                continue
            # Reference-heavy or list-like fragments read badly.
            if sentence.count("、") > 8:
                continue
            out.append(sentence)
    return out


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def search_wikipedia(term: str, timeout: int, limit: int = 4) -> List[str]:
    data = get_json(
        WIKIPEDIA_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srlimit": limit,
            "srnamespace": 0,
            "format": "json",
        },
        timeout=timeout,
    )
    results = ((data or {}).get("query") or {}).get("search") or []
    return [r.get("title", "") for r in results if r.get("title")]


def fetch_wikipedia_article(title: str, timeout: int) -> Optional[Article]:
    data = get_json(
        WIKIPEDIA_API,
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "redirects": 1,
            "titles": title,
            "format": "json",
        },
        timeout=timeout,
    )
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        extract = (page or {}).get("extract") or ""
        if not extract.strip():
            continue
        real_title = page.get("title", title)
        url = "https://ja.wikipedia.org/wiki/" + real_title.replace(" ", "_")
        return Article(
            title=real_title,
            text=extract,
            url=url,
            source_label="ウィキペディア（日本語版）/ 维基百科",
        )
    return None


def fetch_nhk_easy(timeout: int, term: str = "", limit: int = 6) -> List[Article]:
    """Recent NHK News Web Easy articles, optionally filtered by keyword."""
    raw = get_text(NHK_LIST, timeout=timeout)
    raw = raw.lstrip("﻿")
    data = get_json_from_text(raw)
    items: List[dict] = []
    if isinstance(data, list):
        for group in data:
            if isinstance(group, dict):
                for _day, entries in sorted(group.items(), reverse=True):
                    if isinstance(entries, list):
                        items.extend(e for e in entries if isinstance(e, dict))
    if not items:
        raise OnlineError("NHK Easy 没有返回文章列表。")

    if term:
        filtered = [i for i in items if term in (i.get("title") or "")]
        items = filtered or items

    articles: List[Article] = []
    for item in items[: limit * 2]:
        news_id = item.get("news_id") or ""
        title = re.sub(r"<[^>]+>", "", item.get("title") or "")
        if not news_id:
            continue
        try:
            markup = get_text(NHK_ARTICLE.format(news_id=news_id), timeout=timeout)
        except NetworkError:
            continue
        body = extract_nhk_body(markup)
        if not body:
            continue
        articles.append(
            Article(
                title=title,
                text=body,
                url=NHK_PAGE.format(news_id=news_id),
                source_label="NHK NEWS WEB EASY（やさしい日本語）",
            )
        )
        if len(articles) >= limit:
            break
    if not articles:
        raise OnlineError("未能读取 NHK Easy 的正文。")
    return articles


def get_json_from_text(raw: str):
    import json

    try:
        return json.loads(raw)
    except ValueError as exc:
        raise OnlineError(f"NHK Easy 返回的内容无法解析：{exc}") from exc


def extract_nhk_body(markup: str) -> str:
    match = re.search(
        r'<div[^>]+id="js-article-body"[^>]*>(.*?)</div>', markup, re.S | re.I
    )
    if not match:
        match = re.search(
            r'<div[^>]+class="[^"]*article-main__body[^"]*"[^>]*>(.*?)</div>',
            markup,
            re.S | re.I,
        )
    if not match:
        match = re.search(r"<article[^>]*>(.*?)</article>", markup, re.S | re.I)
    if not match:
        return ""
    return strip_html(match.group(1))


# --------------------------------------------------------------------------
# Level-aware window selection
# --------------------------------------------------------------------------


def best_window(sentences: Sequence[str], level: str, size: int) -> Tuple[List[str], float]:
    """Pick ``size`` consecutive sentences closest to ``level``."""
    if not sentences:
        return [], 99.0
    target = float(get_level(level).rank)
    size = max(2, min(size, len(sentences)))
    scores = [score_text(s) for s in sentences]

    best_index, best_cost = 0, float("inf")
    for start in range(0, len(sentences) - size + 1):
        window = scores[start: start + size]
        mean = sum(window) / len(window)
        spread = max(window) - min(window)
        # Prefer windows near the target level and internally consistent;
        # a small bonus keeps us near the top of the article.
        cost = abs(mean - target) + spread * 0.15 + start * 0.01
        if cost < best_cost:
            best_cost, best_index = cost, start

    window = list(sentences[best_index: best_index + size])
    mean = sum(scores[best_index: best_index + size]) / size
    return window, abs(mean - target)


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


class OnlineGenerator:
    def __init__(self, settings: Settings, rng: Optional[random.Random] = None) -> None:
        self.settings = settings
        self.rng = rng or random.Random()

    def _collect(self, request: GenerationRequest, warnings: List[str]) -> List[Article]:
        timeout = self.settings.request_timeout
        topic_id = request.topic
        if topic_id == RANDOM_TOPIC:
            ids = list(available_topic_ids()) or list(TOPICS)
            topic_id = self.rng.choice(ids)

        query = request.topic_query
        if query:
            query = to_japanese(query, timeout=timeout)
        terms = search_terms_for(topic_id, query)
        if not terms:
            topic = get_topic(topic_id)
            terms = [topic.name_ja] if topic else ["日本語"]
        self.rng.shuffle(terms)

        articles: List[Article] = []
        rank = get_level(request.level).rank

        # Learner-friendly news first for the two lowest levels.
        if rank <= 2:
            try:
                articles.extend(fetch_nhk_easy(timeout, term=query or "", limit=4))
            except (NetworkError, OnlineError) as exc:
                warnings.append(f"NHK Easy 暂时不可用（{exc}），已改用维基百科。")

        for term in terms[:3]:
            try:
                titles = search_wikipedia(term, timeout)
            except NetworkError as exc:
                warnings.append(f"维基百科搜索失败：{exc}")
                continue
            for title in titles[:2]:
                try:
                    article = fetch_wikipedia_article(title, timeout)
                except NetworkError:
                    continue
                if article:
                    articles.append(article)
            if len(articles) >= 4:
                break
        return articles

    def generate(self, request: GenerationRequest) -> Lesson:
        warnings: List[str] = []
        articles = self._collect(request, warnings)
        if not articles:
            raise OnlineError(
                "没有搜到合适的日语文章。请检查网络连接，或换一个主题再试。"
            )

        size = target_sentence_count(request.length)
        best: Optional[Tuple[Article, List[str], float]] = None
        for article in articles:
            sentences = clean_sentences(article.text)
            if len(sentences) < 3:
                continue
            window, distance = best_window(sentences, request.level, size)
            if not window:
                continue
            if best is None or distance < best[2]:
                best = (article, window, distance)

        if best is None:
            raise OnlineError("搜到的文章无法拆成合适的句子，请换一个主题再试。")

        article, window, distance = best
        estimated = sum(score_text(s) for s in window) / len(window)
        if distance > 1.2:
            level_name = LEVELS[request.level].label_zh
            warnings.append(
                f"这段网络文本的估计难度约为 {estimated:.1f} 级"
                f"（1=N5，5=N1），与所选的 {request.level}（{level_name}）差距较大。"
                "如需完全贴合级别，请使用离线语料模式。"
            )

        translator = Translator(
            timeout=self.settings.request_timeout,
            api_key=self.settings.anthropic_api_key,
            model=self.settings.anthropic_model,
            enabled=self.settings.online_translate and request.translate,
        )
        translations, warning = translator.translate(window)
        if warning:
            warnings.append(warning)

        topic_id = request.topic if request.topic != RANDOM_TOPIC else ""
        topic = get_topic(topic_id)
        return build_lesson(
            list(zip(window, translations)),
            level=request.level,
            topic=topic_id or (request.topic_query or "web"),
            title_ja=article.title,
            title_zh=request.topic_query or (topic.name_zh if topic else article.title),
            source="online",
            source_label=article.source_label or "网络搜索",
            source_url=article.url,
            warnings=warnings,
        )
