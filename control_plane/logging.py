"""structlog 설정. `HITL_LOG_FORMAT`이 json이면 JSON, 아니면 콘솔 렌더러."""

from __future__ import annotations

import logging
import sys

import structlog

from control_plane.config import Settings

# structlog 호출에서 `event=`는 예약 키워드다. 웹훅 종류 등은 `gh_event=`처럼 다른 이름을 쓴다.


def configure_logging(settings: Settings) -> None:
    """stdlib logging 레벨과 structlog 프로세서 체인을 설정한다. 여러 번 호출해도 안전."""
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s", force=True)

    renderer: structlog.types.Processor
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
