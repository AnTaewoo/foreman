"""P3.2 — orchestrator/context.py: RepoSummary from a local path (red a~f). D-11."""

from __future__ import annotations

from pathlib import Path

import pytest

from control_plane.orchestrator.context import RepoSummary, build_summary, render_summary

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
def summary() -> RepoSummary:
    return build_summary(SAMPLE)


# (a) 요약 필드 — framework는 선언된 의존성으로만 (description의 "FastAPI" 무시)
def test_summary_fields(summary: RepoSummary) -> None:
    assert summary.root == SAMPLE
    assert summary.language == "python"
    assert summary.framework == "flask"
    assert summary.test_runner == "pytest"
    assert summary.readme_head.startswith("# sample-app")
    assert "pyproject.toml" in summary.config_files
    assert summary.config_files["pyproject.toml"].startswith("[project]")
    assert set(summary.docs) >= {"docs/ARCHITECTURE.md", ".ai-platform/CONTEXT.md"}
    assert summary.docs[".ai-platform/CONTEXT.md"].startswith("# CONTEXT.md")


def test_framework_from_dependencies_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndescription="uses django words"\ndependencies=["fastapi"]\n'
    )
    assert build_summary(tmp_path).framework == "fastapi"
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["requests"]\n')
    assert build_summary(tmp_path).framework is None


# (b) 트리는 depth 2까지
def test_tree_depth_limit(summary: RepoSummary) -> None:
    assert "src/app/main.py" in summary.tree
    assert "src/app/deep" in summary.tree  # depth 2 디렉토리 이름은 보인다
    assert not any("deeper" in p or "hidden.py" in p for p in summary.tree)


# (c) 본문은 README·설정·.ai-platform·docs만 — src/** 내용 없음 (X.2: 심볼 "이름"만 색인에 나온다)
def test_bodies_exclude_source(summary: RepoSummary) -> None:
    rendered = render_summary(summary)
    before_symbols = rendered.split("### Symbols")[0]
    assert (
        "def greet" not in before_symbols and "UserStore" not in before_symbols.split("## Docs")[0]
    )
    assert 'return f"hello' not in rendered and "self._users" not in rendered  # 본문 없음
    assert all(not k.startswith("src/") for k in summary.config_files)
    assert all(not k.startswith("src/") for k in summary.docs)


# (d) 제외 디렉토리
def test_excluded_dirs(summary: RepoSummary) -> None:
    for junk in (".venv", "node_modules", "dot_git_stub", ".git"):
        assert not any(p == junk or p.startswith(junk + "/") for p in summary.tree), junk


# (e) URL → NotImplementedError (D-11)
@pytest.mark.parametrize("url", ["https://github.com/org/repo", "git@github.com:org/repo.git"])
def test_url_not_implemented(url: str) -> None:
    with pytest.raises(NotImplementedError, match="GitHub tree API"):
        build_summary(url)


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_summary(tmp_path / "nope")


# (f) render_summary
def test_render_summary(summary: RepoSummary) -> None:
    md = render_summary(summary)
    assert md.startswith("## Repository")
    for section in ("### Tree", "### README", "### Config", "### Docs"):
        assert section in md
    assert "language: python" in md and "framework: flask" in md and "test_runner: pytest" in md


def test_non_python_repo(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","dependencies":{"express":"4"},"scripts":{"test":"jest"}}'
    )
    (tmp_path / "index.js").write_text("console.log(1)")
    s = build_summary(tmp_path)
    assert s.language == "javascript" and s.framework == "express" and s.test_runner == "jest"
    assert s.readme_head == ""


# X.2 (입력부터): 요약에 공개 심볼 색인 — 본문 없이 이름만. 계획자가 기존 심볼을 다시 만들지 않게
def test_symbol_index(summary: RepoSummary, tmp_path: Path) -> None:
    assert "src/app/models.py" in summary.symbols
    models = summary.symbols["src/app/models.py"]
    assert any(s.startswith("class User") for s in models)
    assert any(s.startswith("class UserStore") and "add" in s and "all" in s for s in models)
    assert any(s == "def greet" for s in summary.symbols["src/app/main.py"])
    assert "route GET /users" in summary.symbols["src/app/main.py"]  # 중첩된 @app.get도 (X.2 3차)
    assert any(s.startswith("def test_") for s in summary.symbols["tests/test_main.py"])
    md = render_summary(summary)
    assert "### Symbols" in md and "class UserStore" in md
    assert "return" not in md.split("### Symbols")[1]  # 본문은 없다
    # 파이썬이 아니거나 문법 오류인 파일은 조용히 건너뛴다
    (tmp_path / "bad.py").write_text("def (broken")
    (tmp_path / "ok.py").write_text("class A:\n    def m(self): ...\n")
    s = build_summary(tmp_path)
    assert "bad.py" not in s.symbols and s.symbols["ok.py"] == ("class A: m",)
