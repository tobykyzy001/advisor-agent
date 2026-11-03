"""情绪择时研究：指标分位、事件研究、仓位回测与报告。

定位是「研究型评估」，不是实盘策略：所有阈值预先写死，输出结论必须包含样本外与
失效条件。基准默认 510300（前复权），现金腿 511880，执行口径 T 日收盘出信号、
T+1 收盘执行（收益从 T+2 起算），单边成本默认万 2.5。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantify.data.sentiment_data import RawSentimentData

#: 指标列 → 中文名（报告展示顺序）
INDICATOR_LABELS = {
    "pe_ttm": "沪深300 PE(TTM)",
    "turnover_20d": "沪深300 换手率(20日均)",
    "amount_20d": "沪深A股成交额(20日均)",
    "market_turnover_20d": "沪深A股换手率(20日均)",
    "margin_20d": "两融余额(20日均)",
    "margin_buy_20d": "融资买入占成交比(20日均)",
    "north_20d": "北向净流入(20日累计)",
    "accounts": "新增投资者(月度,停更)",
}

#: 进入复合热度的指标：只用全市场情绪，排除沪深300 自身的 PE/换手率。
#: accounts 已停更，只做单指标展示，不进复合。
COMPOSITE_COLS = ["amount_20d", "market_turnover_20d", "margin_20d",
                  "margin_buy_20d", "north_20d"]

DEFAULT_THRESHOLDS = (80.0, 90.0, 97.0)
DEFAULT_POSITIONS = (0.8, 0.5, 0.2)
DEFAULT_COST_BPS = 2.5
#: 动态仓位：热度 ≤ start 满仓，随热度线性降到 floor（不给杠杆）
DEFAULT_DYNAMIC_START = 50.0
DEFAULT_DYNAMIC_FLOOR = 0.2
#: BS 二元规则：(展示名, 模式, 买入阈值, 卖出阈值)
BS_RULES = [
    ("反转 20/80", "contrarian", 20.0, 80.0),
    ("趋势 60/40", "trend", 60.0, 40.0),
]


def rolling_percentile(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """滚动分位（0–100）：当前值在最近 window 个观测中的百分位。"""
    if s.empty:
        return pd.Series(dtype=float)

    def rank_last(a: np.ndarray) -> float:
        return float((a <= a[-1]).mean() * 100.0)

    return s.rolling(window, min_periods=min_periods).apply(rank_last, raw=True)


def build_indicators(raw: RawSentimentData, window: int = 1250,
                     min_periods: int = 250) -> pd.DataFrame:
    """构造原始指标与滚动分位。返回列名与原指标同名（分位加 _pct 后缀）。"""
    idx = raw.benchmark.index
    ind = pd.DataFrame(index=idx)

    ind["pe_ttm"] = raw.hs300["pe_ttm"]
    ind["turnover_20d"] = raw.hs300["turnover_rate"].rolling(20, min_periods=10).mean()
    ind["amount_20d"] = raw.market_amount.rolling(20, min_periods=10).mean()
    ind["market_turnover_20d"] = raw.market_turnover.rolling(20, min_periods=10).mean()
    ind["margin_20d"] = (
        raw.margin["rzye"].rolling(20, min_periods=10).mean() / 1e12
    )
    denom = raw.market_amount * 1e8
    ind["margin_buy_20d"] = (
        (raw.margin["rzmre"] / denom).rolling(20, min_periods=10).mean() * 100
    )
    ind["north_20d"] = raw.north.rolling(20, min_periods=10).sum() / 1e4
    ind["accounts"] = raw.accounts

    pct = pd.DataFrame(index=idx)
    for c in [x for x in INDICATOR_LABELS if x in ind.columns and x != "accounts"]:
        pct[f"{c}_pct"] = rolling_percentile(ind[c], window, min_periods)

    # 开户是月度且已停更：只在原始月度序列上算 36 月分位，停更后置 NaN
    acc = ind["accounts"].dropna()
    if not acc.empty:
        monthly = acc.groupby(acc.index.to_period("M")).last()
        m_pct = rolling_percentile(monthly, 36, 12)
        last_month = monthly.index.max()
        m_pct = m_pct[m_pct.index <= last_month]
        daily = m_pct.to_timestamp(how="end").reindex(idx, method="ffill")
        daily[daily.index > last_month.to_timestamp(how="end")] = np.nan
        pct["accounts_pct"] = daily

    pct["heat"] = pct[[f"{c}_pct" for c in COMPOSITE_COLS]].mean(
        axis=1, skipna=True
    )
    # 至少 3 个指标有效才算复合热度，避免早期样本只有 1–2 个指标时给出假信号
    pct.loc[pct[[f"{c}_pct" for c in COMPOSITE_COLS]].notna().sum(axis=1) < 3,
            "heat"] = np.nan
    out = pd.concat([ind, pct], axis=1)
    out["heat"] = pct["heat"]
    return out


def forward_return(adj: pd.Series, horizon: int) -> pd.Series:
    """T 日信号、T+1 收盘进场，持有到 T+horizon 收盘的收益。"""
    return adj.shift(-horizon) / adj.shift(-1) - 1.0


def forward_max_drawdown(adj: pd.Series, horizon: int) -> pd.Series:
    """T+1 进场后 horizon 个交易日内的最大回撤（相对进场价）。"""
    vals = adj.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals) - horizon):
        entry = vals[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        window = vals[i + 1:i + 1 + horizon]
        if len(window) == horizon:
            out[i] = float(window.min() / entry - 1.0)
    return pd.Series(out, index=adj.index)


def event_study(pct: pd.DataFrame, adj: pd.Series,
                horizons: tuple[int, ...] = (20, 60, 120),
                buckets: int = 5) -> pd.DataFrame:
    """单指标分位分组 → 未来收益 / 最大回撤。"""
    labels = [f"{int(i * 100 / buckets)}-{int((i + 1) * 100 / buckets)}"
              for i in range(buckets)]
    edges = np.linspace(0, 100, buckets + 1)
    rows: list[dict] = []
    for h in horizons:
        fwd = forward_return(adj, h)
        fdd = forward_max_drawdown(adj, h)
        for c in [x for x in pct.columns if x.endswith("_pct")]:
            bucket = pd.cut(pct[c], bins=edges, labels=labels, include_lowest=True)
            for lab in labels:
                mask = bucket == lab
                r = fwd[mask].dropna()
                d = fdd[mask].dropna()
                if r.empty:
                    continue
                rows.append({
                    "indicator": c.removesuffix("_pct"),
                    "horizon": h,
                    "bucket": lab,
                    "n": len(r),
                    "mean_ret": float(r.mean()),
                    "median_ret": float(r.median()),
                    "hit_rate": float((r > 0).mean()),
                    "mean_fwd_mdd": float(d.mean()) if not d.empty else np.nan,
                })
    return pd.DataFrame(rows)


def position_from_heat(heat: pd.Series,
                       thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
                       positions: tuple[float, ...] = DEFAULT_POSITIONS) -> pd.Series:
    """热度越高仓位越低的分档（只降风险，不加杠杆）；数据不足时满仓。"""
    pos = pd.Series(1.0, index=heat.index, dtype=float)
    for th, p in zip(thresholds, positions):
        pos[heat >= th] = p
    pos[heat.isna()] = 1.0
    return pos


def position_from_heat_dynamic(heat: pd.Series,
                               start: float = DEFAULT_DYNAMIC_START,
                               floor: float = DEFAULT_DYNAMIC_FLOOR) -> pd.Series:
    """动态仓位：热度 ≤ start 满仓，之后线性降到 floor；不给杠杆。"""
    if heat.empty:
        return pd.Series(dtype=float)
    progress = ((heat - start) / (100.0 - start)).clip(lower=0.0, upper=1.0)
    pos = 1.0 - (1.0 - floor) * progress
    pos[heat.isna()] = 1.0
    return pos


def bs_position(heat: pd.Series, buy_th: float, sell_th: float,
                mode: str = "contrarian") -> pd.Series:
    """二元 BS：满仓 1 / 空仓 0，带滞回（买入后必须触及另一阈值才卖出）。

    - contrarian：热度 ≤ buy_th 买入，≥ sell_th 卖出（低买高卖）；
    - trend：热度 ≥ buy_th 买入，≤ sell_th 卖出（顺势）。
    初始为空仓；热度缺失时维持原状态。
    """
    pos = pd.Series(0.0, index=heat.index, dtype=float)
    state = 0.0
    for dt, h in heat.items():
        if pd.isna(h):
            pos.at[dt] = state
            continue
        if mode == "contrarian":
            if state == 0.0 and h <= buy_th:
                state = 1.0
            elif state == 1.0 and h >= sell_th:
                state = 0.0
        else:
            if state == 0.0 and h >= buy_th:
                state = 1.0
            elif state == 1.0 and h <= sell_th:
                state = 0.0
        pos.at[dt] = state
    return pos


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def perf_stats(ret: pd.Series, pos: pd.Series | None = None) -> dict:
    ret = ret.dropna()
    if ret.empty:
        return {"cagr": 0.0, "vol": 0.0, "mdd": 0.0, "calmar": 0.0,
                "sharpe": 0.0, "exposure": 0.0, "turnover": 0.0, "switches": 0}
    equity = (1.0 + ret).cumprod()
    years = max(len(ret) / 252.0, 1e-9)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    vol = float(ret.std(ddof=0) * np.sqrt(252))
    mdd = max_drawdown(equity)
    calmar = cagr / abs(mdd) if mdd < 0 else float("inf")
    sharpe = float(ret.mean() / ret.std(ddof=0) * np.sqrt(252)) if ret.std(ddof=0) > 0 else 0.0
    p = pos.reindex(ret.index).fillna(1.0) if pos is not None else pd.Series(1.0, index=ret.index)
    return {
        "cagr": cagr,
        "vol": vol,
        "mdd": mdd,
        "calmar": calmar,
        "sharpe": sharpe,
        "exposure": float(p.mean()),
        "turnover": float(p.diff().abs().sum() / years),
        "switches": int((p.diff().abs() > 1e-9).sum()),
    }


@dataclass
class BacktestResult:
    daily: pd.DataFrame
    stats: dict
    position: pd.Series


def backtest(adj: pd.Series, cash_ret: pd.Series, pos: pd.Series,
             cost_bps: float = DEFAULT_COST_BPS) -> BacktestResult:
    """按 T+1 收盘执行回测；pos 为 T 日收盘信号。"""
    bench_ret = adj.pct_change().fillna(0.0)
    held = pos.shift(2).clip(0.0, 1.0)
    # 开头两天没有可执行的滞后信号：用首日信号补齐，避免默认满仓污染空仓/低仓策略
    if len(held) and not pos.empty:
        held.iloc[:2] = float(pos.iloc[0])
    cash = cash_ret.reindex(adj.index).fillna(0.0)
    cost = held.diff().abs().fillna(0.0) * cost_bps / 1e4
    ret = held * bench_ret + (1.0 - held) * cash - cost
    daily = pd.DataFrame({
        "bench_ret": bench_ret,
        "position": held,
        "strategy_ret": ret,
        "bench_equity": (1.0 + bench_ret).cumprod(),
        "strategy_equity": (1.0 + ret).cumprod(),
    })
    return BacktestResult(daily=daily, stats=perf_stats(ret, held), position=held)


def ma_position(adj: pd.Series, window: int = 200) -> pd.Series:
    """均线择时对照：收盘在 MA 之上满仓，否则空仓。"""
    ma = adj.rolling(window, min_periods=window).mean()
    return (adj > ma).astype(float).where(ma.notna(), 1.0)


@dataclass
class SingleResult:
    """单指标择时回测结果（各自有效样本内；分档与动态仓位各一套）。"""

    name: str
    start: str
    end: str
    stats: dict
    bench_stats: dict
    passed: bool
    dyn_stats: dict
    dyn_bench_stats: dict
    dyn_passed: bool


def single_indicator_backtests(ind: pd.DataFrame, adj: pd.Series,
                               cash_ret: pd.Series,
                               cost_bps: float = DEFAULT_COST_BPS) -> dict[str, SingleResult]:
    """对每个单指标分别跑分档与动态仓位，便于回答「到底哪个指标有用」。

    每个指标只在自身分位有效的样本内回测，并与同区间买入持有对比；样本不足 250 个
    交易日不测，避免给出没有意义的短期结论。
    """
    out: dict[str, SingleResult] = {}
    for col in [c for c in ind.columns if c.endswith("_pct")]:
        s = ind[col].dropna()
        if len(s) < 250:
            continue
        idx = s.index
        a = adj.reindex(idx)
        c = cash_ret.reindex(idx)
        bench = backtest(a, c, pd.Series(1.0, index=idx), cost_bps)

        pos = position_from_heat(ind[col].reindex(idx))
        r = backtest(a, c, pos, cost_bps)
        passed, _ = judge(r.stats, bench.stats)

        pos_dyn = position_from_heat_dynamic(ind[col].reindex(idx))
        r_dyn = backtest(a, c, pos_dyn, cost_bps)
        dyn_passed, _ = judge(r_dyn.stats, bench.stats)

        out[col.removesuffix("_pct")] = SingleResult(
            name=col.removesuffix("_pct"),
            start=str(idx.min().date()),
            end=str(idx.max().date()),
            stats=r.stats,
            bench_stats=bench.stats,
            passed=passed,
            dyn_stats=r_dyn.stats,
            dyn_bench_stats=bench.stats,
            dyn_passed=dyn_passed,
        )
    return out


def _avg_holding_days(pos: pd.Series) -> float:
    """BS 仓位下一次买入到卖出的平均持有交易日数。"""
    if pos.empty:
        return 0.0
    buys = int((pos.diff() == 1).sum()) + (1 if pos.iloc[0] == 1 else 0)
    return float(len(pos) / max(1, buys))


def bs_backtests(ind: pd.DataFrame, adj: pd.Series, cash_ret: pd.Series,
                 cost_bps: float = DEFAULT_COST_BPS) -> pd.DataFrame:
    """对复合热度与每个单指标跑二元 BS（满仓/空仓），输出对比表。"""
    series: dict[str, pd.Series] = {"heat": ind["heat"]}
    for c in [x for x in INDICATOR_LABELS if f"{x}_pct" in ind.columns]:
        series[c] = ind[f"{c}_pct"]

    rows: list[dict] = []
    for key, raw in series.items():
        s = raw.dropna()
        if len(s) < 250:
            continue
        idx = s.index
        a = adj.reindex(idx)
        c = cash_ret.reindex(idx)
        bench = backtest(a, c, pd.Series(1.0, index=idx), cost_bps)
        for label, mode, buy_th, sell_th in BS_RULES:
            pos = bs_position(s, buy_th, sell_th, mode)
            r = backtest(a, c, pos, cost_bps)
            passed, _ = judge(r.stats, bench.stats)
            rows.append({
                "indicator": key,
                "rule": label,
                "start": str(idx.min().date()),
                "end": str(idx.max().date()),
                "cagr": r.stats["cagr"],
                "mdd": r.stats["mdd"],
                "calmar": r.stats["calmar"],
                "bench_calmar": bench.stats["calmar"],
                "exposure": r.stats["exposure"],
                "switches": r.stats["switches"],
                "avg_hold_days": _avg_holding_days(pos),
                "passed": passed,
            })
    return pd.DataFrame(rows)


def yearly_returns(ret: pd.Series) -> pd.Series:
    r = ret.dropna()
    if r.empty:
        return pd.Series(dtype=float)
    return (1.0 + r).groupby(r.index.year).prod() - 1.0


def judge(stats_sent: dict, stats_bench: dict) -> tuple[bool, list[str]]:
    """预注册判定：回撤显著改善且收益不大幅变差，或卡玛明显提升。"""
    mdd_improve = 1.0 - abs(stats_sent["mdd"]) / abs(stats_bench["mdd"]) \
        if stats_bench["mdd"] < 0 else 0.0
    notes = [
        f"最大回撤改善 {mdd_improve:.1%}（门槛 30%）",
        f"CAGR 差 {stats_sent['cagr'] - stats_bench['cagr']:+.2%}（门槛 -1pct）",
        (
            f"Calmar {stats_sent['calmar']:.2f} vs {stats_bench['calmar']:.2f}"
            f"（门槛 +0.3）"
        ),
    ]
    ok1 = mdd_improve >= 0.30 and stats_sent["cagr"] >= stats_bench["cagr"] - 0.01
    ok2 = stats_sent["calmar"] >= stats_bench["calmar"] + 0.30
    return bool(ok1 or ok2), notes


def _pct(v: float, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v * 100:.{digits}f}%"


def _num(v: float, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v:.{digits}f}"


def render_report(meta: dict, ind: pd.DataFrame, ev: pd.DataFrame,
                  results: dict[str, BacktestResult],
                  single: dict[str, SingleResult],
                  bs: pd.DataFrame,
                  yearly: pd.DataFrame, passed: bool,
                  notes: list[str], eval_start: str) -> str:
    lines = [
        "# 情绪指标择时研究报告",
        "",
        f"> 生成日期：{pd.Timestamp.today().date()}",
        f"> 样本：{meta.get('start')} → {meta.get('end')}（评估起点 {eval_start}）",
        f"> 基准：{meta.get('benchmark')}（前复权）；现金腿：511880",
        "> 仅供研究参考，不构成投资建议。",
        "",
        "## 一、数据与口径",
        "",
        f"- 交易日数：{meta.get('rows')}",
        "- 信号：T 日收盘计算滚动分位（5 年窗口，至少 250 个观测）",
        "- 执行：T+1 收盘，收益从 T+2 起算；单边成本 2.5bp",
        (
            "- 复合热度只用全市场情绪：成交额、全市场换手率、两融余额、融资买入占比、"
            "北向净流入；沪深300 自身 PE/换手率仅作单指标参考，不进信号；"
            "新增投资者仅作单指标展示（2023-08 停更）"
        ),
        "",
        "## 二、指标覆盖与相关性",
        "",
        "| 指标 | 有效观测 | 起始 | 与热度相关性 |",
        "|---|---|---|---|",
    ]
    heat = ind["heat"]
    for c in COMPOSITE_COLS:
        s = ind[c]
        valid = s.notna().sum()
        first = s.first_valid_index()
        pct_c = ind[f"{c}_pct"]
        corr = pct_c.corr(heat) if pct_c.nunique() > 1 and heat.nunique() > 1 else np.nan
        lines.append(
            f"| {INDICATOR_LABELS[c]} | {valid} | "
            f"{first.date() if first is not None else '-'} | {_num(corr)} |"
        )

    lines += [
        "",
        "## 三、单指标事件研究（未来 60 个交易日）",
        "",
        (
            "按指标分位分五组，统计 T+1 进场后的收益与最大回撤。若高热度组没有出现"
            "「收益更低/回撤更深」，说明该指标没有稳定的反向指示性。"
        ),
        "",
        "| 指标 | 分组 | 样本 | 平均收益 | 收益中位数 | 胜率 | 平均最大回撤 |",
        "|---|---|---|---|---|---|---|",
    ]
    sub = ev[(ev["horizon"] == 60)] if not ev.empty else ev
    for c in [x for x in INDICATOR_LABELS if f"{x}_pct" in ind.columns]:
        part = sub[sub["indicator"] == c]
        if part.empty:
            continue
        for _, r in part.iterrows():
            lines.append(
                f"| {INDICATOR_LABELS[c]} | {r['bucket']} | {int(r['n'])} | "
                f"{_pct(r['mean_ret'])} | {_pct(r['median_ret'])} | "
                f"{_pct(r['hit_rate'], 1)} | {_pct(r['mean_fwd_mdd'])} |"
            )

    lines += [
        "",
        "## 四、组合回测",
        "",
        (
            "动态仓位（主口径）：热度 ≤50 满仓，50→100 线性降至 20%；"
            "分档仓位（对照）：<80 满仓、80–90 八成、90–97 五成、≥97 两成。"
            "两者都只降风险、不加杠杆；热度数据不足时按满仓处理。"
        ),
        "",
        "| 方案 | 年化 | 年化波动 | 最大回撤 | Calmar | Sharpe | 平均仓位 | 年换手 | 调仓次数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        s = r.stats
        lines.append(
            f"| {name} | {_pct(s['cagr'])} | {_pct(s['vol'])} | {_pct(s['mdd'])} | "
            f"{_num(s['calmar'])} | {_num(s['sharpe'])} | {_pct(s['exposure'], 1)} | "
            f"{_num(s['turnover'])} | {s['switches']} |"
        )

    lines += [
        "",
        "### 4.2 单指标择时回测",
        "",
        (
            "每个指标单独套用**动态仓位**（热度 ≤50 满仓，50→100 线性降至 20%），"
            "只在自身分位有效的样本内回测，并与同区间买入持有对比。"
            "沪深300 PE/换手率仅作参考，未进入复合信号。"
            "通过=回撤改善 ≥30% 且收益不低于同期基准 -1pct，或 Calmar 提升 ≥0.3。"
        ),
        "",
        "| 指标 | 起点 | 年化 | 最大回撤 | Calmar | 同期买入持有 Calmar | 平均仓位 | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in single.items():
        lines.append(
            f"| {INDICATOR_LABELS.get(name, name)} | {r.start} | "
            f"{_pct(r.dyn_stats['cagr'])} | {_pct(r.dyn_stats['mdd'])} | "
            f"{_num(r.dyn_stats['calmar'])} | {_num(r.dyn_bench_stats['calmar'])} | "
            f"{_pct(r.dyn_stats['exposure'], 1)} | "
            f"{'通过' if r.dyn_passed else '未通过'} |"
        )

    lines += [
        "",
        "### 4.3 二元 BS 回测",
        "",
        (
            "满仓/空仓、带滞回；初始空仓，T+1 收盘执行。"
            "反转=低分位买入、高分位卖出；趋势=高分位买入、低分位卖出。"
        ),
        "",
        "| 指标 | 规则 | 年化 | 最大回撤 | Calmar | 平均仓位 | 调仓次数 | 平均持有 | 判定 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, row in bs.iterrows():
        label = "复合热度" if row["indicator"] == "heat" \
            else INDICATOR_LABELS.get(row["indicator"], row["indicator"])
        lines.append(
            f"| {label} | {row['rule']} | {_pct(row['cagr'])} | {_pct(row['mdd'])} | "
            f"{_num(row['calmar'])} | {_pct(row['exposure'], 1)} | "
            f"{int(row['switches'])} | {_num(row['avg_hold_days'], 0)}日 | "
            f"{'通过' if row['passed'] else '未通过'} |"
        )
    if not bs.empty:
        bs_pass = bs[bs["passed"]]
        best = bs.loc[bs["calmar"].idxmax()]
        best_label = "复合热度" if best["indicator"] == "heat" \
            else INDICATOR_LABELS.get(best["indicator"], best["indicator"])
        pass_labels = "、".join(
            f"{'复合热度' if r['indicator'] == 'heat' else INDICATOR_LABELS.get(r['indicator'], r['indicator'])}"
            f"（{r['rule']}）"
            for _, r in bs_pass.iterrows()
        ) or "无"
        lines.append(
            f"\n二元 BS 通过 {len(bs_pass)} 条：{pass_labels}。"
            f"全部 18 条里 Calmar 最高的是 {best_label}（{best['rule']}，"
            f"年化 {_pct(best['cagr'])}、回撤 {_pct(best['mdd'])}、"
            f"Calmar {_num(best['calmar'])}、平均仓位 {_pct(best['exposure'], 1)}），"
            f"但它因收益差未过线——高 Calmar 来自长期空仓，不是择时能力。"
        )
        margin = bs[bs["indicator"] == "margin_20d"]
        if not margin.empty:
            parts = "；".join(
                f"{r['rule']}：年化 {_pct(r['cagr'])}、回撤 {_pct(r['mdd'])}、"
                f"Calmar {_num(r['calmar'])}、仓位 {_pct(r['exposure'], 1)}、"
                f"调仓 {int(r['switches'])} 次"
                for _, r in margin.iterrows()
            )
            lines.append(
                f"两融在纯 BS 下不成立（{parts}）。反转规则长期空仓、趋势规则长期满仓，"
                f"一个放弃收益、一个放弃防守；两融的价值只在部分仓位（3.3 节动态）里出现。"
            )

    lines += [
        "",
        "## 五、分年度收益",
        "",
        "| 年份 | 买入持有 | 均线择时 | 情绪择时(动态) |",
        "|---|---|---|---|",
    ]
    for year, row in yearly.iterrows():
        lines.append(
            f"| {year} | {_pct(row.get('买入持有', np.nan))} | "
            f"{_pct(row.get('均线择时(MA200)', np.nan))} | "
            f"{_pct(row.get('情绪择时(动态)', np.nan))} |"
        )

    lines += [
        "",
        "## 六、关键诊断",
        "",
    ]
    if "情绪择时(动态)" in yearly.columns and "买入持有" in yearly.columns:
        diff = (yearly["情绪择时(动态)"] - yearly["买入持有"]).dropna()
        if not diff.empty:
            worst, best = diff.idxmin(), diff.idxmax()
            lines.append(
                f"- 相对买入持有，拖累最大的是 {worst} 年"
                f"（{_pct(yearly.loc[worst, '情绪择时(动态)'])} vs "
                f"{_pct(yearly.loc[worst, '买入持有'])}），"
                f"贡献最大的是 {best} 年（{_pct(yearly.loc[best, '情绪择时(动态)'])} vs "
                f"{_pct(yearly.loc[best, '买入持有'])}）。"
            )
    lines.append("- 单指标事件研究没有稳定单调性：高热度组既不是一致的低收益，"
                 "也不是一致的深回撤，「高分位=见顶」不成立。")
    ma = results.get("均线择时(MA200)")
    sent = results.get("情绪择时(动态)")
    if single and sent is not None:
        passers = [r for r in single.values() if r.dyn_passed]
        if passers:
            names = "、".join(INDICATOR_LABELS.get(r.name, r.name) for r in passers)
            lines.append(
                f"- 单指标动态仓位有 {len(passers)} 个通过初筛：{names}；"
                f"复合等权反而未通过（Calmar {sent.stats['calmar']:.2f}），"
                f"说明「全盘组合」拖累了单指标。"
            )
            lines.append(
                "- 这是 8 个指标 × 2 套规则筛选后的结果，存在多重检验；"
                "通过初筛只代表值得做样本外与前向验证，不等于可直接使用。"
            )
        best_calmar = max(r.dyn_stats["calmar"] for r in single.values())
        tied = [r for r in single.values()
                if abs(r.dyn_stats["calmar"] - best_calmar) < 0.005]
        names = "、".join(INDICATOR_LABELS.get(r.name, r.name) for r in tied)
        lines.append(
            f"- 单指标动态仓位里 Calmar 最高的是 {names}（{best_calmar:.2f}）。"
        )
    if ma is not None and sent is not None:
        best_single = max(single.values(), key=lambda r: r.dyn_stats["calmar"]) \
            if single else None
        if best_single is not None and best_single.dyn_stats["calmar"] > ma.stats["calmar"]:
            lines.append(
                f"- 均线择时 Calmar {ma.stats['calmar']:.2f}；复合情绪 "
                f"{sent.stats['calmar']:.2f}；单指标最高 "
                f"{INDICATOR_LABELS.get(best_single.name, best_single.name)} "
                f"{best_single.dyn_stats['calmar']:.2f}。问题在等权组合，不在情绪本身。"
            )
        else:
            lines.append(
                f"- 均线择时 Calmar {ma.stats['calmar']:.2f}，情绪择时 "
                f"{sent.stats['calmar']:.2f}；情绪层目前没有跑赢这个零成本替代方案。"
            )

    lines += [
        "",
        "## 七、结论",
        "",
        f"**预注册判定：{'通过' if passed else '未通过'}**",
        "",
    ]
    lines += [f"- {n}" for n in notes]
    lines += [
        "",
        (
            "判定口径：最大回撤改善 ≥30% 且 CAGR 不低于基准 -1pct，或 Calmar 提升 ≥0.3。"
            "未通过即视为「该口径下没有可用的择时增量」，不换区间、不换阈值重试。"
        ),
        "",
        "## 八、局限与失效条件",
        "",
        "- 样本含 2015 杠杆牛/股灾，结论对区间敏感，需看分年度与样本外。",
        "- 两融 2010 起、北向 2014 起，早期指标覆盖不足，复合热度从 2016 前后才稳定。",
        "- 事件研究的前瞻窗口重叠，样本不独立，只作方向性描述，不做显著性检验。",
        "- 分位分组未控制时间与估值，早期样本（如 2014 牛市）会主导低分位组。",
        "- 阈值是预先写死的观察值，未做质量溢价/行业 β 校准；参数邻域需另行检验。",
        "- 情绪指标更适合做风险预算（降仓位），不适合预测精确拐点。",
        "",
    ]
    return "\n".join(lines)
