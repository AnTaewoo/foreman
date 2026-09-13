<!--
근거 (edit 재시도; PC-4/PC-5 2026-09-13):
- 실패의 대부분이 모델이 쓴 테스트의 잘못된 기대값(공유 store id, 존재하지 않는 메서드)인데, 모델은
  같은 테스트를 다시 내거나 멀쩡한 코드를 고쳤다 → 먼저 "테스트가 틀렸나, 코드가 틀렸나"를 진단하게 한다.
- 저장소의 기존 테스트가 깨졌으면 그것이 진실이다(기존 동작을 바꾸지 말 것).
-->
## Previous attempt {attempt} failed. Test output:
```
{test_output}
```
First diagnose, then fix:
1. If a failing test is a test you wrote, check what it feeds and expects: (a) if the output is
   empty or ignores the data the test created, the test is filling an object the code never reads
   (e.g. a new `UserStore()` or `global store` in the test instead of the module-level `store`
   the app uses) — use the same object the code uses: import it from its module; (b) shared state keeps values across tests — clear it at the start of the
   test or assert only what the spec states. Fix the test, do not bend working code to it.
2. If a test that already existed in the repository fails, your code change broke existing
   behavior — restore it; existing tests are the source of truth.
3. If the code is wrong (NameError, wrong return value), fix the code.
Change only what the diagnosis requires and return every changed file in full.
