"""研报生成提示词。"""
from __future__ import annotations

import json
from typing import Any

ORCHESTRATOR_SYSTEM = """你是"A股/港股投资顾问智能体"，一名严谨、负责任的研究型投顾。
你的工作基于输入的结构化数据与知识库规则，输出面向普通投资者的中文研报。
要求：
- 结论先行，逻辑清晰，分点呈现；数据与观点严格来自输入，不臆造数字。
- 明确区分"事实/数据"与"分析/观点"；提示风险，不承诺收益。
- 本内容仅为研究参考，非投资建议。
"""


def build_research_prompt(
    symbol: str,
    name: str,
    valuation: dict[str, Any],
    screen: dict[str, Any],
    allocation: dict[str, Any],
    risk: list[dict[str, Any]],
    knowledge: list[str],
) -> str:
    payload = {
        "标的": {"symbol": symbol, "name": name},
        "估值分析": valuation,
        "选股信号": screen,
        "建议仓位": allocation,
        "风控提示": risk,
        "参考知识库规则": knowledge,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
