"""Optional Claude-powered generator.

Only used when the learner pastes an Anthropic API key into Settings.  It is
the one back-end that can write a brand-new passage about *any* topic at
*exactly* the requested level, so it is worth supporting - but the app is
fully usable without it.
"""

from __future__ import annotations

import json
import re
from typing import List, Tuple

from ..config import Settings
from ..levels import get_level
from ..models import GenerationRequest, Lesson, target_seconds, target_sentence_count
from ..net import NetworkError, post_json
from ..topics import RANDOM_TOPIC, TOPICS, get_topic
from .builder import build_lesson

ENDPOINT = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


def _topic_description(request: GenerationRequest) -> str:
    if request.topic_query:
        return request.topic_query
    topic = get_topic(request.topic)
    if topic:
        return f"{topic.name_ja}（{topic.name_zh}）"
    return "日常生活"


def build_prompt(request: GenerationRequest, count: int, seconds: int) -> str:
    level = get_level(request.level)
    minutes = seconds / 60.0
    return (
        f"You are writing a Japanese listening-practice talk for a JLPT {level.code} "
        f"learner whose first language is Chinese.\n\n"
        f"Topic: {_topic_description(request)}\n"
        f"Level: {level.code} ({level.label_en}) - {level.style_hint}\n"
        f"Length: about {count} sentences, so that reading it aloud at a natural "
        f"pace takes roughly {minutes:.0f} minute(s).\n\n"
        "Requirements:\n"
        "- Natural, spoken-style Japanese that sounds good when read aloud.\n"
        "- Write developed paragraphs, not a list of disconnected one-line facts. "
        "Sentences should be full and substantial, using subordinate clauses and "
        "connectives so ideas link together into a continuous argument or story.\n"
        "- The talk needs a clear structure: an opening, two or three developed "
        "sections that build on each other, and a closing line.\n"
        f"- Keep vocabulary and grammar within JLPT {level.code}. At {level.code}, "
        "long sentences are fine as long as the grammar stays at that level.\n"
        "- Provide a natural Simplified Chinese translation for every sentence.\n"
        "- Do not use bullet points, headings, romaji or furigana.\n\n"
        "Return ONLY a JSON object with this exact shape:\n"
        '{"title_ja": "...", "title_zh": "...", '
        '"sentences": [{"ja": "...", "zh": "..."}]}'
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start: end + 1]
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LLMError(f"模型返回的内容不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("模型返回的内容格式不正确。")
    return data


class LLMGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.anthropic_api_key.strip())

    def generate(self, request: GenerationRequest) -> Lesson:
        if not self.available:
            raise LLMError("尚未设置 Anthropic API Key，无法使用 AI 生成模式。")

        count = target_sentence_count(request.length, request.level)
        prompt = build_prompt(request, count, target_seconds(request.length))
        try:
            data = post_json(
                ENDPOINT,
                {
                    "model": self.settings.anthropic_model,
                    "max_tokens": 8000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=max(30, self.settings.request_timeout),
                headers={
                    "x-api-key": self.settings.anthropic_api_key.strip(),
                    "anthropic-version": "2023-06-01",
                },
            )
        except NetworkError as exc:
            raise LLMError(f"调用 Claude API 失败：{exc}") from exc

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if not text.strip():
            raise LLMError("Claude API 没有返回内容。")

        payload = _extract_json(text)
        raw_sentences = payload.get("sentences") or []
        pairs: List[Tuple[str, str]] = []
        for item in raw_sentences:
            if not isinstance(item, dict):
                continue
            ja = (item.get("ja") or "").strip()
            if ja:
                pairs.append((ja, (item.get("zh") or "").strip()))
        if not pairs:
            raise LLMError("模型没有生成任何日语句子。")

        topic_id = request.topic
        if topic_id == RANDOM_TOPIC:
            topic_id = request.topic_query or "ai"
        topic = TOPICS.get(topic_id)

        return build_lesson(
            pairs,
            level=request.level,
            topic=topic_id,
            title_ja=(payload.get("title_ja") or "").strip(),
            title_zh=(payload.get("title_zh") or "").strip()
            or (topic.name_zh if topic else request.topic_query),
            source="llm",
            source_label=f"Claude API（{self.settings.anthropic_model}）",
        )
