"""风控模块：基于知识库规则检查组合与个股的合规告警。"""
from __future__ import annotations

from dataclasses import dataclass, field

from quantify.knowledge.base import Rule
from quantify.knowledge.repository import load_knowledge_base
from quantify.portfolio.allocation import Allocation


@dataclass
class RiskCheck:
    rule_id: str
    title: str
    severity: str
    passed: bool
    message: str

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "title": self.title,
                "severity": self.severity, "passed": self.passed, "message": self.message}


def check_portfolio(
    allocations: list[Allocation],
    kb: Rule | None = None,
    *,
    max_position_pct: float = 0.20,
    max_industry_pct: float = 0.30,
) -> list[RiskCheck]:
    """对建议组合执行风控规则检查。静态检查：单标的上限与行业集中(无行业数据时以单标的口径近似)。"""
    results: list[RiskCheck] = []
    rules = kb or load_knowledge_base()

    for rule in rules.by_category("risk"):
        rid = rule.id
        if rid == "RSK-001":
            worst = max((a.target_pct for a in allocations), default=0.0)
            passed = worst <= max_position_pct + 1e-9
            results.append(RiskCheck(rid, rule.title, rule.severity, passed,
                                     f"最高单标仓位 {worst:.1%}，上限 {max_position_pct:.0%}"))
        elif rid == "RSK-002":
            # 无行业数据时以单标的口径近似提示
            total = sum(a.target_pct for a in allocations)
            passed = total <= max_industry_pct + 1e-9
            results.append(RiskCheck(rid, rule.title, rule.severity, passed,
                                     f"组合总仓位 {total:.1%}(行业数据待补充)"))
        elif rid == "RSK-003":
            cash = 1.0 - sum(a.target_pct for a in allocations)
            results.append(RiskCheck(rid, rule.title, rule.severity, cash >= 0.05,
                                     f"现金缓冲 {cash:.1%}"))
        # RSK-004 回撤需历史序列，静态快照不做判断
    return results
