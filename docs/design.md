# Human-in-the-Loop 멀티에이전트 개발 플랫폼 — 상세 설계 문서

> "Connect a GitHub repository, define a goal, and let an AI engineering team build it — while humans stay in control of the decisions that matter."

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.1 (Draft) |
| 작성일 | 2026-09-11 |
| 상태 | 설계 검토 전 |
| 대상 독자 | 본인(단독 개발 초기), 이후 협업자 / 투자자 기술 검토용 |

---

## 목차

1. 개요와 범위
2. 용어 정의
3. 시스템 아키텍처
4. 데이터 모델
5. Agent 설계
6. 워크플로 및 상태 머신
7. GitHub 매핑 규약
8. Policy Engine (자율권 · 승인)
9. 자율 루프와 실패 복구
10. 병렬 작업과 충돌 관리
11. 관측성 · 비용 관리
12. 보안 모델
13. API 설계
14. 기술 스택
15. MVP 로드맵
16. 리스크와 열린 질문

---

## 1. 개요와 범위

### 1.1 목표

- GitHub Repository + 하나의 개발 목표(Goal)를 입력받아, 역할별 AI Agent 팀이 **장시간 자율적으로** 계획·구현·테스트·리뷰를 반복해 프로젝트를 **완수**한다.
- 변경의 **위험도(risk tier)** 에 따라 Agent 자율권을 조절하고, 위험한 결정은 **인간 승인(quorum)** 을 거친다.
- 모든 협업 상태는 GitHub(Issue / Discussion / Branch / PR)에 남겨 **Source of Truth** 를 단일화한다.

### 1.2 비목표 (v1에서 하지 않는 것)

- 자체 LLM 학습 / 파인튜닝
- 완전한 IDE(에디터·디버거) 구현 — Mission Control은 관리 화면이며 코드 편집은 기존 IDE에 위임
- GitHub 이외 SCM(GitLab, Bitbucket) 지원 — 어댑터 계층만 열어둔다
- 무인 프로덕션 배포 — 배포는 항상 승인 tier

### 1.3 설계 원칙

1. **GitHub가 상태다.** 내부 DB는 캐시·인덱스·감사 로그이며, 충돌 시 GitHub가 우선한다.
2. **모든 Agent 행동은 이벤트다.** 재생(replay) 가능해야 하고, 인간이 언제든 개입·중단 가능해야 한다.
3. **위험한 것은 느리게, 안전한 것은 빠르게.** 승인 게이트는 risk tier에만 걸고 나머지는 막지 않는다.
4. **LLM은 교체 가능하다.** Agent 로직은 모델 provider와 분리한다.
5. **실패는 종료가 아니라 새 작업이다.** 테스트 실패·리뷰 거절은 새 Task로 변환된다.

---

## 2. 용어 정의

| 용어 | 정의 |
|---|---|
| Project | 연결된 GitHub Repository 1개 + 정책(autonomy.yaml) + 예산 |
| Goal | 사용자가 입력한 최상위 목표. 하나의 Project는 동시에 여러 Goal을 가질 수 있음 |
| Epic | Goal을 분해한 중간 단위. GitHub Milestone에 매핑 |
| Task | 실행 단위. GitHub Issue 1개에 1:1 매핑 |
| Decision | 인간 승인이 필요한 의사결정. GitHub Discussion(Proposal 카테고리)에 매핑 |
| Run | Agent가 하나의 Task를 수행하는 단일 실행 세션. 로그·비용·산출물 단위 |
| Risk Tier | 변경의 위험도 등급 (T0 자동 ~ T3 다중 승인) |
| Quorum | Decision 승인에 필요한 최소 승인 수·역할 조건 |
| Policy | Project별 자율권·승인·예산 규칙 (autonomy.yaml) |
| Control Plane | 플랫폼 서버 측 두뇌 (Orchestrator, Scheduler, Policy Engine, Event Bus) |
| Worker | 실제 코드를 만지는 격리 실행 환경 (컨테이너 + git worktree) |

---

## 3. 시스템 아키텍처

### 3.1 전체 구성도

```
┌───────────────────────────────────────────────────────────────────┐
│                         Mission Control (Web)                     │
│   Project Dashboard · Agent Board · Decision Inbox · Run Viewer   │
└───────────────┬───────────────────────────────────────▲───────────┘
                │ REST / WebSocket                      │ events
┌───────────────▼───────────────────────────────────────┴───────────┐
│                            Control Plane                          │
│                                                                   │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ API Server │  │ Scheduler  │  │ Policy Engine│  │ Cost Meter│  │
│  └─────┬──────┘  └─────┬──────┘  └──────▲───────┘  └─────▲─────┘  │
│        │               │                │                │        │
│  ┌─────▼───────────────▼────────────────┴────────────────┴─────┐  │
│  │                    Orchestrator (LangGraph)                 │  │
│  │   plan → decompose → assign → monitor → replan → close      │  │
│  └─────┬────────────────────────────────────────────────┬──────┘  │
│        │ dispatch                                       │         │
│  ┌─────▼──────────────┐         ┌───────────────────────▼──────┐  │
│  │  Event Bus (Redis  │         │  State Store (PostgreSQL)    │  │
│  │  Streams)          │         │  projects/tasks/runs/events  │  │
│  └─────┬──────────────┘         └──────────────────────────────┘  │
└────────┼──────────────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────────────┐
│                         Agent Runtime (Workers)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │
│  │Architect │ │ Coding×N │ │ Research │ │  Test    │ │ Review  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘  │
│       │ tools: git, fs, shell(sandbox), github, web, llm     │    │
└───────┼────────────┼────────────┼────────────┼────────────┼───────┘
        │            │            │            │            │
┌───────▼────────────▼────────────▼────────────▼────────────▼───────┐
│                    GitHub Adapter (GitHub App)                    │
│   Issues · Discussions · Branches · PRs · Checks · Webhooks       │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                        ┌───────▼────────┐
                        │  GitHub Repo   │  ← Source of Truth
                        └────────────────┘
```

### 3.2 컴포넌트 책임

| 컴포넌트 | 책임 | 하지 않는 것 |
|---|---|---|
| **API Server** | 인증, Project/Goal CRUD, Decision 응답 수신, Mission Control용 조회 API, WebSocket 브로드캐스트 | Agent 로직 |
| **Orchestrator** | Goal → Epic → Task 분해, Task 할당, 진행 모니터링, 재계획, 완료 판정 | 코드 작성 |
| **Scheduler** | 실행 가능한 Task 큐 관리, 의존성 해소, 동시성 제한, 워커 슬롯 배정 | 무엇을 할지 결정 |
| **Policy Engine** | Task/변경의 risk tier 판정, 승인 필요 여부 결정, quorum 검사, 예산 게이트 | 승인 자체 (인간이 함) |
| **Cost Meter** | Run 단위 토큰·시간·비용 집계, 일일/프로젝트 예산 초과 시 중단 신호 | — |
| **Event Bus** | 모든 상태 변화를 append-only 이벤트로 발행 | 상태 저장 |
| **State Store** | 이벤트 투영(projection), 인덱스, 감사 로그 | GitHub와 충돌 시 우선권 |
| **Agent Runtime** | 격리된 워커에서 Agent 실행, 툴 호출, 산출물을 GitHub로 반영 | 정책 판단 |
| **GitHub Adapter** | GitHub App 토큰 관리, API rate limit 처리, 웹훅 수신→이벤트 변환, 멱등 쓰기 | — |
| **Mission Control** | 인간 인터페이스: 대시보드, 결정함, 실행 로그, 긴급 정지 | 코드 편집 |

### 3.3 실행 흐름 (요약)

```
[Human] Goal 입력
   → [Orchestrator] repo 분석(Research) → Plan 초안 → Discussion "Plan #N" 생성
   → [Policy] Plan 자체는 T2 → 인간 승인 대기 (첫 계획만)
   → [Human] Approve
   → [Orchestrator] Epic/Task 생성 → Issue 발행 → Scheduler 큐 투입
   → [Scheduler] 의존성 없는 Task부터 워커 배정
   → [Coding Agent] branch 생성 → 구현 → 로컬 테스트 → PR 생성
   → [Test Agent] CI 결과 + 추가 테스트 → 실패 시 "fix" Task 생성
   → [Review Agent] 리뷰 → risk tier 재판정 → T0/T1 자동 머지 / T2+ Decision 생성
   → [Human] Decision Inbox에서 Approve/Reject/Request Change
   → [Orchestrator] 결과 반영, 재계획, 다음 Task
   → 모든 Epic 완료 + Goal 검증 통과 → Release Decision → 종료
```

---

## 4. 데이터 모델

### 4.1 핵심 엔티티

```
Project
 ├─ id, name, repo_full_name, installation_id (GitHub App)
 ├─ policy (autonomy.yaml 파싱본, 버전 관리)
 ├─ budget { daily_usd, total_usd, spent_today, spent_total }
 ├─ default_branch, protected_paths[]
 └─ members[] { user_id, role: owner|approver|viewer }

Goal
 ├─ id, project_id, title, description (원문), status
 ├─ acceptance_criteria[] (Orchestrator가 구조화)
 ├─ plan_discussion_id
 └─ status: draft | planning | awaiting_plan_approval | active | blocked | done | cancelled

Epic  (→ GitHub Milestone)
 ├─ id, goal_id, title, order, milestone_number
 └─ status: pending | active | done

Task  (→ GitHub Issue)
 ├─ id, epic_id, issue_number, title, spec (markdown)
 ├─ kind: feature | bugfix | test | refactor | research | fix_from_review | fix_from_test
 ├─ role_required: coding | architect | research | test | review
 ├─ depends_on[] (task_id), blocks[]
 ├─ owned_paths[] (충돌 방지용 파일 소유권)
 ├─ risk_tier: T0 | T1 | T2 | T3  (Policy Engine이 판정, 변경 가능)
 ├─ assignee_agent_id, branch_name, pr_number
 ├─ attempt_count, max_attempts
 └─ status: (6.1 상태 머신 참조)

Run
 ├─ id, task_id, agent_id, worker_id
 ├─ started_at, ended_at, outcome: success | failed | timeout | cancelled | escalated
 ├─ input_snapshot (프롬프트/컨텍스트 해시), tool_calls[], artifacts[]
 ├─ tokens_in, tokens_out, cost_usd, model
 └─ log_ref (object storage key)

Decision  (→ GitHub Discussion, Proposal 카테고리)
 ├─ id, project_id, discussion_number
 ├─ type: plan | architecture | dependency | schema | security | deploy | cost | pr_merge
 ├─ risk_tier, proposal (structured: reason / options / recommendation / risk / rollback)
 ├─ agent_votes[] { agent_role, vote: approve|request_change|reject, rationale }
 ├─ quorum { required: n, roles: [], received: [] }
 ├─ human_responses[] { user_id, response, comment, at }
 ├─ related_task_ids[], related_pr_number
 ├─ expires_at (SLA), escalation_policy
 └─ status: open | approved | rejected | changes_requested | expired

Agent (인스턴스)
 ├─ id, project_id, role, display_name (e.g. "Backend #2")
 ├─ model_config { provider, model, temperature, max_ctx }
 ├─ tool_allowlist[], capabilities[]
 └─ status: idle | working | waiting_approval | blocked | paused | terminated

Event  (append-only)
 ├─ id (ulid), project_id, ts, actor { type: agent|human|system|github, id }
 ├─ type (e.g. task.created, run.started, decision.approved, pr.merged ...)
 ├─ subject { entity, id }, payload (json), correlation_id, causation_id
 └─ signature (감사용 해시 체인 — 저장 시점에 채움, 아래 규약)
```

### 4.2 이벤트 타입 (핵심만)

| 도메인 | 이벤트 |
|---|---|
| goal | goal.created, goal.plan_proposed, goal.activated, goal.decomposed (D-56, 분해 기록), goal.blocked, goal.completed, goal.cancelled |
| epic | epic.created, epic.activated, epic.completed |
| task | task.created, task.assigned, task.started, task.blocked, task.completed, task.failed, task.retried, task.escalated, task.cancelled |
| run | run.started, run.tool_called¹, run.tool_denied, run.artifact_produced, run.finished |
| decision | decision.opened, decision.agent_voted, decision.human_responded, decision.resolved, decision.expired |
| pr | pr.opened, pr.checks_passed, pr.checks_failed, pr.review_submitted, pr.merged, pr.closed |
| policy | policy.updated, policy.tier_overridden, budget.warning, budget.exceeded |
| control | project.created, project.updated, project.paused, project.resumed, agent.killed, control.emergency_stop |

¹ `run.tool_called`는 볼륨이 커서 **감사 체인 밖**이다: `tool_calls` 테이블에만 쓰고(서명 없음) 스트림에는 흘린다. 거부된 호출은 `run.tool_denied`로 체인에 남긴다.

봉투(envelope) 규약:

- `correlation_id`(필수) = Goal 스코프 이벤트는 Goal id, Goal 밖(project/policy/budget/agent/control 도메인)은 Project id. `causation_id` = 직전 원인 이벤트 id, 루트 이벤트(사람·API 명령이 원인)는 `null`. 이 두 개로 "왜 이 PR이 생겼는가"를 Goal까지 역추적한다.
- `signature`는 발행자가 아니라 **저장 시점**에 채운다(워커는 직전 서명을 모른다). 서명 대상은 payload가 아니라 저장된 `canonical_json` 텍스트이며, 체인 순서는 DB append 순번(`seq`)이다. 체인은 저장소 무결성 검증용이지 발행자 인증이 아니다.
- 엔티티 id는 전부 발행자가 만든 ULID다(DB autoincrement 없음). replay로 재구축해도 같은 id가 나와야 한다.

---

## 5. Agent 설계

### 5.1 공통 Agent 계약

모든 Agent는 동일한 인터페이스를 따른다.

```python
class AgentInput:
    task: Task
    project_context: ProjectContext   # repo 요약, 아키텍처 문서, 컨벤션, policy 요약
    memory: AgentMemory               # 해당 role의 프로젝트 내 누적 노트
    budget: RunBudget                 # 이 Run에 허용된 토큰/시간/비용

class AgentOutput:
    outcome: Literal["done", "needs_decision", "blocked", "failed"]
    artifacts: list[Artifact]         # branch, pr, discussion, report, new_tasks
    decision_request: DecisionRequest | None
    new_tasks: list[TaskDraft]
    notes_for_memory: str
    summary: str                      # Issue 코멘트로 남길 요약
```

`AgentOutput.outcome`과 `Run.outcome`(§4.1)은 값 집합이 다르다. `run.finished` 이벤트는 둘 다 싣는다:

| agent_outcome (AgentOutput) | outcome (Run) |
|---|---|
| done | success |
| failed | failed |
| needs_decision | escalated |
| blocked | escalated |
| timeout (워커 시간 초과) | timeout |
| — (kill / emergency stop) | cancelled |

공통 규칙:

- **읽기 전에 쓰지 않는다.** 모든 Agent는 Task 시작 시 `CONTEXT.md`(Orchestrator가 생성하는 프로젝트 요약)와 관련 Issue/Discussion을 먼저 읽는다.
- **자기 Tier를 넘지 않는다.** 작업 중 policy가 정의한 approval 대상(새 dependency 추가 등)을 건드리게 되면 즉시 중단하고 `needs_decision`으로 반환한다.
- **산출물은 GitHub에만.** 워커 로컬 파일은 Run 종료 시 폐기된다.
- **끝나면 요약을 남긴다.** Issue 코멘트로 무엇을 했고 무엇이 남았는지 기록한다.

### 5.2 역할별 명세

#### Orchestrator / PM Agent

| 항목 | 내용 |
|---|---|
| 트리거 | Goal 생성, Task 완료/실패, Decision 해소, 주기적 헬스체크(예: 10분) |
| 입력 | Goal, 전체 Task 그래프, 최근 이벤트, 예산 상태 |
| 도구 | github(issues, milestones, discussions), state_store(read), scheduler(enqueue) |
| 산출물 | Plan Discussion, Epic/Task 그래프, 재계획 diff, 진행률 |
| 종료 조건 | 모든 Epic done + Goal acceptance criteria 검증 통과 → Release Decision |
| 금지 | 코드 수정, PR 생성 |

Plan 산출 형식 (Discussion 본문):

```
## Plan for Goal #<n>: <title>
### Understanding
- repo 요약, 현재 아키텍처, 제약
### Acceptance Criteria (구조화)
- [ ] AC-1 ...
### Epics
1. <epic> — Tasks: 3, est. risk: T1
### Task Graph
T-1 → T-2 → T-4
T-3 ────────↗
### Decisions Expected
- 신규 dependency: redis (T2)
### Budget Estimate
- ~$X, ~N runs
```

#### Architect Agent

| 항목 | 내용 |
|---|---|
| 트리거 | Plan 수립 시, Task spec에 `needs_design` 라벨, Review Agent가 architecture violation 보고 |
| 입력 | 코드베이스 구조, 기존 ADR, 관련 Issue |
| 도구 | fs(read), github(discussions, issues), research 결과 |
| 산출물 | ADR(`docs/adr/NNNN-*.md`), Decision(Proposal), Task spec 보강 |
| 금지 | 기능 구현 |

Proposal 형식은 8.4 참조.

#### Coding Agent (Backend / Frontend / Infra …)

| 항목 | 내용 |
|---|---|
| 트리거 | Task 할당 |
| 입력 | Task spec, owned_paths, 컨벤션, 관련 ADR |
| 도구 | git, fs, shell(sandbox: build/test/lint만 허용), github(pr, comments) |
| 절차 | branch 생성 → 구현 → 로컬 테스트/린트 → 커밋 → PR(draft→ready) → Issue 코멘트 |
| 성공 조건 | PR ready + 로컬 테스트 통과 + owned_paths 밖 파일 미변경 |
| 실패 처리 | max_attempts(기본 3) 초과 시 `blocked` + Orchestrator에 에스컬레이션 |
| 금지 | owned_paths 외 수정, 새 dependency 추가(→ needs_decision), 스키마 변경(→ needs_decision) |

#### Research Agent

| 항목 | 내용 |
|---|---|
| 트리거 | Task kind=research, 다른 Agent의 `ask_research` 호출 |
| 도구 | web, fs(read), package registry 조회 |
| 산출물 | 리포트(Issue 코멘트 또는 `docs/research/*.md`), 라이브러리 비교표, 라이선스·보안 이슈 |
| 제약 | 출처 명시 필수. 코드 수정 금지 |

#### Test Agent

| 항목 | 내용 |
|---|---|
| 트리거 | PR opened/updated, CI 결과 수신, Task kind=test |
| 도구 | shell(sandbox), git, github(checks, comments) |
| 절차 | CI 로그 파싱 → 실패 분류(코드 결함 / 테스트 결함 / 환경 / flaky) → 원인 가설 → `fix_from_test` Task 생성 또는 테스트 보강 |
| 산출물 | 실패 분석 코멘트, 새 Task, 커버리지 리포트 |
| 금지 | 프로덕션 코드 수정 (테스트 코드만) |

#### Review Agent

| 항목 | 내용 |
|---|---|
| 트리거 | PR ready_for_review |
| 체크리스트 | 정확성, 보안(secrets, injection, authz), 컨벤션, ADR 위반, 테스트 존재, diff 범위(owned_paths) |
| 산출물 | GitHub Review(approve / request_changes) + 인라인 코멘트, risk tier 재판정 제안 |
| 결과 | approve & T0/T1 → 자동 머지 큐 / request_changes → `fix_from_review` Task / T2+ → Decision(pr_merge) |
| 금지 | 자기 코드 리뷰 (작성 Agent ≠ 리뷰 Agent 강제) |

#### 선택적 Agent

- **Security Agent**: dependency 감사, secret 스캔, authz 변경 감지 → T3 강제 상향
- **DevOps Agent**: CI 설정, 배포 스크립트 — 배포 실행은 항상 Decision
- **Database Agent**: 마이그레이션 작성, 롤백 스크립트 필수 동반

### 5.3 Agent 메모리

| 계층 | 저장 위치 | 내용 | 수명 |
|---|---|---|---|
| Run 컨텍스트 | 워커 메모리 | 현재 Task 대화·툴 결과 | Run |
| Role 노트 | State Store (`agent_memory`) | "이 프로젝트에서 이 역할이 배운 것" (컨벤션, 함정) | Project |
| 프로젝트 지식 | repo `docs/` + `CONTEXT.md` | 아키텍처, ADR, 결정 이력 | 영구, git 관리 |

컨텍스트 조립 순서: 시스템 프롬프트(role) → policy 요약 → CONTEXT.md → Role 노트 → Task spec → 관련 Issue/Discussion 요약 → 관련 파일. 토큰 예산 초과 시 뒤에서부터 요약.

---

## 6. 워크플로 및 상태 머신

### 6.1 Task 상태 머신

```
                 ┌──────────┐
                 │  draft   │  (Orchestrator 생성, Issue 미발행)
                 └────┬─────┘
                      ▼
                 ┌──────────┐   depends_on 미해소
        ┌────────│  ready   │◄────────────┐
        │        └────┬─────┘             │
        │             ▼ scheduler 배정    │
        │        ┌──────────┐             │
        │        │ assigned │             │
        │        └────┬─────┘             │
        │             ▼                   │
        │        ┌──────────┐  needs_decision   ┌───────────────────┐
        │        │ running  │─────────────────►│ awaiting_decision │
        │        └──┬───┬───┘                   └────────┬──────────┘
        │  fail     │   │ done                           │ approved → running
        │  (attempt │   ▼                                │ rejected → cancelled
        │   < max)  │ ┌───────────┐                      │ changes  → ready (spec 갱신)
        └───────────┘ │ in_review │ (PR 단계)
                      └──┬────┬───┘
     request_changes     │    │ approved+merged
     → fix Task 생성     │    ▼
                         │ ┌──────────┐
                         └►│   done   │
                           └──────────┘
   fail (attempt ≥ max) ──► blocked ──► Orchestrator 에스컬레이션 ──► 인간 or 재분해
   assigned ──fail (워커 기동 실패)──► ready (attempt < max) / blocked (attempt ≥ max)
   blocked ──(사람 승인 / task.retried)──► ready
   any ──► cancelled (인간 취소 / Goal 취소 / 예산 초과) — 이벤트 task.cancelled
```

### 6.2 Decision 상태 머신

```
open ──(agent votes 수집)──► open(voted)
   ──(human responses ≥ quorum, all approve)──► approved
   ──(any reject from required role)──► rejected
   ──(any request_change)──► changes_requested ──(agent 수정 후 재제출)──► open (new revision)
   ──(expires_at 경과)──► expired ──(escalation_policy)──► 알림 재발송 / 상위 승인자 / 자동 reject
```

- Decision은 **revision** 을 가진다. changes_requested 후 재제출은 같은 Discussion에 새 코멘트 + revision 번호.
- `approved` 는 quorum 조건(수 + 역할)이 **모두** 충족될 때만. 부분 승인은 open 유지.

### 6.3 PR 상태 흐름

```
draft ──(Coding 완료)──► ready ──(CI)──┬─ pass ──► agent_review ──┬─ approve ─┬─ T0/T1 ─► auto_merge ─► merged
                                        │                          │           └─ T2/T3 ─► decision(pr_merge) ─► merged / closed
                                        │                          └─ request_changes ─► fix Task ─► (재push) ─► ready
                                        └─ fail ──► Test Agent 분석 ──► fix Task ─► (재push) ─► ready
```

머지 정책: squash merge, 커밋 메시지에 `Task #<issue> / Run <id>` 트레일러 삽입.

---

## 7. GitHub 매핑 규약

### 7.1 GitHub App 권한 (최소)

| 권한 | 수준 | 용도 |
|---|---|---|
| Contents | write | branch, commit |
| Issues | write | Task |
| Pull requests | write | PR, review |
| Discussions | write | Decision, Plan |
| Checks | write | Agent 검사 결과 게시 |
| Metadata | read | — |
| Workflows | read | CI 결과 조회 (workflow 파일 수정은 T2) |
| Webhooks | — | issues, issue_comment, pull_request, pull_request_review, check_suite, discussion, discussion_comment, push |

### 7.2 라벨 스키마

| 라벨 | 의미 |
|---|---|
| `ai:task` | 플랫폼이 관리하는 Issue (사람이 직접 만든 Issue와 구분) |
| `role:coding` `role:test` `role:review` `role:research` `role:architect` | 담당 역할 |
| `tier:T0` ~ `tier:T3` | risk tier |
| `status:ready` `status:running` `status:blocked` `status:awaiting-decision` | 상태 미러 |
| `kind:feature` `kind:bugfix` `kind:fix-from-test` `kind:fix-from-review` … | Task kind |
| `epic:<n>` | 소속 Epic (Milestone과 중복이지만 검색 편의) |
| `human:override` | 인간이 수동 개입한 Task — Agent 재할당 금지 |

### 7.3 네이밍

- Branch: `ai/<epic-slug>/<issue-number>-<task-slug>` (예: `ai/caching/84-redis-cache`)
- PR 제목: `[T-84] Implement Redis cache` — 본문 상단에 자동 생성 블록:

```
<!-- ai-platform:meta task=84 run=01J... agent=backend-2 tier=T2 -->
### Summary
### Changes
### Tests
### Decisions referenced
- Discussion #42 (approved)
### Checklist
- [ ] owned_paths only
- [ ] no new dependency
```

- Discussion 카테고리: `Plans`, `Proposals`, `Reports`
- ADR: `docs/adr/NNNN-<slug>.md` (MADR 형식)
- 플랫폼 컨텍스트 파일: `.ai-platform/autonomy.yaml`, `.ai-platform/CONTEXT.md`, `.ai-platform/conventions.md`

### 7.4 사람이 GitHub에서 직접 개입할 때

- Issue에 `human:override` 라벨 → Agent가 해당 Task 손 뗌
- PR에 사람이 코멘트 → Review Agent가 코멘트를 Task spec 보강으로 흡수
- 사람이 브랜치에 직접 push → 해당 Run 즉시 취소 후 새 Run이 최신 HEAD에서 재시작
- Discussion에 `/approve` `/reject` `/changes <내용>` 슬래시 코멘트 → Mission Control 없이도 승인 가능 (권한 검증은 GitHub 사용자 ↔ 플랫폼 멤버 매핑)

---

## 8. Policy Engine

### 8.1 Risk Tier 정의

| Tier | 의미 | 기본 처리 |
|---|---|---|
| T0 | 국소적, 되돌리기 쉬움 (테스트 추가, 주석, 작은 버그픽스, 포맷팅) | Agent 리뷰 후 자동 머지 |
| T1 | 기능 구현이지만 경계 내 (owned_paths 안, 공개 API 불변) | Agent 리뷰 + CI 후 자동 머지 |
| T2 | 경계를 넘음 (새 dependency, 스키마 변경, 공개 API 변경, CI/workflow 수정, 외부 서비스) | 인간 1인 승인 |
| T3 | 되돌리기 어렵거나 비용/보안 (아키텍처, 인증/인가, 프로덕션 배포, 비용 발생, 데이터 삭제) | 인간 다중 승인 (quorum) |

### 8.2 Tier 판정 절차 (3단 결합, 상향만 허용)

1. **정적 규칙** — diff 경로/내용 기반 결정적 룰
   - `package.json` / `pyproject.toml` / `go.mod` 등 의존성 파일 변경 → ≥T2
   - `migrations/`, `*.sql`, ORM 모델 → ≥T2
   - `auth/`, `security/`, `*.pem`, IAM 정의 → T3
   - `.github/workflows/`, `Dockerfile`, IaC → ≥T2
   - `protected_paths` 매칭 → T3
   - diff 500줄 초과 → +1 tier
2. **Agent 자기 신고** — Coding/Review Agent가 반환하는 `proposed_tier`
3. **Review Agent 판정** — 리뷰 시 재평가

최종 tier = max(1, 2, 3). 하향은 인간만 가능(`policy.tier_overridden` 이벤트, 사유 필수).

### 8.3 autonomy.yaml 스키마

```yaml
version: 1

autonomy:                # T0/T1로 취급할 작업 kind
  implementation: auto
  tests: auto
  bug_fix: auto
  refactoring: auto
  docs: auto

approval_required:       # 승인 quorum (수). roles로 역할 제한 가능
  dependency:    { count: 1 }
  database:      { count: 1, roles: [owner, approver] }
  api_contract:  { count: 1 }
  architecture:  { count: 2, roles: [owner] }
  security:      { count: 2 }
  production:    { count: 2, roles: [owner] }
  cost:          { count: 1 }

decision_sla:
  default_hours: 24
  on_expire: notify        # notify | auto_reject | escalate

budget:
  daily_usd: 30
  total_usd: 500
  warn_at: 0.8             # 80%에서 경고
  on_exceed: pause         # pause | continue_t0_only

concurrency:
  max_workers: 4
  max_coding_agents: 3

protected_paths:
  - "infra/prod/**"
  - "src/auth/**"

merge:
  strategy: squash
  require_ci: true
  require_agent_review: true

models:
  default: { provider: anthropic, model: claude-sonnet }
  architect: { provider: anthropic, model: claude-opus }
  review:    { provider: anthropic, model: claude-opus }
```

- 파일은 repo의 `.ai-platform/autonomy.yaml`에 두고, 변경 자체가 **T3** (정책 변경은 정책 밖에서 승인).
- Control Plane이 파싱·검증 후 버전 태그를 붙여 State Store에 저장. Run 시작 시점의 policy 버전을 Run에 고정한다.

### 8.4 Decision(Proposal) 구조화 포맷

Discussion 본문은 사람이 읽는 마크다운 + 기계가 읽는 프론트매터를 동시에 가진다.

```markdown
<!-- ai-platform:decision id=... type=dependency tier=T2 quorum=1 revision=1 -->
# Proposal #42 — Introduce Redis caching

## Why
- API p95 latency 820ms, 원인: 반복 DB 조회 (Research report #40)

## Options
| # | Option | Pros | Cons |
|---|---|---|---|
| A | Redis (권장) | 성숙, 팀 경험 | 인프라 추가 |
| B | in-process LRU | 인프라 無 | 멀티 인스턴스 불일치 |
| C | 하지 않음 | — | 목표 미달 |

## Recommendation
Option A

## Risk & Rollback
- 인프라 비용 +$X/월, 롤백: feature flag `cache.enabled=false`

## Agent Votes
- Architect: APPROVE — ...
- Backend #1: APPROVE — ...
- Security: REQUEST CHANGE — TLS 필수

## Affected Tasks
- #84, #85 (blocked until resolved)

---
Respond: `/approve` · `/reject <reason>` · `/changes <what>`
```

### 8.5 예산 게이트

- Run 시작 전 `Cost Meter`가 예상 비용(role별 이동평균)을 확인. 남은 예산 < 예상 → Run 보류.
- 일일 예산 80% → `budget.warning` 이벤트 + Mission Control 배너.
- 초과 → policy `on_exceed`에 따라 pause 또는 T0만 지속.
- 예산 상향은 Decision(type=cost, T2).

---

## 9. 자율 루프와 실패 복구

### 9.1 루프

```
Goal → Plan(승인) → Task Graph
   ┌────────────────────────────────────────────┐
   │ Schedule → Run → PR → Test → Review → Merge │
   │      ▲                │        │            │
   │      │   fail_from_test│ changes│            │
   │      └────── new Task ◄┴────────┘            │
   └────────────────────────────────────────────┘
   → Epic 완료 판정 → Goal AC 검증(Test Agent, e2e) → Release Decision(T3) → done
```

### 9.2 실패 분류와 대응

| 실패 유형 | 감지 | 대응 |
|---|---|---|
| 컴파일/테스트 실패 | CI, 로컬 테스트 | Test Agent 분석 → `fix_from_test` Task → 동일 Coding Agent 우선, 2회 실패 시 다른 Agent |
| 리뷰 거절 | Review Agent | `fix_from_review` Task, 리뷰 코멘트를 spec에 첨부 |
| Agent 자체 실패 (툴 오류, 컨텍스트 초과) | Run outcome=failed | attempt+1, 컨텍스트 축소 후 재시도 |
| 타임아웃 | Run 시간 초과(기본 45분) | 부분 커밋 보존(WIP branch), 재시도 시 이어서 |
| 의존성 순환 / 데드락 | Scheduler 그래프 검사 | Orchestrator 재분해 |
| 반복 실패 (attempt ≥ max) | Task blocked | Orchestrator가 Task 재분해 시도 → 실패 시 인간 에스컬레이션 (Decision type=blocked) |
| 정책 위반 시도 | Policy Engine | Run 즉시 중단, 이벤트 기록, Task를 awaiting_decision으로 |
| 예산 초과 | Cost Meter | 9.5 참조 |

### 9.3 무한 루프 방지

- Task별 `max_attempts` (기본 3), Epic별 fix Task 상한 (기본: 원 Task 수 × 2)
- 동일 오류 시그니처(스택트레이스 해시) 3회 반복 → 즉시 blocked
- Goal 전체 Run 수 상한, 시간 상한 → 초과 시 Orchestrator가 "진척 없음" 리포트 후 인간 결정 요청
- Orchestrator 재계획은 Goal당 N회(기본 5) 제한 — 그 이상은 Plan revision Decision

### 9.4 Goal 완료 판정

1. 모든 Epic done
2. Test Agent가 acceptance_criteria 각각에 대해 검증 리포트 작성 (자동 테스트 or 수동 검증 필요 표시)
3. Review Agent가 전체 diff 대상 최종 감사(보안, 의존성)
4. Orchestrator가 Release Decision(T3) 생성: 변경 요약, AC 매트릭스, 남은 이슈
5. 승인 → Goal done, 릴리즈 노트 Discussion(Reports) 게시

MVP 1 최소 판정 (ROADMAP P9.7): Scheduler가 Goal의 Task가 전부 done/cancelled이고 done ≥ 1이면
done Task가 있는 Epic마다 `epic.completed`, 이어서 `goal.completed`를 발행한다. 2~5단계는 MVP 4에서
이 판정 앞단에 들어간다.

### 9.5 인간 개입 지점 (모두 Mission Control + GitHub 양쪽에서 가능)

- Decision 응답
- Task 취소 / 재할당 / spec 수정 / `human:override`
- Agent 일시정지 / 종료
- 프로젝트 Pause / Resume / **Emergency Stop** (모든 Run kill, 진행 중 branch는 WIP 보존)
- Tier override (하향은 사유 필수)

---

## 10. 병렬 작업과 충돌 관리

### 10.1 파일 소유권 (owned_paths)

- Orchestrator가 Task 분해 시 각 Task에 `owned_paths` 부여 (glob). 동시 실행 Task 간 교집합이 없도록 스케줄링.
- 교집합이 불가피하면 `depends_on`으로 직렬화.
- Agent가 owned_paths 밖 파일을 수정하면 PR 체크 실패 → 다음 중 하나:
  - 필요 최소 변경이면 Review Agent가 `scope_expand` 제안 → Orchestrator 승인(자동, T1)
  - 아니면 별도 Task로 분리

### 10.2 브랜치 전략

- 모든 Task는 `default_branch`에서 분기. 장기 Epic은 `ai/epic/<slug>` 통합 브랜치 선택 가능(정책).
- 머지 전 자동 rebase. 충돌 시 Coding Agent가 conflict 해결 Run 수행(T1). 2회 실패 시 인간 에스컬레이션.
- 스택 PR은 v1 미지원 — 의존 Task는 선행 PR 머지 후 시작.

### 10.3 워커 격리

- Task당 컨테이너 1개 + git worktree. 네트워크는 기본 차단, 허용 목록(패키지 레지스트리, GitHub)만 개방.
- 파일시스템 쓰기는 worktree로 제한. Secrets는 워커에 주입하지 않음(CI에서만).
- Run 종료 시 컨테이너 폐기. 산출물은 branch push로만 남는다.

---

## 11. 관측성 · 비용 관리

### 11.0 데모 콘솔 (MVP 1 임시, Mission Control 아님)

MVP 1 제출·시연용으로 API 서버가 `GET /`에서 정적 1페이지를 서빙한다(Goal 생성, Plan 본문·승인/거절, Task·Issue·PR 링크, 이벤트 스트림). §11.1 Mission Control(MVP 5)이 생기면 제거한다. 승인은 §13의 `/approve`·`/reject` 경로를 그대로 쓴다. 데모 모드(`HITL_DEMO_MODE`)에서는 프로젝트 생성·취소를 관리 토큰으로 제한하고 요청 빈도를 제한한다(ROADMAP D-52).

### 11.1 Mission Control 화면 구성

| 화면 | 내용 |
|---|---|
| Project Overview | Goal 진행률, Epic 상태, 예산 게이지, 최근 이벤트 타임라인 |
| Agent Board | Agent별 상태(idle/working/waiting/blocked), 현재 Task, 최근 Run 결과, 일시정지/종료 버튼 |
| Task Graph | DAG 시각화, 병목/blocked 강조, 클릭 시 Issue |
| Decision Inbox | 대기 중 Decision, SLA 잔여 시간, Approve/Reject/Changes, Agent votes |
| PR Queue | 상태별 PR, CI 결과, 리뷰 상태, 자동머지 대기 |
| Run Viewer | Run 단위 툴 호출 트레이스, diff, 토큰/비용, 재실행 버튼 |
| Cost | Role별/일별 비용, 예측, 예산 조정(→ Decision) |
| Audit Log | 이벤트 전체, 필터, 해시 체인 검증 |

### 11.2 지표

- 완수율: Goal done / Goal started
- Human decisions per Goal (낮을수록 자율성↑, 단 tier 위반 0 유지)
- Task 1회 성공률, 평균 attempt
- Decision 응답 SLA 준수율
- PR 머지 리드타임 (open → merge)
- 비용 / merged PR, 비용 / Goal
- 롤백 건수 (머지 후 revert)

### 11.3 트레이싱

- OpenTelemetry: Run = trace, 툴 호출 = span. LLM 호출은 프롬프트 해시·토큰·latency 태깅.
- 로그는 object storage(MinIO/S3)에 Run 단위로, 메타는 PostgreSQL.

---

## 12. 보안 모델

| 영역 | 설계 |
|---|---|
| GitHub 인증 | GitHub App installation token (단기), 사용자 OAuth는 승인 권한 매핑에만. App은 public 1개, installation은 repo마다 — 연결 시 `GET /repos/{o}/{r}/installation`으로 찾아 `projects.installation_id`에 기록하고 그 프로젝트의 모든 GitHub 호출이 그 installation 토큰을 쓴다 (ROADMAP P9.8–P9.10). App 설치가 곧 repo 연결 권한 |
| 권한 분리 | 플랫폼 멤버 role(owner/approver/viewer) ↔ GitHub 사용자 매핑. 승인은 approver 이상 |
| Secrets | 워커에 미주입. Agent가 `.env`, 키 파일 읽기 시도 → 툴 레벨 차단 + 이벤트 |
| Prompt Injection | Issue/PR/웹 내용은 "데이터"로 태깅해 시스템 프롬프트와 분리. 외부 텍스트에 포함된 명령은 툴 호출 권한 없음. Research Agent 결과는 Review Agent가 재검증 |
| 코드 실행 | 샌드박스 컨테이너, 네트워크 허용 목록, 시간/메모리 제한 |
| Supply chain | 새 dependency는 T2 + Security 스캔(라이선스, 알려진 취약점) 통과 필수 |
| 감사 | 이벤트 해시 체인, 모든 Decision에 응답자 ID·시각·코멘트 |
| 공개 데모 | 데모 모드에서 쓰기 API는 관리 토큰(`X-Admin-Token`) 또는 데모 프로젝트 한정 + IP·프로젝트별 레이트리밋. 인증 없는 공개 노출은 데모 모드에서만 (ROADMAP D-52) |
| 데이터 | repo 코드는 워커 임시 저장만. LLM provider로 전송되는 컨텍스트 범위를 policy로 제한 가능 (`context_exclude` glob) |

---

## 13. API 설계 (요약)

```
GET    /                               # 데모 콘솔 정적 페이지 (§11.0, MVP 1 임시)
POST   /projects                       # repo 연결 (installation_id)
GET    /projects/{id}
DELETE /projects/{id}                  # 보관: 미완 Goal·Task 취소 + project.updated{archived} (D-54, 행·이벤트 유지)
PATCH  /projects/{id}/policy           # → Decision(T3) 생성
POST   /projects/{id}/pause | /resume | /emergency-stop

POST   /projects/{id}/goals            # Goal 생성 → planning 시작
GET    /projects/{id}/goals            # 목록 (데모 콘솔용)
GET    /projects/{id}/goals/{gid}      # 진행률, Epic, Task 요약, plan_markdown (D-53)
POST   /projects/{id}/goals/{gid}/approve | /reject   # Plan 승인 (D-51, 웹훅과 동일 경로)
POST   /projects/{id}/goals/{gid}/cancel

GET    /projects/{id}/tasks?status=&epic=
PATCH  /projects/{id}/tasks/{tid}      # spec 수정, 재할당, 취소, override

GET    /projects/{id}/decisions?status=open
POST   /projects/{id}/decisions/{did}/respond   { response: approve|reject|changes, comment }

GET    /projects/{id}/agents
POST   /projects/{id}/agents/{aid}/pause | /kill

GET    /projects/{id}/runs/{rid}       # 트레이스, 로그, 비용
POST   /projects/{id}/runs/{rid}/retry

GET    /projects/{id}/events?since=&type=   # 커서 페이지네이션
WS     /projects/{id}/stream           # 이벤트 실시간

POST   /webhooks/github                # GitHub → 이벤트 변환 (서명 검증)
```

- 모든 쓰기 API는 `Idempotency-Key` 헤더 지원.
- Decision 응답은 GitHub 슬래시 코멘트와 동일한 내부 핸들러를 탄다(진입점만 다름).

---

## 14. 기술 스택

| 계층 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.12 | 가장 숙련, LangGraph 생태계 |
| Agent 오케스트레이션 | LangGraph | 상태 머신·체크포인트·인간 개입(interrupt) 기본 지원 |
| LLM 추상화 | 자체 `ModelProvider` 인터페이스 (Anthropic / OpenAI / Gemini / vLLM 어댑터) | 모델 교체 원칙 |
| API | FastAPI + WebSocket | 비동기, 타입 |
| 워크플로/큐 | Redis Streams (이벤트) + 자체 Scheduler; 규모 커지면 Temporal 검토 | 초기 단순성 |
| State Store | PostgreSQL (+ Alembic) | 이벤트 테이블 + 투영 |
| 로그/아티팩트 | MinIO(S3 호환) | 온프렘 옵션 유지 |
| 워커 | Docker (rootless) + git worktree; 이후 gVisor/Firecracker | 격리 |
| GitHub | GitHub App + PyGithub / 직접 GraphQL (Discussions는 GraphQL 필수) | — |
| 프론트 | Next.js + TypeScript, 상태는 이벤트 스트림 구독 | Mission Control |
| 관측성 | OpenTelemetry → Grafana/Tempo, Prometheus | — |
| 설정 | `.ai-platform/autonomy.yaml` (pydantic 검증) | — |

---

## 15. MVP 로드맵

| 단계 | 범위 | 포함 컴포넌트 | 완료 기준(DoD) |
|---|---|---|---|
| **MVP 1** Repo → Goal → Issue → PR | 단일 Coding Agent, Orchestrator 최소(분해만), GitHub Adapter, Event Bus, State Store | Policy 없음(모두 draft PR, 인간 머지) | 샘플 repo에서 Goal 1개 → Issue 3개 이상 자동 생성 → PR 3개 생성, PR 중 2개 이상 사람 수정 없이 머지 가능 |
| **MVP 2** Multi-Agent | Architect / Test / Review 분리, 역할별 프롬프트·툴 allowlist, owned_paths, Task 상태 머신 | Review Agent가 request_changes → fix Task 자동 생성 | CI 실패 PR이 인간 개입 없이 2회 이내 자동 수정되어 통과 |
| **MVP 3** Human Approval | Policy Engine(정적 규칙 + 자기 신고), autonomy.yaml, Decision(Discussion), 슬래시 코멘트, Decision Inbox(최소 UI) | T2 이상 자동 감지 | 새 dependency 추가 Task가 자동으로 Decision 생성, 승인 후 재개; T0/T1 자동 머지 동작 |
| **MVP 4** Autonomous Loop | 실패 분류, 재시도/에스컬레이션, 무한루프 방지, 예산 게이트, Goal 완료 판정, Release Decision | Orchestrator 재계획 | 중간 규모 Goal(Task 15개+)을 8시간 이상 무인 실행, 인간 결정 5회 이하로 완료 |
| **MVP 5** Mission Control | 전체 대시보드, Agent Board, Task Graph, Run Viewer, Cost, Audit, Emergency Stop | Next.js | 팀 2인 이상이 동일 Project에서 quorum 2 승인 시나리오 완주 |

각 단계는 이전 단계의 이벤트 모델을 깨지 않는다 (이벤트 스키마는 MVP 1에서 확정, 이후 추가만).

### 15.1 MVP 1 상세 (착수용)

```
repo/
├─ control_plane/
│  ├─ api/            # FastAPI: projects, goals, webhooks
│  ├─ orchestrator/   # LangGraph graph: analyze → plan → decompose → emit_tasks
│  ├─ scheduler/      # ready 큐, 워커 슬롯
│  ├─ events/         # Redis Streams pub/sub, projection
│  └─ store/          # SQLAlchemy models, alembic
├─ agents/
│  ├─ base.py         # AgentInput/Output, 공통 루프
│  ├─ coding.py
│  └─ tools/          # git, fs, shell(sandbox), github
├─ github_adapter/    # App auth, issues/prs, webhook → event
├─ worker/            # Dockerfile, entrypoint (task_id 받아 Run 실행)
├─ mission_control/   # (MVP5) Next.js
└─ .ai-platform/      # 샘플 autonomy.yaml, CONTEXT.md 템플릿
```

MVP 1 Orchestrator 그래프:

```
analyze_repo → draft_plan → (human: plan approve via Discussion) → decompose → emit_issues → END
```

MVP 1 Coding Agent 루프 (LangGraph):

```
load_context → plan_changes → edit → run_tests ─┬─ pass → commit_push → open_pr → summarize → END
                                  ▲             └─ fail (attempt<3) ──┘
```

---

## 16. 리스크와 열린 질문

### 16.1 리스크

| 리스크 | 영향 | 완화 |
|---|---|---|
| Orchestrator 분해 품질이 낮으면 전체가 흔들림 | 높음 | 첫 Plan은 항상 인간 승인, Task spec 템플릿 강제, 재계획 상한 |
| GitHub API rate limit (특히 Discussions GraphQL) | 중간 | 어댑터 캐시, 배치, 웹훅 우선 |
| Agent 간 컨텍스트 불일치 (같은 코드베이스 다른 이해) | 중간 | CONTEXT.md 단일 소스, ADR 강제, Review Agent 위반 검사 |
| 비용 폭주 | 높음 | 예산 게이트, Run 예상 비용, T0-only 모드 |
| 인간 승인 병목 (Decision이 쌓임) | 중간 | SLA·에스컬레이션, tier 튜닝 가이드, Decision 묶음 승인 |
| Prompt injection via Issue/웹 | 높음 | 데이터/명령 분리, 툴 권한 최소화, Review 재검증 |
| "장시간 자율" 검증 어려움 | 중간 | 벤치마크 repo 셋 구축(작은 Goal 10개), 완수율 추적 |

### 16.2 열린 질문

1. Orchestrator를 하나의 Agent로 둘지, Planner / Assigner / Monitor 세 그래프로 쪼갤지
2. Decision의 "Agent 투표"를 얼마나 신뢰할지 — 인간에게 보여주는 참고 vs. quorum 일부로 산입
3. Epic 통합 브랜치 vs. 항상 default_branch 직접 — 장기 Goal에서 CI 안정성
4. 워커 격리 수준: Docker면 충분한가, 초기부터 Firecracker가 필요한가
5. Mission Control을 별도 웹앱으로 갈지, VS Code extension으로 먼저 갈지 (MVP 5 시점 재판단)
6. 벤치마크: SWE-bench류 단일 이슈 지표는 부적합. "Goal 완수율" 정의를 어떻게 표준화할지
7. 멀티 LLM 운영 시 Agent별 모델 라우팅 기준 (비용 vs. 품질) 을 policy에 노출할지

---

*다음 단계: 이 문서 리뷰 → 이벤트 스키마 확정 → MVP 1 착수*
