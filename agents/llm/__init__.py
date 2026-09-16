"""LLM 추상화: ModelProvider 인터페이스와 어댑터. ``get_provider``가 설정으로 고른다 (D-33)."""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from agents.llm.anthropic import AnthropicProvider
from agents.llm.base import DEFAULT_MODEL, ModelProvider, ProviderConfigError
from agents.llm.fake import FakeProvider
from agents.llm.ollama import OllamaCompatProvider

__all__ = ["ProviderSettings", "get_provider"]


class ProviderSettings(Protocol):
    """구조적 타입 — Settings 클래스를 import하지 않고 필요한 필드만 본다 (D-23)."""

    anthropic_api_key: SecretStr


def get_provider(settings: ProviderSettings, *, fake: bool = False) -> ModelProvider:
    """``fake=True``면 FakeProvider(테스트 주입 전용; 설정값이 아니다).

    ``llm_provider``(기본 anthropic): anthropic → 키 필수(없으면 ProviderConfigError);
    openai_compat → ``llm_base_url``/``llm_model``/``llm_api_key``로 OllamaCompatProvider (D-33).
    """
    kind = str(getattr(settings, "llm_provider", "anthropic") or "anthropic")
    if fake:
        return FakeProvider(script=[])
    if kind == "openai_compat":
        api_key = getattr(settings, "llm_api_key", None)
        return OllamaCompatProvider(
            base_url=str(getattr(settings, "llm_base_url", "http://localhost:11434/v1")),
            model=str(getattr(settings, "llm_model", "qwen2.5-coder:7b")),
            api_key=api_key.get_secret_value() if api_key is not None else "ollama",
        )
    if kind != "anthropic":
        raise ProviderConfigError(f"unknown llm_provider {kind!r} (anthropic|openai_compat)")
    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ProviderConfigError(
            "llm_provider=anthropic but HITL_ANTHROPIC_API_KEY is empty; pass fake=True for tests"
        )
    model = str(getattr(settings, "anthropic_model", DEFAULT_MODEL) or DEFAULT_MODEL)
    return AnthropicProvider(key, model=model)
