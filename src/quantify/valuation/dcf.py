"""DCF（现金流折现）与分红折现估值。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DCFResult:
    fair_price: float
    fair_value: float          # 合理市值
    implied_growth: float      # 当前价隐含的永续增长/折现率差
    method: str

    def to_dict(self) -> dict:
        return {
            "fair_price": round(self.fair_price, 2),
            "fair_value": round(self.fair_value, 2),
            "implied_growth": self.implied_growth,
            "method": self.method,
        }


def dividend_discount_model(
    base_dividend: float,
    growth: float,
    required_return: float,
) -> float:
    """戈登增长模型：V = D1 / (r - g)。若 r<=g 视为理论上限无限，返回 None 语义由调用方处理。"""
    if required_return <= growth:
        raise ValueError("required_return 必须大于增速 growth")
    return base_dividend * (1 + growth) / (required_return - growth)


def earnings_based_value(
    next_eps: float,
    target_pe: float,
) -> float:
    """基于目标PE的静态估值：V = EPS_t+1 * target_pe。"""
    return next_eps * target_pe


def discount_rate(risk_free: float, equity_risk_premium: float, beta: float = 1.0) -> float:
    """CAPM 折现率。"""
    return risk_free + beta * equity_risk_premium
