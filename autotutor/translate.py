"""Japanese to Chinese translation for text that did not come from the corpus.

Corpus lessons ship with human-written translations, so this module is only
used for material fetched from the web or pasted by the user.  Several free
back-ends are tried in order; if every one of them fails the lesson is still
produced, just without a translation (and with a warning attached).
"""

from __future__ import annotations

import html
import re
from typing import Callable, List, Optional, Sequence, Tuple

from .net import NetworkError, get_json, post_json

_GOOGLE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
_ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"

_SEP = "\n@@@\n"


class TranslationError(RuntimeError):
    pass


def _google(texts: Sequence[str], timeout: int) -> List[str]:
    """Undocumented but widely used endpoint; no API key required."""
    out: List[str] = []
    for text in texts:
        data = get_json(
            _GOOGLE_ENDPOINT,
            params={
                "client": "gtx",
                "sl": "ja",
                "tl": "zh-CN",
                "dt": "t",
                "q": text,
            },
            timeout=timeout,
        )
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise TranslationError("翻译服务返回了意外的格式。")
        chunks = [seg[0] for seg in data[0] if isinstance(seg, list) and seg and seg[0]]
        out.append("".join(chunks).strip())
    return out


def _mymemory(texts: Sequence[str], timeout: int) -> List[str]:
    out: List[str] = []
    for text in texts:
        data = get_json(
            _MYMEMORY_ENDPOINT,
            params={"q": text, "langpair": "ja|zh-CN"},
            timeout=timeout,
        )
        translated = ""
        if isinstance(data, dict):
            translated = ((data.get("responseData") or {}).get("translatedText")) or ""
        translated = html.unescape(str(translated)).strip()
        if not translated:
            raise TranslationError("翻译服务没有返回内容。")
        out.append(translated)
    return out


def _anthropic(texts: Sequence[str], timeout: int, api_key: str, model: str) -> List[str]:
    """Highest quality option - used only when the user configured a key."""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "把下面每一行日语翻译成简体中文。要求：\n"
        "1) 逐行对应，保持行号；\n"
        "2) 只输出译文，不要解释；\n"
        "3) 译文自然、口语化。\n\n" + numbered
    )
    data = post_json(
        _ANTHROPIC_ENDPOINT,
        {
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = [re.sub(r"^\s*\d+[.、)]\s*", "", ln) for ln in lines]
    if len(cleaned) < len(texts):
        cleaned.extend([""] * (len(texts) - len(cleaned)))
    return cleaned[: len(texts)]


def to_japanese(text: str, timeout: int = 15) -> str:
    """Translate a search query into Japanese so web search works better.

    Returns the original text when the request fails - callers treat the
    result as a hint, never as a requirement.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if re.search(r"[぀-ヿ㐀-䶿一-鿿]", text):
        return text  # already Japanese (or at least CJK)
    try:
        data = get_json(
            _GOOGLE_ENDPOINT,
            params={"client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text},
            timeout=timeout,
        )
        chunks = [seg[0] for seg in data[0] if isinstance(seg, list) and seg and seg[0]]
        return "".join(chunks).strip() or text
    except (NetworkError, ValueError, KeyError, IndexError, TypeError):
        return text


class Translator:
    """Tries every configured back-end until one succeeds."""

    def __init__(
        self,
        timeout: int = 20,
        api_key: str = "",
        model: str = "claude-sonnet-5",
        enabled: bool = True,
    ) -> None:
        self.timeout = timeout
        self.api_key = (api_key or "").strip()
        self.model = model
        self.enabled = enabled
        self.last_backend = ""

    def _backends(self) -> List[Tuple[str, Callable[[Sequence[str]], List[str]]]]:
        backends: List[Tuple[str, Callable[[Sequence[str]], List[str]]]] = []
        if self.api_key:
            backends.append(
                (
                    "Claude API",
                    lambda texts: _anthropic(texts, self.timeout, self.api_key, self.model),
                )
            )
        backends.append(("Google", lambda texts: _google(texts, self.timeout)))
        backends.append(("MyMemory", lambda texts: _mymemory(texts, self.timeout)))
        return backends

    def translate(self, texts: Sequence[str]) -> Tuple[List[str], Optional[str]]:
        """Return ``(translations, warning)``.

        ``translations`` always has the same length as ``texts``; entries are
        empty strings when every back-end failed.
        """
        texts = [t or "" for t in texts]
        if not texts:
            return [], None
        if not self.enabled:
            return ["" for _ in texts], None

        errors: List[str] = []
        for name, backend in self._backends():
            try:
                result = backend(texts)
            except (NetworkError, TranslationError, ValueError, KeyError) as exc:
                errors.append(f"{name}: {exc}")
                continue
            if len(result) != len(texts):
                errors.append(f"{name}: 返回条数不匹配")
                continue
            if any(r.strip() for r in result):
                self.last_backend = name
                return [r.strip() for r in result], None

        return (
            ["" for _ in texts],
            "未能获取中文翻译（" + "；".join(errors[:2]) + "）。文本与音频仍可正常使用。",
        )
