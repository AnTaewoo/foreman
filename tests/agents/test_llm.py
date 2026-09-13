"""P3.1 — agents/llm (red a~g): ModelProvider, Fake/Anthropic provider, get_provider."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from agents.llm import get_provider
from agents.llm.anthropic import AnthropicProvider
from agents.llm.base import (
    Completion,
    Message,
    ModelProvider,
    ProviderConfigError,
    ProviderRefusal,
    estimate_tokens,
)
from agents.llm.fake import FakeProvider, ScriptExhausted

ROOT = Path(__file__).resolve().parent.parent.parent


class Plan(BaseModel):
    title: str
    steps: list[str]


def msgs(text: str = "hi") -> list[Message]:
    return [Message(role="user", content=text)]


# (a)(b) 인터페이스
def test_completion_shape() -> None:
    c = Completion(text="x", parsed=None, tokens_in=1, tokens_out=2, model="m")
    assert (c.text, c.parsed, c.tokens_in, c.tokens_out, c.model) == ("x", None, 1, 2, "m")


def test_fake_is_model_provider(fake_provider: FakeProvider) -> None:
    assert isinstance(fake_provider, ModelProvider)
    assert estimate_tokens("a" * 40) == 10


# (c) FakeProvider
async def test_fake_script_order_and_calls(fake_provider: FakeProvider) -> None:
    fake_provider.script.extend(["first", "second"])
    a = await fake_provider.complete(msgs("q1"), system="sys")
    b = await fake_provider.complete(msgs("q2"), model="m2", max_tokens=99)
    assert (a.text, b.text) == ("first", "second")
    assert a.parsed is None and a.model == "fake"
    assert b.model == "m2"
    assert len(fake_provider.calls) == 2
    assert (
        fake_provider.calls[0].system == "sys"
        and fake_provider.calls[0].messages[0].content == "q1"
    )
    assert fake_provider.calls[1].max_tokens == 99
    with pytest.raises(ScriptExhausted):
        await fake_provider.complete(msgs("q3"))


async def test_fake_schema_parsing() -> None:
    p = FakeProvider(
        script=[
            '{"title": "t", "steps": ["a"]}',  # str JSON
            {"title": "t2", "steps": []},  # dict
            Plan(title="t3", steps=["x", "y"]),  # 모델
            "not json",  # 파싱 실패 → parsed None
        ]
    )
    for expected in (["a"], [], ["x", "y"]):
        c = await p.complete(msgs(), schema=Plan)
        assert isinstance(c.parsed, Plan) and c.parsed.steps == expected
        assert json.loads(c.text)["title"].startswith("t")
    bad = await p.complete(msgs(), schema=Plan)
    assert bad.parsed is None and bad.text == "not json"
    assert p.calls[0].schema is Plan


# (d) AnthropicProvider — httpx2.MockTransport로 /v1/messages 가로채기
def _message_response(
    text: str, *, stop_reason: str = "end_turn", model: str = "claude-opus-5"
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


class Recorder:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._responses = list(responses)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-test"
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=self._responses.pop(0))


async def test_anthropic_plain_text() -> None:
    rec = Recorder([_message_response("hello")])
    provider = AnthropicProvider(api_key="sk-test", transport_handler=rec)
    c = await provider.complete(msgs("hi"), system="be brief", max_tokens=50)
    assert c.text == "hello" and c.parsed is None
    assert (c.tokens_in, c.tokens_out, c.model) == (12, 7, "claude-opus-5")
    body = rec.requests[0]
    assert body["model"] == "claude-opus-5" and body["max_tokens"] == 50
    assert body["system"] == "be brief"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "thinking" not in body and "tools" not in body and "output_config" not in body


async def test_anthropic_structured_output_uses_output_config_not_tools() -> None:
    rec = Recorder([_message_response('{"title": "t", "steps": ["a", "b"]}')])
    provider = AnthropicProvider(api_key="sk-test", transport_handler=rec, model="claude-sonnet-5")
    c = await provider.complete(msgs("plan"), schema=Plan)
    assert isinstance(c.parsed, Plan) and c.parsed.steps == ["a", "b"]
    assert c.model == "claude-opus-5"  # 응답의 model
    body = rec.requests[0]
    assert body["model"] == "claude-sonnet-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "tools" not in body and "tool_choice" not in body
    assert "title" in json.dumps(body["output_config"]["format"]["schema"])


async def test_anthropic_refusal_raises() -> None:
    rec = Recorder([_message_response("", stop_reason="refusal")])
    provider = AnthropicProvider(api_key="sk-test", transport_handler=rec)
    with pytest.raises(ProviderRefusal):
        await provider.complete(msgs("x"))
    rec2 = Recorder([_message_response("", stop_reason="refusal")])
    provider2 = AnthropicProvider(api_key="sk-test", transport_handler=rec2)
    with pytest.raises(ProviderRefusal):
        await provider2.complete(msgs("x"), schema=Plan)


async def test_anthropic_model_override_per_call() -> None:
    rec = Recorder([_message_response("ok")])
    provider = AnthropicProvider(api_key="sk-test", transport_handler=rec)
    await provider.complete(msgs(), model="claude-haiku-4-5")
    assert rec.requests[0]["model"] == "claude-haiku-4-5"


# (e) AST: anthropic import는 agents/llm/anthropic.py에서만
def test_anthropic_sdk_imported_only_in_adapter() -> None:
    offenders: list[str] = []
    for p in list(ROOT.rglob("*.py")):
        if ".venv" in p.parts or "tests" in p.parts or p.name == "anthropic.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(n == "anthropic" or n.startswith("anthropic.") for n in names):
                offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], offenders


# (f) fake_provider 픽스처
def test_fake_provider_fixture(fake_provider: FakeProvider) -> None:
    assert fake_provider.script == [] and fake_provider.calls == []


# (g) get_provider — 구조적 Protocol, control_plane.config 미참조 (D-23)
class _Cfg:
    def __init__(self, key: str, model: str = "claude-opus-5") -> None:
        self.anthropic_api_key = SecretStr(key)
        self.anthropic_model = model


def test_get_provider() -> None:
    assert isinstance(get_provider(_Cfg("sk-x")), AnthropicProvider)
    assert isinstance(get_provider(_Cfg(""), fake=True), FakeProvider)
    assert isinstance(get_provider(_Cfg("sk-x"), fake=True), FakeProvider)  # fake 우선
    with pytest.raises(ProviderConfigError):
        get_provider(_Cfg(""))


def test_agents_llm_does_not_import_control_plane_config() -> None:
    for p in (ROOT / "agents" / "llm").rglob("*.py"):
        assert "control_plane.config" not in p.read_text(encoding="utf-8"), p


# ---------------------------------------------------------------- D-33: OpenAI 호환 provider
from agents.llm.ollama import OllamaCompatProvider  # noqa: E402


def _chat_response(content: str, *, finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "qwen2.5-coder:7b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30},
    }


class ChatRecorder:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.requests: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self._responses.pop(0))


async def test_ollama_plain_text_uses_base_url() -> None:
    rec = ChatRecorder([_chat_response("pong")])
    p = OllamaCompatProvider(
        base_url="http://ollama.local:11434/v1", model="qwen2.5-coder:7b", transport_handler=rec
    )
    c = await p.complete(msgs("ping"), system="be terse", max_tokens=20)
    assert c.text == "pong" and c.parsed is None and (c.tokens_in, c.tokens_out) == (21, 9)
    assert c.model == "qwen2.5-coder:7b"
    assert rec.urls[0] == "http://ollama.local:11434/v1/chat/completions"
    body = rec.requests[0]
    assert body["model"] == "qwen2.5-coder:7b" and body["max_tokens"] == 20
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "ping"},
    ]
    assert "response_format" not in body


async def test_ollama_schema_sends_json_schema_and_parses_fenced_json() -> None:
    rec = ChatRecorder([_chat_response('```json\n{"title": "t", "steps": ["a"]}\n```')])
    p = OllamaCompatProvider(base_url="http://x/v1", model="m", transport_handler=rec)
    c = await p.complete(msgs("plan"), schema=Plan)
    assert isinstance(c.parsed, Plan) and c.parsed.steps == ["a"]
    fmt = rec.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["name"] == "Plan"
    assert "title" in json.dumps(fmt["json_schema"]["schema"])


async def test_ollama_invalid_json_enters_retry_path() -> None:
    """잘못된 JSON → parsed None → decompose_with_retry가 재요청(2회차)으로 들어간다."""
    from control_plane.orchestrator.drafts import DecomposeResult, decompose_with_retry

    good = {
        "epics": [{"title": "E", "order": 1, "summary": ""}],
        "tasks": [
            {
                "title": "A",
                "spec": "s",
                "kind": "feature",
                "role_required": "coding",
                "depends_on": [],
                "owned_paths": ["src/a.py"],
                "estimated_tier": "T1",
                "epic": "E",
            }
        ],
    }
    rec = ChatRecorder(
        [_chat_response("Sure! Here is the plan: {oops"), _chat_response(json.dumps(good))]
    )
    p = OllamaCompatProvider(base_url="http://x/v1", model="m", transport_handler=rec)
    first = await p.complete(msgs("x"), schema=DecomposeResult)
    assert first.parsed is None and "oops" in first.text
    rec2 = ChatRecorder(
        [_chat_response("Sure! Here is the plan: {oops"), _chat_response(json.dumps(good))]
    )
    p2 = OllamaCompatProvider(base_url="http://x/v1", model="m", transport_handler=rec2)
    out = await decompose_with_retry(p2, repo_summary="R", plan="P", goal="G")
    assert out.tasks[0].title == "A" and len(rec2.requests) == 2
    assert any("rejected" in m["content"].lower() for m in rec2.requests[1]["messages"])


async def test_ollama_http_error_raises_provider_error() -> None:
    from agents.llm.base import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "model 'm' not found"}})

    p = OllamaCompatProvider(base_url="http://x/v1", model="m", transport_handler=handler)
    with pytest.raises(ProviderError, match="not found"):
        await p.complete(msgs("x"))


class _Cfg2:
    def __init__(self, provider: str, key: str = "") -> None:
        self.anthropic_api_key = SecretStr(key)
        self.anthropic_model = "claude-opus-5"
        self.llm_provider = provider
        self.llm_base_url = "http://localhost:11434/v1"
        self.llm_model = "qwen2.5-coder:7b"
        self.llm_api_key = SecretStr("ollama")


def test_get_provider_selects_by_llm_provider() -> None:
    assert isinstance(get_provider(_Cfg2("openai_compat")), OllamaCompatProvider)
    assert isinstance(get_provider(_Cfg2("fake")), FakeProvider)
    assert isinstance(get_provider(_Cfg2("anthropic", "sk-x")), AnthropicProvider)
    with pytest.raises(ProviderConfigError, match="anthropic"):
        get_provider(_Cfg2("anthropic"))
    with pytest.raises(ProviderConfigError, match="llm_provider"):
        get_provider(_Cfg2("bogus"))


def test_settings_has_llm_provider_fields() -> None:
    from control_plane.config import Settings

    s = Settings(_env_file=None)
    assert s.llm_provider == "anthropic" and s.llm_base_url.endswith("/v1")
    assert s.llm_model and s.llm_api_key.get_secret_value()


# P6.5 (D-39): 비용 = 토큰 × 설정 단가 (USD per 1M tokens). 코드에 단가표를 박지 않는다
def test_estimate_cost() -> None:
    from agents.llm.pricing import Prices, estimate_cost

    assert estimate_cost(0, 0, Prices()) == 0.0
    assert estimate_cost(1_000_000, 0, Prices(in_per_mtok=3.0, out_per_mtok=15.0)) == 3.0
    assert estimate_cost(1000, 500, Prices(3.0, 15.0)) == 0.0105
    assert estimate_cost(123_456, 7_890, Prices(0.8, 4.0)) == round(0.0987648 + 0.03156, 6)
    assert Prices().is_set is False and Prices(1.0, 0.0).is_set is True
    assert Prices.from_env(
        {"WORKER_LLM_PRICE_IN_PER_MTOK": "3", "WORKER_LLM_PRICE_OUT_PER_MTOK": "15"}
    ) == Prices(3.0, 15.0)
    assert Prices.from_env({}) == Prices()
