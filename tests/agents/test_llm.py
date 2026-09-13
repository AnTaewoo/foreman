"""P3.1 — agents/llm (red a~g): ModelProvider, Fake/Anthropic provider, get_provider."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

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
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }  # fmt: skip


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
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )  # fmt: skip
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
