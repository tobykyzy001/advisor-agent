"""tushare 日线数据 → 领域模型 DailySeries 的适配与清洗。

口径说明：
- 输入是 `mcp__tushareMcp__daily` 返回的行（每条含 ts_code / trade_date /
  open / high / low / close / vol 等字段），可能来自 agent 会话内取数后回填。
- tushare 的 vol 单位为「手」，amount 为「千元」；本适配仅对齐成交量为数值口径，
  放量判断用同一标的自身的量能平均值，因此单位差异不影响结论。
- 输出为按 trade_date 升序的 DailySeries，跳过缺 OHLC 的脏行。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from quantify.data.schema import DailyBar, DailySeries


def _to_date(v: Any) -> date:
    """把 tushare 日期（YYYYMMDD 字符串/整数）转成 date。"""
    if isinstance(v, date):
        return v
    s = str(v)
    if len(s) >= 8:
        return datetime.strptime(s[:8], "%Y%m%d").date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def rows_to_series(symbol: str, rows: Iterable[dict], market: str = "A") -> DailySeries:
    """把 tushare daily 行列表转换为 DailySeries（按日期升序）。

    params:
        symbol: 标的 ts_code，如 600519.SH
        rows: 字典列表，至少含 trade_date/open/high/low/close，vol 可选。
    """
    bars: list[DailyBar] = []
    for row in rows:
        o = _to_float(row.get("open"))
        h = _to_float(row.get("high"))
        l = _to_float(row.get("low"))
        c = _to_float(row.get("close"))
        if None in (o, h, l, c):
            continue  # 脏数据/停牌日跳过
        vol = _to_float(row.get("vol")) or 0.0
        try:
            d = _to_date(row.get("trade_date"))
        except (TypeError, ValueError):
            continue
        bars.append(DailyBar(date=d, open=o, high=h, low=l, close=c, volume=vol))

    bars.sort(key=lambda b: b.date)
    return DailySeries(symbol=symbol, market=market, bars=bars)