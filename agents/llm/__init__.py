"""LLM 추상화: ModelProvider 인터페이스와 어댑터. ``get_provider``가 설정으로 고른다 (D-23)."""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from agents.llm.anthropic import AnthropicProvider
from agents.llm.base import DEFAULT_MODEL, ModelProvider, ProviderConfigError
from agents.llm.fake import FakeProvider

__all__ = ["ProviderSettings", "get_provider"]


class ProviderSettings(Protocol):
    """구조적 타입 — Settings 클래스를 import하지 않고 필요한 필드만 본다 (D-23)."""

    anthropic_api_key: SecretStr


def get_provider(settings: ProviderSettings, *, fake: bool = False) -> ModelProvider:
    """``fake=True``면 FakeProvider. 아니면 키가 있어야 Anthropic, 없으면 ProviderConfigError."""
    if fake:
        return FakeProvider(script=[])
    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ProviderConfigError("HITL_ANTHROPIC_API_KEY is empty; pass fake=True for tests")
    model = str(getattr(settings, "anthropic_model", DEFAULT_MODEL) or DEFAULT_MODEL)
    return AnthropicProvider(key, model=model)
