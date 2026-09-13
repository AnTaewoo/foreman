"""비용 계산 (D-39): cost_usd = tokens × 설정 단가(USD per 1M tokens). 단가표를 코드에 박지 않는다.

- control plane: ``HITL_LLM_PRICE_IN_PER_MTOK`` / ``HITL_LLM_PRICE_OUT_PER_MTOK`` (기본 0 → cost 0)
- 워커: ``WORKER_LLM_PRICE_IN_PER_MTOK`` / ``WORKER_LLM_PRICE_OUT_PER_MTOK``
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

ENV_IN = "WORKER_LLM_PRICE_IN_PER_MTOK"
ENV_OUT = "WORKER_LLM_PRICE_OUT_PER_MTOK"


@dataclass(frozen=True)
class Prices:
    in_per_mtok: float = 0.0
    out_per_mtok: float = 0.0

    @property
    def is_set(self) -> bool:
        return self.in_per_mtok > 0 or self.out_per_mtok > 0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Prices:
        return cls(float(env.get(ENV_IN, 0) or 0), float(env.get(ENV_OUT, 0) or 0))

    def to_env(self) -> dict[str, str]:
        return {ENV_IN: str(self.in_per_mtok), ENV_OUT: str(self.out_per_mtok)}


def estimate_cost(tokens_in: int, tokens_out: int, prices: Prices) -> float:
    """USD, 소수 6자리 반올림."""
    return round(
        tokens_in / 1_000_000 * prices.in_per_mtok + tokens_out / 1_000_000 * prices.out_per_mtok, 6
    )


def format_cost(cost_usd: float, prices: Prices) -> str:
    return f"${cost_usd:.2f}" if prices.is_set else "$0.00 (단가 미설정)"
