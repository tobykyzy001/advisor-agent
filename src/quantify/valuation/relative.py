"""相对估值：与行业/市场分位对比。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RelativeValuation:
    symbol: str
    pe_ttm: Optional[float]
    pe_band: list[float]      # [低估, 高估] 参考区间
    pe_percentile: Optional[float] = None  # 历史PE分位 0~1
    pb_ttm: Optional[float] = None
    fair_pe: Optional[float] = None
    verdict: str = ""         # 便宜 / 合理 / 偏贵 / 数据不足

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "pe_ttm": self.pe_ttm,
            "pe_band": self.pe_band,
            "pe_percentile": self.pe_percentile,
            "pb_ttm": self.pb_ttm,
            "fair_pe": self.fair_pe,
            "verdict": self.verdict,
        }


def rate_relative(
    symbol: str,
    pe: Optional[float],
    band: list[float],
    pe_percentile: Optional[float] = None,
) -> RelativeValuation:
    """依据 PE 落入参考区间的相对估值判断。"""
    verdict = "数据不足"
    if pe is not None and band:
        low, high = band
        if pe < low:
            verdict = "便宜"
        elif pe > high:
            verdict = "偏贵"
        else:
            verdict = "合理"
        if pe_percentile is not None:
            if pe_percentile < 0.3:
                verdict = "便宜"
            elif pe_percentile > 0.7:
                verdict = "偏贵"
    return RelativeValuation(
        symbol=symbol,
        pe_ttm=pe,
        pe_band=band,
        pe_percentile=pe_percentile,
        verdict=verdict,
    )
