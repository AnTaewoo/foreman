"""Task 의존 그래프 유틸 (설계 §3.2 Scheduler, §9.2 데드락 감지)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class SchedulerError(Exception):
    pass


def topo_order(deps: Mapping[str, Sequence[str]]) -> list[str]:
    """Kahn 정렬. 준비된 노드는 입력 순서 유지. 사이클이면 SchedulerError('cycle …')."""
    ids = list(deps)
    known = set(ids)
    indeg = {i: len([d for d in deps[i] if d in known]) for i in ids}
    ready = [i for i in ids if indeg[i] == 0]
    out: list[str] = []
    while ready:
        cur = ready.pop(0)
        out.append(cur)
        for i in ids:
            if i in out or i in ready:
                continue
            if cur in deps[i]:
                indeg[i] -= 1
            if indeg[i] == 0:
                ready.append(i)
    if len(out) != len(ids):
        raise SchedulerError(f"dependency cycle among tasks: {[i for i in ids if i not in out]}")
    return out
