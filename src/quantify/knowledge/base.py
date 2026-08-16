"""知识库规则的数据模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Rule(BaseModel):
    id: str
    category: str = "general"      # valuation | risk | strategy | screening
    title: str
    statement: str                 # 核心结论/建议
    rationale: str = ""            # 依据
    conditions: list[str] = Field(default_factory=list)  # 适用条件
    severity: str = "info"         # info | warn | critical
    source: str = ""               # 出处/依据来源


class KnowledgeBase(BaseModel):
    """全部已加载规则，支持按类别/关键词检索。"""

    rules: list[Rule] = Field(default_factory=list)

    def by_category(self, category: str) -> list[Rule]:
        return [r for r in self.rules if r.category == category]

    def by_severity(self, severity: str) -> list[Rule]:
        return [r for r in self.rules if r.severity == severity]

    def search(self, *keywords: str) -> list[Rule]:
        """关键词检索（标题/结论/依据/标签）。"""
        kws = [k.lower() for k in keywords if k]
        if not kws:
            return list(self.rules)
        hits: list[Rule] = []
        for r in self.rules:
            hay = " ".join(
                [r.id, r.title, r.statement, r.rationale, " ".join(r.conditions), r.category]
            ).lower()
            if all(k in hay for k in kws):
                hits.append(r)
        return hits
