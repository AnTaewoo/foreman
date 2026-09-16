"""P0.3 — 개발 인프라 검증: .env.example, compose, /health, structlog (ROADMAP §7 P0.3 red)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "=" in line, f".env.example 형식 오류: {line!r}"
        keys.add(line.split("=", 1)[0].strip())
    return keys


# (a) .env.example 키 집합 == Settings 필드 집합
def test_env_example_matches_settings_fields() -> None:
    from control_plane.config import Settings

    expected = {f"HITL_{name.upper()}" for name in Settings.model_fields}
    actual = _env_example_keys()
    assert actual == expected, (
        f"missing in .env.example: {sorted(expected - actual)}; "
        f"extra in .env.example: {sorted(actual - expected)}"
    )


def test_env_example_values_are_empty() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            _, _, value = line.partition("=")
            assert value == "", f"값은 비워둔다: {line!r}"


# (b) docker-compose.yml: postgres / redis / minio, healthcheck, named volume
def _compose() -> dict[str, object]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", ["postgres", "redis"])
def test_compose_service_has_healthcheck_and_named_volume(service: str) -> None:
    compose = _compose()
    services = compose["services"]  # type: ignore[index]
    assert service in services
    svc = services[service]
    assert "healthcheck" in svc, f"{service}: healthcheck 없음"
    volumes = compose.get("volumes") or {}
    mounts = svc.get("volumes") or []
    named = [m.split(":", 1)[0] for m in mounts if not m.startswith((".", "/"))]
    assert named, f"{service}: named volume 마운트 없음"
    for name in named:
        assert name in volumes, f"{service}: 최상위 volumes에 {name} 없음"


def test_compose_host_ports_are_overridable() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for var in ("POSTGRES_HOST_PORT", "REDIS_HOST_PORT", "MINIO_HOST_PORT"):
        assert f"${{{var}:-" in text, f"{var} 덮어쓰기 불가"


# (c) GET /health → 200 {"status": "ok"}
def test_health_endpoint() -> None:
    from control_plane.api.app import create_app

    with TestClient(create_app()) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# (d) configure_logging(): json / console 렌더러
def _last_processor_name() -> str:
    procs = structlog.get_config()["processors"]
    return type(procs[-1]).__name__


def test_configure_logging_json_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from control_plane.config import Settings
    from control_plane.logging import configure_logging

    monkeypatch.setenv("HITL_LOG_FORMAT", "json")
    configure_logging(Settings(_env_file=None))
    assert _last_processor_name() == "JSONRenderer"
    structlog.get_logger("test").info("hello", gh_event="ping")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["event"] == "hello"
    assert record["gh_event"] == "ping"
    assert record["level"] == "info"


def test_configure_logging_console_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.config import Settings
    from control_plane.logging import configure_logging

    monkeypatch.setenv("HITL_LOG_FORMAT", "console")
    configure_logging(Settings(_env_file=None))
    assert _last_processor_name() == "ConsoleRenderer"


def test_configure_logging_sets_stdlib_level(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.config import Settings
    from control_plane.logging import configure_logging

    monkeypatch.setenv("HITL_LOG_LEVEL", "WARNING")
    configure_logging(Settings(_env_file=None))
    assert logging.getLogger().level == logging.WARNING


# P8.6 (외부 점검 #1): `cp .env.example .env` 그대로(빈 값)여도 Settings가 기본값으로 뜬다
def test_env_example_empty_values_fall_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control_plane.config import Settings

    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    for key in _env_example_keys():
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(env_file))
    assert s.dry_run is True and s.llm_provider == "anthropic" and s.repo_root == "./repos"
    assert s.scheduler_max_workers == 4 and s.llm_price_in_per_mtok == 0.0
    monkeypatch.setenv("HITL_SCHEDULER_MAX_WORKERS", "")  # 빈 환경변수도 기본값
    assert Settings(_env_file=str(env_file)).scheduler_max_workers == 4


# P8.6: 사용자 설정에서 mock 값 제거 — llm_provider에 "fake" 없음, MinIO 설정 없음(profile)
def test_no_mock_settings() -> None:
    from control_plane.config import Settings

    assert "fake" not in str(Settings.model_fields["llm_provider"].annotation)
    assert not any(name.startswith("minio_") for name in Settings.model_fields)
    compose = _compose()
    services = compose["services"]  # type: ignore[index]
    assert "profiles" in services["minio"] and "minio" not in ("postgres", "redis")  # type: ignore[index]
