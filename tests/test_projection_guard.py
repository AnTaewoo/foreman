"""P1.5 (j) — AST 가드: control_plane/ 아래 DB 갱신은 projection.py뿐 (D-24 예외 적용)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTATORS = {"add", "add_all", "merge", "delete"}
ALLOWED: dict[str, set[str]] = {
    "control_plane/events/projection.py": {"*"},
    "control_plane/events/chain.py": {"add"},  # D-24: insert(Event)/insert(ToolCall)
    "control_plane/events/outbox.py": {"execute:update"},  # D-24: published_at/stream_id 부기
}


def _is_session_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return "session" in node.id.lower() or node.id in {"s", "sess", "db"}
    if isinstance(node, ast.Attribute):
        return "session" in node.attr.lower()
    return False


def _violations(path: Path, root: Path = ROOT) -> list[str]:
    rel = str(path.relative_to(root))
    allowed = ALLOWED.get(rel, set())
    if "*" in allowed:
        return []
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not _is_session_receiver(node.func.value):
            continue
        name = node.func.attr
        if name in MUTATORS and name not in allowed:
            out.append(f"{rel}:{node.lineno} session.{name}(")
        if name == "execute" and node.args:
            arg = node.args[0]
            # update(...).where(...).values(...) 체인의 뿌리 Call 이름
            root = arg
            while isinstance(root, ast.Call) and isinstance(root.func, ast.Attribute):
                root = root.func.value
            if isinstance(root, ast.Call) and isinstance(root.func, ast.Name):
                if (
                    root.func.id in {"update", "delete"}
                    and f"execute:{root.func.id}" not in allowed
                ):
                    out.append(f"{rel}:{node.lineno} session.execute({root.func.id}(")
    return out


def test_only_projection_mutates_db() -> None:
    offenders: list[str] = []
    for p in (ROOT / "control_plane").rglob("*.py"):
        offenders.extend(_violations(p))
    assert offenders == [], offenders


def test_guard_catches_a_fake_violation(tmp_path: Path) -> None:
    bad = tmp_path / "x.py"
    bad.write_text(
        "async def f(session, other):\n"
        "    session.add(1)\n"
        "    other.add(2)\n"  # session류 수신자가 아니면 무시
        "    await session.execute(update(T).where(T.id == 1).values(a=1))\n"
        "    await session.execute(select(T))\n"
    )
    hits = _violations(bad, root=tmp_path)
    assert hits == ["x.py:2 session.add(", "x.py:4 session.execute(update("]
