"""플랫폼 설정. 환경변수 `HITL_*` / `.env`에서 읽는다. `DRY_RUN` 기본값은 항상 True."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Control Plane 설정. 비밀값은 `SecretStr`이라 repr/dump에 노출되지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="HITL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 안전장치 ---
    dry_run: bool = Field(default=True, description="True면 실제 GitHub API를 호출하지 않는다")

    # --- 인프라 ---
    database_url: str = "postgresql+asyncpg://hitl:hitl@localhost:5432/hitl"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM (D-33: provider 선택) ---
    # anthropic = 최종 판정(PC-5) / openai_compat = 로컬 Ollama 등 OpenAI 호환 / fake = 테스트
    llm_provider: Literal["anthropic", "openai_compat", "fake"] = "anthropic"
    llm_base_url: str = "http://localhost:11434/v1"  # openai_compat일 때
    llm_model: str = "qwen2.5-coder:7b"  # openai_compat일 때
    llm_api_key: SecretStr = SecretStr("ollama")  # Ollama는 아무 값이나 받는다
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5"

    # --- GitHub App ---
    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_installation_id: int | None = None
    github_webhook_secret: SecretStr = SecretStr("")

    # --- 로그/아티팩트 저장소 (MinIO, S3 호환) ---
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: SecretStr = SecretStr("minioadmin")
    minio_bucket: str = "hitl-runs"

    # --- 로깅 ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 읽는 Settings 싱글턴."""
    return Settings()
