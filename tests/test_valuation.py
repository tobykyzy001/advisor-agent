"""估值指标单元测试。"""
import pytest

from quantify.data.schema import Financials, Quote, StockBundle
from quantify.valuation.metrics import cagr, compute_metrics
from quantify.valuation.relative import rate_relative


def _bundle(**over) -> StockBundle:
    quote = Quote(symbol="600519", name="测试", price=100.0, market_cap=1000.0, **over)
    fin = Financials(symbol="600519", roe_pct=20.0, roe_trend=[18, 19, 20, 21, 22])
    return StockBundle(quote=quote, financials=fin)


def test_cagr_positive():
    assert cagr([2.0, 2.5, 3.0]) == pytest.approx((3.0 / 2.0) ** 0.5 - 1, rel=1e-6)


def test_cagr_negative_start():
    assert cagr([-2.0, 1.0]) is None


def test_compute_metrics_peg():
    b = _bundle(pe_ttm=20.0)
    b.eps_history = [2.0, 2.2, 2.4]  # 年化约 9.5%
    m = compute_metrics(b)
    assert m.roe_median == 20.0
    assert m.peg is not None and m.peg > 1


def test_rate_relative():
    r = rate_relative("x", pe=8.0, band=[10.0, 25.0])
    assert r.verdict == "便宜"
    r2 = rate_relative("x", pe=30.0, band=[10.0, 25.0])
    assert r2.verdict == "偏贵"
