"""情绪指标研究模块的单元测试（纯离线，全部用合成数据）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantify.analysis import sentiment as st
from quantify.data import sentiment_data as sd
from quantify.data.sentiment_data import RawSentimentData


def _raw(n: int = 300, start: str = "2020-01-01") -> RawSentimentData:
    idx = pd.bdate_range(start, periods=n)
    adj = pd.Series(100.0 * (1.001 ** np.arange(n)), index=idx)
    ret = adj.pct_change().fillna(0.0)
    hs300 = pd.DataFrame({"pe_ttm": 15.0, "turnover_rate": 1.0}, index=idx)
    margin = pd.DataFrame({"rzye": 1e12, "rzmre": 5e10}, index=idx)
    return RawSentimentData(
        benchmark_code="510300.SH",
        benchmark=adj,
        benchmark_ret=ret,
        cash_ret=pd.Series(0.0, index=idx),
        hs300=hs300,
        margin=margin,
        north=pd.Series(100.0, index=idx),
        market_amount=pd.Series(5000.0, index=idx),
        market_turnover=pd.Series(1.0, index=idx),
        accounts=pd.Series(100.0, index=idx),
        meta={"benchmark": "510300.SH", "start": str(idx[0].date()),
              "end": str(idx[-1].date()), "rows": n},
    )


def test_rolling_percentile_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    pct = st.rolling_percentile(s, window=4, min_periods=1)
    assert pct.iloc[-1] == 100.0
    assert pct.iloc[0] == 100.0
    down = st.rolling_percentile(pd.Series([4.0, 3.0, 2.0, 1.0]), 4, 1)
    assert down.iloc[-1] == 25.0


def test_build_indicators_columns_and_composite():
    ind = st.build_indicators(_raw(), window=20, min_periods=10)
    for c in st.COMPOSITE_COLS:
        assert c in ind.columns and f"{c}_pct" in ind.columns
    assert ind["heat"].notna().sum() > 0
    # 恒定指标 → 分位 100、复合热度 100、仓位降到最低档
    assert ind["heat"].dropna().iloc[-1] == 100.0
    pos = st.position_from_heat(ind["heat"])
    assert pos.dropna().iloc[-1] == st.DEFAULT_POSITIONS[-1]


def test_build_indicators_requires_three_inputs():
    raw = _raw()
    raw.north = pd.Series(np.nan, index=raw.benchmark.index)
    raw.margin = pd.DataFrame(np.nan, index=raw.benchmark.index,
                              columns=["rzye", "rzmre"])
    raw.market_amount = pd.Series(np.nan, index=raw.benchmark.index)
    raw.market_turnover = pd.Series(np.nan, index=raw.benchmark.index)
    raw.accounts = pd.Series(np.nan, index=raw.benchmark.index)
    ind = st.build_indicators(raw, window=20, min_periods=10)
    assert ind["heat"].notna().sum() == 0      # 只剩 PE / 换手率两个指标，不给复合热度


def test_forward_return_and_maxdd():
    idx = pd.bdate_range("2020-01-01", periods=4)
    adj = pd.Series([100.0, 110.0, 90.0, 120.0], index=idx)
    fwd = st.forward_return(adj, horizon=2)
    # t=0：t+1=110 进场，t+2=90 出场
    assert abs(fwd.iloc[0] - (90.0 / 110.0 - 1.0)) < 1e-12
    dd = st.forward_max_drawdown(adj, horizon=2)
    assert abs(dd.iloc[0] - (90.0 / 110.0 - 1.0)) < 1e-12
    assert np.isnan(dd.iloc[-1])


def test_event_study_buckets():
    raw = _raw(400)
    ind = st.build_indicators(raw, window=60, min_periods=30)
    ind["pe_ttm_pct"] = np.linspace(0.0, 100.0, len(ind))
    ev = st.event_study(ind[["pe_ttm_pct"]], raw.benchmark, horizons=(20,))
    assert set(ev["bucket"]) == {"0-20", "20-40", "40-60", "60-80", "80-100"}
    assert ev["n"].sum() > 300


def test_position_ladder():
    heat = pd.Series([50.0, 85.0, 95.0, 99.0, np.nan])
    pos = st.position_from_heat(heat)
    assert list(pos) == [1.0, 0.8, 0.5, 0.2, 1.0]


def test_position_dynamic_linear():
    heat = pd.Series([0.0, 50.0, 75.0, 100.0, np.nan])
    pos = st.position_from_heat_dynamic(heat)
    assert list(pos[:4]) == pytest.approx([1.0, 1.0, 0.6, 0.2])
    assert pos.iloc[4] == 1.0          # 数据不足按满仓
    assert (pos.dropna() >= 0.2 - 1e-9).all() and (pos.dropna() <= 1.0).all()


def test_backtest_execution_lag_and_cost():
    idx = pd.bdate_range("2020-01-01", periods=100)
    adj = pd.Series(100.0 * (1.01 ** np.arange(100)), index=idx)
    cash = pd.Series(0.0, index=idx)
    flat = pd.Series(0.0, index=idx)
    r = st.backtest(adj, cash, flat, cost_bps=0.0)
    assert r.stats["exposure"] == 0.0
    assert abs(r.stats["cagr"]) < 1e-12

    pos = pd.Series(1.0, index=idx)
    pos.iloc[50:] = 0.0
    r2 = st.backtest(adj, cash, pos, cost_bps=0.0)
    # T 日信号、T+1 执行、T+2 起算：t=52 的持仓才变为 0
    assert r2.daily["position"].iloc[51] == 1.0
    assert r2.daily["position"].iloc[52] == 0.0


def test_perf_stats_mdd():
    idx = pd.bdate_range("2020-01-01", periods=4)
    ret = pd.Series([0.1, -0.5, 0.2, 0.1], index=idx)
    s = st.perf_stats(ret)
    assert s["mdd"] < -0.4


def test_judge_pass_and_fail():
    bench = {"cagr": 0.10, "mdd": -0.40, "calmar": 0.25}
    good = {"cagr": 0.095, "mdd": -0.20, "calmar": 0.48}
    ok, _ = st.judge(good, bench)
    assert ok is True
    bad = {"cagr": 0.05, "mdd": -0.38, "calmar": 0.13}
    ok2, _ = st.judge(bad, bench)
    assert ok2 is False


def test_market_amount_uses_market_aggregate_and_datetime_index():
    raw = pd.DataFrame({
        "trade_date": ["20260105", "20260105", "20260105"],
        "ts_code": ["SH_A", "SH_MARKET", "SZ_MARKET"],
        "amount": [100.0, 120.0, 80.0],
    })
    s = sd._market_amount(raw)
    assert isinstance(s.index, pd.DatetimeIndex)
    assert list(s) == [200.0]          # 沪市优先 SH_MARKET，再加深市


def test_market_turnover_is_amount_weighted():
    raw = pd.DataFrame({
        "trade_date": ["20260105", "20260105"],
        "ts_code": ["SH_A", "SZ_MARKET"],
        "amount": [100.0, 300.0],
        "tr": [1.0, 3.0],
    })
    out = sd._market_series(raw)
    assert out["turnover"].iloc[0] == 2.5      # (1×100 + 3×300) / 400
    assert out["amount"].iloc[0] == 400.0


def test_single_indicator_backtests():
    raw = _raw(400)
    ind = st.build_indicators(raw, window=60, min_periods=30)
    single = st.single_indicator_backtests(ind, raw.benchmark, raw.cash_ret)
    assert set(single) >= set(st.COMPOSITE_COLS)
    r = single["pe_ttm"]
    assert 0.0 <= r.stats["exposure"] <= 1.0
    assert r.bench_stats["cagr"] > 0
    assert 0.0 <= r.dyn_stats["exposure"] <= 1.0


def test_bs_position_hysteresis():
    heat = pd.Series([50.0, 10.0, 50.0, 90.0, 50.0])
    pos = st.bs_position(heat, 20.0, 80.0, "contrarian")
    assert list(pos) == [0.0, 1.0, 1.0, 0.0, 0.0]   # 中间区间维持原状态
    trend = st.bs_position(heat, 60.0, 40.0, "trend")
    assert list(trend) == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_bs_backtests_table():
    raw = _raw(400)
    ind = st.build_indicators(raw, window=60, min_periods=30)
    bs = st.bs_backtests(ind, raw.benchmark, raw.cash_ret)
    assert not bs.empty
    assert set(bs["rule"]) == {label for label, *_ in st.BS_RULES}
    assert "heat" in set(bs["indicator"])
    assert (bs["exposure"].between(0, 1)).all()


def test_render_report_smoke():
    raw = _raw(300)
    ind = st.build_indicators(raw, window=20, min_periods=10)
    ev = st.event_study(ind, raw.benchmark, horizons=(20,))
    pos = st.position_from_heat_dynamic(ind["heat"])
    res = st.backtest(raw.benchmark, raw.cash_ret, pos)
    single = st.single_indicator_backtests(ind, raw.benchmark, raw.cash_ret)
    bs = st.bs_backtests(ind, raw.benchmark, raw.cash_ret)
    yearly = pd.DataFrame({"买入持有": st.yearly_returns(raw.benchmark_ret),
                           "情绪择时(动态)": st.yearly_returns(res.daily["strategy_ret"])})
    md = st.render_report(raw.meta, ind, ev, {"情绪择时(动态)": res}, single, bs,
                          yearly, False, ["测试"], "2020-01-01")
    assert "# 情绪指标择时研究报告" in md
    assert "单指标择时回测" in md
    assert "二元 BS 回测" in md
    assert "预注册判定" in md
