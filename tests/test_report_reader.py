"""研报阅读器（report_reader.py）的单元测试。

测试对象是自包含脚本 src/workspace-init/report_reader.py（纯标准库）。通过 importlib
把脚本当模块导入，保证测的正是「分发出去的同一份真源」。

全程离线：URL 抓取用假 opener 拦截，其余用例只喂本地文本与证据文件。
测试重点不是「抽得全不全」，而是**采信原则**：拿不出证据证伪时，必须原样采信研报估算，
只有拿到证据（内部矛盾 / 与实测冲突）才允许替换或作废主张。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "workspace-init" / "report_reader.py"


@pytest.fixture()
def tmp_path():
    """本仓库工作区内的临时目录（不用系统 temp：会话沙箱下系统临时目录不可写）。"""
    root = (Path(__file__).resolve().parents[1] / ".tmp" / "test_report_reader"
            / uuid.uuid4().hex[:10])
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _load_module():
    spec = importlib.util.spec_from_file_location("report_reader", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["report_reader"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


rr = _load_module()

# ─────────────────────────────────────────────────────────────────────────
# 语料：一份自洽的研报（2027E EPS 87.6 × 25 倍 = 2190 元 = 目标价）
# ─────────────────────────────────────────────────────────────────────────

REPORT = """\
XX证券研究报告 | 食品饮料 | 白酒

贵州茅台（600519.SH）2025 年报点评：量价齐升，维持“买入”评级

投资评级：买入（维持）
目标价：2190.00 元
报告日期：2026年3月15日

| 指标 | 2024A | 2025A | 2026E | 2027E |
|---|---|---|---|---|
| 营业收入（百万元） | 150,000 | 170,000 | 195,000 | 220,000 |
| 归母净利润（百万元） | 70,000 | 80,000 | 95,000 | 110,000 |
| 每股收益（元） | 55.7 | 63.7 | 75.6 | 87.6 |
| 同比增长率（%） | 10.5 | 14.3 | 18.8 | 15.8 |

给予公司 2027 年 25 倍 PE，对应目标价 2190 元。
风险提示：宏观需求疲弱、渠道库存高企。
"""

# fetch_quotes.py --snapshot 的原样输出（总市值单位为万元）
SNAPSHOT = """\
===== 600519.SH 贵州茅台 =====
  日期：2026-09-11（数据时点：tushare 日线）
  收盘：1,520.00  涨跌幅：+1.20%  成交量：123,456.00 手
  换手率：0.35%  PE(TTM)：21.50  PE(静)：22.80  PB：7.50
  总市值：191,000,000.00万  流通市值：191,000,000.00万  股息率(TTM)：3.20%
"""

# fetch_forecast.py 的原样输出片段
CONSENSUS = """\
===== 600519.SH 贵州茅台（同花顺 F10 机构业绩预测） =====
抓取时点：2026-09-14 14:50:00（来源：basic.10jqka.com.cn/600519/worth.html，A 股）
【净利润一致预期（亿元）】
| 年度 | 机构数 | 最小值 | 均值 | 最大值 | 行业平均 |
| 2026E | 32 | 900.00 | 930.00 | 980.00 | 850.00 |
| 2027E | 30 | 1,000.00 | 1,060.00 | 1,150.00 | 950.00 |
"""

AS_OF = date(2026, 6, 1)


def _pipeline(claims, snap=None, cons=None, pe_band=(15.0, 30.0)):
    """跑一遍「核查 → 采信 → 估值桥」，返回三段结果。"""
    findings, overrides, rejected = rr.critique(claims, snap, cons, AS_OF)
    adoption = rr.adopt(claims, findings, overrides, rejected)
    bridge = rr.build_bridge(adoption, claims, snap, cons, pe_band, AS_OF)
    return findings, adoption, bridge


def _levels(findings, code):
    return [f.level for f in findings if f.code == code]


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────
# 一、抽取
# ─────────────────────────────────────────────────────────────────────────


def test_extract_core_fields_from_table():
    c = rr.extract_claims(REPORT)
    assert c.symbol == "600519.SH"
    assert c.name == "贵州茅台"
    assert c.rating == "买入"
    assert c.target_price == 2190.0
    assert c.report_date == date(2026, 3, 15)
    assert (c.basis_year, c.basis_pe) == (2027, 25.0)
    # A 列 → 已披露实际值；E 列 → 预测
    assert sorted(c.actuals) == [2024, 2025]
    assert sorted(c.forecasts) == [2026, 2027]
    assert c.forecasts[2026].net_profit == 95000.0
    assert c.forecasts[2027].eps == 87.6
    assert c.actuals[2025].revenue == 170000.0
    # 增速行按惯例归属归母净利润
    assert c.forecasts[2026].net_profit_yoy == 18.8
    assert c.forecasts[2027].net_profit_yoy == 15.8


def test_extract_inline_forecast_when_no_table():
    text = ("某某证券研究报告\n预计公司 2026-2028 年归母净利润分别为 "
            "95.00 亿元、110.00 亿元、129.00 亿元，维持买入评级。\n"
            "目标价：2000 元\n报告日期：2026-03-15\n")
    c = rr.extract_claims(text)
    assert sorted(c.forecasts) == [2026, 2027, 2028]
    assert c.forecasts[2026].net_profit == 9500.0     # 亿元 → 百万元
    assert c.forecasts[2028].net_profit == 12900.0


def test_missing_fields_are_reported_as_gaps_not_invented():
    c = rr.extract_claims("这是一份没有任何关键数字的文本。")
    assert c.target_price is None and c.rating is None and not c.forecasts
    joined = "；".join(c.gaps)
    assert "目标价" in joined and "分年度预测" in joined


def test_html_source_keeps_table_cells_on_one_line():
    html = ("<html><body><table>"
            "<tr><td>指标</td><td>2026E</td><td>2027E</td></tr>"
            "<tr><td>归母净利润（百万元）</td><td>95,000</td><td>110,000</td></tr>"
            "</table></body></html>")
    text = rr.html_to_text(html)
    assert "归母净利润（百万元） | 95,000 | 110,000" in text
    c = rr.extract_claims(text)
    assert c.forecasts[2026].net_profit == 95000.0


# ─────────────────────────────────────────────────────────────────────────
# 二、核心原则：拿不出证据 → 原样采信
# ─────────────────────────────────────────────────────────────────────────


def test_no_evidence_means_full_adoption_of_report_numbers():
    """全部自洽、无外部证据：每条主张都必须原样采信，数字一个不许改。"""
    c = rr.extract_claims(REPORT)
    findings, adoption, bridge = _pipeline(c)
    assert [f.level for f in findings if f.level == "falsified"] == []
    assert adoption.overrides == [] and adoption.rejections == []
    assert adoption.target_price == 2190.0 and adoption.target_price_status == "assumed"
    assert adoption.forecasts[2026].net_profit == 95000.0
    assert adoption.forecasts[2027].net_profit == 110000.0
    assert adoption.forecasts[2027].eps == 87.6
    # 采信后估值桥照常出结论（缺现价时如实说明，而不是编数）
    assert "缺现价" in bridge.verdict


def test_suspicion_never_changes_numbers():
    """可疑项（报告偏旧 / 偏离一致预期 / 增速异常）只登记，绝不改数字。"""
    old = REPORT.replace("2026年3月15日", "2024年1月10日")
    c = rr.extract_claims(old)
    cons = rr.parse_consensus(CONSENSUS.replace("1,060.00", "600.00"))
    findings, adoption, _ = _pipeline(c, rr.parse_snapshot(SNAPSHOT), cons)
    assert "suspicious" in _levels(findings, "A5")                 # 报告偏旧
    assert "suspicious" in _levels(findings, "A6")                 # 与一致预期分歧大
    assert adoption.overrides == []                               # 存疑不产生任何替换
    assert adoption.forecasts[2027].net_profit == 110000.0         # 数字原样采信
    assert adoption.caveats                                        # 但疑点必须留痕


def test_consensus_divergence_does_not_override_report():
    c = rr.extract_claims(REPORT)
    cons = rr.parse_consensus(CONSENSUS.replace("930.00", "500.00").replace("1,060.00", "600.00"))
    findings, adoption, _ = _pipeline(c, rr.parse_snapshot(SNAPSHOT), cons)
    assert _levels(findings, "A6").count("suspicious") == 2
    assert adoption.forecasts[2026].net_profit == 95000.0
    assert adoption.forecasts[2027].net_profit == 110000.0
    # 差异必须如实给出百分比，供人工定性
    assert any("偏离" in f.detail for f in findings if f.code == "A6")


# ─────────────────────────────────────────────────────────────────────────
# 三、有证据证伪：替换或作废
# ─────────────────────────────────────────────────────────────────────────


def test_target_price_arithmetic_conflict_is_replaced_by_report_own_anchor():
    text = REPORT.replace("目标价：2190.00 元", "目标价：1800.00 元")
    c = rr.extract_claims(text)
    findings, adoption, bridge = _pipeline(c)
    assert "falsified" in _levels(findings, "A1")
    assert adoption.target_price == 2190.0            # 用报告自己的锚重算
    assert adoption.target_price_status == "overridden"
    assert bridge.anchor_price == 2190.0


def test_growth_mismatch_adopts_table_value_over_wording():
    """行文增速与表格绝对值矛盾：按证据等级采信表格数（复算增速），不是改绝对值。"""
    text = REPORT.replace("| 归母净利润（百万元） | 70,000 | 80,000 | 95,000 | 110,000 |",
                          "| 归母净利润（百万元） | 70,000 | 80,000 | 100,000 | 110,000 |")
    c = rr.extract_claims(text)
    findings, adoption, bridge = _pipeline(c)
    assert "falsified" in _levels(findings, "A2")
    assert adoption.forecasts[2026].net_profit == 100000.0     # 绝对值不动
    assert adoption.forecasts[2026].net_profit_yoy == 25.0     # 增速按复算替换
    assert bridge.growth_pct == 25.0 if bridge.forward_year == 2026 else True


def test_actual_beats_forecast_same_year():
    c = rr.extract_claims(REPORT)
    c.forecasts[2026].net_profit = 95000.0
    c.actuals[2026] = rr.Forecast(year=2026, net_profit=60000.0)
    findings, adoption, _ = _pipeline(c)
    assert "falsified" in _levels(findings, "A4")
    assert adoption.forecasts[2026].net_profit == 60000.0      # 实测优先
    assert any(o.field == "net_profit" for o in adoption.overrides)


def test_bullish_rating_with_target_below_price_rejects_target():
    text = REPORT.replace("目标价：2190.00 元", "目标价：1400.00 元")
    c = rr.extract_claims(text)
    findings, adoption, _ = _pipeline(c, rr.parse_snapshot(SNAPSHOT))
    assert "falsified" in _levels(findings, "A1b")
    assert any("目标价" in r for r in adoption.rejections)


def test_wildly_inconsistent_eps_is_rejected_and_valuation_stays_on_market_cap_path():
    """EPS 与净利/股本差 20% 以上 → 报告 EPS 作废；估值主口径（市值+净利）不受影响。"""
    text = REPORT.replace("| 每股收益（元） | 55.7 | 63.7 | 75.6 | 87.6 |",
                          "| 每股收益（元） | 55.7 | 63.7 | 75.6 | 50.0 |")
    c = rr.extract_claims(text)
    findings, adoption, bridge = _pipeline(c, rr.parse_snapshot(SNAPSHOT))
    assert "falsified" in _levels(findings, "A3")
    assert all(fc.eps is None for fc in adoption.forecasts.values())
    assert adoption.rejections                                   # 作废必须留痕
    assert bridge.forward_pe == pytest.approx(19100 / 1100, rel=1e-3)   # 仍算得出前瞻PE
    assert adoption.forecasts[2027].net_profit == 110000.0


def test_eps_used_for_anchor_falls_back_to_derived_when_report_eps_conflicts():
    """EPS 与净利矛盾时，复算目标价不得继续用一个自相矛盾的 EPS。"""
    text = REPORT.replace("| 每股收益（元） | 55.7 | 63.7 | 75.6 | 87.6 |",
                          "| 每股收益（元） | 55.7 | 63.7 | 75.6 | 50.0 |")
    c = rr.extract_claims(text)
    findings, adoption, _ = _pipeline(c, rr.parse_snapshot(SNAPSHOT))
    a1 = next(f for f in findings if f.code == "A1")
    assert "推算" in a1.detail
    assert adoption.target_price == pytest.approx(round(87.5393 * 25, 2), rel=0.02)


# ─────────────────────────────────────────────────────────────────────────
# 四、估值桥
# ─────────────────────────────────────────────────────────────────────────


def test_bridge_math_uses_adopted_forecasts():
    c = rr.extract_claims(REPORT)
    snap = rr.parse_snapshot(SNAPSHOT)
    _, _, bridge = _pipeline(c, snap, None, pe_band=(15.0, 30.0))
    assert snap.price == 1520.0
    assert snap.mcap_yi == pytest.approx(19100.0)          # 万元 → 亿元
    # 前瞻年跟随研报自述锚年（不偷换成最远年份）
    assert bridge.forward_year == 2027
    assert bridge.forward_pe == pytest.approx(17.36, abs=0.01)
    assert bridge.peg == pytest.approx(1.1, abs=0.01)
    assert bridge.target_upside_pct == pytest.approx(44.1, abs=0.1)
    assert bridge.fair_low == pytest.approx(1313.36, abs=0.5)
    assert bridge.required_growth_pct == bridge.forward_pe
    assert bridge.pe_by_year[2026] == pytest.approx(20.11, abs=0.01)
    assert "合理" in bridge.verdict


def test_bridge_verdict_tiers():
    c = rr.extract_claims(REPORT)
    snap = rr.parse_snapshot(SNAPSHOT)
    _, _, cheap = _pipeline(c, snap, None, pe_band=(20.0, 30.0))    # 前瞻PE < 保守倍数
    _, _, rich = _pipeline(c, snap, None, pe_band=(8.0, 12.0))       # 前瞻PE > 中性倍数
    assert cheap.verdict.startswith("便宜")
    assert rich.verdict.startswith("贵")


def test_bridge_reports_consensus_forward_pe_and_gap():
    c = rr.extract_claims(REPORT)
    _, _, bridge = _pipeline(c, rr.parse_snapshot(SNAPSHOT), rr.parse_consensus(CONSENSUS))
    assert bridge.consensus_forward_pe == pytest.approx(18.02, abs=0.01)
    assert bridge.consensus_gap[2027] == pytest.approx(3.8, abs=0.1)


def test_bridge_without_snapshot_is_honest():
    c = rr.extract_claims(REPORT)
    _, _, bridge = _pipeline(c)
    assert bridge.forward_pe is None and bridge.fair_low is None
    assert "缺现价" in bridge.verdict


def test_parse_consensus_handles_missing_tables():
    cons = rr.parse_consensus("===== 600519.SH 茅台 =====\n【净利润一致预期（亿元）】\n")
    assert cons.np_mean_yi == {}


# ─────────────────────────────────────────────────────────────────────────
# 五、入口（文件 / URL / PDF）、--claims、落盘
# ─────────────────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: bytes, ctype: str = "text/html; charset=utf-8"):
        self._payload = payload
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_url_input_goes_through_http_and_strips_html(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResp("<html><body><p>目标价：2190 元</p></body></html>".encode())

    monkeypatch.setattr(rr.urllib.request, "urlopen", fake_urlopen)
    src = rr.load_source("https://example.com/rpt.html")
    assert seen["url"] == "https://example.com/rpt.html"
    assert src.kind == "url"
    assert "目标价：2190 元" in src.text and "<p>" not in src.text


def test_url_failure_is_fail_closed(monkeypatch):
    def boom(req, timeout=None):
        raise rr.urllib.error.URLError("connection refused")

    monkeypatch.setattr(rr.urllib.request, "urlopen", boom)
    with pytest.raises(rr.InputUnavailable):
        rr.load_source("https://example.com/rpt.html")


def test_pdf_input_is_rejected_with_clear_gap(tmp_path):
    pdf = _write(tmp_path, "rpt.pdf", "%PDF-1.7 ...")
    with pytest.raises(rr.InputUnavailable) as exc:
        rr.load_source(str(pdf))
    assert "PDF" in str(exc.value)
    # CLI 层面：退出码 2，绝不产出编造的报告
    code = rr.main(["--input", str(pdf), "--no-report"])
    assert code == 2


def test_claims_json_overrides_auto_extraction(tmp_path):
    claims_file = _write(tmp_path, "claims.json", json.dumps({
        "symbol": "600519.SH", "name": "贵州茅台", "broker": "XX证券", "analyst": "张三",
        "report_date": "2026-03-15", "rating": "买入", "target_price": 2190.0,
        "valuation_basis": {"year": 2027, "pe": 25.0},
        "forecasts": [{"year": 2026, "net_profit": 95000, "net_profit_yoy": 18.8, "eps": 75.6},
                      {"year": 2027, "net_profit": 110000, "net_profit_yoy": 15.8, "eps": 87.6}],
        "risks": ["行业政策风险"],
    }, ensure_ascii=False))
    given = rr.load_claims_json(claims_file)
    auto = rr.extract_claims(REPORT)
    merged = rr.merge_claims(given, auto)
    assert merged.broker == "XX证券" and merged.analyst == "张三"
    assert merged.risks == ["行业政策风险"]
    assert sorted(merged.actuals) == [2024, 2025]        # 自动抽取补空缺
    assert merged.forecasts[2027].eps == 87.6


def test_end_to_end_writes_report_and_json(tmp_path, capsys):
    text_file = _write(tmp_path, "rpt.md", REPORT)
    snap_file = _write(tmp_path, "snap.txt", SNAPSHOT)
    cons_file = _write(tmp_path, "cons.txt", CONSENSUS)
    out_dir = tmp_path / "out"
    code = rr.main([
        "--input", str(text_file), "--snapshot-file", str(snap_file),
        "--consensus-file", str(cons_file), "--out-dir", str(out_dir),
        "--as-of", "2026-06-01", "--pe-band", "15,30",
    ])
    assert code == 0
    capsys.readouterr()
    md_files = list(out_dir.glob("report_*.md"))
    json_files = list(out_dir.glob("report_*.json"))
    assert len(md_files) == 1 and len(json_files) == 1
    md = md_files[0].read_text(encoding="utf-8")
    assert "# 研报阅读报告：贵州茅台" in md
    assert "采信账本" in md and "估值桥" in md and "批判性核查" in md
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["report"]["symbol"] == "600519.SH"
    assert payload["adoption"]["target_price_status"] == "assumed"
    assert payload["bridge"]["forward_year"] == 2027
    assert payload["snapshot"]["mcap_yi"] == pytest.approx(19100.0)
    assert "2027" in payload["consensus"]["np_mean_yi"]          # JSON 年份键统一为字符串
    assert "2027" in payload["bridge"]["consensus_gap"]


def test_text_out_dumps_body(tmp_path):
    text_file = _write(tmp_path, "rpt.md", REPORT)
    out_text = tmp_path / "body.txt"
    rr.main(["--input", str(text_file), "--text-out", str(out_text), "--no-report"])
    assert "贵州茅台" in out_text.read_text(encoding="utf-8")


def test_pe_band_and_as_of_validation():
    assert rr._parse_pe_band("30,15") == (15.0, 30.0)
    assert rr._parse_as_of("2026-06-01") == date(2026, 6, 1)
    with pytest.raises(rr.InputUnavailable):
        rr._parse_pe_band("8,25,30")
