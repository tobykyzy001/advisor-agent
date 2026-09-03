"""W底放量筛选：自包含单文件脚本（纯标准库，可下载即跑，不依赖 quantify 包）。

这是「观察仓 W底 + 放量」形态筛选的**唯一可执行真源**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py` 提供给目标工作区里的
agent 下载执行（与 workspace-init/init_workspace.py 同一分发模式）。

设计约束（与 init_workspace.py 一致）：
- 纯标准库（dataclass / json / argparse / datetime / pathlib / re），零第三方依赖。
- 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。
- 脚本本身**不调 tushare MCP**（MCP 只在 agent 会话内可用）；取数由 agent 完成并回填 JSON。

三层式工作流（三段式，与 copy-trade 一致）：
 1) python w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml --plan
        —— 打印观察仓标的清单 + 待取数清单（含每只 ts_code）。
        池子大于 --shard-size（默认 10 只）时自动按分片打印，也可用 --shards N 显式指定片数。
 2) agent 取数（行情数据体积大，禁止进入主会话上下文）：
    - 小池子（单片）：主会话逐只调 mcp__tushareMcp__daily 取近 ~30 交易日，
      整理成 JSON，写到 output/w-bottom/quotes.json，格式：
        {"600519.SH": [{"trade_date":"20250102","open":..,"high":..,"low":..,"close":..,"vol":..}, ...], ...}
    - 大池子（分片）：每个分片派一个独立子代理（subagent）并行取数，子代理把本片 JSON
      直接写到 output/w-bottom/quotes/shard_<k>.json（不同写同一文件），主会话只收一行回执。
      取数前先清空 quotes 目录，避免上轮残留分片混入。
 3) python w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml \
        --data output/w-bottom/quotes.json     # 单片小池子
    python w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml \
        --data output/w-bottom/quotes          # 分片目录（合并全部 *.json）
        —— 跑形态判定，输出命中报告 output/w-bottom/screen_<时间戳>.md。

形态口径（可选参数调整）：
- lookback=30      回看交易日数
- trough-tol=0.03  双底低点偏差上限 |B1-A|/A
- confirm-window=3 第二底 B1 之后确认 K 线最多交易日数
- ma-window=5      放量基准：确认 K 线之前 N 日均量
- anchor-window=5  确认 K 线需落在近 N 个交易日内
确认条件：B1 之后出现「阳线（close>open）且 volume >= MA(ma_window)」。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 领域结构（用标准库 dataclass，等价于 quantify.data.schema.DailyBar / DailySeries）
# ---------------------------------------------------------------------------


@dataclass
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def is_up(self) -> bool:
        return self.close > self.open


@dataclass
class Series:
    symbol: str
    bars: list[Bar] = field(default_factory=list)


@dataclass
class WBottomParams:
    lookback: int = 30
    trough_tol: float = 0.03
    confirm_window: int = 3
    ma_window: int = 5
    anchor_window: int = 5


@dataclass
class WBottomResult:
    symbol: str
    hit: bool = False
    trough_a_price: float = 0.0
    trough_b_price: float = 0.0
    confirm_date: str = ""
    volume_ratio: float = 0.0
    message: str = ""
    reasons: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 观察仓清单读取（极简 YAML 子集解析：只针对 watchlist 结构，避免依赖 PyYAML）
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class WatchItem:
    ts_code: str
    name: str = ""
    market: str = "A"
    note: str = ""


_ITEM_KEY = re.compile(r"^\s*-\s+ts_code:\s*(.+?)\s*$")
# 子键行：任意缩进（≥1 空格）都收——标准格式为 2 空格（与 PyYAML safe_dump 同风格，
# 量化核心 save_watchlist 写出的清单可直接读），也宽容兼容旧 4 空格手编格式。
_KEY = re.compile(r"^\s+(\w+):\s*(.*)$")


def load_watchlist(path: Path | None = None) -> list[WatchItem]:
    """读取 watchlist.yaml（极简解析：条目行认 `- ts_code:`，子键行认任意缩进）。缺失则报错引导。"""
    p = path or Path("output/watchlist/watchlist.yaml")
    if not p.exists():
        raise FileNotFoundError(
            f"观察仓清单不存在：{p}。请先运行 workspace-init 技能初始化工作区"
            f"（`python src/workspace-init/init_workspace.py`），或在投研工具设置里检查默认工作区。"
        )
    lines = p.read_text(encoding="utf-8").splitlines()
    items: list[WatchItem] = []
    cur: dict[str, str] | None = None
    for ln in lines:
        m = _ITEM_KEY.match(ln)
        if m:
            if cur is not None:
                items.append(_mkitem(cur))
            cur = {"ts_code": m.group(1).strip().strip('"').strip("'")}
            continue
        km = _KEY.match(ln)
        if km and cur is not None:
            cur[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    if cur is not None:
        items.append(_mkitem(cur))
    return items


def _mkitem(d: dict[str, str]) -> WatchItem:
    return WatchItem(
        ts_code=d.get("ts_code", "").strip(),
        name=d.get("name", "").strip(),
        market=d.get("market", "A").strip() or "A",
        note=d.get("note", "").strip(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# tushare 日线行 → Series（清洗 + 排序；等价于 tushare_adapter.rows_to_series）
# ═══════════════════════════════════════════════════════════════════════════


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        if len(s) >= 8 and s[:8].isdigit():
            return datetime.strptime(s[:8], "%Y%m%d").date()
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def rows_to_series(symbol: str, rows: list[dict]) -> Series:
    bars: list[Bar] = []
    for row in rows:
        o = _to_float(row.get("open"))
        h = _to_float(row.get("high"))
        l = _to_float(row.get("low"))
        c = _to_float(row.get("close"))
        if None in (o, h, l, c):
            continue  # 脏数据/停牌日跳过
        d = _to_date(row.get("trade_date"))
        if d is None:
            continue
        vol = _to_float(row.get("vol")) or 0.0
        bars.append(Bar(date=d, open=o, high=h, low=l, close=c, volume=vol))
    bars.sort(key=lambda b: b.date)
    return Series(symbol=symbol, bars=bars)


# ═══════════════════════════════════════════════════════════════════════════
# W底形态识别（等价于 quantify.analysis.w_bottom 的算法）
# ═══════════════════════════════════════════════════════════════════════════


def _find_local_lows(bars: list[Bar]) -> list[int]:
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


def _pick_troughs(bars: list[Bar], lows: list[int]) -> tuple[int, int] | None:
    if len(lows) < 2:
        return None
    for a in lows:
        for b in lows:
            if b - a >= 2:
                return a, b
    return None


def _ma_volume_before(bars: list[Bar], upto: int, window: int) -> float:
    start = max(0, upto - window)
    seg = bars[start:upto]
    if not seg:
        return 0.0
    return sum(b.volume for b in seg) / len(seg)


def detect(series: Series, p: WBottomParams | None = None) -> WBottomResult:
    p = p or WBottomParams()
    bars = series.bars
    r = WBottomResult(symbol=series.symbol)

    if len(bars) < p.ma_window + 3:
        r.message = f"日线不足（{len(bars)} 根），无法判定。"
        return r

    window_bars = bars[-p.lookback:] if len(bars) > p.lookback else bars
    lows = _find_local_lows(window_bars)

    picked = _pick_troughs(window_bars, lows)
    if picked is None:
        r.message = "未找到两个相近低点，不构成双底。"
        return r
    a_idx, b_idx = picked

    a_price = window_bars[a_idx].low
    b_price = window_bars[b_idx].low
    dev = abs(b_price - a_price) / a_price if a_price else float("inf")
    if dev > p.trough_tol:
        r.message = f"两底偏差 {dev:.2%} 超上限 {p.trough_tol:.2%}，不计双底。"
        return r

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

    last_idx = len(window_bars) - 1
    if last_idx - confirm_idx >= p.anchor_window:
        r.message = f"放量确认 K 线已超出近 {p.anchor_window} 个交易日，非最新信号。"
        return r

    ma_v = _ma_volume_before(window_bars, confirm_idx, p.ma_window)
    r.hit = True
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


def screen(series_list: list[Series], p: WBottomParams | None = None) -> list[WBottomResult]:
    hits = [detect(s, p) for s in series_list]
    hits = [h for h in hits if h.hit]
    hits.sort(key=lambda h: h.volume_ratio, reverse=True)
    return hits


# ═══════════════════════════════════════════════════════════════════════════
# 分片取数（大池子多 agent 并行）与行情载入
# ═══════════════════════════════════════════════════════════════════════════

#: 每个取数分片的默认标的数：单片 MCP 取数上下文控制在子代理可承受范围内
SHARD_SIZE_DEFAULT = 10


def shard_groups(codes: list[str], shards: int | None = None,
                 shard_size: int = SHARD_SIZE_DEFAULT) -> list[list[str]]:
    """把 ts_code 清单切成若干片。

    指定 shards（片数）时按片数均分；否则按 shard_size（每片只数）切。
    保持原清单顺序，保证各片子代理取数范围确定、互不重叠。
    """
    if not codes:
        return []
    if shards and shards > 0:
        size = max(1, -(-len(codes) // shards))  # 向上取整
    else:
        size = max(1, shard_size)
    return [codes[i:i + size] for i in range(0, len(codes), size)]


def load_quotes(data_path: Path) -> tuple[dict[str, list], list[str]]:
    """读取回填行情：单个 JSON 文件，或一个目录（合并目录下全部 *.json 分片）。

    目录模式用于多 agent 分片并行取数：每个子代理把本分片写到 quotes/shard_<k>.json，
    本函数按文件名排序逐片合并；同一 ts_code 跨片重复时保留行数较多者。
    返回 (raw, notes)：raw 为 {ts_code: [bar, ...]}，notes 为合并提示（重复/覆盖等）。
    文件坏 JSON / 顶层非 dict 直接抛 ValueError（指明文件名，便于重派该分片）。
    """
    if not data_path.exists():
        raise FileNotFoundError(str(data_path))
    files = [data_path] if data_path.is_file() else sorted(data_path.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"目录下没有任何 .json 分片：{data_path}")

    raw: dict[str, list] = {}
    notes: list[str] = []
    for fp in files:
        try:
            chunk = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"分片文件不是合法 JSON：{fp}（{e}）") from e
        if not isinstance(chunk, dict):
            raise ValueError(f"分片文件顶层必须是 {{\"ts_code\": [...]}} 的对象：{fp}")
        for ts, rows in chunk.items():
            if not isinstance(rows, list):
                notes.append(f"{ts} 在 {fp.name} 中的数据不是数组，已跳过")
                continue
            if ts in raw:
                if len(rows) > len(raw[ts]):
                    notes.append(f"{ts} 在 {fp.name} 重复出现，保留行数较多者（{len(rows)} 行）")
                    raw[ts] = rows
                else:
                    notes.append(f"{ts} 在 {fp.name} 重复出现，已跳过（保留先出现的 {len(raw[ts])} 行）")
            else:
                raw[ts] = rows
    return raw, notes


# ═══════════════════════════════════════════════════════════════════════════
# 两步子命令：--plan（列清单）/ --data（判形态出报告）
# ═══════════════════════════════════════════════════════════════════════════


def _plan(watchlist_path: Path, shards: int | None = None,
          shard_size: int = SHARD_SIZE_DEFAULT) -> None:
    try:
        items = load_watchlist(watchlist_path)
    except FileNotFoundError as e:
        print(f"[warning] {e}")
        print("请先运行 workspace-init 技能初始化工作区，或手动创建观察仓清单。")
        return
    if not items:
        print("观察仓清单为空，请先编辑 output/watchlist/watchlist.yaml。")
        return
    print("观察仓清单（%d 只）：" % len(items))
    for it in items:
        print(f"  - {it.ts_code}\t{it.name}\t{it.market}\t{it.note}")
    print()

    codes = [it.ts_code for it in items]
    groups = shard_groups(codes, shards, shard_size)

    if len(groups) <= 1:
        # 小池子：单片直取（数据量可控，主会话一次取齐）
        print("池子较小（单片），主会话逐只调用 mcp__tushareMcp__daily 取近 ~30 交易日日线：")
        for code in codes:
            print(f"  ts_code={code}")
        print()
        print("取数字段保留：trade_date, open, high, low, close, vol。")
        print("整理成 JSON 保存为 output/w-bottom/quotes.json，格式：")
        print('  {"600519.SH": [{"trade_date":"20250102","open":..,"high":..,')
        print('                "low":..,"close":..,"vol":..}, ...], ...}')
        return

    # 大池子：分片模式，多 agent 并行取数 + 落盘旁路，行情数据不进主会话
    print(f"池子较大，分 {len(groups)} 片取数（每片 {len(groups[0])} 只左右）。取数纪律：")
    print("- 行情数据体积大，禁止进入主会话上下文（不回显、不粘贴、不汇总明细）。")
    print("- 取数前先清空 output/w-bottom/quotes/ 目录，避免上轮残留分片混入。")
    print("- 每个分片派一个独立子代理（subagent）并行取数：子代理对片内每只 ts_code 调")
    print("  mcp__tushareMcp__daily 取近 ~30 交易日日线，字段保留 trade_date/open/high/low/close/vol，")
    print("  把本片 JSON 直接写入对应分片文件；回复主会话只需一行回执（如「shard 1：完成 10/10，")
    print("  失败 []」），不得粘贴行情。")
    print()
    for k, grp in enumerate(groups, 1):
        print(f"shard {k}/{len(groups)} → output/w-bottom/quotes/shard_{k}.json"
              f"（{len(grp)} 只）：{' '.join(grp)}")
    print()
    print("全部分片回齐后运行（--data 指向目录，脚本自动合并全部 *.json 分片）：")
    print("  python w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml")
    print("      --data output/w-bottom/quotes")


def _data(watchlist_path: Path, data_path: Path, params: WBottomParams) -> int:
    try:
        raw, merge_notes = load_quotes(data_path)
    except FileNotFoundError as e:
        print(f"数据不存在：{e}，请先完成取数（--plan 后按提示回填）。")
        return 1
    except ValueError as e:
        print(f"[error] {e}")
        print("请重新取数补全该分片后再运行。")
        return 1
    for note in merge_notes:
        print(f"[merge] {note}")
    if not raw:
        print("数据为空（所有分片均无标的），请先完成取数。")
        return 1
    name_map: dict[str, str] = {}
    try:
        name_map = {it.ts_code: it.name for it in load_watchlist(watchlist_path)}
    except FileNotFoundError:
        pass  # 清单缺失不影响形态判定，仅报告缺名称

    series_list: list[Series] = [rows_to_series(ts, rows) for ts, rows in raw.items()]
    hits = screen(series_list, params)

    lines: list[str] = []
    lines.append("# W底放量观察仓筛选报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 扫描标的数：{len(series_list)}")
    lines.append(f"- 命中数：{len(hits)}")
    lines.append("")
    if not hits:
        lines.append("> 本次无命中标的。")
    else:
        lines.append("| 代码 | 名称 | 左底 | 右底 | 确认日 | 量比 |")
        lines.append("|---|---|---|---|---|---|")
        for h in hits:
            name = name_map.get(h.symbol, "")
            lines.append(
                f"| {h.symbol} | {name} | {h.trough_a_price:.2f} | {h.trough_b_price:.2f} "
                f"| {h.confirm_date} | {h.volume_ratio:.2f} |"
            )
        lines.append("")
        for h in hits:
            name = name_map.get(h.symbol, "")
            lines.append(f"## {h.symbol}" + (f"（{name}）" if name else ""))
            for r in h.reasons:
                lines.append(f"- {r}")
            lines.append("")

    out_path = data_path.parent / f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告已保存：{out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="w_bottom_screen")
    ap.add_argument("--watchlist", default="output/watchlist/watchlist.yaml",
                    help="观察仓清单 YAML 路径")
    ap.add_argument("--plan", action="store_true", help="输出观察仓清单与待取数清单")
    ap.add_argument("--shards", type=int, default=None,
                    help="取数分片数（仅 --plan 有效）；缺省时池子超过 --shard-size 只自动分片")
    ap.add_argument("--shard-size", type=int, default=SHARD_SIZE_DEFAULT,
                    help=f"每片标的数（默认 {SHARD_SIZE_DEFAULT}），配合 --plan 自动分片")
    ap.add_argument("--data", help="回填的日线 JSON 路径（单文件），或分片目录（合并目录下全部 *.json）")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--trough-tol", type=float, default=0.03)
    ap.add_argument("--confirm-window", type=int, default=3)
    ap.add_argument("--ma-window", type=int, default=5)
    ap.add_argument("--anchor-window", type=int, default=5)
    args = ap.parse_args(argv)

    if args.plan:
        _plan(Path(args.watchlist), shards=args.shards, shard_size=args.shard_size)
        return 0
    if args.data:
        params = WBottomParams(
            lookback=args.lookback,
            trough_tol=args.trough_tol,
            confirm_window=args.confirm_window,
            ma_window=args.ma_window,
            anchor_window=args.anchor_window,
        )
        return _data(Path(args.watchlist), Path(args.data), params)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())