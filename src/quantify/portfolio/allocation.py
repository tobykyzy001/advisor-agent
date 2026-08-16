"""仓位配置：依据信号与质量给出建议仓位，并做约束裁剪。"""
from __future__ import annotations

from dataclasses import dataclass, field

from quantify.analysis.screener import ScreenItem
from quantify.config import Settings


@dataclass
class Allocation:
    symbol: str
    name: str = ""
    signal: str = "观望"
    target_pct: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "name": self.name, "signal": self.signal,
                "target_pct": round(self.target_pct, 2), "reasons": self.reasons}


def _base_weight(signal: str) -> float:
    return {"买入": 0.15, "关注": 0.07, "观望": 0.0, "卖出/规避": 0.0}.get(signal, 0.0)


def suggest_allocations(
    items: list[ScreenItem],
    settings: Settings,
    total_capital: float = 1.0,
) -> list[Allocation]:
    """把信号映射为建议仓位(占组合比例)，并裁剪到单标的上限与总仓位约束。"""
    max_pos = settings.portfolio.max_position_pct
    allocs: list[Allocation] = []
    for it in items:
        w = _base_weight(it.signal)
        w = min(w, max_pos)
        reasons = [f"信号:{it.signal}(得分{it.score:.0f})"] + it.reasons[:2]
        allocs.append(
            Allocation(symbol=it.symbol, name=it.name, signal=it.signal,
                       target_pct=round(w, 3), reasons=reasons)
        )

    # 总仓位约束：若超出可投资限额(1-cash)则等比压缩
    investable = 1.0 - settings.portfolio.cash_buffer_min
    total = sum(a.target_pct for a in allocs)
    if total > investable and total > 0:
        scale = investable / total
        for a in allocs:
            a.target_pct = round(a.target_pct * scale, 3)
    return allocs
