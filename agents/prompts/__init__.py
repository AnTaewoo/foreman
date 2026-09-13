"""Coding Agent 프롬프트 세트 (X.2): 파일 하나 = 프롬프트 하나, 상단 ``<!-- -->``에 근거.

``load_prompt(name, **vars)``는 주석을 떼고 ``{var}``를 치환한다 (orchestrator와 같은 규약).
프롬프트를 코드 문자열에 흩뿌리지 않고 여기 모아 두어, 튜닝은 항상 세트 단위로 검토·검증한다.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str, **variables: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("<!--"):
        end = stripped.index("-->") + len("-->")
        text = stripped[end:]
    text = text.strip()
    return text.format(**variables) if variables else text  # 변수 없으면 원문(중괄호 보존)
