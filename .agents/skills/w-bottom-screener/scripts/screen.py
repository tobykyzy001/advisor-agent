"""W底放量观察仓筛选技能：端到端编排。

用法（三段式，取数由 agent 在会话内调 mcp__tushareMcp__daily 完成）：

  1) 列出观察仓 + 待取数清单：
     python .agents/skills/w-bottom-screener/scripts/screen.py --plan

  2) agent 在会话内对每个标的调 mcp__tushareMcp__daily 取近 N 日线，
     整理成 JSON（{ts_code: [ {trade_date,open,high,low,close,vol}, ... ]}），
     保存为 output/w-bottom/quotes.json。

  3) 跑形态判定 + 出报告：
     python .agents/skills/w-bottom-screener/scripts/screen.py --data output/w-bottom/quotes.json

输出：output/w-bottom/screen_<日期>.md（已 gitignore）

数据源约束：本技能「只用 tushare MCP」取日线，无 akshare 兜底、不写 SDK 直连。
脚本本身不调用 MCP（MCP 只在 agent 会话内可用），故离线可测。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parents[3] / "src"  # .agents/skills/../..  -> 仓库根/src
sys.path.insert(0, str(SRC_DIR))

from quantify.analysis.w_bottom import (  # noqa: E402
    WBottomParams,
    detect_w_bottom,
    screen_w_bottom,
)
from quantify.analysis.watchlist import load_watchlist  # noqa: E402
from quantify.data.schema import DailySeries  # noqa: E402
from quantify.data.tushare_adapter import rows_to_series  # noqa: E402

OUT_DIR = Path("output") / "w-bottom"


def _plan() -> None:
    """打印观察仓清单与待取数清单。"""
    try:
        items = load_watchlist()
    except FileNotFoundError as e:
        print(f"⚠️  {e}")
        print("请先运行 workspace-init 技能初始化工作区，或手动创建 output/watchlist/watchlist.yaml。")
        return
    if not items:
        print("观察仓清单为空，请先编辑 output/watchlist/watchlist.yaml。")
        return
    print("观察仓清单（%d 只）：" % len(items))
    for it in items:
        print(f"  - {it.ts_code}\t{it.name}\t{it.market}\t{it.note}")
    print()
    print("请对以下 ts_code 逐只调用 mcp__tushareMcp__daily：")
    for it in items:
        print(f"  ts_code={it.ts_code}")
    print()
    print("取数字段保留：trade_date, open, high, low, close, vol。")
    print("整理成 JSON 保存为 output/w-bottom/quotes.json，格式：")
    print('  {"600519.SH": [{"trade_date":"20250102","open":..,"high":..,"low":..,"close":..,"vol":..}, ...], ...}')


def _run(data_path: str, params: WBottomParams | None = None) -> int:
    """读回填的日线数据，跑 W底判定并出报告。"""
    p = Path(data_path)
    if not p.exists():
        print(f"数据文件不存在：{p}，请先完成取数。")
        return 1
    raw = json.loads(p.read_text(encoding="utf-8"))

    # 观察仓名称映射（用于报告展示）
    try:
        name_map = {it.ts_code: it.name for it in load_watchlist()}
    except FileNotFoundError:
        name_map = {}  # 清单缺失不影响形态判定，仅报告缺名称

    series_list: list[DailySeries] = []
    for ts_code, rows in raw.items():
        series_list.append(rows_to_series(ts_code, rows))

    hits = screen_w_bottom(series_list, params or WBottomParams())

    # 生成报告
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

    out = OUT_DIR / f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告已保存：{out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="w-bottom-screener")
    ap.add_argument("--plan", action="store_true", help="输出观察仓清单与待取数清单")
    ap.add_argument("--data", help="回填的日线 JSON 路径")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--trough-tol", type=float, default=0.03)
    ap.add_argument("--confirm-window", type=int, default=3)
    ap.add_argument("--ma-window", type=int, default=5)
    ap.add_argument("--anchor-window", type=int, default=5)
    args = ap.parse_args(argv)

    if args.plan:
        _plan()
        return 0
    if args.data:
        params = WBottomParams(
            lookback=args.lookback,
            trough_tol=args.trough_tol,
            confirm_window=args.confirm_window,
            ma_window=args.ma_window,
            anchor_window=args.anchor_window,
        )
        return _run(args.data, params)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())