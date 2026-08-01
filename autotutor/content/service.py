"""Dispatch a :class:`GenerationRequest` to the right back-end.

Everything the UI needs is behind :func:`generate_lesson`, including the
fallback rules: if an online source fails for any reason the learner still
gets a lesson from the bundled corpus, with an explanation attached.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ..config import Settings
from ..models import GenerationRequest, Lesson
from ..net import NetworkError
from ..translate import Translator
from .builder import build_lesson, split_into_sentences
from .llm import LLMError, LLMGenerator
from .offline import OfflineGenerator
from .online import OnlineError, OnlineGenerator

ProgressFn = Optional[Callable[[str], None]]

SOURCE_OFFLINE = "offline"
SOURCE_ONLINE = "online"
SOURCE_LLM = "llm"
SOURCE_CUSTOM = "custom"


class GenerationError(RuntimeError):
    pass


def describe_sources() -> List[Tuple[str, str, str]]:
    """``(id, label, help text)`` for every content source, for the UI."""
    return [
        (
            SOURCE_OFFLINE,
            "离线语料库",
            "完全离线，使用内置的分级课文与人工中文翻译。",
        ),
        (
            SOURCE_ONLINE,
            "联网搜索",
            "从 NHK News Web Easy 与日文维基百科抓取真实文章，并按级别筛选。",
        ),
        (
            SOURCE_LLM,
            "AI 生成（需 API Key）",
            "用 Claude API 按所选级别与主题现写一篇课文，任意主题都可以。",
        ),
        (
            SOURCE_CUSTOM,
            "自备文本",
            "把你自己的日语文本粘贴进来，生成注音、翻译和朗读音频。",
        ),
    ]


_offline = OfflineGenerator()


def _generate_custom(request: GenerationRequest, settings: Settings) -> Lesson:
    sentences = split_into_sentences(request.custom_text)
    if not sentences:
        raise GenerationError("请先粘贴一段日语文本。")

    warnings: List[str] = []
    translations = ["" for _ in sentences]
    if request.translate:
        if settings.allow_online:
            translator = Translator(
                timeout=settings.request_timeout,
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
                enabled=True,
            )
            translations, warning = translator.translate(sentences)
            if warning:
                warnings.append(warning)
        else:
            warnings.append("自备文本的中文翻译需要联网；已在设置中关闭联网，故只生成注音与音频。")

    return build_lesson(
        list(zip(sentences, translations)),
        level=request.level,
        topic=request.topic_query or "custom",
        title_ja="自作テキスト",
        title_zh="自备文本",
        source=SOURCE_CUSTOM,
        source_label="自备文本",
        warnings=warnings,
    )


def generate_lesson(
    request: GenerationRequest,
    settings: Settings,
    progress: ProgressFn = None,
) -> Lesson:
    """Produce a lesson, falling back to the offline corpus when needed."""

    def report(message: str) -> None:
        if progress:
            progress(message)

    source = request.source or SOURCE_OFFLINE

    if source == SOURCE_CUSTOM:
        report("正在处理自备文本…")
        return _generate_custom(request, settings)

    if source == SOURCE_OFFLINE:
        report("正在从内置语料库组织课文…")
        return _offline.generate(request)

    if not settings.allow_online:
        report("联网功能已关闭，改用离线语料库…")
        lesson = _offline.generate(request)
        lesson.warnings.insert(
            0, "联网功能未开启（设置 → 允许联网），本次使用了内置离线语料。"
        )
        return lesson

    if source == SOURCE_LLM:
        report("正在用 Claude API 生成课文…")
        try:
            return LLMGenerator(settings).generate(request)
        except (LLMError, NetworkError) as exc:
            report("AI 生成失败，改用离线语料库…")
            lesson = _offline.generate(request)
            lesson.warnings.insert(0, f"AI 生成未成功，已改用离线语料：{exc}")
            return lesson

    report("正在联网搜索合适的日语文章…")
    try:
        return OnlineGenerator(settings).generate(request)
    except (OnlineError, NetworkError) as exc:
        report("联网搜索失败，改用离线语料库…")
        lesson = _offline.generate(request)
        lesson.warnings.insert(0, f"联网搜索未成功，已改用内置离线语料：{exc}")
        return lesson
