"""W底 + 放量识别算法、观察仓清单、tushare 适配的单元测试。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from quantify.analysis.w_bottom import (
    WBottomParams,
    detect_w_bottom,
    screen_w_bottom,
)
from quantify.analysis.watchlist import WatchItem, load_watchlist, save_watchlist
from quantify.data.schema import DailyBar, DailySeries
from quantify.data.tushare_adapter import rows_to_series


def _d(days_ago: int) -> date:
    return date(2025, 1, 15) - timedelta(days=days_ago * 7)  # 用周间隔避免周末问题


def _bar(days_ago: int, o: float, h: float, l: float, c: float, v: float) -> DailyBar:
    return DailyBar(date=_d(days_ago), open=o, high=h, low=l, close=c, volume=v)


def _series(symbol: str, bars: list[DailyBar]) -> DailySeries:
    return DailySeries(symbol=symbol, market="A", bars=bars)


@pytest.fixture
def w_params() -> WBottomParams:
    return WBottomParams(
        lookback=30,
        trough_tol=0.03,
        confirm_window=3,
        ma_window=5,
        anchor_window=5,
    )


def _standard_w_bottom_bars() -> list[DailyBar]:
    """构造一个标准 W底并放量确认：左底 10、反弹 12、右底 9.9、放量阳线确认。

    时间从旧到新排列（bars 列表按日期升序）：索引越大越接近当前。
    关键：确认 K 线需落在「近 5 日」（anchor_window=5）内，即确认 K 线索引要贴近
    序列末尾，故把双底形态压缩到最后几根：

      0~3 高位横盘（历史填充）
      4 下跌 10.5 -> 5 左底 A=10.0(low) -> 6 反弹 12.0 -> 7 回落到右底 B1=9.9(low)
      -> 8 确认放量阳线(量300 vs 前5均~100) -> 9 小涨
    """
    bars = [
        _bar(15, 11.8, 11.9, 11.6, 11.7, 100),
        _bar(14, 11.7, 11.8, 11.5, 11.6, 100),
        _bar(13, 11.6, 11.7, 11.4, 11.5, 100),
        _bar(12, 11.5, 11.6, 11.3, 11.4, 100),
        _bar(11, 10.6, 10.7, 10.4, 10.5, 100),     # idx4: 下探（low 10.4）
        _bar(10, 10.4, 10.5, 10.0, 10.3, 100),     # idx5: 左底 A.low=10.0
        _bar(9, 10.3, 12.1, 10.2, 12.0, 100),      # idx6: 反弹
        _bar(8, 12.0, 12.0, 9.9, 10.1, 100),       # idx7: 右底 B1.low=9.9（偏差1%）
        _bar(7, 10.0, 11.0, 9.9, 10.8, 300),       # idx8: 确认放量阳线（量300 vs 均100）
        _bar(6, 10.8, 11.0, 10.7, 10.9, 110),      # idx9: 之后小K（收在近5日内）
    ]
    return bars


def test_standard_w_bottom_hit(w_params):
    bars = _standard_w_bottom_bars()
    r = detect_w_bottom(_series("X.SH", bars), w_params)
    assert r.hit is True
    assert r.trough_a_price == 10.0
    assert r.trough_b_price == 9.9
    assert r.volume_ratio == pytest.approx(3.0, rel=0.01)
    assert "命中" in r.message


def test_trough_deviation_exceeds_tol(w_params):
    """两底偏差超过 3% 时不命中。"""
    bars = _standard_w_bottom_bars()
    # 把右底 B1（idx7）的低点改成 8.5（相对左底 10.0 偏差 15%）
    bars[7] = _bar(8, 12.0, 12.0, 8.5, 10.1, 100)
    r = detect_w_bottom(_series("600.SH", bars), w_params)
    assert r.hit is False
    assert "偏差" in r.message


def test_no_volume_confirmation(w_params):
    """B1 之后 3 日内确无放量阳线 → 不命中。"""
    bars = _standard_w_bottom_bars()
    # B1 在 idx7，其后 idx8、idx9 均非「阳线且量>=MA5」=> 无确认 => 不命中
    bars[8] = _bar(7, 10.8, 10.9, 10.0, 10.4, 50)   # 阴线、量不足
    bars[9] = _bar(6, 10.4, 10.5, 10.3, 10.4, 90)   # 阳线但量不足（90 < 均100）
    r = detect_w_bottom(_series("600.SH", bars), w_params)
    assert r.hit is False
    assert "放量阳线" in r.message


def test_confirm_bar_beyond_window(w_params):
    """B1 之后首根放量阳线出现在第 4 根（=confirm_window 之外）→ 不命中。"""
    # 构造：左底 A 在 idx5(10.0)，右底 B1 在 idx7(9.9)，之后 idx8~11 中
    # 只有 idx11（B1 后第 4 根）是放量阳线，其余均非放量 → 超 confirm_window。
    bars = _standard_w_bottom_bars()
    # 先扩展两根到末尾（复制 idx9，量普通）
    bars.append(_bar(5, 10.9, 11.0, 10.8, 10.9, 110))   # idx10
    bars.append(_bar(4, 10.9, 11.0, 10.8, 10.9, 110))   # idx11
    # idx8、idx9、idx10 非放量；idx11（B1 后第 4 根）放量
    bars[8] = _bar(7, 10.0, 10.2, 9.9, 10.1, 90)
    bars[9] = _bar(6, 10.1, 10.3, 10.0, 10.2, 90)
    bars[10] = _bar(5, 10.2, 10.4, 10.1, 10.3, 90)
    bars[11] = _bar(4, 10.3, 11.5, 10.3, 11.4, 300)     # 放量但超窗口
    # 注：B1 仍在 idx7，其后第 1~3 根(idx8,9,10)无放量，第 4 根(idx11)才放量
    r = detect_w_bottom(_series("600.SH", bars), w_params)
    assert r.hit is False
    assert "放量阳线" in r.message


def test_rows_to_series_sorting_and_dirty():
    rows = [
        {"trade_date": "20250110", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100},
        {"trade_date": "20250109", "open": 10, "high": 11, "low": 9, "close": 10, "vol": 120},
        {"trade_date": "20250108", "open": None, "high": 11, "low": 9, "close": 10, "vol": 100},
    ]
    s = rows_to_series("600.SH", rows)
    assert len(s.bars) == 2  # 脏行被跳过
    assert s.bars[0].date.isoformat() == "2025-01-09"  # 升序


def test_watchlist_roundtrip():
    p = Path(".tmp") / "test_watchlist_roundtrip.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    items = [WatchItem(ts_code="600519.SH", name="贵州茅台", market="A", note="x")]
    save_watchlist(items, p)
    got = load_watchlist(p)
    p.unlink(missing_ok=True)
    assert len(got) == 1
    assert got[0].ts_code == "600519.SH"
    assert got[0].name == "贵州茅台"


def test_watchlist_missing_file_raises():
    """清单文件缺失时应抛 FileNotFoundError（模板真源归 workspace-init，不自动生成）。"""
    p = Path(".tmp") / "__nonexistent_watchlist__.yaml"
    with pytest.raises(FileNotFoundError):
        load_watchlist(p)


def test_screen_w_bottom_filters_hits(w_params):
    hit_series = _series("HIT.SH", _standard_w_bottom_bars())
    # 非命中：单底（缺第二底）
    miss_bars = [
        _bar(5, 10, 10.5, 9.5, 10, 100),
        _bar(4, 10, 10.5, 9.5, 10, 100),
        _bar(3, 10, 10.5, 9.5, 10, 100),
        _bar(2, 10, 10.5, 9.5, 10, 100),
        _bar(1, 10, 10.5, 9.5, 10, 100),
        _bar(0, 10, 10.5, 9.5, 10, 100),
    ]
    miss_series = _series("MISS.SH", miss_bars)
    hits = screen_w_bottom([hit_series, miss_series], w_params)
    assert len(hits) == 1
    assert hits[0].symbol == "HIT.SH"