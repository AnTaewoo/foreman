"""P9.24 — 환경 실패: 이번 run이 안 건드린 파일이 repo에 없는 외부 모듈을 import해 테스트가 깨지면
편집으로는 못 고친다 → 재시도 없이 ``task.blocked{environment}``.

라이브(2026-09-21, melpes/RhythmTasker Issue #2): 기존 테스트 5개가 ``hypothesis``를 import —
워커에 없어 수집 에러, 편집 3회 × run 3회를 모두 같은 이유로 실패했다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.coding import missing_environment_modules
from agents.llm.fake import FakeProvider
from tests.agents.conftest import Spy, git
from tests.agents.test_coding import make_agent, make_input, script

LEGACY = (
    "from not_installed_pkg_xyz import given\n\n\ndef test_legacy() -> None:\n    assert given\n"
)

LIVE_TAIL = """\
_________ ERROR collecting RhythmTasker/tests/test_task_properties.py __________
ImportError while importing test module '/tmp/work/R/repo/RhythmTasker/tests/test_x.py'.
Traceback:
/usr/local/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
RhythmTasker/tests/test_task_properties.py:7: in <module>
    from hypothesis import given, strategies as st, settings
E   ModuleNotFoundError: No module named 'hypothesis'
_________________ ERROR collecting tests/test_rhythm_model.py __________________
Traceback:
/usr/local/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_rhythm_model.py:7: in <module>
    from RhythmTasker.rhythm_model import RhythmModel
E   ModuleNotFoundError: No module named 'RhythmTasker.rhythm_model'
"""


def test_missing_environment_modules_live_output(tmp_path: Path) -> None:
    (tmp_path / "RhythmTasker" / "tests").mkdir(parents=True)
    found = missing_environment_modules(LIVE_TAIL, tmp_path, touched=["tests/test_rhythm_model.py"])
    # hypothesis: repo에 없음 + 안 건드린 파일 → 환경. RhythmTasker.*: repo 안 모듈 → 에이전트 몫
    assert found == {"hypothesis": ["RhythmTasker/tests/test_task_properties.py"]}


def test_missing_module_in_touched_file_is_not_environment(tmp_path: Path) -> None:
    """에이전트가 새로 쓴 파일의 import는 다음 편집에서 고칠 수 있다 → 환경 아님."""
    out = (
        "tests/test_new.py:1: in <module>\n    import numpy\n"
        "E   ModuleNotFoundError: No module named 'numpy'\n"
    )
    assert missing_environment_modules(out, tmp_path, touched=["tests/test_new.py"]) == {}


async def test_environment_failure_blocks_without_retry(worktree: Path, spy: Spy) -> None:
    (worktree / "tests" / "test_legacy.py").write_text(LEGACY)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "legacy test with a missing dependency")
    agent = make_agent("pass", spy, worktree)
    provider = agent.provider
    assert isinstance(provider, FakeProvider)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "blocked", out
    edit_calls = [c for c in provider.calls if "=== FILE:" in c.messages[-1].content]
    assert len(edit_calls) == 1  # 첫 테스트 뒤 바로 멈춘다 (편집 재시도 없음)
    blocked = next(e for e in spy.events if e.type.value == "task.blocked")
    assert blocked.payload["reason"] == "environment"
    assert blocked.payload["run_id"] == "01RUN"
    assert blocked.payload["modules"] == {"not_installed_pkg_xyz": ["tests/test_legacy.py"]}
    assert "not_installed_pkg_xyz" in blocked.payload["test_output"]
    assert "task.failed" not in spy.types()  # Scheduler 재시도 대상 아님
    assert spy.events[-1].payload["outcome"] == "escalated"


async def test_own_missing_import_still_retries(worktree: Path, spy: Spy, tmp_path: Path) -> None:
    """에이전트 자신의 테스트가 없는 모듈을 import하면 기존대로 편집 재시도 → task.failed."""
    steps: list[Any] = script("pass")
    bad = json.loads(json.dumps(steps[1]))
    bad["files"][1]["content"] = (
        "import not_installed_pkg_xyz\n\n\ndef test_x() -> None:\n    pass\n"
    )
    agent = make_agent("pass", spy, worktree)
    agent.provider = FakeProvider(script=[steps[0], bad, bad, bad, steps[2]])
    out = await agent.run(make_input(worktree))
    assert out.outcome == "failed"
    assert "task.blocked" not in spy.types()
    failed = next(e for e in spy.events if e.type.value == "task.failed")
    assert failed.payload["reason"] == "tests_failed" and failed.payload["edit_rounds"] == 3
