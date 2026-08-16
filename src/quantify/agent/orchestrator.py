"""智能体编排：串联 数据 → 估值 → 选股 → 配仓 → 风控 → 研报。"""
from __future__ import annotations

from dataclasses import dataclass, field

from quantify.agent.llm import RuleBasedReport, build_chat_model
from quantify.agent.prompts import build_research_prompt
from quantify.analysis.market import MarketContext, build_market_context
from quantify.analysis.screener import ScreenItem, screen_many
from quantify.config import Settings, get_settings
from quantify.data.fetcher import BaseProvider, resolve_provider
from quantify.knowledge.repository import load_knowledge_base
from quantify.portfolio.allocation import Allocation, suggest_allocations
from quantify.portfolio.risk import RiskCheck, check_portfolio


@dataclass
class ResearchResult:
    market: MarketContext
    screen: list[ScreenItem]
    allocations: list[Allocation]
    risks: list[RiskCheck]
    report_text: str
    offline: bool = False
    used_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "market": self.market.to_dict(),
            "screen": [s.to_dict() for s in self.screen],
            "allocations": [a.to_dict() for a in self.allocations],
            "risks": [r.to_dict() for r in self.risks],
            "offline": self.offline,
            "report": self.report_text,
        }


class InvestAdvisor:
    """投资顾问智能体。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider: BaseProvider = resolve_provider(self.settings)
        self.kb = load_knowledge_base()
        self.model = build_chat_model(self.settings)

    def research(self, symbols: list[str], market: str = "A") -> ResearchResult:
        s = self.settings
        bundles = [self.provider.fetch_bundle(sym, market) for sym in symbols]

        items = screen_many(bundles, s)
        allocations = suggest_allocations(items, s)
        risks = check_portfolio(allocations, self.kb,
                                max_position_pct=s.portfolio.max_position_pct,
                                max_industry_pct=s.portfolio.max_single_industry_pct)
        market_ctx = build_market_context(s)

        # 组装研报
        sections: list[str] = []
        for sym in symbols:
            it = next((x for x in items if x.symbol == sym), None)
            if not it:
                continue
            al = next((a for a in allocations if a.symbol == sym), Allocation(symbol=sym))
            kb_ref = [r.title for r in
                      self.kb.search(*([it.signal] if it.signal != "观望" else ["估值"]))]
            prompt = build_research_prompt(
                symbol=it.symbol, name=it.name,
                valuation=it.report.to_dict() if it.report else {},
                screen=it.to_dict(),
                allocation=al.to_dict(),
                risk=[r.to_dict() for r in risks if not r.passed],
                knowledge=kb_ref,
            )
            section = self.model.generate(
                prompt, max_tokens=s.llm.max_tokens, temperature=s.llm.temperature
            )
            sections.append(section)

        report_text = "\n\n".join(sections)
        offline = isinstance(self.model, RuleBasedReport)
        return ResearchResult(
            market=market_ctx,
            screen=items,
            allocations=allocations,
            risks=risks,
            report_text=report_text,
            offline=offline,
            used_symbols=list(symbols),
        )
