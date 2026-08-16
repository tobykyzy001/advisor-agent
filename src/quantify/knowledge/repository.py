"""知识库加载与检索。从 YAML 规则文件构建 KnowledgeBase。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from quantify.config import Settings
from quantify.knowledge.base import KnowledgeBase, Rule

RULES_DIR = Path(__file__).resolve().parent / "rules"


def load_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    rules: list[Rule] = []
    for yaml_file in sorted(rules_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or []
        for item in data:
            rules.append(Rule(**item))
    return rules


@lru_cache
def load_knowledge_base(rules_dir: str | Path | None = None) -> KnowledgeBase:
    """加载完整知识库（对路径做缓存）。

    传入自定义 rules_dir 时按路径缓存；缺省用包内内置规则。
    """
    target = Path(rules_dir) if rules_dir else RULES_DIR
    return KnowledgeBase(rules=load_rules(target))
