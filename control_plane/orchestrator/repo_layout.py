"""P9.25: 하위 폴더 repo 감지 — 플래너·워커는 repo 루트를 프로젝트 루트로 쓴다.

라이브(2026-09-21, melpes/RhythmTasker)는 코드가 ``RhythmTasker/`` 안에 있어 Task 경로와 테스트가
전부 어긋났다. 하위 폴더 지원(project_root)은 설계 §10.3 결정 대기 — 그전엔 Plan 전에 멈춘다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 프로젝트 루트 표식: manifest·진입점 파일, 소스·테스트 디렉토리
_MARKER_FILE_RE = re.compile(
    r"^(requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|Pipfile|package\.json|"
    r"go\.mod|Cargo\.toml|pom\.xml|build\.gradle|Makefile)$"
)
_MARKER_DIRS = frozenset({"src", "tests", "test"})
_CODE_EXT_RE = re.compile(r"\.(py|js|jsx|ts|tsx|mjs|go|rs|java|kt|rb|php|c|cc|cpp|h|cs|html)$")


def _is_project_dir(path: Path) -> bool:
    """폴더 바로 아래에 표식이 있는가 (루트든 하위 폴더든 같은 기준)."""
    for child in path.iterdir():
        name = child.name
        if child.is_dir() and name in _MARKER_DIRS:
            return True
        if child.is_file() and (_MARKER_FILE_RE.match(name) or _CODE_EXT_RE.search(name)):
            return True
    return False


def _has_code(path: Path) -> bool:
    """폴더 아래 어느 깊이든 표식이 있는가 (P9.27 — Casks/a/*.rb 같은 깊은 코드)."""
    for _dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if any(d in _MARKER_DIRS for d in dirnames):
            return True
        if any(_MARKER_FILE_RE.match(f) or _CODE_EXT_RE.search(f) for f in filenames):
            return True
    return False


def nested_project_root(repo: Path) -> str | None:
    """루트에 표식이 없고 코드를 가진 최상위 폴더가 정확히 하나면 그 이름, 아니면 None.

    후보가 둘 이상(backend/ + frontend/)이거나 루트에 코드가 있으면 막지 않는다 — 오탐 방지.
    P9.27: 형제 폴더의 깊은 코드도 센다 (homebrew-cask의 cmd/ 오탐)."""
    if not repo.is_dir() or _is_project_dir(repo):  # Dry 경로가 없을 수 있다
        return None
    folders = [
        d
        for d in sorted(repo.iterdir())
        if d.is_dir() and not d.name.startswith(".") and d.name != "docs"
    ]
    candidates = [d.name for d in folders if _has_code(d)]
    if len(candidates) != 1 or not _is_project_dir(repo / candidates[0]):
        return None
    return candidates[0]
