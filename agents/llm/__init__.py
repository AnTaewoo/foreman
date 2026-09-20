"""LLM 추상화: ModelProvider 인터페이스와 어댑터. ``get_provider``가 설정으로 고른다 (D-33)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import SecretStr

from agents.llm.anthropic import AnthropicProvider
from agents.llm.base import DEFAULT_MODEL, Message, ModelProvider, ProviderConfigError
from agents.llm.fake import FakeProvider
from agents.llm.ollama import OllamaCompatProvider

__all__ = [
    "ProviderSettings",
    "available_profiles",
    "default_profile",
    "get_provider",
    "profile_env",
]


class ProviderSettings(Protocol):
    """구조적 타입 — Settings 클래스를 import하지 않고 필요한 필드만 본다 (D-23)."""

    anthropic_api_key: SecretStr


PROFILES = ("ollama", "openai", "anthropic")


def _secret(v: object) -> str:
    return v.get_secret_value() if hasattr(v, "get_secret_value") else str(v or "")


def profile_env(settings: object, profile: str) -> dict[str, Any]:
    """프로파일 → provider·base_url·model·api_key·token_param·max_tokens (D-57).

    키 없음 = 빈 문자열. ``token_param``/``max_tokens``는 OpenAI 프로브 진단 #1·#3:
    gpt-5.x는 ``max_completion_tokens``만 받고 추론 토큰이 예산을 먹는다.
    """
    if profile == "ollama":
        return {
            "provider": "openai_compat",
            "base_url": str(getattr(settings, "llm_base_url", "http://localhost:11434/v1")),
            "model": str(getattr(settings, "llm_model", "qwen2.5-coder:7b")),
            "api_key": _secret(getattr(settings, "llm_api_key", "ollama")) or "ollama",
            "token_param": "max_tokens",
            "max_tokens": None,
        }
    if profile == "openai":
        return {
            "provider": "openai_compat",
            "base_url": str(getattr(settings, "openai_base_url", "https://api.openai.com/v1")),
            "model": str(getattr(settings, "openai_model", "gpt-5.6")),
            "api_key": _secret(getattr(settings, "openai_api_key", "")),
            "token_param": "max_completion_tokens",
            "max_tokens": int(getattr(settings, "openai_max_tokens", 16384) or 16384),
        }
    if profile == "anthropic":
        return {
            "provider": "anthropic",
            "base_url": "",
            "model": str(getattr(settings, "anthropic_model", DEFAULT_MODEL) or DEFAULT_MODEL),
            "api_key": _secret(getattr(settings, "anthropic_api_key", "")),
            "token_param": "max_tokens",
            "max_tokens": None,
        }
    raise ProviderConfigError(f"unknown llm profile {profile!r} (ollama|openai|anthropic)")


def available_profiles(settings: object) -> list[dict[str, object]]:
    """콘솔이 보여줄 목록. api_key는 절대 포함하지 않는다."""
    out: list[dict[str, object]] = []
    for name in PROFILES:
        env = profile_env(settings, name)
        available = name == "ollama" or bool(env["api_key"])
        out.append(
            {
                "name": name,
                "provider": env["provider"],
                "model": env["model"],
                "available": available,
            }
        )
    return out


def default_profile(settings: object) -> str:
    """콘솔의 기본 선택이자 Goal이 ``llm``을 생략했을 때 쓰는 프로파일.

    P9.15(사용자 결정 2026-09-20): openai 키가 있으면 ``openai``.
    ``llm_default_profile``(``HITL_LLM_DEFAULT_PROFILE``)로 고정할 수 있고,
    쓸 수 없는 값(키 없는 프로파일·오타)이면 무시한다.
    키가 없으면 기존 규칙(D-33 ``llm_provider``): openai_compat → ollama, anthropic → anthropic.
    """
    usable = {str(p["name"]) for p in available_profiles(settings) if p["available"]}
    forced = str(getattr(settings, "llm_default_profile", "") or "")
    if forced in usable:
        return forced
    if "openai" in usable:
        return "openai"
    kind = str(getattr(settings, "llm_provider", "anthropic") or "anthropic")
    if kind == "anthropic" and "anthropic" not in usable:
        return "ollama"  # 키 없는 anthropic 기본값은 고를 수 없으니 항상 가능한 ollama로
    return "ollama" if kind == "openai_compat" else "anthropic"


def get_provider(
    settings: ProviderSettings, *, fake: bool = False, profile: str | None = None
) -> ModelProvider:
    """``fake=True``면 FakeProvider(테스트 주입 전용; 설정값이 아니다).

    ``llm_provider``(기본 anthropic): anthropic → 키 필수(없으면 ProviderConfigError);
    openai_compat → ``llm_base_url``/``llm_model``/``llm_api_key``로 OllamaCompatProvider (D-33).
    """
    kind = str(getattr(settings, "llm_provider", "anthropic") or "anthropic")
    if fake:
        return FakeProvider(script=[])
    if profile is not None:  # D-57
        env = profile_env(settings, profile)
        if env["provider"] == "openai_compat":
            return OllamaCompatProvider(
                base_url=env["base_url"],
                model=env["model"],
                api_key=env["api_key"] or "ollama",
                token_param=env["token_param"],
                max_tokens=env["max_tokens"],
            )
        if not env["api_key"]:
            raise ProviderConfigError(f"llm profile {profile!r}: anthropic API key is empty")
        return AnthropicProvider(env["api_key"], model=env["model"])
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


_PROBED: set[str] = set()  # 프로세스 안에서 성공한 프로파일 (진단 #4)


def reset_probe_cache() -> None:
    _PROBED.clear()


async def probe_profile(
    settings: Any,  # Any: Settings 또는 테스트의 SimpleNamespace
    profile: str,
    *,
    factory: Callable[..., ModelProvider] | None = None,
) -> None:
    """원격 프로파일을 Goal 생성 전에 1콜 검증한다 (진단 #4: 파라미터 오류가 Goal을 죽이지 않게).

    ollama는 프로브하지 않는다(로컬, 모델 로드 비용). 성공은 프로파일당 한 번만 기록한다.
    실패는 ProviderError 그대로 올린다 — 호출자가 400으로 바꾼다.
    """
    if profile == "ollama" or profile in _PROBED:
        return
    make = factory or get_provider
    provider = make(settings, profile=profile)
    await provider.complete([Message(role="user", content="Reply with OK.")], max_tokens=16)
    _PROBED.add(profile)
