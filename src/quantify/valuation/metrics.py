"""估值指标：基于行情与基本面的派生指标。"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from quantify.data.schema import StockBundle


@dataclass
class Metrics:
    """从 StockBundle 派生的估值指标集。"""

    symbol: str
    pe_ttm: Optional[float]
    pb: Optional[float]
    ps: Optional[float] = None
    dividend_yield: Optional[float] = None
    roe_pct: Optional[float] = None
    roe_median: Optional[float] = None       # 近5年ROE中位数
    peg: Optional[float] = None              # PEG = PE / 盈利增速
    eps_growth_rate: Optional[float] = None  # 近5年EPS复合增速(近似CAGR)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "ps": self.ps,
            "dividend_yield": self.dividend_yield,
            "roe_pct": self.roe_pct,
            "roe_median": self.roe_median,
            "peg": self.peg,
            "eps_growth_rate": self.eps_growth_rate,
        }


def cagr(values: list[float], years: int | None = None) -> Optional[float]:
    """近似年复合增速。values 为按年递增的序列(如EPS)，years 为段数。"""
    if len(values) < 2:
        return None
    n = years or (len(values) - 1)
    start, end = values[0], values[-1]
    if start <= 0:
        return None
    return (end / start) ** (1.0 / n) - 1.0


def compute_metrics(bundle: StockBundle) -> Metrics:
    """集成为可复用的估值指标集。"""
    q, f = bundle.quote, bundle.financials
    roe_median = (
        sorted(f.roe_trend)[len(f.roe_trend) // 2]
        if f.roe_trend
        else (f.roe_pct if f.roe_pct is not None else None)
    )
    growth = cagr(bundle.eps_history) if len(bundle.eps_history) >= 2 else None

    peg = None
    if q.pe_ttm is not None and growth:
        denom = growth * 100.0 if -1 < growth < 1 else growth
        if denom > 0:
            peg = q.pe_ttm / denom

    ps = None
    # ps 需要营收，schema 暂未携带；留空由扩展字段补充
    return Metrics(
        symbol=bundle.symbol,
        pe_ttm=q.pe_ttm,
        pb=q.pb,
        dividend_yield=q.dividend_yield,
        roe_pct=f.roe_pct,
        roe_median=roe_median,
        peg=peg,
        eps_growth_rate=growth,
    )


def roe_stability(roe_trend: list[float]) -> Optional[float]:
    """ROE 稳定性：变异系数(越小越稳定)，ROE 恒正的代表质量高。"""
    if len(roe_trend) < 2:
        return None
    m = mean(roe_trend)
    if m == 0:
        return None
    var = sum((x - m) ** 2 for x in roe_trend) / len(roe_trend)
    return (var ** 0.5) / abs(m)
