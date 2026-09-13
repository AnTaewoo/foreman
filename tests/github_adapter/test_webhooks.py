"""P2.4 — webhooks (red a~e): HMAC, 6종 변환, 슬래시 훅, 중복 delivery, 미등록 repo, 봇 자기 루프(B10)."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.events.schema import Event, EventType
from github_adapter.webhooks import (
    DeliveryCache,
    ProjectRef,
    SlashCommand,
    WebhookHandler,
    build_webhook_router,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "webhooks"
SECRET = "s3cret"


class Spy:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.commands: list[SlashCommand] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)

    async def on_slash(self, cmd: SlashCommand) -> None:
        self.commands.append(cmd)


async def resolve_project(repo: str) -> ProjectRef | None:
    if repo == "org/demo":
        return ProjectRef(project_id="P1", default_branch="main")
    return None


async def resolve_goal(project_id: str, task_id: str) -> str | None:
    return "G1" if task_id == "01TASK" else None


@pytest.fixture
def spy() -> Spy:
    return Spy()


@pytest.fixture
def app(spy: Spy) -> TestClient:
    handler = WebhookHandler(
        secret=SECRET,
        publish=spy.publish,
        resolve_project=resolve_project,
        on_slash_command=spy.on_slash,
        cache=DeliveryCache(ttl_seconds=60, max_size=100),
        resolve_goal=resolve_goal,
        bot_login="foreman[bot]",
    )
    application = FastAPI()
    application.include_router(build_webhook_router(handler))
    return TestClient(application)


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def _sig(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post(
    client: TestClient,
    event: str,
    name: str,
    *,
    delivery: str | None = None,
    sig: str | None = None,
) -> Any:
    body = _fixture(name)
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or f"d-{name}",
        "X-Hub-Signature-256": _sig(body) if sig is None else sig,
        "Content-Type": "application/json",
    }
    return client.post("/webhooks/github", content=body, headers=headers)


# (a) 서명
def test_bad_signature_401(app: TestClient, spy: Spy) -> None:
    assert post(app, "issues", "issues_opened", sig="sha256=deadbeef").status_code == 401
    assert post(app, "issues", "issues_opened", sig=_sig(b"other")).status_code == 401
    body = _fixture("issues_opened")
    res = app.post(
        "/webhooks/github", content=body,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "x", "Content-Type": "application/json"},
    )  # fmt: skip
    assert res.status_code == 401
    assert spy.events == []


# (b) 6종 변환
def test_issues_ignored(app: TestClient, spy: Spy) -> None:
    assert post(app, "issues", "issues_opened").status_code == 204
    assert spy.events == []


@pytest.mark.parametrize(
    ("name", "command", "argument", "author"),
    [
        ("issue_comment_approve", "approve", "", "alice"),
        ("issue_comment_changes", "changes", "split task 2 into two", "bob"),
    ],
)
def test_slash_command_hook(
    app: TestClient, spy: Spy, name: str, command: str, argument: str, author: str
) -> None:
    res = post(app, "issue_comment", name)
    assert res.status_code == 202
    assert spy.events == []
    assert len(spy.commands) == 1
    cmd = spy.commands[0]
    assert cmd.command == command and cmd.argument == argument and cmd.author == author
    assert cmd.issue_number == 5 and cmd.repo == "org/demo" and cmd.project_id == "P1"


def test_plain_comment_ignored(app: TestClient, spy: Spy) -> None:
    assert post(app, "issue_comment", "issue_comment_plain").status_code == 204
    assert spy.commands == [] and spy.events == []


def test_pr_opened(app: TestClient, spy: Spy) -> None:
    assert post(app, "pull_request", "pull_request_opened").status_code == 202
    (e,) = spy.events
    assert e.type is EventType.PR_OPENED and e.project_id == "P1"
    assert e.subject.entity == "pr" and e.subject.id == "42"
    assert e.actor.type == "github" and e.actor.id == "alice"
    assert e.correlation_id == "G1" and e.causation_id is None and e.signature is None
    assert e.payload["task_id"] == "01TASK" and e.payload["run_id"] == "01RUN"
    assert e.payload["pr_number"] == 42 and e.payload["head"] == "ai/epic/12-x"
    assert e.payload["base"] == "main" and e.payload["draft"] is True


def test_pr_merged_and_closed(app: TestClient, spy: Spy) -> None:
    assert post(app, "pull_request", "pull_request_closed_merged").status_code == 202
    assert post(app, "pull_request", "pull_request_closed_unmerged").status_code == 202
    merged, closed = spy.events
    assert merged.type is EventType.PR_MERGED and merged.payload["merged_by"] == "alice"
    assert merged.payload["task_id"] == "01TASK" and merged.payload["pr_number"] == 42
    assert closed.type is EventType.PR_CLOSED and closed.payload["task_id"] == "01TASK"


def test_human_pr_without_meta_ignored(app: TestClient, spy: Spy) -> None:
    assert post(app, "pull_request", "pull_request_opened_human").status_code == 204
    assert spy.events == []


def test_review_submitted(app: TestClient, spy: Spy) -> None:
    assert post(app, "pull_request_review", "pull_request_review_submitted").status_code == 202
    (e,) = spy.events
    assert e.type is EventType.PR_REVIEW_SUBMITTED
    assert e.payload == {
        "task_id": "01TASK",
        "pr_number": 42,
        "state": "approved",
        "reviewer": "bob",
    }
    assert e.actor.id == "bob"


def test_check_suite(app: TestClient, spy: Spy) -> None:
    assert post(app, "check_suite", "check_suite_success").status_code == 202
    assert post(app, "check_suite", "check_suite_failure").status_code == 202
    assert post(app, "check_suite", "check_suite_no_pr").status_code == 204
    ok, bad = spy.events
    assert ok.type is EventType.PR_CHECKS_PASSED and ok.subject.id == "42"
    assert ok.payload["conclusion"] == "success" and ok.payload["pr_number"] == 42
    assert bad.type is EventType.PR_CHECKS_FAILED and bad.payload["conclusion"] == "failure"
    assert ok.correlation_id == "P1"  # task_id를 모르므로 project 스코프 (D-25)


def test_push_logged_only(app: TestClient, spy: Spy) -> None:
    assert post(app, "push", "push").status_code == 204
    assert spy.events == []


# (c) 미지원 타입
def test_unsupported_event_204(app: TestClient, spy: Spy) -> None:
    assert post(app, "star", "push").status_code == 204


# (d) 같은 delivery 두 번
def test_duplicate_delivery(app: TestClient, spy: Spy) -> None:
    assert post(app, "pull_request", "pull_request_opened", delivery="same").status_code == 202
    res = post(app, "pull_request", "pull_request_opened", delivery="same")
    assert res.status_code == 200 and res.json() == {"status": "duplicate"}
    assert len(spy.events) == 1


def test_delivery_cache_ttl_and_max() -> None:
    cache = DeliveryCache(ttl_seconds=10, max_size=2)
    assert cache.seen("a", now=0.0) is False
    assert cache.seen("a", now=1.0) is True
    assert cache.seen("a", now=11.0) is False  # TTL 만료
    cache.seen("b", now=11.0)
    cache.seen("c", now=11.0)  # max 2 → 가장 오래된 것 제거
    assert cache.seen("a", now=12.0) is False


# (e) 미등록 repo
def test_unknown_repo_204(app: TestClient, spy: Spy) -> None:
    body = json.loads(_fixture("pull_request_opened"))
    body["repository"]["full_name"] = "other/repo"
    raw = json.dumps(body).encode()
    res = app.post(
        "/webhooks/github", content=raw,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "u", "X-Hub-Signature-256": _sig(raw), "Content-Type": "application/json"},
    )  # fmt: skip
    assert res.status_code == 204 and spy.events == []


# B10 — 앱 봇 자신의 이벤트 무시, 다른 봇(github-actions)은 처리
def test_own_bot_events_ignored(app: TestClient, spy: Spy) -> None:
    assert post(app, "issue_comment", "issue_comment_bot").status_code == 204
    assert post(app, "pull_request", "pull_request_opened_bot").status_code == 204
    assert spy.commands == [] and spy.events == []


def test_dedupe_before_ignore_paths(app: TestClient, spy: Spy) -> None:
    # 무시(204)된 delivery도 중복이면 200
    assert post(app, "push", "push", delivery="p1").status_code == 204
    assert post(app, "push", "push", delivery="p1").status_code == 200
