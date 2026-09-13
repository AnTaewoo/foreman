"""Anthropic 어댑터 — provider SDK를 import하는 유일한 곳 (ruff banned-api, D-23).

SDK 사용법은 claude-api 스킬(2026-09-13)에서 확인:
- anthropic 1.x는 httpx2 기반. 테스트는 ``DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h))`` 주입 (D-21).
- 구조화 출력은 ``messages.parse(output_format=Model)`` → ``output_config.format.type == "json_schema"``.
  tool_use 강제(``tool_choice: any/tool``)는 최신 모델에서 400이라 쓰지 않는다.
- Opus 5는 thinking이 기본 adaptive — ``thinking`` 인자는 생략. prefill 없음.
- ``stop_reason == "refusal"`` → ``ProviderRefusal``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from anthropic.types import MessageParam
from pydantic import BaseModel

from agents.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    Completion,
    Message,
    ProviderRefusal,
)

TransportHandler = Callable[[httpx2.Request], httpx2.Response]


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        transport_handler: TransportHandler | None = None,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        http_client = None
        if transport_handler is not None:
            http_client = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(transport_handler))
        self._client = AsyncAnthropic(
            api_key=api_key, http_client=http_client, max_retries=max_retries
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        params: list[MessageParam] = [{"role": m.role, "content": m.content} for m in messages]
        use_model = model or self._model
        if schema is None:
            res = await self._client.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                messages=params,
                **({"system": system} if system is not None else {}),
            )
            if res.stop_reason == "refusal":
                raise ProviderRefusal(f"{use_model} refused")
            text = "".join(b.text for b in res.content if b.type == "text")
            return Completion(
                text=text, parsed=None, tokens_in=res.usage.input_tokens,
                tokens_out=res.usage.output_tokens, model=res.model,
            )  # fmt: skip

        parsed_res = await self._client.messages.parse(
            model=use_model,
            max_tokens=max_tokens,
            messages=params,
            output_format=schema,
            **({"system": system} if system is not None else {}),
        )
        if parsed_res.stop_reason == "refusal":
            raise ProviderRefusal(f"{use_model} refused")
        text = "".join(b.text for b in parsed_res.content if b.type == "text")
        parsed = parsed_res.parsed_output
        return Completion(
            text=text, parsed=parsed if isinstance(parsed, BaseModel) else None,
            tokens_in=parsed_res.usage.input_tokens, tokens_out=parsed_res.usage.output_tokens,
            model=parsed_res.model,
        )  # fmt: skip
