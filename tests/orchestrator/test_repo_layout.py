"""P9.25 — 하위 폴더 repo 감지: 프로젝트 파일이 최상위 폴더 하나에만 있으면 그 폴더 이름.

라이브(2026-09-21, melpes/RhythmTasker): 코드가 ``RhythmTasker/`` 안에 있어 플래너는 루트 경로를,
워커는 루트에서 ``pytest``를 썼다. 지원 전까지는 Plan 전에 멈추고 이유를 남긴다.
"""

from __future__ import annotations

from pathlib import Path

from control_plane.orchestrator.repo_layout import nested_project_root


def make(root: Path, files: list[str]) -> Path:
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    return root


def test_live_rhythmtasker_layout(tmp_path: Path) -> None:
    make(
        tmp_path,
        [
            "RhythmTasker/requirements.txt",
            "RhythmTasker/main.py",
            "RhythmTasker/src/core/scheduler.py",
            "RhythmTasker/tests/test_scheduler.py",
            ".gitignore",
        ],
    )
    assert nested_project_root(tmp_path) == "RhythmTasker"


def test_readme_only_subfolder_project(tmp_path: Path) -> None:
    make(tmp_path, ["README.md", "LICENSE", "web/package.json", "web/index.js", "docs/a.md"])
    assert nested_project_root(tmp_path) == "web"


def test_root_code_is_not_nested(tmp_path: Path) -> None:
    """fullstack Test2: 루트 app.py + frontend/ — 막지 않는다."""
    make(tmp_path, ["app.py", "tests/test_api.py", "frontend/package.json", "frontend/app.js"])
    assert nested_project_root(tmp_path) is None


def test_root_manifest_or_src_is_not_nested(tmp_path: Path) -> None:
    make(tmp_path, ["pyproject.toml", "pkg/mod.py"])
    assert nested_project_root(tmp_path) is None
    other = make(tmp_path / "b", ["src/app/main.py", "README.md"])
    assert nested_project_root(other) is None


def test_two_candidate_folders_are_ambiguous(tmp_path: Path) -> None:
    make(tmp_path, ["README.md", "backend/requirements.txt", "frontend/package.json"])
    assert nested_project_root(tmp_path) is None


def test_empty_or_readme_only_repo_is_not_nested(tmp_path: Path) -> None:
    """README만 있는 새 repo(콘솔 1단계) — 감지 대상 아님."""
    make(tmp_path, ["README.md"])
    assert nested_project_root(tmp_path) is None
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    assert nested_project_root(tmp_path) is None


def test_code_in_other_top_level_folder_is_not_nested(tmp_path: Path) -> None:
    """P9.27 라이브(hjunhuh/homebrew-cask): ``cmd/``에 .rb 하나, 코드는 ``Casks/a/*.rb`` 깊이에."""
    make(
        tmp_path,
        [
            "README.md",
            ".rubocop.yml",
            "Casks/a/alfred.rb",
            "Casks/b/brave.rb",
            "cmd/find-appcast.rb",
            "developer/bin/generate_cask_token",
            "doc/cask_language_reference.md",
        ],
    )
    assert nested_project_root(tmp_path) is None


def test_non_code_sibling_folder_keeps_detection(tmp_path: Path) -> None:
    """코드 없는 형제 폴더(assets/)는 판단을 바꾸지 않는다."""
    make(tmp_path, ["README.md", "assets/logo.png", "app/main.py", "app/requirements.txt"])
    assert nested_project_root(tmp_path) == "app"
