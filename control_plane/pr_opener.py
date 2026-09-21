"""PR 생성은 control plane (D-37, 리뷰 A4): task.completed{branch, summary} → open_pr → pr.opened.

- 워커는 push까지만 하고 GitHub 토큰을 받지 않는다(§12). Dry/실 client 선택은 여기서만.
- 멱등: 프로세스 내 ``(project_id, task_id)`` memo + ``tasks.pr_number`` + client의 head/base 멱등.
  같은 ``task.completed``가 미서명·서명본으로 두 번 와도 PR 1건, ``pr.opened`` 1건.
- Issue 요약 코멘트(key ``summary:<run>``)도 여기서.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.dry_merge import GIT_ENV
from control_plane.events.bus import Delivery, EventBus
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.repo_cache import mask_token, token_url
from control_plane.store import models as m
from control_plane.store.session import get_session
from github_adapter.protocol import GitHubClient, PrMeta

log = structlog.get_logger(__name__)

PR_OPENER_ACTOR = Actor(type="system", id="pr-opener")
RepoPathFor = Callable[[str], Path]


class Pusher(Protocol):
    def push(self, repo_path: Path, repo: str, branch: str) -> None: ...


class GitPusher:
    """D-41: 워커가 로컬 clone(RepoCache)에 push한 브랜치를 control plane이 GitHub로 push 한다.

    토큰 URL은 push 명령 인자로만 쓰고 remote 설정(.git/config)에 남기지 않는다. 오류는 마스킹.
    """

    def __init__(
        self,
        token_getter: Callable[[str], str],  # repo → 토큰 (P9.9 installation별)
        *,
        url_for: Callable[[str], str] | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> None:
        self._token_getter = token_getter
        self._url_for = url_for
        self._env = dict(git_env or GIT_ENV)

    def url_for(self, repo: str) -> str:
        return self._url_for(repo) if self._url_for else token_url(repo, self._token_getter(repo))

    def push(self, repo_path: Path, repo: str, branch: str) -> None:
        r = subprocess.run(
            ["git", "push", self.url_for(repo), f"refs/heads/{branch}:refs/heads/{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            env=self._env,
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git push failed: {mask_token(r.stderr.strip()[-300:])}")


class PrOpener:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        github: GitHubClient,
        *,
        agent_id: str = "coding-1",
        pusher: Pusher | None = None,
        repo_path_for: RepoPathFor | None = None,
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._github = github
        self._agent_id = agent_id
        self.pusher = pusher  # None = Dry(로컬 clone이 곧 origin) — D-41
        self._repo_path_for = repo_path_for
        self.opened: list[tuple[str, str]] = []
        self.push_failed: list[tuple[str, str]] = []
        self._seen: set[tuple[str, str]] = set()
        self._failed_seen: set[tuple[str, str, str]] = set()  # (project, task, run) P9 버그 #5

    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        if event.type is EventType.TASK_FAILED:
            await self._handle_failed(event)
            return
        if event.type is EventType.TASK_BLOCKED:
            await self._handle_blocked(event)
            return
        if event.type is not EventType.TASK_COMPLETED:
            return
        branch = event.payload.get("branch")
        if not branch:
            return  # D-37 이전 형식 또는 브랜치 없는 완료 — 할 일 없음
        key = (event.project_id, event.subject.id)
        if key in self._seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, event.subject.id)
            project = await s.get(m.Project, event.project_id)
        if task is None or project is None:
            log.warning(
                "pr_opener.missing_rows", task_id=event.subject.id, project_id=event.project_id
            )
            return
        self._seen.add(key)
        if self.pusher is not None and self._repo_path_for is not None:  # D-41: 실 모드 push
            try:
                await asyncio.to_thread(
                    self.pusher.push,
                    self._repo_path_for(project.repo_full_name),
                    project.repo_full_name,
                    str(branch),
                )
            except Exception as exc:
                log.error(
                    "pr_opener.push_failed", task_id=task.id, error=mask_token(str(exc))[-300:]
                )
                self.push_failed.append(key)
                self._seen.discard(key)  # 다음 전달(서명본)에서 재시도할 수 있게
                return
        run_id = str(event.payload.get("run_id") or "")
        summary = str(event.payload.get("summary") or "")
        title = f"[T-{task.issue_number or '?'}] {task.title}"
        meta = PrMeta(
            task_id=task.id, run_id=run_id, agent_id=self._agent_id, tier=task.risk_tier.value
        )
        pr = await self._github.open_pr(
            project.repo_full_name, str(branch), project.default_branch, title, summary, True, meta
        )
        if not pr.created and task.pr_number == pr.number:
            log.info("pr_opener.already_open", task_id=task.id, pr_number=pr.number)
            return
        if task.issue_number is not None:
            await self._github.comment(
                project.repo_full_name, task.issue_number, summary, key=f"summary:{run_id}"
            )
        opened = Event(
            project_id=event.project_id,
            actor=PR_OPENER_ACTOR,
            type=EventType.PR_OPENED,
            subject=Subject(entity="pr", id=str(pr.number)),
            payload={
                "task_id": task.id,
                "run_id": run_id,
                "pr_number": pr.number,
                "head": str(branch),
                "base": project.default_branch,
                "draft": True,
                "url": pr.url,
            },
            correlation_id=event.correlation_id,
            causation_id=event.id,
        )
        async with get_session(self._factory) as s:
            await self._bus.publish(s, opened)
        self.opened.append(key)
        log.info("pr_opener.opened", task_id=task.id, pr_number=pr.number, head=branch)

    async def _handle_blocked(self, event: Event) -> None:
        """3차 라이브 #2: needs_decision 안내 코멘트는 워커(Dry)가 아니라 control plane이 올린다."""
        payload = event.payload
        if payload.get("reason") == "environment":
            await self._handle_environment(event)
            return
        if payload.get("reason") != "needs_decision":
            return
        run_id = str(payload.get("run_id") or "")
        key = (event.project_id, event.subject.id, f"needs-decision:{run_id}")
        if key in self._failed_seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, event.subject.id)
            project = await s.get(m.Project, event.project_id)
        if task is None or project is None or task.issue_number is None:
            return
        self._failed_seen.add(key)
        files = ", ".join(f"`{f}`" for f in (payload.get("files") or []))
        body = (
            f"**승인 필요** — 이 Task는 의존성 파일을 바꾸려 합니다: {files}\n\n"
            "설계 §8.2에 따라 T2 결정입니다. `/approve` 또는 `/reject <이유>`로 답해 주세요. "
            "(MVP 1에서는 승인 뒤 재개가 아직 없으므로 Task를 다시 만들어야 합니다)"
        )
        await self._github.comment(
            project.repo_full_name, task.issue_number, body, key=f"needs-decision:{run_id}"
        )
        log.info("pr_opener.needs_decision_reported", task_id=task.id, run_id=run_id)

    async def _handle_environment(self, event: Event) -> None:
        """P9.24: repo가 워커에 없는 패키지를 import — 재시도로 못 고친다, 원인을 안내."""
        payload = event.payload
        run_id = str(payload.get("run_id") or "")
        key = (event.project_id, event.subject.id, f"environment:{run_id}")
        if key in self._failed_seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, event.subject.id)
            project = await s.get(m.Project, event.project_id)
        if task is None or project is None or task.issue_number is None:
            return
        self._failed_seen.add(key)
        modules = payload.get("modules") or {}
        lines = "\n".join(
            f"- `{mod}` ← " + ", ".join(f"`{f}`" for f in files) for mod, files in modules.items()
        )
        body = (
            "**실행 환경 문제로 멈췄습니다** — 이 repo의 기존 파일이 Foreman 워커에 설치되지 않은 "
            f"패키지를 import해서 테스트(`pytest -q`)를 실행할 수 없습니다.\n\n{lines}\n\n"
            "워커는 Python 3.12 + pytest·flask·httpx·pydantic·sqlalchemy만 갖고 있고, repo의 "
            "requirements·pyproject를 설치하지 않습니다. 코드를 고쳐서 해결되는 문제가 아니라 "
            "재시도하지 않았습니다. 위 패키지가 없어도 도는 repo에서 다시 시도해 주세요."
        )
        await self._github.comment(
            project.repo_full_name, task.issue_number, body, key=f"environment:{run_id}"
        )
        log.info("pr_opener.environment_reported", task_id=task.id, run_id=run_id)

    async def _handle_failed(self, event: Event) -> None:
        """P9 bug #5: push the failed attempt WIP branch and comment the test tail (no PR)."""
        payload = event.payload
        if payload.get("reason") != "tests_failed" or not payload.get("branch"):
            return
        run_id = str(payload.get("run_id") or "")
        key = (event.project_id, event.subject.id, run_id)
        if key in self._failed_seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, event.subject.id)
            project = await s.get(m.Project, event.project_id)
        if task is None or project is None:
            return
        self._failed_seen.add(key)
        branch = str(payload["branch"])
        pushed = "pushed"
        if self.pusher is not None and self._repo_path_for is not None:
            try:
                await asyncio.to_thread(
                    self.pusher.push,
                    self._repo_path_for(project.repo_full_name),
                    project.repo_full_name,
                    branch,
                )
            except Exception as exc:
                log.error(
                    "pr_opener.wip_push_failed", task_id=task.id, error=mask_token(str(exc))[-300:]
                )
                pushed = "not pushed"
        if task.issue_number is None:
            return
        tail = str(payload.get("test_output") or "").strip()[-2000:]
        rounds = payload.get("edit_rounds")
        body = (
            f"Attempt {payload.get('attempt')} failed ({rounds} edit rounds). "
            f"WIP branch `{branch}` ({pushed}).\n\nLast test output:\n```\n{tail}\n```"
        )
        await self._github.comment(
            project.repo_full_name, task.issue_number, body, key=f"failure:{run_id}"
        )
        log.info("pr_opener.failure_reported", task_id=task.id, run_id=run_id, branch=branch)
