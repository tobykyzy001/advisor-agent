"""抄作业技能配套：端到端编排 —— 解析→事件→映射→回测→「是否值得抄」结论。

用法：
  python .agents/skills/copy-trade/scripts/analyze.py "<链接>" [--period-end YYYY-MM-DD]

本脚本负责「确定性」部分（抓取/解析/事件/映射/回测骨架），并把需要联网的
tushare 环节拆成明确的「取数步骤」打印给 agent：agent 在会话内调
mcp__tushareMcp__daily 取日K、调 stock_basic 反查名称后，回填 quotes 再运行
backtest。这样脚本离线可测、联网由 agent 按需补齐。

输出：output/copy-trade/analysis_<作者>.md （已 gitignore）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_homework import parse_messages, _open_http, derive_author  # noqa: E402
from symbol_map import load_map, resolve_alias, know_themes  # noqa: E402
from timeline import extract_events, split_themes  # noqa: E402

OUT_DIR = Path("output") / "copy-trade"


def load_messages(args) -> tuple[list[dict], str]:
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
        author = "local"
    elif args.url:
        html = _open_http(args.url).decode("utf-8", "ignore")
        author = derive_author(args.url)
    else:
        raise SystemExit("需要 --html 或 url 参数")
    return parse_messages(html), author


def infer_themes(messages: list[dict], amap: dict) -> dict[str, list[str]]:
    """归集每条消息命中的题材 → 已确认/待确认状态。"""
    theme_set = know_themes(amap)
    confirmed: dict[str, list[str]] = {}
    unconfirmed: set[str] = set()
    for m in messages:
        body = m.get("正文", "")
        if not body or m.get("是否撤回"):
            continue
        for t in split_themes(body, theme_set):
            cand = resolve_alias(t, amap)
            if len(cand) == 1:
                confirmed.setdefault(t, cand)
            else:
                unconfirmed.add(t)
    return confirmed, unconfirmed


def _need_quotes(confirmed: dict[str, list[str]]) -> list[str]:
    """回测需要哪些标的的日K。"""
    out: list[str] = []
    for cands in confirmed.values():
        for c in cands:
            if c not in out:
                out.append(c)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="copy-trade analyze")
    ap.add_argument("url", nargs="?", help="作业链接")
    ap.add_argument("--html", help="本地 HTML")
    ap.add_argument("--quotes", help="已拉取的日线 JSON 路径（{ts_code:[{date,open,close}]}）")
    ap.add_argument("--period-end", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    messages, author = load_messages(args)
    amap = load_map()
    confirmed, unconfirmed = infer_themes(messages, amap)
    events = extract_events(messages)

    lines: list[str] = []
    lines.append(f"# 抄作业分析 · {author}")
    lines.append(f"- 抓取/分析时点：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 消息数：{len(messages)}，信号事件数：{len(events)}")
    lines.append("")

    lines.append("## 1. 已确认题材 → 标的")
    for t, cand in sorted(confirmed.items()):
        lines.append(f"- **{t}** → {', '.join(cand)}")
    if unconfirmed:
        lines.append("\n## 2. 待确认题材（未纳入回测）")
        for t in sorted(unconfirmed):
            lines.append(f"- {t}")
    lines.append("")

    # 信号时间线（最近 40 条事件）
    lines.append("## 3. 信号事件时间线（最近，倒序）")
    for ev in sorted(events, key=lambda e: e.ts, reverse=True)[:40]:
        lines.append(f"- `{ev.ts}` [{ev.speaker}] **{ev.action}** {ev.raw[:60]}")
    lines.append("")

    # 回测（若给了 quotes）
    if args.quotes:
        quotes = json.loads(Path(args.quotes).read_text(encoding="utf-8"))
        from backtest import resolve_signals, run_backtest, FullSignal
        signals = resolve_signals(messages, amap)
        period_end = args.period_end or ""
        result = run_backtest(signals, quotes, period_end)
        d = result.to_dict()
        lines.append("## 4. 轻量回测（近似，非精确业绩）")
        lines.append(f"- 总收益(等权)≈ {d['总收益%']}% · 最大回撤≈ {d['最大回撤%']}% · 胜率≈ {d['胜率%']}%")
        lines.append(f"- 交易笔数：{len(d['trades'])}")
        for t in d["trades"]:
            lines.append(
                f"- {t['ts_code']} {t['buy_date']}({t['buy_price']:.2f}) → "
                f"{t['sell_date']}({t['sell_price']:.2f}) : {t['ret_pct']:+.2f}%"
            )
        lines.append("")
    else:
        needs = _need_quotes(confirmed)
        lines.append("## 4. 回测待补数据（需 agent 调 tushare 拉日K）")
        lines.append("- 标的：" + (", ".join(needs) if needs else "无"))
        lines.append("- 命令提示：`mcp__tushareMcp__daily` 逐只拉，json 化后写 quotes.json 再回填 `--quotes`")
        lines.append("")

    lines.append("> 免责：对第三方作业整理与近似回测，仅供参考，不构成投资建议。")
    out_path = Path(args.out) if args.out else OUT_DIR / f"analysis_{author}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))