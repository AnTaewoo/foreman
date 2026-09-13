<!--
근거 (edit 노드 응답 형식; PC-4 2026-09-13):
- JSON 문자열 안의 코드는 7B 모델이 `"""`·`@dataclass`·빈 줄을 잃는다 → 파일 블록 텍스트 (parse_edit_plan).
- 마커를 `###`로 쓰거나 END FILE을 빼먹는 경우가 있어 파서가 느슨하게 받지만, 형식은 여기서 못 박는다.
-->
Write every file you create or change in full. Copy every unchanged line verbatim from the file
shown above, including decorators such as @dataclass, docstrings and blank lines; add only what
the task needs. Existing tests must keep passing.

Respond with plain text in exactly this format (no JSON, no extra prose):
=== MESSAGE: <one-line commit message> ===
=== FILE: <repo-relative path> ===
<complete file content, exactly as it should be saved>
=== END FILE ===
Repeat the FILE/END FILE pair for every file you create or change.
