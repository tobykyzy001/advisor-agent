"""知识库 / 配置 / 端到端冒烟测试。"""
from quantify.config import load_settings, Settings
from quantify.knowledge.repository import load_knowledge_base, load_rules
from quantify.agent.orchestrator import InvestAdvisor


def test_load_settings_defaults():
    s = load_settings()
    assert s.valuation.pe_band == [8.0, 25.0]
    assert s.portfolio.cash_buffer_min == 0.05


def test_knowledge_base_loads_rules():
    rules = load_rules()
    assert any(r.id == "VAL-002" for r in rules)
    assert any(r.category == "risk" for r in rules)


def test_knowledge_search():
    kb = load_knowledge_base()
    hit = kb.search("PEG")
    assert any("PEG" in r.title for r in hit)


def test_end_to_end_local():
    """离线端到端流水线：数据→估值→选股→配仓→风控→研报。"""
    s = Settings()
    s.data.provider = "local"
    advisor = InvestAdvisor(s)
    r = advisor.research(["600519", "000333"])
    assert len(r.screen) == 2
    assert r.screen[0].symbol in {"600519", "000333"}
    assert r.allocations
    assert r.report_text
    # 风控结构齐全
    assert any(chk.rule_id == "RSK-001" for chk in r.risks)
