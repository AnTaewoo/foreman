"""fs 툴: worktree 안에서만 read / write / list. secrets 읽기·쓰기 금지, write는 owned_paths 안에서만 (§12)."""

from __future__ import annotations

from agents.tools.base import ToolContext, ToolDenied, guarded, is_secret_path

EXCLUDED = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}


class FsTool:
    name = "fs"

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx

    async def read(self, path: str) -> str:
        async with guarded(self._ctx, "fs.read", {"path": path}):
            target = self._ctx.resolve(path)
            rel = target.relative_to(self._ctx.worktree).as_posix()
            if is_secret_path(rel):
                raise ToolDenied("secret", rel)
            if not target.is_file():
                raise FileNotFoundError(rel)
            return target.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        async with guarded(self._ctx, "fs.write", {"path": path, "bytes": len(content)}):
            target = self._ctx.resolve(path)
            rel = target.relative_to(self._ctx.worktree).as_posix()
            if is_secret_path(rel):
                raise ToolDenied("secret", rel)
            if not self._ctx.is_owned(rel):
                raise ToolDenied("owned_paths", rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    async def list(self, path: str = ".") -> list[str]:
        async with guarded(self._ctx, "fs.list", {"path": path}):
            base = self._ctx.resolve(path)
            if not base.is_dir():
                raise FileNotFoundError(path)
            out: list[str] = []
            for p in sorted(base.rglob("*")):
                rel_parts = p.relative_to(self._ctx.worktree).parts
                if any(part in EXCLUDED for part in rel_parts):
                    continue
                if p.is_file():
                    out.append(p.relative_to(self._ctx.worktree).as_posix())
            return out
