"""Regression checks for hidden upstream failures and non-streaming AI requests."""
import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import data, scientist
from pipeline import analysis_service
from pipeline.ai_response import AIResponseError, chat_content


def response(body=None, *, status=200, text=None, content_type=None):
    request = httpx.Request("POST", "https://provider.invalid/chat/completions")
    kwargs = {"request": request}
    if text is None:
        kwargs["json"] = body
    else:
        kwargs["text"] = text
    if content_type:
        kwargs["headers"] = {"content-type": content_type}
    return httpx.Response(status, **kwargs)


def completion(content, **choice_fields):
    return response({"choices": [{"message": {"content": content}, **choice_fields}]})


@pytest.mark.parametrize("upstream, expected", [
    (response(text="data: {\"secret\":\"must-not-appear\"}", content_type="text/event-stream"), "流式"),
    (response(text="<html>must-not-appear</html>", content_type="text/html"), "不是合法 JSON"),
    (response({"choices": []}), "choices"),
    (response({"choices": None}), "choices"),
    (response({"error": {"message": "must-not-appear"}}), "choices"),
    (response({"choices": [{"message": {}}]}), "message.content"),
    (completion(" "), "空的正文"),
    (completion("", finish_reason="length"), "长度上限"),
])
def test_bad_upstream_response_is_specific_and_does_not_echo_body(upstream, expected):
    with pytest.raises(AIResponseError, match=expected) as error:
        chat_content(upstream)
    assert "must-not-appear" not in str(error.value)


def test_nonstreaming_content_is_preserved():
    assert chat_content(completion('{"summary":"source-grounded"}')) == '{"summary":"source-grounded"}'


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.sent = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.sent = kwargs["json"]
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.asyncio
async def test_summary_reports_auth_failure_instead_of_empty_success(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "synthetic-key")
    client = FakeClient(response({"error": {"message": "must-not-appear"}}, status=401))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(AIResponseError, match="HTTP 401") as error:
        await analysis_service._summarize_batch([{"title": "paper", "text": "evidence"}], 1)
    assert client.sent["stream"] is False
    assert "synthetic-key" not in str(error.value)
    assert "must-not-appear" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("content, expected", [("not json", "批摘要不是合法 JSON"), ('{"ok":true}', "summary")])
async def test_summary_distinguishes_model_json_from_http_envelope(monkeypatch, content, expected):
    monkeypatch.setenv("AI_API_KEY", "synthetic-key")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient(completion(content)))
    with pytest.raises(AIResponseError, match=expected):
        await analysis_service._summarize_batch([{"title": "paper", "text": "evidence"}], 1)


@pytest.mark.asyncio
async def test_idea_request_is_nonstreaming(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "synthetic-key")
    client = FakeClient(completion('{"ideas":[]}'))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    assert await scientist._call_llm({"instruction": "return JSON"}) == '{"ideas":[]}'
    assert client.sent["stream"] is False


def test_analysis_api_exposes_ai_failure_as_502(monkeypatch):
    async def failed(*args, **kwargs):
        raise AIResponseError("AI 批摘要请求失败（HTTP 401）")
    monkeypatch.setattr(data, "analyze_file", failed)
    with TestClient(app) as client:
        result = client.post("/api/data/analyze", json={"path": "arxiv/example.jsonl"})
    assert result.status_code == 502
    assert "HTTP 401" in result.json()["detail"]
