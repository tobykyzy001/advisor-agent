"""估值选股：基于估值指标给标的打分并产出买卖信号。"""
from __future__ import annotations

from dataclasses import dataclass, field

from quantify.config import Settings
from quantify.data.schema import StockBundle
from quantify.valuation.core import ValuationReport, valuate
from quantify.valuation.metrics import Metrics


@dataclass
class ScreenItem:
    symbol: str
    name: str = ""
    score: float = 0.0             # 0~100
    signal: str = "观望"           # 买入 | 观望 | 卖出 | 规避
    reasons: list[str] = field(default_factory=list)
    report: ValuationReport | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "score": round(self.score, 1),
            "signal": self.signal,
            "reasons": self.reasons,
        }


def _score_metrics(m: Metrics) -> tuple[float, list[str]]:
    """基础估值/质量评分 0~100。"""
    score = 50.0
    reasons: list[str] = []
    pe, pb = m.pe_ttm, m.pb
    if pe is not None and pe > 0:
        if pe < 10:
            score += 12
            reasons.append("PE偏低")
        elif pe < 18:
            score += 6
            reasons.append("PE合理偏低")
        elif pe > 40:
            score -= 15
            reasons.append("PE偏高")
    if pb is not None and pb > 0 and pb < 2:
        score += 8
        reasons.append("PB偏低")
    if m.roe_median is not None:
        if m.roe_median >= 20:
            score += 15
            reasons.append(f"盈利质量高(ROE≈{m.roe_median:.0f}%)")
        elif m.roe_median >= 10:
            score += 6
        else:
            score -= 8
            reasons.append("ROE偏低")
    if m.peg is not None:
        if m.peg < 1:
            score += 8
            reasons.append("PEG<1 成长与估值匹配")
        elif m.peg > 1.5:
            score -= 6
            reasons.append("PEG偏高")
    if m.dividend_yield and m.dividend_yield >= 0.03:
        score += 6
        reasons.append("高股息提供安全垫")
    return max(0.0, min(100.0, score)), reasons


def _to_signal(score: float, verdict: str) -> str:
    if score >= 75:
        return "买入"
    if verdict == "便宜" and score >= 65:
        return "买入"
    if score >= 55:
        return "关注"
    if verdict == "偏贵" or score < 35:
        return "卖出/规避"
    return "观望"


def screen_one(bundle: StockBundle, settings: Settings) -> ScreenItem:
    """对单个标的做估值评分与信号生成。"""
    report = valuate(bundle, settings)
    score, reasons = _score_metrics(report.metrics)
    signal = _to_signal(score, report.relative.verdict)
    return ScreenItem(
        symbol=bundle.symbol,
        name=bundle.quote.name,
        score=score,
        signal=signal,
        reasons=reasons,
        report=report,
    )


def screen_many(bundles: list[StockBundle], settings: Settings) -> list[ScreenItem]:
    return sorted(
        (screen_one(b, settings) for b in bundles),
        key=lambda x: x.score,
        reverse=True,
    )
