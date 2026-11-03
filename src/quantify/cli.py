"""quantify-agent 命令行入口。

用法示例：
  python -m quantify.cli research 600519 000333
  python -m quantify.cli research 600519 --market A --out output/reports
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="quantify", description="A股/港股投资顾问智能体")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_research = sub.add_parser("research", help="对一组标的做估值选股与研报生成")
    p_research.add_argument("symbols", nargs="+", help="标的代码，如 600519 000333")
    p_research.add_argument("--market", default="A", choices=["A", "HK"])
    p_research.add_argument("--out", default="output/reports")
    p_research.add_argument("--json", action="store_true", help="同时输出JSON结果")

    p_copy = sub.add_parser("copy", help="抄作业分析：抓取群作业链接→还原路线→回测")
    p_copy.add_argument("url", nargs="?", help="作业链接，如 http://.../dingall?a=xxx")
    p_copy.add_argument("--html", help="本地作业 HTML 文件（离线分析）")
    p_copy.add_argument("--quotes", help="标的日线 JSON 路径（回测用，{ts_code:[{date,open,close}]}）")
    p_copy.add_argument("--period-end", default=None, help="回测期末 YYYY-MM-DD")
    p_copy.add_argument("--out", default=None)

    p_wb = sub.add_parser("w-bottom", help="观察仓 W底放量形态筛选（离线部分：读清单/出取数清单）")
    p_wb.add_argument("--plan", action="store_true", help="输出观察仓清单与待取数清单")
    p_wb.add_argument("--data", help="回填的日线 JSON 路径（{ts_code:[{trade_date,open,high,low,close,vol}]}）")
    p_wb.add_argument("--watchlist", help="观察仓清单路径（默认 output/watchlist/watchlist.yaml）")
    p_wb.add_argument("--lookback", type=int, default=30)
    p_wb.add_argument("--trough-tol", type=float, default=0.03)
    p_wb.add_argument("--ma-window", type=int, default=5)

    p_sent = sub.add_parser("sentiment", help="情绪指标择时研究（基于沪深300ETF，跑分位/事件研究/回测）")
    p_sent.add_argument("--start", default="2013-01-01", help="评估起点（默认 2013-01-01）")
    p_sent.add_argument("--end", default=None, help="评估终点（默认今天）")
    p_sent.add_argument("--store", default="output/quotes-store", help="本地行情库目录")
    p_sent.add_argument("--cache", default="output/sentiment/cache", help="原始数据缓存目录")
    p_sent.add_argument("--out", default="output/sentiment", help="报告与明细输出目录")
    p_sent.add_argument("--token", default=None, help="tushare token（默认环境变量/.env）")
    p_sent.add_argument("--refresh", action="store_true", help="忽略缓存，重新拉取全部原始数据")
    p_sent.add_argument("--window", type=int, default=1250, help="滚动分位窗口（交易日，默认 5 年）")
    p_sent.add_argument("--min-periods", type=int, default=250, help="分位计算最少观测数")
    p_sent.add_argument("--cost-bps", type=float, default=2.5, help="单边交易成本（bp）")
    p_sent.add_argument("--horizons", default="20,60,120", help="事件研究前瞻窗口（交易日）")

    args = parser.parse_args(argv)

    if args.cmd == "research":
        _cmd_research(args)
    elif args.cmd == "copy":
        _cmd_copy(args)
    elif args.cmd == "w-bottom":
        _cmd_w_bottom(args)
    elif args.cmd == "sentiment":
        _cmd_sentiment(args)


def _cmd_copy(args: argparse.Namespace) -> None:
    """抄作业：转调 copy-trade 技能的 analyze 脚本。"""
    import subprocess
    import sys as _sys
    from pathlib import Path

    script = Path(".agents/skills/copy-trade/scripts/analyze.py")
    cmd = [_sys.executable, str(script)]
    if args.html:
        cmd += ["--html", args.html]
    elif args.url:
        cmd += [args.url]
    if args.quotes:
        cmd += ["--quotes", args.quotes]
    if args.period_end:
        cmd += ["--period-end", args.period_end]
    if args.out:
        cmd += ["--out", args.out]
    result = subprocess.run(cmd)
    raise SystemExit(result.returncode)


def _cmd_w_bottom(args: argparse.Namespace) -> None:
    """观察仓 W底筛选：转调自包含脚本 w_bottom_screen.py（读本地行情库判定，取数走 fetch_quotes.py）。"""
    import subprocess
    import sys as _sys
    from pathlib import Path

    script = Path("src/workspace-init/w_bottom_screen.py")
    cmd = [_sys.executable, str(script)]
    if args.watchlist:
        cmd += ["--watchlist", args.watchlist]
    if args.plan:
        cmd += ["--plan"]
    if args.data:
        cmd += ["--out-dir", args.data, "--lookback", str(args.lookback),
                "--trough-tol", str(args.trough_tol), "--ma-window", str(args.ma_window)]
    result = subprocess.run(cmd)
    raise SystemExit(result.returncode)


def _cmd_sentiment(args: argparse.Namespace) -> None:
    """情绪指标择时研究：取数 → 指标分位 → 事件研究 → 仓位回测 → 报告。"""
    import pandas as pd

    from quantify.analysis import sentiment as st
    from quantify.data import sentiment_data as sd
    from quantify.data.tushare_client import TushareError, resolve_token

    try:
        token = resolve_token(args.token)
    except TushareError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(2) from e

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    start_ts = pd.Timestamp(args.start)
    warmup_start = (start_ts - pd.DateOffset(years=5)).strftime("%Y-%m-%d")

    console.print(f"[bold]情绪择时研究[/bold] 评估 {args.start} → {end}，"
                  f"预热起点 {warmup_start}")
    raw = sd.load_raw_data(token, warmup_start, end, store_dir=args.store,
                           cache_dir=args.cache, refresh=args.refresh)
    ind = st.build_indicators(raw, window=args.window, min_periods=args.min_periods)

    eval_mask = ind.index >= start_ts
    ind_eval = ind.loc[eval_mask]
    adj_eval = raw.benchmark.loc[eval_mask]
    cash_eval = raw.cash_ret.reindex(adj_eval.index)
    if adj_eval.empty:
        console.print("[red]评估区间内没有基准行情，请检查 --start/--end。[/red]")
        raise SystemExit(1)

    horizons = tuple(int(x) for x in str(args.horizons).split(",") if x.strip())
    ev = st.event_study(ind_eval, adj_eval, horizons=horizons)

    pos_dynamic = st.position_from_heat_dynamic(ind["heat"]).reindex(adj_eval.index)
    pos_ladder = st.position_from_heat(ind["heat"]).reindex(adj_eval.index)
    ma_pos = st.ma_position(raw.benchmark).reindex(adj_eval.index)

    results = {
        "买入持有": st.backtest(adj_eval, cash_eval,
                             pd.Series(1.0, index=adj_eval.index), args.cost_bps),
        "均线择时(MA200)": st.backtest(adj_eval, cash_eval, ma_pos, args.cost_bps),
        "情绪择时(动态)": st.backtest(adj_eval, cash_eval, pos_dynamic, args.cost_bps),
        "情绪择时(分档)": st.backtest(adj_eval, cash_eval, pos_ladder, args.cost_bps),
    }
    passed, notes = st.judge(results["情绪择时(动态)"].stats,
                             results["买入持有"].stats)
    single = st.single_indicator_backtests(ind_eval, adj_eval, cash_eval, args.cost_bps)
    bs = st.bs_backtests(ind_eval, adj_eval, cash_eval, args.cost_bps)

    yearly = pd.DataFrame({
        name: st.yearly_returns(r.daily["strategy_ret"]) for name, r in results.items()
    })

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    ind.to_csv(out / f"indicators_{stamp}.csv", encoding="utf-8-sig")
    ev.to_csv(out / f"event_study_{stamp}.csv", index=False, encoding="utf-8-sig")
    results["情绪择时(动态)"].daily.to_csv(out / f"backtest_{stamp}.csv", encoding="utf-8-sig")
    pd.DataFrame([
        {"indicator": r.name, "start": r.start, "end": r.end,
         "dyn_cagr": r.dyn_stats["cagr"], "dyn_mdd": r.dyn_stats["mdd"],
         "dyn_calmar": r.dyn_stats["calmar"],
         "bench_calmar": r.dyn_bench_stats["calmar"],
         "dyn_exposure": r.dyn_stats["exposure"], "dyn_passed": r.dyn_passed,
         "ladder_cagr": r.stats["cagr"], "ladder_mdd": r.stats["mdd"],
         "ladder_calmar": r.stats["calmar"], "ladder_passed": r.passed}
        for r in single.values()
    ]).to_csv(out / f"single_indicator_{stamp}.csv", index=False, encoding="utf-8-sig")
    bs.to_csv(out / f"bs_backtest_{stamp}.csv", index=False, encoding="utf-8-sig")
    report = st.render_report(raw.meta, ind, ev, results, single, bs, yearly,
                              passed, notes, args.start)
    report_path = out / f"report_{stamp}.md"
    report_path.write_text(report, encoding="utf-8")

    table = Table(title="回测结果（510300 前复权，T+1 收盘执行）")
    for col in ("方案", "年化", "波动", "最大回撤", "Calmar", "Sharpe", "平均仓位"):
        table.add_column(col)
    for name, r in results.items():
        s = r.stats
        table.add_row(name, f"{s['cagr']:.1%}", f"{s['vol']:.1%}", f"{s['mdd']:.1%}",
                      f"{s['calmar']:.2f}", f"{s['sharpe']:.2f}", f"{s['exposure']:.0%}")
    console.print(table)

    single_table = Table(title="单指标动态仓位回测（各自有效样本内）")
    for col in ("指标", "起点", "年化", "最大回撤", "Calmar", "同期基准Calmar", "仓位", "判定"):
        single_table.add_column(col)
    for r in single.values():
        single_table.add_row(
            st.INDICATOR_LABELS.get(r.name, r.name), r.start,
            f"{r.dyn_stats['cagr']:.1%}", f"{r.dyn_stats['mdd']:.1%}",
            f"{r.dyn_stats['calmar']:.2f}", f"{r.dyn_bench_stats['calmar']:.2f}",
            f"{r.dyn_stats['exposure']:.0%}",
            "通过" if r.dyn_passed else "未通过",
        )
    console.print(single_table)

    if not bs.empty:
        bs_table = Table(title="二元 BS 回测（满仓/空仓，带滞回）")
        for col in ("指标", "规则", "年化", "最大回撤", "Calmar", "仓位", "调仓", "平均持有", "判定"):
            bs_table.add_column(col)
        for _, row in bs.iterrows():
            label = "复合热度" if row["indicator"] == "heat" \
                else st.INDICATOR_LABELS.get(row["indicator"], row["indicator"])
            bs_table.add_row(
                label, row["rule"], f"{row['cagr']:.1%}", f"{row['mdd']:.1%}",
                f"{row['calmar']:.2f}", f"{row['exposure']:.0%}",
                str(int(row["switches"])), f"{row['avg_hold_days']:.0f}日",
                "通过" if row["passed"] else "未通过",
            )
        console.print(bs_table)

    console.print(f"\n{'[green]判定：通过[/green]' if passed else '[yellow]判定：未通过[/yellow]'}")
    for n in notes:
        console.print(f"  - {n}")
    console.print(f"\n报告与明细：{report_path}")


def _cmd_research(args: argparse.Namespace) -> None:
    from quantify.agent.orchestrator import InvestAdvisor
    from quantify.agent.reporting import render_markdown

    advisor = InvestAdvisor()
    console.print(f"[bold]开始研究[/bold] 标的={args.symbols} 市场={args.market}")

    result = advisor.research(args.symbols, market=args.market)
    mode = "离线(示例数据)" if result.offline else "在线"
    console.print(f"[dim]数据模式：{mode}[/dim]")

    table = Table(title="估值/选股评分")
    table.add_column("标的")
    table.add_column("名称")
    table.add_column("评分")
    table.add_column("信号")
    table.add_column("PE")
    table.add_column("ROE中位")
    for it in result.screen:
        m = it.report.metrics if it.report else None
        table.add_row(
            it.symbol, it.name, f"{it.score:.0f}", it.signal,
            f"{m.pe_ttm:.1f}" if m and m.pe_ttm is not None else "-",
            f"{m.roe_median:.0f}%" if m and m.roe_median is not None else "-",
        )
    console.print(table)

    for r in result.risks:
        icon = "✅" if r.passed else "⚠️"
        console.print(f"  {icon} {r.title}: {r.message}")

    md = render_markdown(result)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"research_{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    console.print(f"\n[green]报告已保存[/green] {md_path}")

    if args.json:
        jpath = out_dir / f"research_{stamp}.json"
        jpath.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), "utf-8")
        console.print(f"[green]JSON已保存[/green] {jpath}")


if __name__ == "__main__":
    main()
