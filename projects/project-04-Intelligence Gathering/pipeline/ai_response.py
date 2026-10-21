"""Validate non-streaming chat responses without exposing provider bodies or keys."""
from __future__ import annotations

import httpx


class AIResponseError(ValueError):
    """A safe, user-visible upstream AI response error."""


def chat_content(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "未提供").split(";", 1)[0]
    if content_type == "text/event-stream" or response.text.lstrip().startswith("data:"):
        raise AIResponseError("AI 接口返回了流式响应，当前调用需要非流式 JSON 响应。")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AIResponseError(
            f"AI 接口返回的不是合法 JSON（HTTP {response.status_code}，Content-Type: {content_type}）。"
        ) from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIResponseError("AI 接口 JSON 缺少有效的 choices 列表，请核对服务的 Chat Completions 兼容性。")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise AIResponseError("AI 接口 JSON 缺少 message.content 文本，请核对模型响应格式。")
    content = message["content"]
    if not content.strip():
        if choices[0].get("finish_reason") == "length":
            raise AIResponseError("模型在返回正文前达到生成长度上限，请检查模型与生成参数。")
        raise AIResponseError("AI 接口返回了空的正文，请检查模型设置或重试。")
    return content
