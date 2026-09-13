"""LLM 추상화 (설계 §1.3-4, §14). Agent 로직은 이 인터페이스만 본다 — provider SDK는 어댑터 안에만.

``ModelProvider.complete(messages, *, system, schema, model, max_tokens) -> Completion``.
``schema``(pydantic 모델)를 주면 구조화 출력을 강제하고 ``Completion.parsed``에 인스턴스를 넣는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 4096


class ProviderError(Exception):
    """provider 호출 실패의 공통 베이스."""


class ProviderRefusal(ProviderError):
    """모델이 안전 이유로 거절 (stop_reason=refusal)."""


class ProviderConfigError(ProviderError):
    """자격 증명·설정 부족."""


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class Completion(BaseModel):
    """한 번의 호출 결과. ``parsed``는 schema를 준 경우의 인스턴스(파싱 실패면 None)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    text: str
    parsed: BaseModel | None
    tokens_in: int
    tokens_out: int
    model: str


@runtime_checkable
class ModelProvider(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion: ...


def estimate_tokens(text: str) -> int:
    """대략 4자 = 1토큰. 컨텍스트 예산 계산용 (§5.3), 정확도는 필요 없다."""
    return len(text) // 4
