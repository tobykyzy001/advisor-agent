"""市场概览与情绪/趋势的轻量分析（研究型，不做预测）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketContext:
    benchmark: str = "沪深300(000300)"
    benchmark_change_pct: Optional[float] = None
    risk_free_rate: Optional[float] = None
    equity_risk_premium: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "benchmark_change_pct": self.benchmark_change_pct,
            "risk_free_rate": self.risk_free_rate,
            "equity_risk_premium": self.equity_risk_premium,
            "note": self.note,
        }


def build_market_context(settings) -> MarketContext:
    """从估值配置构建市场参数上下文。"""
    v = settings.valuation
    return MarketContext(
        benchmark=settings.data.asharpe_index,
        risk_free_rate=v.risk_free_rate,
        equity_risk_premium=v.equity_risk_premium,
        note="当前为研究型市场概览，未接入实时大盘数据。",
    )
