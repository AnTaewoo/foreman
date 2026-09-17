"""프로파일 라우터 (D-57): Goal마다 고른 LLM 프로파일로 호출을 넘긴다.

Orchestrator 그래프는 provider 하나를 붙들고 있으므로, 실행 중인 Goal의 프로파일을
contextvar에 두고 호출 시점에 그 프로파일의 provider로 위임한다.
각 Goal은 자기 asyncio Task 안에서 돌아 contextvar가 섞이지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel

from agents.llm.base import DEFAULT_MAX_TOKENS, Completion, Message, ModelProvider

CURRENT_PROFILE: ContextVar[str | None] = ContextVar("foreman_llm_profile", default=None)


class ProfileRouter(ModelProvider):
    def __init__(self, settings: Any, default: str | None = None) -> None:  # Any: Settings-like
        from agents.llm import default_profile, get_provider

        self._settings = settings
        self._default = default or default_profile(settings)
        self._get = get_provider
        self._cache: dict[str, ModelProvider] = {}

    def provider_for(self, profile: str | None) -> ModelProvider:
        name = profile or CURRENT_PROFILE.get() or self._default
        if name not in self._cache:
            self._cache[name] = self._get(self._settings, profile=name)
        return self._cache[name]

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        # model 인자는 무시한다 — 프로파일이 모델을 정한다
        return await self.provider_for(None).complete(
            messages, system=system, schema=schema, max_tokens=max_tokens
        )
