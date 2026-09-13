"""Dry 자동 머지 (D-36, 리뷰 A7): ``dry_run=true``면 ``pr.opened`` → ``pr.merged`` (사람 머지 흉내).

- 상태 머신·§6.1·``pick_ready``는 그대로: projection의 ``pr.merged`` 핸들러가 in_review → done (또는
  ``pr_merged_at`` 플래그, D-30 b)을 옮긴다. 실 모드에서는 ``enabled=False``로 아무것도 하지 않는다.
- 멱등: 프로세스 내 ``(project_id, pr_number)`` memo와 ``tasks.pr_merged_at`` 둘 다 확인. 같은
  ``pr.opened``가 미서명(워커 XADD)·서명본(relay) 두 번 와도 1건. 새어 나가도 projection이 전이 없이
  흡수한다.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.session import get_session

log = structlog.get_logger(__name__)

DRY_MERGE_ACTOR = Actor(type="system", id="dry-merge")
OnMerge = Callable[[str, int, str], None]  # (project_id, pr_number, task_id)
RepoPathFor = Callable[[str], Path]
GIT_ENV: dict[str, str] = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "foreman-dry-merge",
    "GIT_AUTHOR_EMAIL": "dry-merge@foreman.local",
    "GIT_COMMITTER_NAME": "foreman-dry-merge",
    "GIT_COMMITTER_EMAIL": "dry-merge@foreman.local",
}


def git_merge_branch(repo: Path, head: str, base: str, env: Mapping[str, str] = GIT_ENV) -> str:
    """``head``를 ``base``에 반영: fast-forward | merge | conflict | skipped.

    PC-6 발견: pr.merged 이벤트만 흉내 내면 후속 Task가 선행 코드 없는 main에서 시작한다. 사람
    머지가 GitHub에서 main을 옮기듯 Dry에서는 여기서 옮긴다. ff가 안 되면 임시 worktree에서 머지.
    """

    def run(*args: str, cwd: Path = repo) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, env=dict(env), check=False
        )

    if run("rev-parse", "--git-dir").returncode != 0:
        return "skipped"  # git repo가 아님(FakeLauncher 등) — 이벤트만 흉내
    head_sha = run("rev-parse", "--verify", f"refs/heads/{head}").stdout.strip()
    if not head_sha:
        return "skipped"  # 워커가 push한 브랜치가 없다 — 이벤트만
    if run("merge-base", "--is-ancestor", base, head).returncode == 0:
        r = run("update-ref", f"refs/heads/{base}", head_sha)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return "fast-forward"
    with tempfile.TemporaryDirectory(prefix="dry-merge-") as tmp:
        wt = Path(tmp) / "wt"
        r = run("worktree", "add", "--detach", str(wt), base)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        try:
            m = run(
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"dry-merge: {head} into {base}",
                head,
                cwd=wt,
            )
            if m.returncode != 0:
                run("merge", "--abort", cwd=wt)
                return "conflict"
            sha = run("rev-parse", "HEAD", cwd=wt).stdout.strip()
            r = run("update-ref", f"refs/heads/{base}", sha)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())
            return "merge"
        finally:
            run("worktree", "remove", "--force", str(wt))


class DryMerger:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        *,
        enabled: bool,
        on_merge: OnMerge | None = None,
        repo_path_for: RepoPathFor | None = None,
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._enabled = enabled
        self._on_merge = on_merge
        self._repo_path_for = repo_path_for  # None이면 이벤트만 (git main은 안 옮김 — 단위 테스트)
        self.merged: list[tuple[str, int]] = []
        self.git_merges: list[tuple[str, int, str]] = []  # (project_id, pr, 결과)
        self._seen: set[tuple[str, int]] = set()

    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        if not self._enabled or event.type is not EventType.PR_OPENED:
            return
        pr_number = int(event.payload.get("pr_number") or event.subject.id)
        task_id = str(event.payload.get("task_id") or "")
        key = (event.project_id, pr_number)
        if key in self._seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, task_id) if task_id else None
            project = await s.get(m.Project, event.project_id)
        if task is not None and task.pr_merged_at is not None:
            self._seen.add(key)
            return
        self._seen.add(key)
        if self._repo_path_for is not None and project is not None:
            head = str(event.payload.get("head") or "")
            base = str(event.payload.get("base") or project.default_branch)
            try:
                result = await asyncio.to_thread(
                    git_merge_branch, self._repo_path_for(project.repo_full_name), head, base
                )
            except Exception as exc:
                log.error("dry_merge.git_failed", pr_number=pr_number, error=str(exc)[-300:])
                result = "error"
            self.git_merges.append((event.project_id, pr_number, result))
            if result in ("conflict", "error"):
                log.warning("dry_merge.not_merged", pr_number=pr_number, result=result, head=head)
                return  # 사람이 풀어야 할 PR — in_review에 남긴다
        merged = Event(
            project_id=event.project_id,
            actor=DRY_MERGE_ACTOR,
            type=EventType.PR_MERGED,
            subject=Subject(entity="pr", id=str(pr_number)),
            payload={"task_id": task_id, "pr_number": pr_number, "merged_by": "dry-run"},
            correlation_id=event.correlation_id,
            causation_id=event.id,
        )
        async with get_session(self._factory) as s:
            await self._bus.publish(s, merged)
        self.merged.append(key)
        log.info(
            "dry_merge.merged", project_id=event.project_id, pr_number=pr_number, task_id=task_id
        )
        if self._on_merge is not None:
            self._on_merge(event.project_id, pr_number, task_id)
