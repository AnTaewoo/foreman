"""``python -m control_plane`` — 상주 control plane (P6.1). SIGINT/SIGTERM에 정리."""

from __future__ import annotations

import asyncio
import signal
import sys

from control_plane.config import get_settings
from control_plane.logging import configure_logging
from control_plane.runtime import Runtime, build_runtime


async def serve(runtime: Runtime, stop: asyncio.Event) -> None:
    """start → stop 신호 대기 → stop. 테스트는 stop Event를 직접 set 한다."""
    await runtime.start()
    try:
        await stop.wait()
    finally:
        await runtime.stop()


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(runtime, stop)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
