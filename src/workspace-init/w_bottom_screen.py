"""W底放量筛选：自包含单文件脚本（纯标准库，可下载即跑，不依赖 quantify 包）。

这是「观察仓 W底 + 放量」形态筛选的**唯一可执行真源**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py` 提供给目标工作区里的
agent 下载执行（与 workspace-init/init_workspace.py 同一分发模式）。

设计约束（与 init_workspace.py 一致）：
- 纯标准库（dataclass / csv / argparse / datetime / pathlib / re），零第三方依赖。
- 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。
- 脚本本身**不取行情**：取数统一走 fetch_quotes.py（直连 tushare REST API，
  增量刷库）；本脚本只从本地 CSV 行情库读数判定形态。

本地 CSV 行情库（与 momentum_strategy.py 共享 output/quotes-store/，由
fetch_quotes.py 刷库写回）：
- 每只标的一份 CSV：output/quotes-store/<ts_code>.csv，列 trade_date,open,high,low,close,vol。
- 判定前做数据门禁：库内无数据 / 最后交易日距今超过 10 个自然日的标的视为缺口，
  fail-closed 列出缺口并提示先跑 fetch_quotes.py，不拿陈旧数据误出形态。

两层式工作流：
  1) python fetch_quotes.py --watchlist output/watchlist/watchlist.yaml
         --min-bars 30 --full-days 90
         —— 刷库：对照库内最后交易日增量补到最新（新票/不足 30 根的全量重取），
         幂等合并写回 output/quotes-store/。
  2) python w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml
         —— 直接读库判定形态，输出命中报告 output/w-bottom/screen_<时间戳>.md。
  （--plan 仅作诊断：打印库内每只的根数/最后交易日/待补区间，不取数。）

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
import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

#: 数据门禁：库内最后交易日距今超过该自然日数视为数据过期（覆盖周末与长假）
MAX_STALE_DAYS = 10

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
        lo = _to_float(row.get("low"))
        c = _to_float(row.get("close"))
        if None in (o, h, lo, c):
            continue  # 脏数据/停牌日跳过
        d = _to_date(row.get("trade_date"))
        if d is None:
            continue
        vol = _to_float(row.get("vol")) or 0.0
        bars.append(Bar(date=d, open=o, high=h, low=lo, close=c, volume=vol))
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
# 行情库读取（数据由 fetch_quotes.py 刷库写回，本脚本只读）
# ═══════════════════════════════════════════════════════════════════════════


def read_store(store_dir: Path, ts_code: str) -> list[dict]:
    """读库内某标的全部行（按日期升序）；无文件返回空表。"""
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return []
    rows = _read_store_rows(fp)
    return sorted(rows, key=lambda r: r["trade_date"])


# ═══════════════════════════════════════════════════════════════════════════
# 本地 CSV 行情库（每标的一份，增量合并，幂等）
# ═══════════════════════════════════════════════════════════════════════════

#: CSV 列固定：与 --plan 提示的取数字段一致，momentum_strategy.py 共用同一库结构
CSV_FIELDS = ["trade_date", "open", "high", "low", "close", "vol"]


def store_csv_path(store_dir: Path, ts_code: str) -> Path:
    """库内某标的的 CSV 路径（文件名即 ts_code）。"""
    return store_dir / f"{ts_code}.csv"


def _read_store_rows(fp: Path) -> list[dict]:
    """读一份 CSV 为 dict 行（只保留 CSV_FIELDS 中存在的非空字段）。"""
    rows: list[dict] = []
    with fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            td = (r.get("trade_date") or "").strip()
            if not td:
                continue
            row = {"trade_date": td}
            for k in CSV_FIELDS[1:]:
                v = (r.get(k) or "").strip()
                if v:
                    row[k] = v
            rows.append(row)
    return rows


def store_status(store_dir: Path, ts_code: str) -> tuple[int, str | None]:
    """库内该标的的 (行数, 最后交易日)；无文件或空文件返回 (0, None)。"""
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return 0, None
    rows = _read_store_rows(fp)
    if not rows:
        return 0, None
    return len(rows), max(r["trade_date"] for r in rows)


def _next_date_str(trade_date: str) -> str:
    """'20250201'/'2025-02-01' → 次日 '20250202'（解析失败则原样返回）。"""
    d = _to_date(trade_date)
    if d is None:
        return trade_date
    return (d + timedelta(days=1)).strftime("%Y%m%d")


# ═══════════════════════════════════════════════════════════════════════════
# 两步子命令：--plan（列清单）/ --data（判形态出报告）
# ═══════════════════════════════════════════════════════════════════════════


def _plan(watchlist_path: Path, store_dir: Path, lookback: int) -> None:
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

    # 依据本地行情库给每只标注状态（与 fetch_quotes.py 的刷库口径一致）
    today = date.today().strftime("%Y%m%d")
    print(f"本地行情库：{store_dir}（每只一份 CSV，由 fetch_quotes.py 刷库写回）")
    for it in items:
        n, last = store_status(store_dir, it.ts_code)
        if n >= lookback and last:
            start = _next_date_str(last)
            if start > today:
                print(f"  {it.ts_code}  免取（库内已到 {last}，共 {n} 根）")
            else:
                print(f"  {it.ts_code}  待增量 {start} → {today}（库内 {n} 根，最新 {last}）")
        elif last:
            print(f"  {it.ts_code}  待全量（库内仅 {n} 根 < {lookback}，最新 {last}）")
        else:
            print(f"  {it.ts_code}  待全量（库内无数据）")
    print()
    print("刷库命令（增量自动补到最新交易日，新票/不足历史自动全量）：")
    print(f"  python fetch_quotes.py --watchlist {watchlist_path}"
          f" --min-bars {lookback} --full-days 90")
    print("刷库完成后直接判定（无需 --data，直接读库）：")
    print(f"  python w_bottom_screen.py --watchlist {watchlist_path}")


def _run(watchlist_path: Path, out_dir: Path, params: WBottomParams,
         store_dir: Path) -> int:
    try:
        items = load_watchlist(watchlist_path)
    except FileNotFoundError as e:
        print(f"[error] {e}")
        return 1
    if not items:
        print("[error] 观察仓清单为空，请先编辑 output/watchlist/watchlist.yaml。")
        return 1

    # 数据门禁：读库 + 新鲜度校验（缺口 fail-closed，不拿陈旧数据误出形态）
    today = date.today()
    raw: dict[str, list] = {}
    gaps: list[str] = []
    for it in items:
        rows = read_store(store_dir, it.ts_code)
        if not rows:
            gaps.append(f"{it.ts_code}（库内无数据）")
            continue
        last = _to_date(rows[-1]["trade_date"])
        if last is None or (today - last).days > MAX_STALE_DAYS:
            gaps.append(f"{it.ts_code}（库内最新 {rows[-1]['trade_date']}，距今超过 "
                        f"{MAX_STALE_DAYS} 自然日）")
            continue
        raw[it.ts_code] = rows
    if gaps:
        print("[error] 以下标的行情库数据缺失或过期，本次 fail-closed 不判定：")
        for g in gaps:
            print(f"  - {g}")
        print(f"请先刷库：python fetch_quotes.py --watchlist {watchlist_path}"
              f" --min-bars {params.lookback} --full-days 90")
        return 1

    name_map = {it.ts_code: it.name for it in items}
    series_list: list[Series] = [rows_to_series(ts, rows) for ts, rows in raw.items()]
    hits = screen(series_list, params)

    # 数据截止日 = 库内全部标的的最大交易日（写进报告，标注口径）
    as_of = max(rows[-1]["trade_date"] for rows in raw.values())

    lines: list[str] = []
    lines.append("# W底放量观察仓筛选报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据截止：{as_of}（本地行情库 {store_dir}，由 fetch_quotes.py 刷库）")
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

    out_path = out_dir / f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告已保存：{out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="w_bottom_screen")
    ap.add_argument("--watchlist", default="output/watchlist/watchlist.yaml",
                    help="观察仓清单 YAML 路径")
    ap.add_argument("--plan", action="store_true",
                    help="仅诊断：打印观察仓清单与库内每只状态（不取数、不判定）")
    ap.add_argument("--out-dir", default="output/w-bottom",
                    help="命中报告输出目录（默认 output/w-bottom）")
    ap.add_argument("--store", default="output/quotes-store",
                    help="本地 CSV 行情库目录（默认 output/quotes-store，"
                         "由 fetch_quotes.py 刷库写回，本脚本只读）")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--trough-tol", type=float, default=0.03)
    ap.add_argument("--confirm-window", type=int, default=3)
    ap.add_argument("--ma-window", type=int, default=5)
    ap.add_argument("--anchor-window", type=int, default=5)
    args = ap.parse_args(argv)

    params = WBottomParams(
        lookback=args.lookback,
        trough_tol=args.trough_tol,
        confirm_window=args.confirm_window,
        ma_window=args.ma_window,
        anchor_window=args.anchor_window,
    )

    if args.plan:
        _plan(Path(args.watchlist), Path(args.store), lookback=args.lookback)
        return 0
    return _run(Path(args.watchlist), Path(args.out_dir), params, Path(args.store))


if __name__ == "__main__":
    raise SystemExit(main())