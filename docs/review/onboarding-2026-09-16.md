# Foreman — 온보딩 점검 (fresh clone + README 그대로 따라가기)

> 사용자가 2026-09-16 커밋 `25ea1d5`(README 초판) 기준으로 외부 점검 결과를 전달. P8.6의 근거. 항목 번호는 ROADMAP·§6에서 그대로 참조한다.

## 초기 상태에서 막히는 순서

1. **[Blocker]** `cp .env.example .env` 하면 즉시 아무것도 안 된다. README는 "값은 비워도 기본값으로 동작"이라 하지만, 빈 값이 빈 문자열로 읽혀 `Settings()`가 검증 오류 8건으로 실패한다. `make migrate`와 e2e 모두 여기서 죽고, `.env`를 지우면 e2e `--fake`는 통과한다. 수정 후보: config의 `env_ignore_empty=True` 또는 `.env.example`을 주석 예시로.
2. **[Blocker]** 워커 이미지 빌드 안내가 없다. 기본 런처가 docker이고 이미지 `foreman-worker:dev`를 요구하는데 README에 `docker build`가 없고, compose의 worker 서비스는 profile이라 `make docker-up`이 빌드하지 않는다. 첫 Task 배정에서 실패한다.
3. **[Blocker]** README가 미구현 P8 기능을 현재 동작처럼 설명한다. "죽은 워커 정리(P8)"와 `docker run --user <uid>`가 적혀 있지만 ROADMAP P8.1~P8.6은 전부 todo이고 코드에 `--user`가 없다. §3을 따라가면 워커가 push 권한 오류로 죽고 Task가 영구 고착된다(3차 점검에서 재현).
4. **[High]** `make docker-up`이 "선언만"인 MinIO의 healthy까지 요구한다. 9000 포트가 잡혀 있으면 전체가 실패하고, 대체 포트 예시는 runbook에만 있다.
5. **[High]** §3 curl 순서대로 치면 `POST /goals`가 404 "project not found". projection 반영까지 수 초~수십 초, control plane이 없으면 영원히 404. 지연 안내가 없다.
6. **[High]** Plan 승인 방법이 "테스트 코드를 읽어라". fixture 경로, HMAC 서명, `repository.full_name`이 프로젝트 repo 문자열과 정확히 같아야 하는 것, 작성자가 members의 owner여야 하는 것을 전부 스스로 알아내야 한다. 대안 `pc6_via_api.py`는 `--secret`이 필수인데 README 1단계 `.env`에는 시크릿이 비어 있다.
7. **[Medium]** `make run-api`의 `--reload`가 워커 실행마다 API를 재시작한다. 기본 `HITL_REPO_ROOT=./repos`가 프로젝트 폴더 안이라 워커의 파일 쓰기가 리로드를 유발하고, `repos/`가 gitignore에도 없다.
8. **[Medium]** 존재하지 않는 repo 경로도 201. README 예제의 `/path/to/repo`를 그대로 넣으면 성공 응답이 오고 오류는 한참 뒤 워커에서만 난다.

자잘한 것 7건: `X-User-Id` 헤더의 의미, 목록 API 부재, `.python-version` 없음, Ollama 안내 부족 등.

## 통과한 것

fresh clone에서 `uv sync`, `.env` 없이 migrate, e2e `--fake`, `make lint`, `/health`, `POST /projects`는 정상. Goal 예제의 title만 있는 형식도 스키마상 유효.

## 처리 (P8.6, 2026-09-16)

| # | 처리 |
|---|---|
| 1 | `Settings(env_ignore_empty=True)` — 빈 값은 미설정. 테스트 `test_env_example_empty_values_fall_back_to_defaults` |
| 2 | `make worker-image` 타깃 + README 1단계 |
| 3 | P8.1~P8.5 구현 완료(D-43 `--user` 호스트 uid, D-44 reaper) 후 README 재작성 |
| 4 | MinIO는 compose `profiles: ["storage"]`(기본 미기동), `Settings`에서 `minio_*` 제거 |
| 5 | `GET /projects/{id}`·`POST /goals`는 `project.created` 이벤트로 존재 확인(D-46); README에 읽기 지연 안내 |
| 6 | `POST /projects/{id}/goals/{gid}/approve` · `/reject`(D-51) — README 3단계 |
| 7 | `run-api`는 `API_RELOAD=1`일 때만 reload; `repos/` gitignore (D-48) |
| 8 | `POST /projects` repo 검증 400 (D-51) |
| 자잘 | README에 `X-User-Id` 의미, `GET /projects`, `.python-version`, Ollama 모델 안내; `fake`는 설정값에서 제거(테스트 전용 `get_provider(fake=True)`) |
