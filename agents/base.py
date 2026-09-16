"""공통 Agent 계약 (설계 §5.1) + BaseAgent 실행 루프.

``BaseAgent.run(input)``: ``task.started`` → ``run.started`` → ``execute`` → ``run.finished``.
``run.finished.payload.outcome``은 RunOutcome, ``agent_outcome``은 원값 (D-28): done→success,
failed→failed, needs_decision/blocked→escalated, timeout→timeout. ``execute`` 예외 → 둘 다 failed.
이 모듈은 ``control_plane.config``를 import하지 않는다 (D-23).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agents.llm.base import ModelProvider
from agents.llm.pricing import Prices, estimate_cost
from control_plane.events.schema import Actor, EntityType, Event, EventType, Subject

log = structlog.get_logger(__name__)

Publish = Callable[[Event], Awaitable[Event]]
Outcome = Literal["done", "needs_decision", "blocked", "failed", "timeout"]
OUTCOME_TO_RUN: dict[str, str] = {
    "done": "success",
    "failed": "failed",
    "needs_decision": "escalated",
    "blocked": "escalated",
    "timeout": "timeout",
}


# --------------------------------------------------------------------------- 입력


class TaskRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    spec: str
    kind: str = "feature"
    role_required: str = "coding"
    owned_paths: list[str] = Field(default_factory=list)
    issue_number: int | None = None
    epic_slug: str = "epic"
    risk_tier: str = "T1"
    depends_on: list[str] = Field(default_factory=list)
    attempt: int = 1
    max_attempts: int = 3


class ProjectContext(BaseModel):
    """repo 요약·아키텍처 문서·컨벤션·policy 요약 (§5.1)."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    goal_id: str
    repo: str  # "owner/repo" 또는 로컬 경로
    default_branch: str = "main"
    context_md: str | None = None  # .ai-platform/CONTEXT.md 본문
    conventions: str = ""
    policy_summary: str = ""  # MVP 1: 빈 문자열 (Policy Engine은 MVP 3)
    related_summaries: list[str] = Field(default_factory=list)


class AgentMemory(BaseModel):
    role_notes: str = ""


class RunBudget(BaseModel):
    max_tokens: int = 200_000
    max_seconds: int = 45 * 60
    max_cost_usd: float = 5.0


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task: TaskRef
    project_context: ProjectContext
    memory: AgentMemory = Field(default_factory=AgentMemory)
    budget: RunBudget = Field(default_factory=RunBudget)
    run_id: str
    agent_id: str = "coding-1"


# --------------------------------------------------------------------------- 출력


class Artifact(BaseModel):
    kind: Literal["branch", "pr", "comment", "discussion", "report", "new_tasks"]
    ref: str
    url: str | None = None


class DecisionRequest(BaseModel):
    type: str  # plan | architecture | dependency | schema | security | deploy | cost | pr_merge
    reason: str
    options: list[str] = Field(default_factory=list)
    recommendation: str = ""
    files: list[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
    outcome: Outcome
    artifacts: list[Artifact] = Field(default_factory=list)
    decision_request: DecisionRequest | None = None
    new_tasks: list[dict[str, Any]] = Field(default_factory=list)  # Any: TaskDraft dump
    notes_for_memory: str = ""
    summary: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None


# --------------------------------------------------------------------------- BaseAgent


class BaseAgent(ABC):
    def __init__(
        self,
        *,
        publish: Publish,
        provider: ModelProvider,
        model: str | None = None,
        prices: Prices | None = None,
    ) -> None:
        self._publish = publish
        self.provider = provider
        self.model = model
        self.prices = prices or Prices()  # D-39: 단가 미설정이면 cost_usd 0
        self.last_event_id: str | None = None
        self._published_types: list[
            EventType
        ] = []  # 이번 run에서 낸 타입 (task.failed 보장용, D-44)

    async def publish(
        self,
        input: AgentInput,
        type_: EventType,
        entity: EntityType,
        id_: str,
        payload: dict[str, Any],
    ) -> Event:
        self._published_types.append(type_)
        event = Event(
            project_id=input.project_context.project_id,
            actor=Actor(type="agent", id=input.agent_id),
            type=type_,
            subject=Subject(entity=entity, id=id_),
            payload=payload,
            correlation_id=input.project_context.goal_id,
            causation_id=self.last_event_id,
        )
        out = await self._publish(event)
        self.last_event_id = out.id
        return out

    @abstractmethod
    async def execute(self, input: AgentInput) -> AgentOutput: ...

    async def run(self, input: AgentInput) -> AgentOutput:
        started = time.monotonic()
        self._published_types = []
        await self.publish(
            input, EventType.TASK_STARTED, "task", input.task.id, {"run_id": input.run_id}
        )
        await self.publish(
            input,
            EventType.RUN_STARTED,
            "run",
            input.run_id,
            {
                "task_id": input.task.id,
                "agent_id": input.agent_id,
                "model": self.model or "unknown",
            },
        )
        try:
            output = await self.execute(input)
        except Exception as exc:  # 어떤 예외든 Run은 실패로 끝난다
            log.exception("agent.execute_failed", run_id=input.run_id, task_id=input.task.id)
            output = AgentOutput(
                outcome="failed", summary=f"{type(exc).__name__}: {exc}", error=str(exc)
            )
        if output.outcome == "failed" and EventType.TASK_FAILED not in self._published_types:
            # D-44 (F-5c): task.failed 없이 run.finished(failed)만 나가면 Task가 running에 영구 고착
            await self.publish(
                input,
                EventType.TASK_FAILED,
                "task",
                input.task.id,
                {"run_id": input.run_id, "reason": "error", "attempt": input.task.attempt},
            )
        if output.cost_usd == 0.0 and self.prices.is_set:  # execute가 준 값이 있으면 그대로
            output = output.model_copy(
                update={"cost_usd": estimate_cost(output.tokens_in, output.tokens_out, self.prices)}
            )
        await self.publish(
            input,
            EventType.RUN_FINISHED,
            "run",
            input.run_id,
            {
                "outcome": OUTCOME_TO_RUN[output.outcome],
                "agent_outcome": output.outcome,
                "tokens_in": output.tokens_in,
                "tokens_out": output.tokens_out,
                "cost_usd": output.cost_usd,
                "duration_s": round(time.monotonic() - started, 3),
                "error": output.error,
            },
        )
        return output
