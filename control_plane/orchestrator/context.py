"""RepoSummary — Orchestrator의 analyze_repo 입력 (설계 §5.2, §5.3). 로컬 경로만 (D-11).

읽는 것: README 머리, 설정 파일, `docs/**.md`, `.ai-platform/*`, 트리(depth ≤ 2).
읽지 않는 것: 소스 본문. 프레임워크는 **선언된 의존성**으로만 판정한다(설명 문구 무시).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = frozenset(
    {".git", "dot_git_stub", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
     ".ruff_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode"}
)  # fmt: skip
CONFIG_FILES = (
    "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt", "package.json",
    "tsconfig.json", "go.mod", "Cargo.toml", "Makefile", "docker-compose.yml", "Dockerfile",
    ".ai-platform/autonomy.yaml",
)  # fmt: skip
MAX_TREE_DEPTH = 2
SYMBOL_MAX_FILES = 60  # 심볼 색인에 넣을 .py 파일 수 상한 (X.2)
SYMBOL_MAX_PER_FILE = 40
README_HEAD_CHARS = 2000
CONFIG_MAX_CHARS = 4000
DOC_MAX_CHARS = 4000

# 의존성 이름 → 프레임워크 (판정 우선순위 순)
PY_FRAMEWORKS = ("fastapi", "django", "flask", "starlette", "litestar", "aiohttp", "tornado")
JS_FRAMEWORKS = ("next", "nuxt", "express", "fastify", "koa", "nestjs", "react", "vue", "svelte")


@dataclass(frozen=True)
class RepoSummary:
    root: Path
    language: str | None
    framework: str | None
    test_runner: str | None
    tree: tuple[str, ...]  # depth ≤ 2 디렉토리의 내용까지(상대 경로), 디렉토리는 이름만
    readme_head: str
    config_files: dict[str, str] = field(default_factory=dict)  # 상대 경로 → 본문(잘림)
    docs: dict[str, str] = field(default_factory=dict)  # docs/**.md, .ai-platform/*.md → 본문(잘림)
    # X.2: 공개 심볼 색인 (본문 없이 이름만) — 계획자가 이미 있는 심볼을 다시 만들지 않게
    symbols: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git@", "ssh://"))


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit]


def _walk_tree(root: Path) -> tuple[str, ...]:
    out: list[str] = []

    def visit(dir_: Path, depth: int) -> None:
        try:
            entries = sorted(dir_.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for p in entries:
            if p.name in EXCLUDED_DIRS:
                continue
            rel = p.relative_to(root).as_posix()
            out.append(rel)
            if p.is_dir() and depth + 1 <= MAX_TREE_DEPTH:  # depth-2 디렉토리의 내용까지 나열
                visit(p, depth + 1)

    visit(root, 0)
    return tuple(out)


def _python_deps(pyproject: Path) -> list[str]:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    names: list[str] = []
    project = data.get("project", {})
    for dep in project.get("dependencies", []):
        names.append(_dep_name(str(dep)))
    for group in (data.get("dependency-groups") or {}).values():
        for dep in group:
            if isinstance(dep, str):
                names.append(_dep_name(dep))
    for extra in (project.get("optional-dependencies") or {}).values():
        for dep in extra:
            names.append(_dep_name(str(dep)))
    return names


def _dep_name(spec: str) -> str:
    name = spec.strip().lower()
    for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
        name = name.split(sep, 1)[0]
    return name.replace("_", "-")


def _detect(root: Path) -> tuple[str | None, str | None, str | None]:
    """(language, framework, test_runner)."""
    pyproject = root / "pyproject.toml"
    if (
        pyproject.is_file()
        or (root / "requirements.txt").is_file()
        or (root / "setup.py").is_file()
    ):
        deps = _python_deps(pyproject) if pyproject.is_file() else []
        if (root / "requirements.txt").is_file():
            deps += [
                _dep_name(line)
                for line in _read(root / "requirements.txt", CONFIG_MAX_CHARS).splitlines()
                if line.strip() and not line.startswith("#")
            ]
        framework = next((f for f in PY_FRAMEWORKS if f in deps), None)
        runner = "pytest" if "pytest" in deps or (root / "tests").is_dir() else None
        return "python", framework, runner
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        js_deps: dict[str, str] = {
            **data.get("dependencies", {}),
            **data.get("devDependencies", {}),
        }
        framework = next((f for f in JS_FRAMEWORKS if f in js_deps), None)
        test_script = str((data.get("scripts") or {}).get("test", ""))
        runner = next((r for r in ("jest", "vitest", "mocha") if r in test_script), None)
        language = "typescript" if (root / "tsconfig.json").is_file() else "javascript"
        return language, framework, runner
    if (root / "go.mod").is_file():
        return "go", None, "go test"
    if (root / "Cargo.toml").is_file():
        return "rust", None, "cargo test"
    return None, None, None


def _symbol_index(root: Path) -> dict[str, tuple[str, ...]]:
    """.py 파일의 최상위 class/def 이름(클래스는 메서드 이름까지). 문법 오류·비파이썬은 건너뛴다."""
    import ast

    out: dict[str, tuple[str, ...]] = {}
    files = sorted(
        p for p in root.rglob("*.py") if not any(part in EXCLUDED_DIRS for part in p.parts)
    )
    for path in files[:SYMBOL_MAX_FILES]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        names: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = [
                    n.name
                    for n in node.body
                    if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                    and not n.name.startswith("_")
                ]
                names.append(f"class {node.name}" + (f": {', '.join(methods)}" if methods else ""))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.append(f"def {node.name}")
        if names:
            out[path.relative_to(root).as_posix()] = tuple(names[:SYMBOL_MAX_PER_FILE])
    return out


def build_summary(path: str | Path) -> RepoSummary:
    """로컬 경로의 요약. URL은 MVP 1 이후 (GitHub tree API)."""
    if isinstance(path, str) and _is_url(path):
        raise NotImplementedError("GitHub tree API: MVP1 이후")
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    language, framework, runner = _detect(root)
    readme = next(
        (p for p in (root / "README.md", root / "README.rst", root / "README") if p.is_file()), None
    )
    config_files = {
        rel: _read(root / rel, CONFIG_MAX_CHARS) for rel in CONFIG_FILES if (root / rel).is_file()
    }
    docs: dict[str, str] = {}
    for base in (root / "docs", root / ".ai-platform"):
        if base.is_dir():
            for p in sorted(base.rglob("*.md")):
                if not any(part in EXCLUDED_DIRS for part in p.parts):
                    docs[p.relative_to(root).as_posix()] = _read(p, DOC_MAX_CHARS)
    return RepoSummary(
        root=root,
        language=language,
        framework=framework,
        test_runner=runner,
        tree=_walk_tree(root),
        readme_head=_read(readme, README_HEAD_CHARS) if readme else "",
        config_files=config_files,
        docs=docs,
        symbols=_symbol_index(root),
    )


def render_summary(summary: RepoSummary) -> str:
    """LLM 프롬프트에 넣는 마크다운."""
    lines = [
        "## Repository",
        f"- root: {summary.root.name}",
        f"- language: {summary.language or 'unknown'}",
        f"- framework: {summary.framework or 'none detected'}",
        f"- test_runner: {summary.test_runner or 'unknown'}",
        "",
        "### Tree (depth ≤ 2)",
        "```",
        *summary.tree,
        "```",
        "",
        "### README (head)",
        summary.readme_head or "(none)",
        "",
        "### Config",
    ]
    for name, body in summary.config_files.items():
        lines += [f"#### {name}", "```", body, "```"]
    lines += ["", "### Docs"]
    for name, body in summary.docs.items():
        lines += [f"#### {name}", body]
    lines += ["", "### Symbols (existing public names, no bodies — reuse, do not recreate)"]
    for name, syms in summary.symbols.items():
        lines.append(f"- {name}: " + "; ".join(syms))
    if not summary.symbols:
        lines.append("(none)")
    return "\n".join(lines)
