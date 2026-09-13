"""워커 이벤트 발행: DB 없이 Redis ``events:{project_id}``에 XADD (D-26 — 서명·DB append는 ingest).

스트림 필드는 ``events.bus.stream_fields``와 같은 형식(id/type/seq/canonical/signature)이지만 워커는
sqlalchemy를 끌어오지 않도록 여기서 다시 만든다. ``FilePublisher``는 테스트/디버그용 JSON-lines.
"""

from __future__ import annotations

import json
from pathlib import Path

from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from control_plane.events.schema import Event, canonical_json


def stream_key(project_id: str) -> str:
    return f"events:{project_id}"


def stream_fields_for(event: Event) -> dict[FieldT, EncodableT]:
    return {
        "id": event.id,
        "type": event.type.value,
        "seq": "",
        "canonical": canonical_json(event),
        "signature": event.signature or "",
    }


class RedisPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def __call__(self, event: Event) -> Event:
        await self._redis.xadd(stream_key(event.project_id), stream_fields_for(event))
        return event


class FilePublisher:
    """한 줄에 이벤트 하나: ``{"stream": ..., **stream_fields}``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def __call__(self, event: Event) -> Event:
        record = {"stream": stream_key(event.project_id), **stream_fields_for(event)}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return event
