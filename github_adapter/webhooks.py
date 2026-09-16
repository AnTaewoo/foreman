"""GitHub 웹훅 → 내부 Event (설계 §7.1, §7.4, §13). ``POST /webhooks/github``.

매핑 표 (X-GitHub-Event / action → 결과):

| 웹훅 | 조건 | 결과 |
|---|---|---|
| issues.* | — | 무시 204 (Issue는 플랫폼이 만든다) |
| issue_comment.created | ``/approve`` ``/reject <r>`` ``/changes <t>`` | slash 훅, 202 |
| issue_comment.created | 그 외 | 204 |
| pull_request.opened | 본문에 §7.3 메타 블록 | ``pr.opened`` 202 (메타 없으면 사람 PR → 204) |
| pull_request.closed | ``merged`` true / false | ``pr.merged`` / ``pr.closed`` 202 |
| pull_request_review.submitted | 메타 블록 | ``pr.review_submitted`` 202 |
| check_suite.completed | PR 있음 | ``pr.checks_passed`` / ``pr.checks_failed`` 202 |
| check_suite.completed | PR 없음 | 204 |
| push | — | 로그만 204 |
| 그 외 타입 | — | 204 |

공통: 서명 불일치/누락 → 401. 같은 ``X-GitHub-Delivery`` 두 번째 → 200 ``{"status":"duplicate"}``.
``resolve_project(repo)``가 None → 204. sender가 앱 봇 자신(``bot_login``) → 204 (B10 자기 루프).
correlation_id는 ``resolve_goal``이 goal을 주면 goal_id, 아니면 project_id (D-25).
causation_id는 None(GitHub에서 온 루트 이벤트). 서명은 publish 측(chain.append_signed)이 채운다.
MVP 1 dev에서는 테스트가 서명된 가짜 웹훅을 직접 POST 한다 (D-13).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from control_plane.events.schema import Actor, Event, EventType, Subject
from github_adapter import markers

log = structlog.get_logger(__name__)

SlashName = Literal["approve", "reject", "changes"]
SLASH_COMMANDS: tuple[SlashName, ...] = ("approve", "reject", "changes")


@dataclass(frozen=True)
class ProjectRef:
    project_id: str
    default_branch: str = "main"


@dataclass(frozen=True)
class SlashCommand:
    command: SlashName
    argument: str
    author: str
    issue_number: int  # 호환: issue_comment면 issue.number, discussion_comment면 discussion.number
    repo: str
    project_id: str
    delivery_id: str
    source: Literal["issue", "discussion"] = "issue"  # P6.4 (리뷰 A5)
    number: int = 0  # source의 번호 (issue.number | discussion.number)


Publish = Callable[[Event], Awaitable[None]]
ResolveProject = Callable[[str], Awaitable[ProjectRef | None]]
ResolveGoal = Callable[[str, str], Awaitable[str | None]]
SlashHook = Callable[[SlashCommand], Awaitable[None]]


class DeliveryCache:
    """``X-GitHub-Delivery`` 중복 방지 (TTL + 상한, 메모리)."""

    def __init__(self, ttl_seconds: float = 3600, max_size: int = 10_000) -> None:
        self._ttl = ttl_seconds
        self._max = max_size
        self._seen: OrderedDict[str, float] = OrderedDict()

    def seen(self, delivery_id: str, *, now: float | None = None) -> bool:
        """처음 보면 기록하고 False, 이미 봤으면 True."""
        ts = time.monotonic() if now is None else now
        expired = [k for k, t in self._seen.items() if ts - t >= self._ttl]
        for k in expired:
            del self._seen[k]
        if delivery_id in self._seen:
            return True
        self._seen[delivery_id] = ts
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return False


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_slash_command(body: str) -> tuple[SlashName, str] | None:
    text = body.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text[1:].partition(" ")
    name = head.strip().lower()
    if name not in SLASH_COMMANDS:
        return None
    return name, rest.strip()


_IGNORED = Response(status_code=204)
_ACCEPTED = Response(status_code=202)


class WebhookHandler:
    def __init__(
        self,
        *,
        secret: str,
        publish: Publish,
        resolve_project: ResolveProject,
        on_slash_command: SlashHook,
        cache: DeliveryCache | None = None,
        resolve_goal: ResolveGoal | None = None,
        bot_login: str | None = None,
    ) -> None:
        self._secret = secret
        self._publish = publish
        self._resolve_project = resolve_project
        self._on_slash = on_slash_command
        self._cache = cache or DeliveryCache()
        self._resolve_goal = resolve_goal
        self._bot_login = bot_login

    async def handle(self, request: Request) -> Response:
        if not self._secret:  # D-50 (F-8): 시크릿 없이는 어떤 서명도 믿지 않는다
            return JSONResponse({"detail": "webhook secret not configured"}, status_code=503)
        body = await request.body()
        if not verify_signature(self._secret, body, request.headers.get("X-Hub-Signature-256")):
            return JSONResponse({"detail": "invalid signature"}, status_code=401)
        delivery = request.headers.get("X-GitHub-Delivery", "")
        if delivery and self._cache.seen(delivery):
            return JSONResponse({"status": "duplicate"}, status_code=200)
        gh_event = request.headers.get("X-GitHub-Event", "")
        payload: dict[str, Any] = await request.json()
        if gh_event == "ping":  # App 저장/웹훅 설정 시 GitHub가 보낸다 (P7.1) — 서명 검증 뒤 200
            log.info("webhook.ping", hook_id=payload.get("hook_id"), delivery=delivery)
            return JSONResponse({"pong": True}, status_code=200)
        sender = payload.get("sender") or {}
        if self._bot_login and sender.get("login") == self._bot_login:
            log.debug("webhook.own_bot_ignored", gh_event=gh_event, delivery=delivery)
            return _IGNORED
        repo = str((payload.get("repository") or {}).get("full_name", ""))
        project = await self._resolve_project(repo) if repo else None
        if project is None:
            log.info("webhook.unknown_repo", repo=repo, gh_event=gh_event)
            return _IGNORED
        actor = Actor(type="github", id=str(sender.get("login", "unknown")))
        ctx = _Ctx(project=project, repo=repo, actor=actor, delivery=delivery, payload=payload)
        dispatch = {
            "issue_comment": self._issue_comment,
            "discussion_comment": self._discussion_comment,
            "pull_request": self._pull_request,
            "pull_request_review": self._pull_request_review,
            "check_suite": self._check_suite,
            "push": self._push,
        }
        fn = dispatch.get(gh_event)
        if fn is None:
            log.debug("webhook.ignored", gh_event=gh_event)
            return _IGNORED
        return await fn(ctx)

    # ------------------------------------------------------------------ 이벤트 생성
    async def _correlation(self, project_id: str, task_id: str | None) -> str:
        if task_id and self._resolve_goal is not None:
            goal_id = await self._resolve_goal(project_id, task_id)
            if goal_id:
                return goal_id
        return project_id

    async def _emit(
        self,
        ctx: _Ctx,
        type_: EventType,
        subject: Subject,
        payload: dict[str, Any],
        task_id: str | None,
    ) -> Response:
        event = Event(
            project_id=ctx.project.project_id,
            actor=ctx.actor,
            type=type_,
            subject=subject,
            payload=payload,
            correlation_id=await self._correlation(ctx.project.project_id, task_id),
            causation_id=None,
        )
        await self._publish(event)
        log.info("webhook.published", type=type_.value, delivery=ctx.delivery)
        return _ACCEPTED

    # ------------------------------------------------------------------ 종류별
    async def _issue_comment(self, ctx: _Ctx) -> Response:
        p = ctx.payload
        if p.get("action") != "created":
            return _IGNORED
        parsed = parse_slash_command(str((p.get("comment") or {}).get("body", "")))
        if parsed is None:
            return _IGNORED
        name, argument = parsed
        number = int((p.get("issue") or {}).get("number", 0))
        cmd = SlashCommand(
            command=name,
            argument=argument,
            author=str((p.get("comment") or {}).get("user", {}).get("login", ctx.actor.id)),
            issue_number=number,
            repo=ctx.repo,
            project_id=ctx.project.project_id,
            delivery_id=ctx.delivery,
            source="issue",
            number=number,
        )
        await self._on_slash(cmd)
        return _ACCEPTED

    async def _discussion_comment(self, ctx: _Ctx) -> Response:
        """Plan Discussion의 ``/approve`` 등 (P6.4, 리뷰 A5).

        페이로드: docs.github.com/en/webhooks/webhook-events-and-payloads#discussion_comment
        (2026-09-13 확인) — ``action`` created|edited|deleted, ``comment{body, user.login, …}``,
        ``discussion{number, title, …}``. created만 처리한다.
        """
        p = ctx.payload
        if p.get("action") != "created":
            return _IGNORED
        parsed = parse_slash_command(str((p.get("comment") or {}).get("body", "")))
        if parsed is None:
            return _IGNORED
        name, argument = parsed
        number = int((p.get("discussion") or {}).get("number", 0))
        cmd = SlashCommand(
            command=name,
            argument=argument,
            author=str((p.get("comment") or {}).get("user", {}).get("login", ctx.actor.id)),
            issue_number=number,
            repo=ctx.repo,
            project_id=ctx.project.project_id,
            delivery_id=ctx.delivery,
            source="discussion",
            number=number,
        )
        await self._on_slash(cmd)
        return _ACCEPTED

    async def _pull_request(self, ctx: _Ctx) -> Response:
        p = ctx.payload
        pr = p.get("pull_request") or {}
        meta = markers.parse_pr_meta(pr.get("body"))
        if meta is None:
            log.debug("webhook.pr_without_meta_ignored", number=pr.get("number"))
            return _IGNORED
        number = int(pr["number"])
        subject = Subject(entity="pr", id=str(number))
        action = p.get("action")
        if action == "opened":
            payload: dict[str, Any] = {
                "task_id": meta.task_id, "run_id": meta.run_id, "pr_number": number,
                "head": str(pr["head"]["ref"]), "base": str(pr["base"]["ref"]),
                "draft": bool(pr.get("draft", False)), "url": str(pr.get("html_url", "")),
            }  # fmt: skip
            return await self._emit(ctx, EventType.PR_OPENED, subject, payload, meta.task_id)
        if action == "closed":
            if pr.get("merged"):
                payload = {
                    "task_id": meta.task_id,
                    "pr_number": number,
                    "merged_by": str((pr.get("merged_by") or {}).get("login", ctx.actor.id)),
                }
                return await self._emit(ctx, EventType.PR_MERGED, subject, payload, meta.task_id)
            payload = {"task_id": meta.task_id, "pr_number": number}
            return await self._emit(ctx, EventType.PR_CLOSED, subject, payload, meta.task_id)
        return _IGNORED

    async def _pull_request_review(self, ctx: _Ctx) -> Response:
        p = ctx.payload
        if p.get("action") != "submitted":
            return _IGNORED
        pr = p.get("pull_request") or {}
        meta = markers.parse_pr_meta(pr.get("body"))
        if meta is None:
            return _IGNORED
        review = p.get("review") or {}
        number = int(pr["number"])
        payload = {
            "task_id": meta.task_id,
            "pr_number": number,
            "state": str(review.get("state", "")),
            "reviewer": str((review.get("user") or {}).get("login", ctx.actor.id)),
        }
        return await self._emit(
            ctx, EventType.PR_REVIEW_SUBMITTED, Subject(entity="pr", id=str(number)), payload,
            meta.task_id,
        )  # fmt: skip

    async def _check_suite(self, ctx: _Ctx) -> Response:
        p = ctx.payload
        if p.get("action") != "completed":
            return _IGNORED
        suite = p.get("check_suite") or {}
        prs = suite.get("pull_requests") or []
        if not prs:
            return _IGNORED
        number = int(prs[0]["number"])
        conclusion = str(suite.get("conclusion", ""))
        type_ = (
            EventType.PR_CHECKS_PASSED if conclusion == "success" else EventType.PR_CHECKS_FAILED
        )
        payload = {
            "pr_number": number,
            "conclusion": conclusion,
            "head_sha": str(suite.get("head_sha", "")),
        }
        # check_suite payload에는 PR 본문이 없어 task_id를 모른다 → project 스코프 correlation
        return await self._emit(ctx, type_, Subject(entity="pr", id=str(number)), payload, None)

    async def _push(self, ctx: _Ctx) -> Response:
        log.info("webhook.push", repo=ctx.repo, ref=ctx.payload.get("ref"))
        return _IGNORED


@dataclass(frozen=True)
class _Ctx:
    project: ProjectRef
    repo: str
    actor: Actor
    delivery: str
    payload: dict[str, Any]


def build_webhook_router(handler: WebhookHandler) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/github")
    async def github_webhook(request: Request) -> Response:
        return await handler.handle(request)

    return router
