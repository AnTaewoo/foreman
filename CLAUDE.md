# CLAUDE.md — HITL Multi-Agent Dev Platform

이 파일은 Claude Code가 이 저장소에서 작업할 때 항상 읽는 프로젝트 컨텍스트다.

## 프로젝트 한 줄 정의

GitHub Repository + Goal을 입력하면 역할별 AI Agent 팀이 Issue/PR로 협업해 프로젝트를 완수하고, 위험한 결정만 인간이 승인하는 플랫폼.

## 설계 문서

- `docs/design.md` — 상세 설계 (섹션 번호로 참조: "설계 §6.1 Task 상태 머신")
- `ROADMAP.md` — 실행 계획. **세션 시작 시 항상 읽고 §0 실행 프로토콜대로 다음 Task를 고른다.** 상태 보드는 에이전트가 직접 갱신한다. 결정이 필요하면 ROADMAP §4를 먼저 본다.
- `docs/prompts.md` — 원래 세션별 프롬프트. ROADMAP과 겹치면 ROADMAP이 우선.
- 설계와 충돌하는 구현은 하지 않는다. 설계를 바꿔야 하면 먼저 `docs/design.md`를 수정하고 이유를 커밋 메시지에 남긴다.

## 현재 단계

**MVP 1**: Repo → Goal → Orchestrator → Issue → Coding Agent → PR
- Policy Engine / Decision / Multi-Agent는 아직 만들지 않는다 (인터페이스 자리만 남긴다)
- 단, 이벤트 스키마(설계 §4.2)는 MVP 1에서 확정 — 이후 단계에서 추가만 가능, 변경 불가

## 스택

- Python 3.12, `uv`로 패키지 관리, `pyproject.toml` 단일
- FastAPI, LangGraph, SQLAlchemy 2.x (async) + Alembic, Redis Streams, PyGithub + GraphQL(httpx)
- 테스트: pytest + pytest-asyncio, HTTP mock은 respx
- 린트/포맷: ruff, 타입: mypy(strict는 `control_plane/` 이하만)

## 디렉토리 (설계 §15.1)

```
control_plane/   api/ orchestrator/ scheduler/ events/ store/
agents/          base.py coding.py tools/
github_adapter/
worker/
tests/
docs/
.ai-platform/    autonomy.yaml CONTEXT.md (샘플)
```

## 코딩 규칙

- 모든 상태 변화는 이벤트로 발행한다. DB를 직접 갱신하는 코드는 `events/projection.py`에만 둔다.
- GitHub 쓰기 함수는 전부 멱등해야 한다 (같은 Task로 Issue 두 번 만들지 않기 — `ai-platform:meta` 마커로 존재 확인).
- Agent 툴은 `agents/tools/` 아래 하나의 파일 = 하나의 툴. 각 툴은 `allowlist` 검사 후 실행.
- shell 툴은 허용된 명령 프리픽스(`pytest`, `ruff`, `npm test`, `make` 등)만 실행. 임의 명령 실행 금지.
- 워커는 secrets를 받지 않는다. `.env` 읽기 시도는 툴 레벨에서 차단.
- LLM 호출은 `agents/llm/base.py`의 `ModelProvider` 인터페이스만 통해서. provider SDK를 직접 import하는 곳은 `agents/llm/anthropic.py` 같은 어댑터뿐 (ruff banned-api로 강제).
- 함수 시그니처와 pydantic 모델에 타입 필수. `Any` 사용 시 주석으로 이유.
- 한국어 주석 허용, 식별자·커밋 메시지는 영어.

## 작업 방식

- **이 저장소의 개발은 GitHub 브랜치/PR을 쓰지 않는다.** Task 단위 절차는 `ROADMAP.md` §0 (Red → Green → Refactor → Gate → `main` 커밋). 사람 검토는 Plan 사이의 PC(Prototype Check)에서 한다.
- MVP 1 개발 전 구간에서 실제 GitHub API 호출은 0이다. GitHub 어댑터는 respx mock + `DryRunGitHubClient`로만 검증하고, 실 연결은 ROADMAP §8 후속 세션에서.
- 새 기능은 먼저 `tests/`에 실패하는 테스트 → 구현 → 통과. 통합 테스트는 `tests/integration/` (Docker 필요, CI에선 skip 마커).
- 한 번의 작업 단위는 하나의 모듈/기능으로 제한. 여러 모듈을 한꺼번에 뜯지 않는다.
- 작업 끝에 `make check` (ruff + mypy + pytest) 통과를 확인하고 결과를 보고한다.
- 모르는 외부 API(GitHub GraphQL Discussions 등)는 추측하지 말고 문서를 확인하거나 사용자에게 묻는다.
- 설계 §16.2 열린 질문에 해당하는 결정은 임의로 내리지 않고 옵션을 제시한다.

## 절대 하지 말 것

- 실제 GitHub 저장소에 테스트로 Issue/PR을 생성하는 코드를 기본 활성화 상태로 두기 (항상 `DRY_RUN=true` 기본)
- 이벤트 스키마 필드 삭제/이름 변경
- **플랫폼이 관리하는 대상 repo**의 `main`/default 브랜치에 Agent 툴이 push 하기 — `agents/tools/git.py`가 거부한다. (이 저장소 자체의 개발 절차와는 무관 — ROADMAP §0)
- Gate(`make check` 등 Task가 정한 명령) 통과 전 `main` 커밋
- ROADMAP §4에 없는 설계 결정을 임의로 내리기 — 옵션을 §6에 적고 멈춘다
