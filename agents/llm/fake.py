"""테스트용 provider: 미리 정한 스크립트를 순서대로 돌려준다. 네트워크 없음."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.llm.base import DEFAULT_MAX_TOKENS, Completion, Message

ScriptItem = str | dict[str, Any] | BaseModel  # Any: JSON 형태의 dict


class ScriptExhausted(Exception):
    """스크립트 항목보다 호출이 많다."""


@dataclass(frozen=True)
class Call:
    messages: tuple[Message, ...]
    system: str | None
    schema: type[BaseModel] | None
    model: str | None
    max_tokens: int


@dataclass
class FakeProvider:
    script: list[ScriptItem] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    model_name: str = "fake"

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        self.calls.append(Call(tuple(messages), system, schema, model, max_tokens))
        if not self.script:
            raise ScriptExhausted(f"no scripted response for call #{len(self.calls)}")
        item = self.script.pop(0)
        text, parsed = _render(item, schema)
        tokens_in = sum(len(m.content) for m in messages) // 4
        return Completion(
            text=text,
            parsed=parsed,
            tokens_in=tokens_in,
            tokens_out=len(text) // 4,
            model=model or self.model_name,
        )


def _render(item: ScriptItem, schema: type[BaseModel] | None) -> tuple[str, BaseModel | None]:
    if isinstance(item, BaseModel):
        text = item.model_dump_json()
    elif isinstance(item, dict):
        text = json.dumps(item, ensure_ascii=False)
    else:
        text = item
    if schema is None:
        return text, None
    try:
        return text, schema.model_validate_json(text)
    except ValidationError:
        return text, None
