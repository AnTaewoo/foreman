"""P5.4 — e2e dry-run 스크립트 (red a~c). ``--fake``면 LLM은 Fake, GitHub은 Dry, remote는 tmp bare.

스크립트는 패키지가 아니라서 파일 경로로 import 한다. 진짜 Redis(DB 14)가 필요하다(D-32).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
def e2e() -> Any:
    spec = importlib.util.spec_from_file_location(
        "e2e_dry_run", ROOT / "scripts" / "e2e_dry_run.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_dry_run"] = mod  # dataclass 주석(PEP 563) 해석에 필요
    spec.loader.exec_module(mod)
    return mod


SECTIONS = [
    "=== 1. RepoSummary ===",
    "=== 2. Plan ===",
    "(auto-approve)",
    "=== 3. TaskDrafts ===",
    "would create issue",
    "=== 4. Coding Agent 결과 ===",
    "=== 5. bare remote 브랜치 ===",
    "=== 6. 토큰 합계 ===",
]


def positions(out: str, markers: list[str]) -> list[int]:
    pos = [out.find(m) for m in markers]
    assert all(p >= 0 for p in pos), [m for m, p in zip(markers, pos, strict=True) if p < 0]
    return pos


# (a) --fake: 네트워크 0으로 전체 흐름 완주 (b) 출력 섹션 순서
async def test_fake_full_flow(e2e: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary = await e2e.run(
        ["--fake", "--workdir", str(tmp_path), str(SAMPLE), "Add a users API with tests"]
    )
    out = capsys.readouterr().out
    assert summary.exit_code == 0, out[-2000:]
    assert summary.provider == "fake" and summary.dry_run is True
    pos = positions(out, SECTIONS)
    assert pos == sorted(pos), "섹션 순서"
    assert len(re.findall(r"would create issue", out)) == summary.task_count == 2
    assert summary.issue_count == 2
    assert [c["outcome"] for c in summary.coding] == ["done", "done"]
    assert len(summary.branches) == 2 and all(b.startswith("ai/") for b in summary.branches)
    for b in summary.branches:
        assert b in out
    assert summary.tokens["calls"] == 2 + 3 * 2  # orchestrator 2 + Task당 3
    assert summary.tokens["tokens_in"] > 0 and summary.tokens["tokens_out"] > 0
    assert "Understanding" in out  # Plan 마크다운 6섹션 중 하나


# (c) --no-coding: Plan + Issue까지만
async def test_no_coding(e2e: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary = await e2e.run(
        ["--fake", "--no-coding", "--workdir", str(tmp_path), str(SAMPLE), "Add a users API"]
    )
    out = capsys.readouterr().out
    assert summary.exit_code == 0
    assert summary.issue_count == 2 and summary.coding == [] and summary.branches == []
    positions(out, SECTIONS[:5] + [SECTIONS[-1]])
    assert "=== 4. Coding Agent 결과 ===" not in out and "=== 5. bare remote 브랜치 ===" not in out
    assert summary.tokens["calls"] == 2
