"""P6.6 — RepoCache (D-38, red a~c, f): 로컬 경로는 그대로, owner/name은 root/owner/name에 clone·fetch."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from control_plane.config import Settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GENV = {
    "GIT_AUTHOR_NAME": "s",
    "GIT_AUTHOR_EMAIL": "s@x",
    "GIT_COMMITTER_NAME": "s",
    "GIT_COMMITTER_EMAIL": "s@x",
    "PATH": "/usr/bin:/bin",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GENV
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns("dot_git_stub", ".venv", "node_modules"),
    )
    git(seed, "init", "-q", "-b", "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    return remote


# (a) 존재하는 로컬 경로 → 그 경로, clone 없음
def test_local_path_passthrough(tmp_path: Path) -> None:
    from control_plane.repo_cache import RepoCache

    local = tmp_path / "repo"
    local.mkdir()
    cache = RepoCache(tmp_path / "root")
    assert cache.ensure(str(local)) == local.resolve()
    assert not (tmp_path / "root").exists()


# (b) owner/name → root/owner/name에 git clone <url_for(repo)>  (c) 두 번째는 fetch
def test_clone_then_fetch(tmp_path: Path, remote: Path) -> None:
    from control_plane.repo_cache import RepoCache

    cache = RepoCache(tmp_path / "root", url_for=lambda repo: str(remote), git_env=GENV)
    path = cache.ensure("org/demo")
    assert path == (tmp_path / "root" / "org" / "demo").resolve() and (path / ".git").is_dir()
    assert "main" in git(path, "branch", "--list")
    assert cache.last_action == "clone"
    # remote에 커밋 추가 → ensure는 fetch만 (재clone 없음) → origin/main이 새 커밋
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "NEW.md").write_text("x")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "second")
    git(work, "push", "-q", "origin", "main")
    again = cache.ensure("org/demo")
    assert again == path and cache.last_action == "fetch"
    assert "second" in git(path, "log", "--format=%s", "origin/main", "-n", "1")


# (b-2) 기본 url_for = https://github.com/<owner>/<name>.git
def test_default_url() -> None:
    from control_plane.repo_cache import RepoCache

    assert RepoCache(Path("/x")).url_for("org/demo") == "https://github.com/org/demo.git"


# (e) clone 실패 → RepoUnavailable
def test_clone_failure_raises(tmp_path: Path) -> None:
    from control_plane.repo_cache import RepoCache, RepoUnavailable

    cache = RepoCache(
        tmp_path / "root", url_for=lambda repo: str(tmp_path / "nope.git"), git_env=GENV
    )
    with pytest.raises(RepoUnavailable, match="org/none"):
        cache.ensure("org/none")


# (f) Settings.repo_root 기본 ./repos
def test_settings_repo_root() -> None:
    assert Settings(_env_file=None).repo_root == "./repos"
