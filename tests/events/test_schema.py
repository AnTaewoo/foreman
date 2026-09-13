"""P1.1 — 이벤트 스키마: EventType 44, 봉투, canonical/sign/verify, PAYLOAD_TYPES (P1.1 red)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from ulid import ULID

from control_plane.events import schema
from control_plane.events.schema import (
    PAYLOAD_TYPES,
    UNCHAINED,
    Actor,
    Event,
    EventType,
    Subject,
    canonical_json,
    required_payload_keys,
    sign,
    signed,
    verify_chain,
)

# 설계 §4.2 표 (D-19·D-27·D-31 반영) — 44개. 이 목록이 진실이고 Enum이 여기에 맞춰야 한다.
DESIGN_EVENT_NAMES: list[str] = [
    # goal (6)
    "goal.created", "goal.plan_proposed", "goal.activated", "goal.blocked", "goal.completed",
    "goal.cancelled",
    # epic (3, D-27)
    "epic.created", "epic.activated", "epic.completed",
    # task (9 = 8 + task.cancelled, D-27)
    "task.created", "task.assigned", "task.started", "task.blocked", "task.completed",
    "task.failed", "task.retried", "task.escalated", "task.cancelled",
    # run (5 = 4 + run.tool_denied, D-31)
    "run.started", "run.tool_called", "run.tool_denied", "run.artifact_produced", "run.finished",
    # decision (5)
    "decision.opened", "decision.agent_voted", "decision.human_responded", "decision.resolved",
    "decision.expired",
    # pr (6)
    "pr.opened", "pr.checks_passed", "pr.checks_failed", "pr.review_submitted", "pr.merged",
    "pr.closed",
    # policy (4)
    "policy.updated", "policy.tier_overridden", "budget.warning", "budget.exceeded",
    # control (6)
    "project.created", "project.updated", "project.paused", "project.resumed", "agent.killed",
    "control.emergency_stop",
]  # fmt: skip


def _event(**overrides: Any) -> Event:
    base: dict[str, Any] = {
        "project_id": "P1",
        "actor": {"type": "system", "id": "api"},
        "type": EventType.GOAL_CREATED,
        "subject": {"entity": "goal", "id": "G1"},
        "payload": {"title": "t", "description": "d"},
        "correlation_id": "G1",
        "causation_id": None,
    }
    base.update(overrides)
    return Event(**base)


# ---------------------------------------------------------------- (a) EventType 44개 양방향
def test_event_type_matches_design_table_exactly() -> None:
    assert len(DESIGN_EVENT_NAMES) == 44
    assert len(set(DESIGN_EVENT_NAMES)) == 44
    enum_values = {e.value for e in EventType}
    assert enum_values - set(DESIGN_EVENT_NAMES) == set(), "설계에 없는 타입"
    assert set(DESIGN_EVENT_NAMES) - enum_values == set(), "Enum에 빠진 타입"


@pytest.mark.parametrize("name", DESIGN_EVENT_NAMES)
def test_event_type_is_domain_dot_name(name: str) -> None:
    domain, _, short = name.partition(".")
    assert domain and short and "." not in short
    member = EventType(name)
    assert member.name == name.upper().replace(".", "_")
    assert member.domain == domain


def test_unchained_is_only_run_tool_called() -> None:
    assert UNCHAINED == frozenset({EventType.RUN_TOOL_CALLED})


# ---------------------------------------------------------------- (b) 라운드트립, ULID, UTC
def test_event_json_roundtrip_and_ulid_and_utc_ts() -> None:
    e = _event()
    ULID.from_str(e.id)  # ULID 형식이 아니면 ValueError
    assert e.ts.tzinfo is not None and e.ts.utcoffset() == timedelta(0)
    again = Event.model_validate_json(e.model_dump_json())
    assert again == e


def test_ts_naive_treated_as_utc_and_aware_normalized() -> None:
    naive = datetime(2026, 9, 13, 1, 2, 3, 456)
    assert _event(ts=naive).ts == naive.replace(tzinfo=UTC)
    kst = datetime(2026, 9, 13, 10, 2, 3, 456, tzinfo=UTC).astimezone(
        datetime.now().astimezone().tzinfo
    )
    assert _event(ts=kst).ts.utcoffset() == timedelta(0)
    assert _event(ts=kst).ts == kst


def test_event_is_frozen() -> None:
    e = _event()
    with pytest.raises(ValidationError):
        e.project_id = "P2"  # type: ignore[misc]


# ---------------------------------------------------------------- (c) canonical_json / sign
def test_canonical_json_is_key_order_independent_and_excludes_signature() -> None:
    a = _event(payload={"b": 1, "a": {"y": [1, 2], "x": "한글"}})
    b = Event(**{**a.model_dump(), "payload": {"a": {"x": "한글", "y": [1, 2]}, "b": 1}})
    assert canonical_json(a) == canonical_json(b)
    assert '"signature"' not in canonical_json(a)
    assert "한글" in canonical_json(a)  # ensure_ascii=False
    assert " " not in canonical_json(a).replace("한글", "")  # compact separators
    assert canonical_json(signed(a, "abc")) == canonical_json(a)


def test_sign_is_deterministic_and_chains_on_prev() -> None:
    c = canonical_json(_event())
    assert sign(None, c) == sign(None, c)
    assert sign(None, c) != sign("prev", c)
    assert sign("p1", c) != sign("p2", c)
    assert len(sign(None, c)) == 64  # sha256 hex


# ---------------------------------------------------------------- (d) verify_chain
def _chain(n: int) -> list[Event]:
    out: list[Event] = []
    prev: str | None = None
    for i in range(n):
        e = _event(payload={"title": f"t{i}", "description": "d"}, causation_id=None)
        sig = sign(prev, canonical_json(e))
        e = signed(e, sig)
        out.append(e)
        prev = sig
    return out


def test_verify_chain_ok() -> None:
    assert verify_chain([]) is True
    assert verify_chain(_chain(5)) is True


def test_verify_chain_detects_tamper_reorder_unsigned() -> None:
    chain = _chain(4)
    tampered = list(chain)
    tampered[2] = Event(**{**chain[2].model_dump(), "payload": {"title": "X", "description": "d"}})
    assert verify_chain(tampered) is False
    assert verify_chain([chain[0], chain[2], chain[1], chain[3]]) is False
    unsigned = list(chain)
    unsigned[1] = Event(**{**chain[1].model_dump(), "signature": None})
    assert verify_chain(unsigned) is False
    forged = list(chain)
    forged[3] = signed(chain[3], "0" * 64)
    assert verify_chain(forged) is False


# ---------------------------------------------------------------- (e) 필수 필드, None 허용, 값 집합
def test_correlation_and_causation_fields_are_required() -> None:
    base = _event().model_dump()
    for field in ("correlation_id", "causation_id"):
        data = dict(base)
        del data[field]
        with pytest.raises(ValidationError):
            Event(**data)


def test_causation_none_is_root_and_signature_none_is_unsigned() -> None:
    e = _event(causation_id=None)
    assert e.causation_id is None
    assert e.signature is None
    assert _event(causation_id="01ABC").causation_id == "01ABC"
    with pytest.raises(ValidationError):
        _event(correlation_id=None)


@pytest.mark.parametrize("actor_type", ["agent", "human", "system", "github"])
def test_actor_types(actor_type: str) -> None:
    assert Actor(type=actor_type, id="x").type == actor_type


def test_actor_type_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        Actor(type="bot", id="x")


@pytest.mark.parametrize(
    "entity", ["project", "goal", "epic", "task", "run", "decision", "pr", "agent", "policy"]
)
def test_subject_entities(entity: str) -> None:
    assert Subject(entity=entity, id="1").entity == entity


def test_subject_entity_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        Subject(entity="issue", id="1")


# ---------------------------------------------------------------- (f) PAYLOAD_TYPES
P1_PUBLISHED = [
    "project.created", "goal.created", "epic.created", "task.created", "task.assigned",
    "task.started", "task.completed", "task.failed", "run.started", "run.tool_called",
    "run.finished", "pr.opened",
]  # fmt: skip


def test_p1_payload_types_registered() -> None:
    assert len(P1_PUBLISHED) == 12
    for name in P1_PUBLISHED:
        assert EventType(name) in PAYLOAD_TYPES, name
    assert EventType.GOAL_PLAN_PROPOSED in PAYLOAD_TYPES


def test_run_finished_payload_has_outcome_and_agent_outcome() -> None:
    keys = required_payload_keys(EventType.RUN_FINISHED)
    assert {"outcome", "agent_outcome", "tokens_in", "tokens_out"} <= keys
    hints = PAYLOAD_TYPES[EventType.RUN_FINISHED].__annotations__
    assert set(schema.RUN_OUTCOMES) == {"success", "failed", "timeout", "cancelled", "escalated"}
    assert set(schema.AGENT_OUTCOMES) == {"done", "needs_decision", "blocked", "failed", "timeout"}
    assert "outcome" in hints and "agent_outcome" in hints


def test_plan_proposed_has_revision_and_task_created_keys() -> None:
    assert "revision" in required_payload_keys(EventType.GOAL_PLAN_PROPOSED)
    assert {
        "epic_id", "title", "spec", "kind", "role_required", "depends_on", "owned_paths",
        "risk_tier", "issue_number",
    } <= required_payload_keys(EventType.TASK_CREATED)  # fmt: skip
    assert required_payload_keys(EventType.EPIC_CREATED) >= {"goal_id", "title", "order"}


def test_required_payload_keys_unregistered_type_is_empty() -> None:
    assert required_payload_keys(EventType.DECISION_OPENED) == frozenset()


# ---------------------------------------------------------------- (g) payload 값 제한
@pytest.mark.parametrize(
    "bad",
    [
        {"x": math.nan},
        {"x": math.inf},
        {"x": [1, {"y": -math.inf}]},
        {"x": Decimal("1.5")},
        {"x": datetime.now(UTC)},
        {"x": {1, 2}},
        {"x": b"bytes"},
        {"x": ULID()},
    ],
    ids=["nan", "inf", "nested-inf", "decimal", "datetime", "set", "bytes", "ulid"],
)
def test_payload_rejects_non_json_values(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _event(payload=bad)


def test_payload_accepts_json_primitives_and_is_stable() -> None:
    payload = {
        "f": 1e-5, "g": 0.1 + 0.2, "big": 2**53 + 1, "neg0": -0.0, "s": "한글 🚀 ",
        "empty": {}, "list": [], "nested": [[1, [2, [3]]]], "b": True, "n": None,
    }  # fmt: skip
    e = _event(payload=payload)
    c = canonical_json(e)
    assert json.loads(c)["payload"] == payload
    assert canonical_json(Event.model_validate_json(e.model_dump_json())) == c
