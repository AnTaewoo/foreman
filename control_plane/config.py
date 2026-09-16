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
        env_ignore_empty=True,  # P8.6: `cp .env.example .env`의 빈 값은 기본값 (외부 점검 #1)
        extra="ignore",
    )

    # --- 안전장치 ---
    dry_run: bool = Field(default=True, description="True면 실제 GitHub API를 호출하지 않는다")

    # --- 인프라 ---
    database_url: str = "postgresql+asyncpg://hitl:hitl@localhost:5432/hitl"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM (D-33: provider 선택) ---
    # anthropic / openai_compat(로컬 Ollama 등 OpenAI 호환). 테스트 더블은 코드에서 주입한다(P8.6)
    llm_provider: Literal["anthropic", "openai_compat"] = "anthropic"
    llm_base_url: str = "http://localhost:11434/v1"  # openai_compat일 때
    llm_model: str = "qwen2.5-coder:7b"  # openai_compat일 때
    llm_api_key: SecretStr = SecretStr("ollama")  # Ollama는 아무 값이나 받는다
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5"
    # D-39: 비용 = 토큰 × 단가(USD per 1M tokens). 기본 0 → cost_usd 0. 단가표를 코드에 박지 않는다
    llm_price_in_per_mtok: float = 0.0
    llm_price_out_per_mtok: float = 0.0

    # --- GitHub App ---
    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_installation_id: int | None = None
    github_webhook_secret: SecretStr = SecretStr("")

    # --- 상주 control plane (P6.1) ---
    # docker = DockerCliLauncher(D-15) / inprocess = 이 프로세스 안에서 CodingAgent(개발용)
    worker_launcher: Literal["docker", "inprocess"] = "docker"
    worker_image: str = "foreman-worker:dev"
    scheduler_max_workers: int = 4
    repo_root: str = (
        "./repos"  # D-38: owner/name → repo_root/<owner>/<name> clone (docker 런처는 마운트)
    )

    # --- 공개 데모 (P9.3, D-52) ---
    # True면 POST /projects·cancel·task patch는 X-Admin-Token, Goal 생성은 프로젝트·시간·IP 한도
    demo_mode: bool = False
    admin_token: SecretStr = SecretStr("")  # 비어 있으면 데모 모드의 관리 라우트는 항상 401
    demo_user_id: str = "judge"  # 콘솔이 보내는 X-User-Id (데모 프로젝트 members의 approver)
    demo_max_running_goals: int = 1  # 프로젝트당 진행 중(draft|planning|active) Goal
    demo_goals_per_hour: int = 6  # 프로젝트당 시간당 생성
    demo_post_per_ip_per_min: int = 10  # IP당 쓰기 요청/분

    # --- 로깅 ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 읽는 Settings 싱글턴."""
    return Settings()
