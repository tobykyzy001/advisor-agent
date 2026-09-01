"""W底（双重底）+ 放量形态识别。

口径（可参数化）：
- 回看窗口 lookback：近 N 个交易日。
- 两个底 A、B1：局部低点自动识别，A 在前、B1 在后且互不重叠；|B1-A|/A <= trough_tol。
- 中间不要求反弹幅度：允许横盘/窄幅震荡（变体 W底）。
- W底确认：B1 之后 confirm_window 个交易日内，出现一根「阳线（收盘>开盘）且
  成交量 >= MA(ma_window)」的 K 线（放量上涨确认 K 线）。
- 时间锚点：确认 K 线落在近 anchor_window 个交易日内。

本模块为纯函数，不依赖任何数据源，便于离线单测与复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from quantify.data.schema import DailyBar, DailySeries


@dataclass
class WBottomParams:
    """W底识别参数（均有默认值，可按需调整）。"""

    lookback: int = 30          # 回看交易日数
    trough_tol: float = 0.03    # 两底低点偏差上限（占比）
    confirm_window: int = 3     # B1 之后确认 K 线允许的最多交易日数
    ma_window: int = 5          # 放量基准：N 日均量（确认 K 线之前的均量）
    anchor_window: int = 5      # 确认 K 线需落在近 N 个交易日内


@dataclass
class WBottomResult:
    """单标的 W底识别结果。"""

    symbol: str
    hit: bool = False
    # 命中的形态细节
    trough_a_idx: int = -1          # A 底在窗口 bars 中的索引
    trough_b_idx: int = -1          # B1 底索引
    confirm_idx: int = -1           # 放量确认 K 线索引
    trough_a_price: float = 0.0
    trough_b_price: float = 0.0
    confirm_date: str = ""
    volume_ratio: float = 0.0       # 确认 K 线量 / MA(ma_window) 量
    message: str = ""
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "hit": self.hit,
            "trough_a_price": round(self.trough_a_price, 2),
            "trough_b_price": round(self.trough_b_price, 2),
            "confirm_date": self.confirm_date,
            "volume_ratio": round(self.volume_ratio, 2),
            "message": self.message,
            "reasons": self.reasons,
        }


def _find_local_lows(bars: list[DailyBar]) -> list[int]:
    """找局部低点索引：low[i] <= 左右邻（允许等值，如横盘平台）。

    首尾各扩展一根虚拟 +inf bar，保证端点也可能成为局部低点。
    """
    n = len(bars)
    if n == 0:
        return []
    idxs: list[int] = []
    for i in range(n):
        left = bars[i - 1].low if i - 1 >= 0 else float("inf")
        right = bars[i + 1].low if i + 1 < n else float("inf")
        if bars[i].low <= left and bars[i].low <= right:
            idxs.append(i)
    return idxs


def _pick_troughs(bars: list[DailyBar], lows: list[int]) -> tuple[int, int] | None:
    """从局部低点中挑出构成 W 底的两个底 (a_idx, b_idx)。

    A 在前、B1 在后；要求 b - a >= 2（两底之间至少留 1 根中间 K 线，用于反弹/横盘）。
    返回 None 表示候选不足。
    """
    if len(lows) < 2:
        return None
    for a in lows:
        for b in lows:
            if b - a >= 2:
                return a, b
    return None


def _ma_volume_before(bars: list[DailyBar], upto: int, window: int) -> float:
    """计算索引 upto 之前（不含 upto 自身）window 根 bar 的成交量均值。

    这是「放量」的比较基准（确认 K 线之前的均量）。数据不足时退化为已有均量。
    """
    start = max(0, upto - window)
    seg = bars[start:upto]
    if not seg:
        return 0.0
    return sum(b.volume for b in seg) / len(seg)


def detect_w_bottom(series: DailySeries, params: WBottomParams | None = None) -> WBottomResult:
    """对单标的日线做 W底 + 放量判定。"""
    p = params or WBottomParams()
    bars = series.bars
    r = WBottomResult(symbol=series.symbol)

    if len(bars) < p.ma_window + 3:
        r.message = f"日线不足（{len(bars)} 根），无法判定。"
        return r

    # 只看近 lookback 个交易日
    window_bars = bars[-p.lookback:] if len(bars) > p.lookback else bars
    lows = _find_local_lows(window_bars)

    picked = _pick_troughs(window_bars, lows)
    if picked is None:
        r.message = "未找到两个相近低点，不构成双底。"
        return r
    a_idx, b_idx = picked

    a_price = window_bars[a_idx].low
    b_price = window_bars[b_idx].low
    # 双底偏差：|B1 - A| / A
    dev = abs(b_price - a_price) / a_price if a_price else float("inf")
    if dev > p.trough_tol:
        r.message = f"两底偏差 {dev:.2%} 超上限 {p.trough_tol:.2%}，不计双底。"
        return r

    # 找 B1 之后 confirm_window 根内的放量阳线
    confirm_idx = -1
    for j in range(b_idx + 1, min(b_idx + 1 + p.confirm_window, len(window_bars))):
        bar = window_bars[j]
        ma_v = _ma_volume_before(window_bars, j, p.ma_window)
        if ma_v <= 0:
            continue
        if bar.is_up and bar.volume >= ma_v:
            confirm_idx = j
            break

    if confirm_idx < 0:
        r.message = "B1 之后未出现放量阳线确认，W底未成型。"
        return r

    # 时间闸门：确认 K 线需落在近 anchor_window 根内（即靠近序列末尾）
    last_idx = len(window_bars) - 1
    if last_idx - confirm_idx >= p.anchor_window:
        r.message = f"放量确认 K 线已超出近 {p.anchor_window} 个交易日，非最新信号。"
        return r

    ma_v = _ma_volume_before(window_bars, confirm_idx, p.ma_window)
    r.hit = True
    r.trough_a_idx = a_idx
    r.trough_b_idx = b_idx
    r.confirm_idx = confirm_idx
    r.trough_a_price = a_price
    r.trough_b_price = b_price
    r.confirm_date = window_bars[confirm_idx].date.isoformat()
    r.volume_ratio = window_bars[confirm_idx].volume / ma_v if ma_v else 0.0
    r.message = "命中：W底形态 + 放量确认。"
    r.reasons = [
        f"W底：左底 {a_price:.2f} / 右底 {b_price:.2f}（偏差 {dev:.2%}）",
        f"放量确认：{r.confirm_date} 量比 {r.volume_ratio:.2f}（相对 {p.ma_window} 日均量）",
    ]
    return r


def screen_w_bottom(series_list: list[DailySeries], params: WBottomParams | None = None) -> list[WBottomResult]:
    """对一组标的批量做 W底判定，只返回命中项（按量比降序）。"""
    results = [detect_w_bottom(s, params) for s in series_list]
    hits = [r for r in results if r.hit]
    hits.sort(key=lambda r: r.volume_ratio, reverse=True)
    return hits