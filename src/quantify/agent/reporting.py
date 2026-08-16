"""把 ResearchResult 渲染为 Markdown 报告，便于人读与存档。"""
from __future__ import annotations

from datetime import date

from quantify.agent.orchestrator import ResearchResult


def render_markdown(result: ResearchResult) -> str:
    lines: list[str] = []
    lines.append("# 投资研究报告")
    lines.append("")
    lines.append(f"- 生成日期：{date.today().isoformat()}")
    lines.append(f"- 数据模式：{'离线(示例数据)' if result.offline else '在线数据源'}")
    lines.append(f"- 市场基准：{result.market.benchmark}")
    if result.market.risk_free_rate is not None:
        lines.append(f"- 无风险利率：{result.market.risk_free_rate:.2%}")
    lines.append("")

    lines.append("## 估值/选股评分")
    lines.append("")
    lines.append("| 标的 | 名称 | 评分 | 信号 | PE-TTM | ROE中位数 |")
    lines.append("|------|------|------|------|--------|-----------|")
    for it in result.screen:
        m = it.report.metrics if it.report else None
        pe = f"{m.pe_ttm:.1f}" if m and m.pe_ttm is not None else "-"
        roe = f"{m.roe_median:.0f}%" if m and m.roe_median is not None else "-"
        lines.append(f"| {it.symbol} | {it.name} | {it.score:.0f} | {it.signal} | {pe} | {roe} |")
    lines.append("")

    lines.append("## 建议仓位")
    lines.append("")
    for a in result.allocations:
        lines.append(f"- **{a.name}({a.symbol})** {a.signal}：目标仓位 {a.target_pct:.0%}")
        details = "；".join(a.reasons)
        if details:
            lines.append(f"  - {details}")
    lines.append(f"- 现金/缓冲：{max(0.0, 1.0 - sum(x.target_pct for x in result.allocations)):.0%}")
    lines.append("")

    lines.append("## 风控提示")
    lines.append("")
    if not result.risks:
        lines.append("- 无（暂未配置检查规则）")
    for r in result.risks:
        icon = "✅" if r.passed else "⚠️"
        lines.append(f"- {icon} **{r.title}**（{r.rule_id}）：{r.message}")
    lines.append("")

    lines.append("## 投顾结论 / 研报")
    lines.append("")
    lines.append(result.report_text.strip() or "_（无可生成内容）_")
    lines.append("")
    lines.append("---")
    lines.append("_本研究仅作研究参考，不构成投资建议。数据可能存在延迟或误差。_")
    return "\n".join(lines)
