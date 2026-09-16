"""repo 확보 (D-38, 리뷰 A8): ``project.repo``를 control plane의 로컬 경로로.

- 존재하는 로컬 경로 → 그대로 (clone 없음; 개발·픽스처).
- ``owner/name`` 또는 URL → ``root/<owner>/<name>``에 ``git clone``(있으면 ``fetch``). Orchestrator
  분석 경로와 Scheduler ``LaunchSpec.repo_url``이 같은 경로. docker 런처는 root를 컨테이너에 마운트.
- clone 인증(App 토큰)은 X.1 — 지금은 공개 URL 또는 로컬 bare remote(테스트의 ``url_for``)만.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

GIT_ENV_DEFAULT: dict[str, str] = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_TERMINAL_PROMPT": "0",
}


_TOKEN_RE = re.compile(r"(x-access-token:)[^@\s]+@")


def mask_token(text: str) -> str:
    """URL 안의 installation 토큰을 ``***``로 (로그·예외용)."""
    return _TOKEN_RE.sub(r"\1***@", text)


def token_url(repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git"


class RepoUnavailable(Exception):
    def __init__(self, repo: str, detail: str) -> None:
        super().__init__(f"{repo}: {detail}")
        self.repo = repo
        self.detail = detail


def _is_url(repo: str) -> bool:
    return repo.startswith(("http://", "https://", "git@", "ssh://"))


class RepoCache:
    def __init__(
        self,
        root: Path,
        *,
        url_for: Callable[[str], str] | None = None,
        git_env: Mapping[str, str] | None = None,
        token_getter: Callable[[], str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.root = Path(root)
        self.dry_run = dry_run  # D-48: Dry 모드는 원격 clone을 하지 않는다 (F-1)
        self._url_for = url_for
        self._token_getter = token_getter  # D-41: 실 모드면 installation 토큰으로 clone/fetch
        self._env = dict(git_env or GIT_ENV_DEFAULT)
        self.last_action: str | None = None  # passthrough | clone | fetch

    def url_for(self, repo: str) -> str:
        if self._url_for is not None:
            return self._url_for(repo)
        if _is_url(repo):
            return repo
        if self._token_getter is not None:
            return token_url(repo, self._token_getter())
        return f"https://github.com/{repo}.git"

    def target_for(self, repo: str) -> Path:
        name = repo
        if _is_url(repo):
            parts = repo.rstrip("/").split("/")
            name = "/".join(parts[-2:])
        return self.root / name.removesuffix(".git")

    def ensure(self, repo: str) -> Path:
        """로컬 경로 그대로 / clone / fetch 후 경로. 실패면 ``RepoUnavailable``."""
        local = Path(repo)
        if not _is_url(repo) and local.exists():
            self.last_action = "passthrough"
            return local.resolve()
        if not _is_url(repo) and (local.is_absolute() or repo.count("/") != 1):
            raise RepoUnavailable(repo, "local path does not exist (expected owner/name or URL)")
        if self.dry_run:
            raise RepoUnavailable(repo, "dry-run mode never clones remote repos — use a local path")
        if self._token_getter is None and self._url_for is None:
            raise RepoUnavailable(
                repo, "no installation token — real mode needs GitHub App settings"
            )
        target = self.target_for(repo)
        if (target / ".git").is_dir():
            try:
                self._git(
                    target,
                    "fetch",
                    "-q",
                    self.url_for(repo),
                    "+refs/heads/*:refs/remotes/origin/*",
                )
            except subprocess.CalledProcessError as exc:  # 오프라인이면 기존 clone으로 계속
                log.warning(
                    "repo_cache.fetch_failed", repo=repo, error=mask_token(exc.stderr[-300:])
                )
            self.last_action = "fetch"
            return target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git(target.parent, "clone", "-q", self.url_for(repo), str(target))
        except (subprocess.CalledProcessError, OSError) as exc:
            detail = mask_token(str(getattr(exc, "stderr", "") or exc))
            log.error("repo_cache.clone_failed", repo=repo, error=detail[-300:])
            raise RepoUnavailable(repo, f"clone failed: {detail.strip()[-200:]}") from exc
        self.last_action = "clone"
        log.info("repo_cache.cloned", repo=repo, path=str(target))
        return target.resolve()

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=self._env
        ).stdout
