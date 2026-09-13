"""P0.1 — 골격·툴체인·Settings·import 가드 검증 (ROADMAP §7 P0.1 red)."""

from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parent.parent

PACKAGES = [
    "control_plane",
    "control_plane.api",
    "control_plane.orchestrator",
    "control_plane.scheduler",
    "control_plane.events",
    "control_plane.store",
    "agents",
    "agents.tools",
    "agents.llm",
    "github_adapter",
    "worker",
]


# (a) §15.1 패키지 전부 import 가능 + 한 줄 docstring
@pytest.mark.parametrize("name", PACKAGES)
def test_package_importable_with_one_line_docstring(name: str) -> None:
    module = importlib.import_module(name)
    doc = module.__doc__
    assert doc, f"{name}: docstring 없음"
    assert len(doc.strip().splitlines()) == 1, f"{name}: docstring은 한 줄이어야 함"


# (b) Settings — dry_run 기본 True, HITL_ 프리픽스, SecretStr
def test_settings_dry_run_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.config import Settings

    monkeypatch.delenv("HITL_DRY_RUN", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert Settings(_env_file=None).dry_run is True


def test_settings_uses_hitl_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.config import Settings

    assert Settings.model_config.get("env_prefix") == "HITL_"
    monkeypatch.setenv("HITL_DRY_RUN", "false")
    assert Settings(_env_file=None).dry_run is False
    # 프리픽스 없는 변수는 무시된다
    monkeypatch.delenv("HITL_DRY_RUN")
    monkeypatch.setenv("DRY_RUN", "false")
    assert Settings(_env_file=None).dry_run is True


def test_settings_secrets_are_secretstr_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.config import Settings

    marker = "sk-ant-should-not-leak"
    monkeypatch.setenv("HITL_ANTHROPIC_API_KEY", marker)
    monkeypatch.setenv("HITL_GITHUB_APP_PRIVATE_KEY", marker)
    monkeypatch.setenv("HITL_GITHUB_WEBHOOK_SECRET", marker)
    s = Settings(_env_file=None)
    for field in ("anthropic_api_key", "github_app_private_key", "github_webhook_secret"):
        value = getattr(s, field)
        assert isinstance(value, SecretStr), f"{field}는 SecretStr이어야 함"
        assert value.get_secret_value() == marker
    assert marker not in repr(s)
    assert marker not in str(s)
    assert marker not in s.model_dump_json()


# (c) AST: agents/, worker/ 아래 control_plane.config import 없음 (D-23)
def _py_files(*dirs: str) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        files.extend(p for p in (ROOT / d).rglob("*.py") if ".venv" not in p.parts)
    return files


def _imports_control_plane_config(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.startswith("control_plane.config") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("control_plane.config"):
                return True
            if mod == "control_plane" and any(a.name == "config" for a in node.names):
                return True
    return False


def test_agents_and_worker_do_not_import_control_plane_config() -> None:
    files = _py_files("agents", "worker")
    assert files, "agents/, worker/ 에 .py 파일이 없음"
    offenders = [str(p.relative_to(ROOT)) for p in files if _imports_control_plane_config(p)]
    assert offenders == [], f"control_plane.config import 금지: {offenders}"


# (d) ruff banned-api / per-file-ignores
def _pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_ruff_bans_anthropic_sdk_except_adapter() -> None:
    lint = _pyproject()["tool"]["ruff"]["lint"]  # type: ignore[index]
    banned = lint["flake8-tidy-imports"]["banned-api"]
    assert "anthropic" in banned
    assert "TID251" in lint["per-file-ignores"]["agents/llm/anthropic.py"]
    assert "TID" in lint["select"] or "TID251" in lint["select"]


# (e) .ai-platform 샘플
def test_ai_platform_samples_exist() -> None:
    autonomy = ROOT / ".ai-platform" / "autonomy.yaml"
    context = ROOT / ".ai-platform" / "CONTEXT.md"
    assert autonomy.is_file()
    assert context.is_file()
    data = yaml.safe_load(autonomy.read_text(encoding="utf-8"))
    assert data["version"] == 1
    for key in ("autonomy", "approval_required", "budget", "concurrency", "protected_paths"):
        assert key in data, f"autonomy.yaml에 {key} 없음 (설계 §8.3)"
    assert context.read_text(encoding="utf-8").strip()
