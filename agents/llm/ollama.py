"""OpenAI 호환 엔드포인트 provider — 로컬 Ollama 등 (D-33). ``POST {base_url}/chat/completions``.

- httpx 직접 호출(provider SDK 없음). 테스트는 ``transport_handler``로 ``httpx.MockTransport`` 주입.
- ``schema``가 있으면 ``response_format`` json_schema를 보내고, 응답은 코드펜스·앞뒤 잡음을
  걷어낸 뒤 pydantic으로 파싱한다. 실패하면 ``parsed=None`` — 호출자(drafts)의 재시도 경로가 처리.
- thinking 모델은 ``reasoning``에 추론을 넣고 content를 비우기도 하므로 비-thinking 모델 권장.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from agents.llm.base import DEFAULT_MAX_TOKENS, Completion, Message, ProviderError, extract_json

TransportHandler = Callable[[httpx.Request], httpx.Response]


class OllamaCompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "ollama",
        transport_handler: TransportHandler | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._model = model
        self.model = model  # D-57: 프로파일 확인용 (읽기 전용)
        self.base_url = base_url.rstrip("/")
        transport = httpx.MockTransport(transport_handler) if transport_handler else None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
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
        use_model = model or self._model
        chat: list[dict[str, str]] = []
        if system is not None:
            chat.append({"role": "system", "content": system})
        chat += [{"role": m.role, "content": m.content} for m in messages]
        body: dict[str, Any] = {"model": use_model, "messages": chat, "max_tokens": max_tokens}
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        try:
            res = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai_compat request failed: {exc}") from exc
        if res.status_code >= 400:
            raise ProviderError(f"openai_compat HTTP {res.status_code}: {res.text[:300]}")
        data = res.json()
        choice = (data.get("choices") or [{}])[0]
        text = str((choice.get("message") or {}).get("content") or "")
        usage = data.get("usage") or {}
        parsed: BaseModel | None = None
        if schema is not None and text:
            try:
                parsed = schema.model_validate_json(extract_json(text))
            except (ValidationError, ValueError):
                parsed = None
        return Completion(
            text=text,
            parsed=parsed,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model") or use_model),
        )
