"""情绪择时研究的数据层：拉取、缓存、对齐原始指标。

只负责「拿到干净的原始序列」，不负责分位/合成/回测（在 analysis/sentiment.py）。
数据源：
- tushare：沪深300每日指标、两融汇总、北向资金、沪深市场每日成交统计；
- akshare：新增投资者开户（月度，2015-04 至 2023-08，已停更，仅作辅助）；
- 本地行情库 output/quotes-store/：510300（基准）、511880（现金腿）。

tushare 长区间接口普遍有单次条数上限，统一用 query_chunked 切片，避免被静默截断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from quantify.data import tushare_client as ts

_HS300 = "000300.SH"
_BENCH = "510300.SH"
_CASH = "511880.SH"


@dataclass
class RawSentimentData:
    """原始序列集合，索引统一为交易日（accounts 为月末）。"""

    benchmark_code: str
    benchmark: pd.Series                 # 前复权收盘价（由 pct_chg 复利连乘）
    benchmark_ret: pd.Series
    cash_ret: pd.Series
    hs300: pd.DataFrame                  # pe_ttm / turnover_rate
    margin: pd.DataFrame                 # rzye / rzmre（三所合计）
    north: pd.Series                     # north_money（万元）
    market_amount: pd.Series             # 沪深A股成交额（亿元）
    accounts: pd.Series                  # 月度新增投资者（万户）
    meta: dict = field(default_factory=dict)


def load_store_series(store_dir: str | Path, ts_code: str) -> pd.Series:
    """从本地行情库读一只标的的前复权收盘价（pct_chg 复利连乘）。

    缺失文件或全部行无 close 时返回空 Series，由调用方决定降级/报错。
    """
    fp = Path(store_dir) / f"{ts_code}.csv"
    if not fp.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(fp, dtype={"trade_date": str})
    if df.empty or "close" not in df.columns:
        return pd.Series(dtype=float)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    if df.empty:
        return pd.Series(dtype=float)
    pct = pd.to_numeric(df.get("pct_chg"), errors="coerce").fillna(0.0)
    adj = (1.0 + pct / 100.0).cumprod()
    idx = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    s = pd.Series(adj.to_numpy(), index=idx)
    return s[~s.index.isna()].sort_index()


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _cache_path(cache_dir: Path, name: str) -> Path:
    return cache_dir / f"{name}.csv"


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"trade_date": str})
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _fetch_or_cache(name: str, cache_dir: Path, refresh: bool,
                    fetch_fn, end: str | None = None) -> pd.DataFrame:
    path = _cache_path(cache_dir, name)
    if not refresh:
        cached = _read_cache(path)
        if cached is not None and not cached.empty:
            if end and "trade_date" in cached.columns:
                try:
                    last = pd.to_datetime(str(cached["trade_date"].max()), format="%Y%m%d")
                    # 缓存落后超过 10 天视为过期（覆盖周末/长假场景），自动重拉
                    if last >= pd.Timestamp(end) - pd.Timedelta(days=10):
                        return cached
                except (TypeError, ValueError):
                    return cached
            else:
                return cached
    df = fetch_fn()
    if df is None:
        df = pd.DataFrame()
    if not df.empty:
        _write_cache(path, df)
    return df


def fetch_hs300_dailybasic(token: str, start: str, end: str) -> pd.DataFrame:
    rows = ts.query_chunked(
        "index_dailybasic", {"ts_code": _HS300},
        "ts_code,trade_date,turnover_rate,pe,pe_ttm,pb",
        token, start, end, chunk_days=365,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("turnover_rate", "pe", "pe_ttm", "pb"):
        if c in df.columns:
            df[c] = _to_num(df[c])
    return df.dropna(subset=["trade_date"])


def fetch_margin(token: str, start: str, end: str) -> pd.DataFrame:
    rows = ts.query_chunked(
        "margin", {}, "trade_date,rzye,rzmre,rzche,rqye,rzrqye",
        token, start, end, chunk_days=365,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("rzye", "rzmre", "rzche", "rqye", "rzrqye"):
        if c in df.columns:
            df[c] = _to_num(df[c])
    # tushare margin 每个交易日返回沪/深/北三行且不带 exchange 字段，按日汇总
    return df.groupby("trade_date", as_index=False)[["rzye", "rzmre"]].sum()


def fetch_northbound(token: str, start: str, end: str) -> pd.DataFrame:
    rows = ts.query_chunked(
        "moneyflow_hsgt", {}, "trade_date,north_money",
        token, start, end, chunk_days=180,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["north_money"] = _to_num(df["north_money"])
    return df.dropna(subset=["trade_date"])


def fetch_daily_info(token: str, start: str, end: str) -> pd.DataFrame:
    rows = ts.query_chunked(
        "daily_info", {}, "trade_date,ts_code,ts_name,amount",
        token, start, end, chunk_days=60,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["amount"] = _to_num(df["amount"])
    return df.dropna(subset=["trade_date"])


def fetch_accounts() -> pd.DataFrame:
    """新增投资者开户（月度）。akshare 源已停更在 2023-08，仅作辅助指标；失败不阻断主流程。"""
    try:
        import akshare as ak

        df = ak.stock_account_statistics_em()
    except Exception:  # noqa: BLE001 - 第三方接口异常类型不稳定，失败即跳过该辅助指标
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    out = df[["数据日期", "新增投资者-数量"]].copy()
    out.columns = ["trade_date", "new_accounts"]
    out["trade_date"] = out["trade_date"].astype(str)
    out["new_accounts"] = _to_num(out["new_accounts"])
    return out.dropna(subset=["trade_date"])


def _market_amount(raw: pd.DataFrame) -> pd.Series:
    """沪深A股成交额（亿元）：沪市取 SH_MARKET（无则 SH_A），深市取 SZ_MARKET。"""
    if raw.empty:
        return pd.Series(dtype=float)
    p = raw.pivot_table(index="trade_date", columns="ts_code",
                        values="amount", aggfunc="sum")

    def col(name: str) -> pd.Series:
        if name in p.columns:
            return p[name]
        return pd.Series(index=p.index, dtype=float)

    sh = col("SH_MARKET")
    sh = sh.fillna(col("SH_A"))
    sz = col("SZ_MARKET")
    sz = sz.fillna(col("SZ_A").fillna(0) + col("SZ_SME").fillna(0) + col("SZ_GEM").fillna(0))
    total = sh + sz
    total.index = pd.to_datetime(total.index, format="%Y%m%d", errors="coerce")
    total = total[~total.index.isna()]
    return total.dropna().sort_index()


def load_raw_data(token: str, start: str, end: str,
                  store_dir: str | Path = "output/quotes-store",
                  cache_dir: str | Path = "output/sentiment/cache",
                  refresh: bool = False) -> RawSentimentData:
    """拉取/读取全部原始序列，并对齐到基准交易日。"""
    cache = Path(cache_dir)
    bench = load_store_series(store_dir, _BENCH)
    bench_code = _BENCH
    if bench.empty:
        bench = load_store_series(store_dir, _HS300)
        bench_code = _HS300
    if bench.empty:
        raise RuntimeError(
            f"本地行情库 {store_dir} 缺 {_BENCH} 与 {_HS300}；"
            f"请先运行 fetch_quotes.py --codes 510300.SH,000300.SH,511880.SH "
            f"--full-days 8000 --realtime off"
        )

    cash = load_store_series(store_dir, _CASH)
    bench = bench.loc[bench.index <= pd.Timestamp(end)]
    bench = bench.loc[bench.index >= pd.Timestamp(start)] if start else bench
    bench_ret = bench.pct_change().fillna(0.0)
    cash_ret = cash.pct_change().reindex(bench.index).fillna(0.0)

    fetch_start = start or "20050101"
    hs300_raw = _fetch_or_cache(
        "hs300_dailybasic", cache, refresh,
        lambda: fetch_hs300_dailybasic(token, fetch_start, end), end=end,
    )
    margin_raw = _fetch_or_cache(
        "margin", cache, refresh,
        lambda: fetch_margin(token, fetch_start, end), end=end,
    )
    north_raw = _fetch_or_cache(
        "northbound", cache, refresh,
        lambda: fetch_northbound(token, fetch_start, end), end=end,
    )
    market_raw = _fetch_or_cache(
        "daily_info", cache, refresh,
        lambda: fetch_daily_info(token, fetch_start, end), end=end,
    )
    accounts_raw = _fetch_or_cache(
        "accounts", cache, refresh, fetch_accounts, end=None,
    )

    def daily_index(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        if df.empty or "trade_date" not in df.columns:
            return pd.DataFrame(index=bench.index, columns=cols, dtype=float)
        d = df.copy()
        d.index = pd.to_datetime(d["trade_date"], format="%Y%m%d", errors="coerce")
        d = d[~d.index.isna()].reindex(bench.index)
        for c in cols:
            d[c] = _to_num(d[c]) if c in d.columns else float("nan")
        return d[cols]

    hs300 = daily_index(hs300_raw, ["pe_ttm", "turnover_rate"])
    margin = daily_index(margin_raw, ["rzye", "rzmre"])
    north = daily_index(north_raw, ["north_money"])["north_money"]
    market_amount = _market_amount(market_raw).reindex(bench.index)

    accounts = pd.Series(dtype=float)
    if not accounts_raw.empty:
        a = accounts_raw.copy()
        a["month"] = pd.to_datetime(a["trade_date"], format="%Y-%m", errors="coerce")
        a = a.dropna(subset=["month"]).set_index("month")["new_accounts"].sort_index()
        accounts = a.reindex(bench.index, method="ffill")

    meta = {
        "benchmark": bench_code,
        "start": str(bench.index.min().date()) if len(bench) else "",
        "end": str(bench.index.max().date()) if len(bench) else "",
        "rows": len(bench),
        "has_cash": bool(not cash.empty),
        "sources": {
            "hs300": "tushare index_dailybasic",
            "margin": "tushare margin（三所合计）",
            "north": "tushare moneyflow_hsgt",
            "market_amount": "tushare daily_info（SH_MARKET + SZ_MARKET）",
            "accounts": "akshare stock_account_statistics_em（2015-04~2023-08）",
            "benchmark": f"output/quotes-store/{bench_code}.csv",
            "cash": f"output/quotes-store/{_CASH}.csv",
        },
    }
    return RawSentimentData(
        benchmark_code=bench_code,
        benchmark=bench,
        benchmark_ret=bench_ret,
        cash_ret=cash_ret,
        hs300=hs300,
        margin=margin,
        north=north,
        market_amount=market_amount,
        accounts=accounts,
        meta=meta,
    )
