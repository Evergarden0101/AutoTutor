"""Tiny HTTP helper built on the standard library.

Keeping this dependency-free matters: the offline build of AutoTutor must not
pull in ``requests`` or ``aiohttp`` just to be able to *not* use them.
"""

from __future__ import annotations

import json
import gzip
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

# A descriptive User-Agent is the polite default, but several Japanese news
# sites sit behind a CDN that answers anything non-browser with 401/403 - NHK
# News Web Easy's article list is one of them. These are public, unauthenticated
# endpoints serving the same JSON to any browser, so we identify as one.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
}

DEFAULT_TIMEOUT = 20


class NetworkError(RuntimeError):
    """Raised when a request cannot be completed."""


def _open(url: str, timeout: int, headers: Optional[Dict[str, str]] = None) -> bytes:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept-Encoding", "gzip")
    for key, value in DEFAULT_HEADERS.items():
        request.add_header(key, value)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
            return payload
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise NetworkError(f"无法连接：{exc}") from exc


def get_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> str:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _open(url, timeout, headers).decode(encoding, errors="replace")


def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    text = get_text(url, params, timeout, headers)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise NetworkError(f"返回的内容不是有效的 JSON：{url}") from exc


def post_json(
    url: str,
    payload: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # pragma: no cover - best effort only
            pass
        raise NetworkError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
        raise NetworkError(f"请求失败：{exc}") from exc


def online() -> bool:
    """Cheap connectivity probe (no exception, just True/False)."""
    try:
        _open("https://ja.wikipedia.org/robots.txt", timeout=6)
        return True
    except NetworkError:
        return False
