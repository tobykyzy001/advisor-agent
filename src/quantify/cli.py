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

    args = parser.parse_args(argv)

    if args.cmd == "research":
        _cmd_research(args)


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
