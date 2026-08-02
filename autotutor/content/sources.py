"""Where online Japanese material comes from.

Each source knows how to turn a topic (or a URL) into :class:`Article` objects.
They are deliberately small and independent: a source that breaks - a feed that
moves, a site that changes its markup - fails on its own and the rest carry on.

Sources differ in *register* as much as in difficulty, which is what makes the
spoken/written choice possible:

===============  ==========  ==========================================
source           register    what it gives you
===============  ==========  ==========================================
nhk_easy         written     news rewritten for learners (N5-N4)
nhk_news         written     national news headlines and summaries
wikinews         written     current-affairs articles
wikipedia        written     encyclopedic prose, any topic
podcast          spoken      episode notes from Japanese podcast feeds
youtube          spoken      captions from a video URL you paste
===============  ==========  ==========================================

Nothing here needs an API key.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from ..net import NetworkError, get_text
from ..reading import normalise

# Register tags.
REGISTER_SPOKEN = "spoken"
REGISTER_WRITTEN = "written"
REGISTER_ANY = "any"


class SourceError(RuntimeError):
    """A single source failed; the caller should try the next one."""


@dataclass
class Article:
    title: str
    text: str
    url: str = ""
    source_label: str = ""
    source_id: str = ""
    register: str = REGISTER_WRITTEN


# --------------------------------------------------------------------------
# Shared helpers
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


def _localname(tag: str) -> str:
    """``{http://...}item`` -> ``item``."""
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, limit: int = 20) -> List[Dict[str, str]]:
    """Parse an RSS 2.0 or Atom feed into ``{title, summary, link}`` dicts.

    Written against ElementTree rather than a feed library so the offline
    build stays dependency-free; namespaces are ignored on purpose because
    podcast feeds use a zoo of them.
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise SourceError(f"无法解析订阅源：{exc}") from exc

    entries: List[Dict[str, str]] = []
    for element in root.iter():
        if _localname(element.tag) not in {"item", "entry"}:
            continue
        entry = {"title": "", "summary": "", "link": ""}
        for child in element:
            name = _localname(child.tag)
            value = (child.text or "").strip()
            if name == "title" and not entry["title"]:
                entry["title"] = strip_html(value)
            elif name in {"description", "summary", "encoded", "subtitle"}:
                if len(strip_html(value)) > len(entry["summary"]):
                    entry["summary"] = strip_html(value)
            elif name == "content" and not entry["summary"]:
                entry["summary"] = strip_html(value or "".join(child.itertext()))
            elif name == "link" and not entry["link"]:
                entry["link"] = value or child.attrib.get("href", "")
        if entry["title"] or entry["summary"]:
            entries.append(entry)
        if len(entries) >= limit:
            break
    if not entries:
        raise SourceError("订阅源里没有找到文章。")
    return entries


# --------------------------------------------------------------------------
# MediaWiki (Wikipedia and Wikinews share an API)
# --------------------------------------------------------------------------


def _mediawiki_json(api: str, params: Dict[str, object], timeout: int):
    from ..net import get_json

    return get_json(api, params=params, timeout=timeout)


def mediawiki_search(api: str, term: str, timeout: int, limit: int = 4) -> List[str]:
    data = _mediawiki_json(
        api,
        {
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srlimit": limit,
            "srnamespace": 0,
            "format": "json",
        },
        timeout,
    )
    results = ((data or {}).get("query") or {}).get("search") or []
    return [r.get("title", "") for r in results if r.get("title")]


def mediawiki_extract(api: str, title: str, timeout: int) -> Optional[str]:
    data = _mediawiki_json(
        api,
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "redirects": 1,
            "titles": title,
            "format": "json",
        },
        timeout,
    )
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        extract = (page or {}).get("extract") or ""
        if extract.strip():
            return extract
    return None


# --------------------------------------------------------------------------
# YouTube captions
# --------------------------------------------------------------------------

_YOUTUBE_ID_RE = re.compile(
    r"(?:v=|/shorts/|youtu\.be/|/embed/|/live/)([0-9A-Za-z_-]{11})"
)


def youtube_video_id(url: str) -> Optional[str]:
    """Extract the 11-character video id from any common YouTube URL form."""
    url = (url or "").strip()
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url):
        return url
    match = _YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def _caption_tracks(watch_page: str) -> List[Dict[str, str]]:
    """Pull the caption track list out of the watch page's player response."""
    match = re.search(r'"captionTracks":\s*(\[.*?\])', watch_page, re.S)
    if not match:
        return []
    try:
        tracks = json.loads(match.group(1))
    except ValueError:
        return []
    return [t for t in tracks if isinstance(t, dict) and t.get("baseUrl")]


def pick_japanese_track(tracks: Sequence[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Prefer a human-written Japanese track over an auto-generated one."""
    japanese = [t for t in tracks if str(t.get("languageCode", "")).startswith("ja")]
    if not japanese:
        return None
    manual = [t for t in japanese if t.get("kind") != "asr"]
    return (manual or japanese)[0]


def parse_caption_xml(xml_text: str) -> str:
    """Join a timedtext XML transcript into readable text."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise SourceError(f"字幕格式无法解析：{exc}") from exc

    parts: List[str] = []
    for node in root.iter():
        if _localname(node.tag) != "text":
            continue
        line = html.unescape(strip_html(node.text or "")).strip()
        if line:
            parts.append(line)
    if not parts:
        raise SourceError("这个视频的字幕是空的。")

    # Caption lines break mid-sentence; join them and let the sentence splitter
    # do the work. A line that already ends a sentence keeps its break.
    text = ""
    for line in parts:
        text += line
        if line.endswith(("。", "！", "？", "」")):
            text += "\n"
    return text


def fetch_youtube_captions(url: str, timeout: int) -> Article:
    """Fetch the Japanese captions of a public video the user pointed us at.

    This is URL-driven on purpose: AutoTutor never crawls or searches YouTube,
    it only reads the caption track of a video the learner chose.
    """
    video_id = youtube_video_id(url)
    if not video_id:
        raise SourceError("这不是一个有效的 YouTube 链接。")

    try:
        page = get_text(
            f"https://www.youtube.com/watch?v={video_id}",
            timeout=timeout,
            headers={"Accept-Language": "ja"},
        )
    except NetworkError as exc:
        raise SourceError(f"无法打开这个视频页面：{exc}") from exc

    track = pick_japanese_track(_caption_tracks(page))
    if not track:
        raise SourceError(
            "这个视频没有日语字幕。请换一个带日语字幕（CC）的视频。"
        )

    try:
        caption_xml = get_text(track["baseUrl"], timeout=timeout)
    except NetworkError as exc:
        raise SourceError(f"无法下载字幕：{exc}") from exc

    title_match = re.search(r'"title":"([^"]{1,200})"', page)
    title = title_match.group(1).encode().decode("unicode_escape", "ignore") if title_match else "YouTube"
    auto = track.get("kind") == "asr"
    return Article(
        title=normalise(title),
        text=parse_caption_xml(caption_xml),
        url=f"https://www.youtube.com/watch?v={video_id}",
        source_label="YouTube 字幕" + ("（自动生成）" if auto else ""),
        source_id="youtube",
        register=REGISTER_SPOKEN,
    )


# --------------------------------------------------------------------------
# Source definitions
# --------------------------------------------------------------------------

# Japanese podcast / spoken-word feeds. Episode notes are conversational, which
# is what makes them useful here - we read the notes, not the audio.
PODCAST_FEEDS: List[Dict[str, str]] = [
    {"name": "NHKラジオニュース", "url": "https://www.nhk.or.jp/radionews/podcast/nhkradionews.xml"},
    {"name": "バイリンガルニュース", "url": "https://bilingualnews.libsyn.com/rss"},
    {"name": "Nihongo con Teppei", "url": "https://anchor.fm/s/1ad4c6c/podcast/rss"},
    {"name": "日本語の聴解", "url": "https://anchor.fm/s/2e4b8a4/podcast/rss"},
]

NEWS_FEEDS: List[Dict[str, str]] = [
    {"name": "NHK 主要ニュース", "url": "https://www.nhk.or.jp/rss/news/cat0.xml"},
    {"name": "NHK 生活・科学", "url": "https://www.nhk.or.jp/rss/news/cat3.xml"},
]

WIKIPEDIA_API = "https://ja.wikipedia.org/w/api.php"
WIKINEWS_API = "https://ja.wikinews.org/w/api.php"
NHK_EASY_LIST = "https://www3.nhk.or.jp/news/easy/news-list.json"
NHK_EASY_ARTICLE = "https://www3.nhk.or.jp/news/easy/{news_id}/{news_id}.html"


@dataclass
class Source:
    """One place to look for Japanese text."""

    id: str
    label_zh: str
    register: str
    fetch: Callable[..., List[Article]]
    # Levels this source suits best (used to order the search).
    best_levels: Sequence[str] = field(default_factory=tuple)
    note_zh: str = ""


def _feed_articles(
    feeds: Sequence[Dict[str, str]],
    source_id: str,
    register: str,
    timeout: int,
    query: str = "",
    limit: int = 4,
) -> List[Article]:
    """Turn a set of RSS feeds into articles, tolerating individual failures."""
    articles: List[Article] = []
    errors: List[str] = []
    for feed in feeds:
        if len(articles) >= limit:
            break
        try:
            entries = parse_feed(get_text(feed["url"], timeout=timeout))
        except (NetworkError, SourceError) as exc:
            errors.append(f"{feed['name']}: {exc}")
            continue
        if query:
            matched = [e for e in entries if query in e["title"] + e["summary"]]
            entries = matched or entries
        for entry in _substantial_first(entries):
            body = "\n".join(part for part in (entry["title"], entry["summary"]) if part)
            if len(body) < _MIN_ENTRY_CHARS:
                continue
            articles.append(
                Article(
                    title=entry["title"] or feed["name"],
                    text=body,
                    url=entry["link"],
                    source_label=feed["name"],
                    source_id=source_id,
                    register=register,
                )
            )
            if len(articles) >= limit:
                break
    if not articles:
        raise SourceError("；".join(errors[:2]) or "没有取到内容。")
    return articles


# Below this an entry is a headline, not something you can listen to.
_MIN_ENTRY_CHARS = 40
# An episode note this long can carry a lesson by itself, which is what makes
# the result a continuous piece of speech rather than a montage of blurbs.
_SUBSTANTIAL_CHARS = 200


def _substantial_first(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Entries that can stand alone first, the rest after, order preserved.

    Not a plain sort by length: within each group the feed's own order is kept
    so the newest episode still wins ties, and a feed of uniformly short notes
    is degraded rather than rejected.
    """
    def size(entry: Dict[str, str]) -> int:
        return len(entry.get("title", "")) + len(entry.get("summary", ""))

    big = [e for e in entries if size(e) >= _SUBSTANTIAL_CHARS]
    small = [e for e in entries if size(e) < _SUBSTANTIAL_CHARS]
    return big + small


def fetch_podcasts(query: str = "", timeout: int = 20, limit: int = 4) -> List[Article]:
    return _feed_articles(PODCAST_FEEDS, "podcast", REGISTER_SPOKEN, timeout, query, limit)


def fetch_news_feeds(query: str = "", timeout: int = 20, limit: int = 4) -> List[Article]:
    return _feed_articles(NEWS_FEEDS, "nhk_news", REGISTER_WRITTEN, timeout, query, limit)


def fetch_wikipedia(query: str = "", timeout: int = 20, limit: int = 3) -> List[Article]:
    return _fetch_mediawiki(
        WIKIPEDIA_API, "wikipedia", "ウィキペディア（日本語版）/ 维基百科",
        "https://ja.wikipedia.org/wiki/", query, timeout, limit,
    )


def fetch_wikinews(query: str = "", timeout: int = 20, limit: int = 3) -> List[Article]:
    return _fetch_mediawiki(
        WIKINEWS_API, "wikinews", "ウィキニュース / 维基新闻",
        "https://ja.wikinews.org/wiki/", query, timeout, limit,
    )


def _fetch_mediawiki(api, source_id, label, base_url, query, timeout, limit):
    if not query:
        raise SourceError("需要一个搜索关键词。")
    try:
        titles = mediawiki_search(api, query, timeout, limit)
    except NetworkError as exc:
        raise SourceError(str(exc)) from exc

    articles: List[Article] = []
    for title in titles[:limit]:
        try:
            extract = mediawiki_extract(api, title, timeout)
        except NetworkError:
            continue
        if not extract:
            continue
        articles.append(
            Article(
                title=title,
                text=extract,
                url=base_url + title.replace(" ", "_"),
                source_label=label,
                source_id=source_id,
                register=REGISTER_WRITTEN,
            )
        )
    if not articles:
        raise SourceError("没有搜到条目。")
    return articles


def fetch_nhk_easy(query: str = "", timeout: int = 20, limit: int = 4) -> List[Article]:
    """Recent NHK News Web Easy articles, optionally filtered by keyword."""
    raw = get_text(NHK_EASY_LIST, timeout=timeout).lstrip("﻿")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SourceError(f"NHK Easy 返回的内容无法解析：{exc}") from exc

    items: List[dict] = []
    if isinstance(data, list):
        for group in data:
            if isinstance(group, dict):
                for _day, entries in sorted(group.items(), reverse=True):
                    if isinstance(entries, list):
                        items.extend(e for e in entries if isinstance(e, dict))
    if not items:
        raise SourceError("NHK Easy 没有返回文章列表。")

    if query:
        matched = [i for i in items if query in (i.get("title") or "")]
        items = matched or items

    articles: List[Article] = []
    for item in items[: limit * 2]:
        news_id = item.get("news_id") or ""
        if not news_id:
            continue
        try:
            markup = get_text(NHK_EASY_ARTICLE.format(news_id=news_id), timeout=timeout)
        except NetworkError:
            continue
        body = extract_nhk_body(markup)
        if not body:
            continue
        articles.append(
            Article(
                title=re.sub(r"<[^>]+>", "", item.get("title") or ""),
                text=body,
                url=NHK_EASY_ARTICLE.format(news_id=news_id),
                source_label="NHK NEWS WEB EASY（やさしい日本語）",
                source_id="nhk_easy",
                register=REGISTER_WRITTEN,
            )
        )
        if len(articles) >= limit:
            break
    if not articles:
        raise SourceError("未能读取 NHK Easy 的正文。")
    return articles


def extract_nhk_body(markup: str) -> str:
    for pattern in (
        r'<div[^>]+id="js-article-body"[^>]*>(.*?)</div>',
        r'<div[^>]+class="[^"]*article-main__body[^"]*"[^>]*>(.*?)</div>',
        r"<article[^>]*>(.*?)</article>",
    ):
        match = re.search(pattern, markup, re.S | re.I)
        if match:
            return strip_html(match.group(1))
    return ""


SOURCES: List[Source] = [
    Source("nhk_easy", "NHK 简易新闻", REGISTER_WRITTEN, fetch_nhk_easy,
           best_levels=("N5", "N4", "N3"),
           note_zh="为日语学习者改写的新闻，最适合初级。"),
    Source("podcast", "播客节目笔记", REGISTER_SPOKEN, fetch_podcasts,
           best_levels=("N4", "N3", "N2"),
           note_zh="日语播客的节目简介，语气偏口语。"),
    Source("youtube", "YouTube 字幕", REGISTER_SPOKEN, lambda *a, **k: [],
           best_levels=("N4", "N3", "N2", "N1"),
           note_zh="粘贴一个带日语字幕的视频链接，读取它的字幕。"),
    Source("nhk_news", "NHK 新闻", REGISTER_WRITTEN, fetch_news_feeds,
           best_levels=("N3", "N2", "N1"),
           note_zh="日本国内新闻的标题与摘要。"),
    Source("wikinews", "维基新闻", REGISTER_WRITTEN, fetch_wikinews,
           best_levels=("N3", "N2", "N1"),
           note_zh="时事报道，比维基百科口语一些。"),
    Source("wikipedia", "维基百科", REGISTER_WRITTEN, fetch_wikipedia,
           best_levels=("N2", "N1"),
           note_zh="任何主题都查得到，但文体最正式。"),
]

SOURCES_BY_ID: Dict[str, Source] = {s.id: s for s in SOURCES}
# Sources that are searched automatically; youtube needs a URL from the user.
AUTO_SOURCE_IDS: List[str] = [s.id for s in SOURCES if s.id != "youtube"]


def sources_for(register: str, level: str) -> List[Source]:
    """Order the searchable sources for a register and level, best first."""
    def rank(source: Source) -> tuple:
        register_miss = 0
        if register in (REGISTER_SPOKEN, REGISTER_WRITTEN):
            register_miss = 0 if source.register == register else 1
        level_miss = 0 if (not source.best_levels or level in source.best_levels) else 1
        return (register_miss, level_miss, SOURCES.index(source))

    return sorted((s for s in SOURCES if s.id in AUTO_SOURCE_IDS), key=rank)
