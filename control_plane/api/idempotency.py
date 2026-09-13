"""`Idempotency-Key` 미들웨어 (설계 §13): 쓰기 요청의 응답을 키별로 메모리에 TTL 보관.

- 같은 키 + 같은 본문 → 저장된 응답을 그대로 재전송(핸들러 미실행, publish 0회)
- 같은 키 + 다른 본문 → 422
- 5xx 응답은 저장하지 않는다(재시도 허용). 프로세스 로컬 dict — 다중 프로세스는 후속(§8).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


@dataclass
class _Entry:
    digest: str
    expires: float
    status: int = 0
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)
    body: bytes = b""


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, *, ttl_s: float = 3600.0) -> None:
        self._app = app
        self._ttl = ttl_s
        self._store: dict[str, _Entry] = {}

    def _purge(self, now: float) -> None:
        for k in [k for k, v in self._store.items() if v.expires <= now]:
            del self._store[k]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in WRITE_METHODS:
            await self._app(scope, receive, send)
            return
        key = next(
            (v.decode() for k, v in scope.get("headers", []) if k == b"idempotency-key"), None
        )
        if not key:
            await self._app(scope, receive, send)
            return
        body = await _read_body(receive)
        digest = hashlib.sha256(body).hexdigest()
        now = time.monotonic()
        self._purge(now)
        hit = self._store.get(key)
        if hit is not None:
            if hit.digest != digest:
                await _send_json(
                    send, 422, {"detail": "Idempotency-Key reused with a different body"}
                )
                return
            await _send_raw(send, hit.status, hit.headers, hit.body)
            return

        entry = _Entry(digest=digest, expires=now + self._ttl)
        sent_request = False

        async def receive_replay() -> Message:
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        chunks: list[bytes] = []

        async def send_capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                entry.status = int(message["status"])
                entry.headers = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(bytes(message.get("body", b"")))
            await send(message)

        await self._app(scope, receive_replay, send_capture)
        entry.body = b"".join(chunks)
        if 0 < entry.status < 500:
            self._store[key] = entry


async def _read_body(receive: Receive) -> bytes:
    parts: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        parts.append(bytes(message.get("body", b"")))
        if not message.get("more_body", False):
            break
    return b"".join(parts)


async def _send_raw(
    send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes
) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    await _send_raw(send, status, headers, body)
