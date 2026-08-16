"""估值主流程：把多方法估值结果汇总为一份报告。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from quantify.config import Settings
from quantify.data.schema import StockBundle
from quantify.valuation.dcf import (
    discount_rate,
    dividend_discount_model,
    earnings_based_value,
)
from quantify.valuation.metrics import Metrics, compute_metrics, roe_stability
from quantify.valuation.relative import RelativeValuation, rate_relative


@dataclass
class ValuationReport:
    symbol: str
    metrics: Metrics
    relative: RelativeValuation
    # 多方法合理价格估计
    price_by_dcf: Optional[float] = None
    price_by_target_pe: Optional[float] = None
    roe_stability_cv: Optional[float] = None
    summary: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "metrics": self.metrics.to_dict(),
            "relative": self.relative.to_dict(),
            "price_by_dcf": round(self.price_by_dcf, 2) if self.price_by_dcf else None,
            "price_by_target_pe": (
                round(self.price_by_target_pe, 2) if self.price_by_target_pe else None
            ),
            "roe_stability_cv": self.roe_stability_cv,
            "summary": self.summary,
            "tags": self.tags,
        }


def _recommend_target_pe(settings: Settings, m: Metrics) -> Optional[float]:
    """依据质量(ROE)在参考区间内给出目标PE。高ROE溢价给偏高PE。"""
    band = settings.valuation.pe_band
    if not band:
        return None
    low, high = band
    if m.roe_median is None:
        return (low + high) / 2
    if m.roe_median >= 25:
        return high
    if m.roe_median >= 15:
        return (low + high) / 2
    return low


def valuate(bundle: StockBundle, settings: Settings) -> ValuationReport:
    """对单个标的做多方法估值并给结论。"""
    m = compute_metrics(bundle)
    band = settings.valuation.pe_band
    rel = rate_relative(bundle.symbol, m.pe_ttm, band)

    r = settings.valuation
    required_return = discount_rate(r.risk_free_rate, r.equity_risk_premium)

    price_dcf = None
    if bundle.dividend_history and len(bundle.dividend_history) >= 2:
        try:
            price_dcf = dividend_discount_model(
                bundle.dividend_history[-1], m.eps_growth_rate or 0.03, required_return
            )
        except ValueError:
            price_dcf = None

    target_pe = _recommend_target_pe(settings, m)
    price_target = None
    if target_pe and bundle.eps_history:
        price_target = earnings_based_value(bundle.eps_history[-1], target_pe)

    tags: list[str] = []
    summary_parts: list[str] = []
    if m.roe_median is not None and m.roe_median >= 15:
        tags.append("高ROE")
        summary_parts.append("盈利质量较高(ROE中位数 {:.0f}%)".format(m.roe_median))
    if m.peg is not None:
        tags.append("PEG {:.2f}".format(m.peg))
    if rel.verdict:
        tags.append(rel.verdict)
        summary_parts.append(f"相对估值：{rel.verdict}(PE {m.pe_ttm})")
    if m.dividend_yield and m.dividend_yield >= 0.03:
        tags.append("高股息")
        summary_parts.append("股息率 {:.1f}%".format(m.dividend_yield * 100))

    return ValuationReport(
        symbol=bundle.symbol,
        metrics=m,
        relative=rel,
        price_by_dcf=price_dcf,
        price_by_target_pe=price_target,
        roe_stability_cv=roe_stability(bundle.financials.roe_trend),
        summary="；".join(summary_parts) if summary_parts else "数据不足，难以给出明确结论",
        tags=tags,
    )
