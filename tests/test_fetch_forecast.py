"""机构业绩预测取数 CLI（fetch_forecast.py）的单元测试。

测试对象是自包含脚本 src/workspace-init/fetch_forecast.py（纯标准库、直连同花顺
F10 盈利预测页）。通过 importlib 把脚本当作模块导入，保证测试的正是「分发出去的
同一份真源」。

全程离线：页面解析用内置的合成 HTML（结构与真实页面一致——caption 标注的两张
一致预期汇总表、带年份子表头的业绩预测详表、预测列单元格内嵌 tipbox 浮层小表的
详细指标矩阵），网络层用假 fetch_worth_html 拦截，不发起任何真实请求。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# 把自包含脚本作为模块导入（脚本路径固定，纯标准库、可脱离 quantify 运行）
SCRIPT = Path(__file__).resolve().parents[1] / "src" / "workspace-init" / "fetch_forecast.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_forecast", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["fetch_forecast"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ff = _load_module()


# ─────────────────────────────────────────────────────────────────────────
# 合成页面（结构取自真实 worth.html，含 tipbox 嵌套表陷阱）
# ─────────────────────────────────────────────────────────────────────────

PAGE = """<!DOCTYPE html>
<html><head><meta charset="gbk"><title>麦格米特(002851) 盈利预测_F10_同花顺金融服务网</title></head>
<body>
<div class="m_box" id="forecast"><div class="hd"><h2>业绩预测</h2></div><div class="bd">
<div class="fl yjyc"><table class="m_table m_hl">
<caption class="hltip m_cap"><span class="fr tip">单位：元</span>汇总--预测年报每股收益 </caption>
<thead><tr><th>年度</th><th>预测机构数</th><th>最小值</th><th>均值</th><th>最大值</th><th>行业平均数</th></tr></thead>
<tbody>
<tr><th>2026</th><td class="tc">15</td><td>0.88</td><td>1.42</td><td>1.93</td><td>2.57</td></tr>
<tr><th>2027</th><td class="tc">14</td><td>1.67</td><td>2.71</td><td>3.12</td><td>3.36</td></tr>
</tbody></table></div>
<div class="fr yjyc"><table class="m_table m_hl">
<caption class="hltip m_cap"><span class="fr tip">单位：亿元</span>汇总--预测年报净利润 </caption>
<thead><tr><th>年度</th><th>预测机构数</th><th>最小值</th><th>均值</th><th>最大值</th><th>行业平均数</th></tr></thead>
<tbody>
<tr><th>2026</th><td class="tc">15</td><td>5.15</td><td>8.32</td><td>11.00</td><td>62.04</td></tr>
<tr><th>2027</th><td class="tc">14</td><td>9.77</td><td>15.85</td><td>18.23</td><td>76.44</td></tr>
</tbody></table></div>
</div></div>
<div class="m_box" id="forecastdetail"><div class="hd"><h2>业绩预测详表</h2></div><div class="bd">
<table class="m_table m_hl">
<thead>
<tr><th>机构名称</th><th>研究员</th><th colspan="3">预测年报每股收益（元）</th><th colspan="3">预测年报净利润（元）</th><th>报告日期</th></tr>
<tr><th>2026预测</th><th>2027预测</th><th>2028预测</th><th>2026预测</th><th>2027预测</th><th>2028预测</th></tr>
</thead>
<tbody>
<tr><td>华安证券</td><td>张志邦</td><td>1.61</td><td>2.58</td><td>3.82</td><td>9.37亿</td><td>15.00亿</td><td>22.21亿</td><td>2026-05-07</td></tr>
<tr><td>招商证券</td><td>蒋国峰</td><td><s class="up"></s>1.37</td><td>2.75</td><td>4.39</td><td>8.02亿</td><td>16.06亿</td><td>25.69亿</td><td>2026-09-04</td></tr>
</tbody></table>
</div></div>
<table class="m_table m_hl ggintro ggintro_1 organData">
<caption class="m_cap adjust"><span class="table_cap">详细指标预测</span></caption>
<thead><tr><th>预测指标</th><th>2023（实际值）</th><th>2024（实际值）</th><th>2025（实际值）</th><th>预测2026（平均）</th><th>预测2027（平均）</th></tr></thead>
<tbody>
<tr><th class="tl">营业收入(元)</th><td> 67.54亿 </td><td> 81.72亿 </td><td> 94.03亿 </td>
<td><div class="pr"><span> 131.96亿 </span><a class="m_more fr" targ="box_1" href="javascript:void(0)"></a>
<div class="tipbox box_1" style="display:none;"><div class="tipbox_hd"><h4>预测机构一览</h4></div>
<div class="tipbox_bd p0_5"><table class="m_table" style="width:350px">
<thead><tr><th class="tc">研究机构</th><th class="tc">研究员</th><th class="tc">预测值</th><th class="tc">评级</th></tr></thead>
<tbody><tr><td>招商证券</td><td>蒋国峰</td><td>120.00亿</td><td>买 入</td></tr></tbody>
</table></div></div></div></td>
<td><div class="pr"><span> 176.49亿 </span></div></td></tr>
<tr><th class="tl">净利润(元)</th><td>6.29亿</td><td>4.36亿</td><td>1.46亿</td><td>8.32亿</td><td>15.85亿</td></tr>
</tbody></table>
<script>var chart = {"a": 1};</script>
</body></html>"""

#: 有效股票页但没有业绩预测表格（无机构覆盖的小盘股）
PAGE_NO_COVERAGE = """<html><head><meta charset="gbk">
<title>海希通讯(831305) 盈利预测_F10_同花顺金融服务网</title></head>
<body><div>暂无数据</div></body></html>"""

#: 空壳页（无效代码 / 港股）：标题里没有「股票名(6位代码)」
PAGE_EMPTY = """<html><head><meta charset="gbk">
<title>F10_同花顺金融服务网</title></head><body></body></html>"""


# ─────────────────────────────────────────────────────────────────────────
# 页面解析
# ─────────────────────────────────────────────────────────────────────────


def test_parse_full_page():
    data = ff.parse_worth_page(PAGE)
    assert data["name"] == "麦格米特"
    # 两张汇总表按 caption 区分（表头完全相同，不能按位置猜）
    assert data["np"] == [["2026", "15", "5.15", "8.32", "11.00", "62.04"],
                          ["2027", "14", "9.77", "15.85", "18.23", "76.44"]]
    assert data["eps"] == [["2026", "15", "0.88", "1.42", "1.93", "2.57"],
                           ["2027", "14", "1.67", "2.71", "3.12", "3.36"]]
    # 明细表列名来自首行表头 + 年份子表头（前半 EPS、后半净利润）
    assert data["detail_labels"] == [
        "机构", "研究员",
        "EPS 2026E", "EPS 2027E", "EPS 2028E",
        "净利 2026E", "净利 2027E", "净利 2028E",
        "报告日期"]
    assert len(data["detail_rows"]) == 2
    assert data["detail_rows"][0][0] == "华安证券"  # 页面原序
    assert data["detail_rows"][1][-1] == "2026-09-04"
    # 指标矩阵：预测列单元格里的 tipbox 嵌套小表不得混入单元格文本
    header, rows = data["matrix"]
    assert header[0] == "预测指标"
    assert rows[0] == ["营业收入(元)", "67.54亿", "81.72亿", "94.03亿",
                       "131.96亿", "176.49亿"]


def test_parse_tipbox_nested_table_suppressed():
    """回归：详细指标矩阵预测列单元格里嵌「预测机构一览」tipbox 小表，必须整体跳过。"""
    out = ff.render_stock("002851", ff.parse_worth_page(PAGE), None, "2026-01-01 00:00")
    for leaked in ("预测机构一览", "研究机构", "买 入", "120.00亿"):
        assert leaked not in out
    assert "131.96亿" in out  # 浮层外的预测均值本身要保留


def test_parse_no_coverage():
    data = ff.parse_worth_page(PAGE_NO_COVERAGE)
    assert data["name"] == "海希通讯"
    assert data["eps"] is None and data["np"] is None
    assert data["detail_rows"] == [] and data["matrix"] is None
    out = ff.render_stock("831305", data, None, "2026-01-01 00:00")
    assert "未解析到业绩预测表格" in out


def test_parse_invalid_or_hk_page():
    with pytest.raises(ff.FetchForecastError) as ei:
        ff.parse_worth_page(PAGE_EMPTY)
    assert "港股" in str(ei.value)


def test_decode_html_by_meta_charset():
    assert ff._decode_html("同花顺".encode("gbk")) == "同花顺"


# ─────────────────────────────────────────────────────────────────────────
# 代码规范化
# ─────────────────────────────────────────────────────────────────────────


def test_normalize_code():
    assert ff.normalize_code("002851") == "002851"
    assert ff.normalize_code("600519.SH") == "600519"
    assert ff.normalize_code("000333.SZ") == "000333"
    assert ff.normalize_code("833171.BJ") == "833171"
    with pytest.raises(ff.FetchForecastError, match="港股"):
        ff.normalize_code("00700")
    with pytest.raises(ff.FetchForecastError, match="港股"):
        ff.normalize_code("00700.HK")
    with pytest.raises(ff.FetchForecastError, match="无法识别"):
        ff.normalize_code("茅台")


# ─────────────────────────────────────────────────────────────────────────
# 渲染：日期倒序 / 前瞻PE / 空列剔除
# ─────────────────────────────────────────────────────────────────────────


def test_render_detail_sorted_by_date_desc():
    out = ff.render_stock("002851", ff.parse_worth_page(PAGE), None, "2026-01-01 00:00")
    assert out.index("招商证券") < out.index("华安证券")  # 09-04 排到 05-07 前
    assert "【净利润一致预期（亿元）】" in out
    assert "| 2026E | 15 | 5.15 | 8.32 | 11.00 | 62.04 |" in out
    assert "【每股收益一致预期（元）】" in out
    assert "【机构预测明细】2 条" in out


def test_render_forward_pe():
    out = ff.render_stock("002851", ff.parse_worth_page(PAGE), 772.0, "2026-01-01 00:00")
    assert "总市值 772 亿元" in out
    assert "| 2026E | 8.32 | 92.8x |" in out   # 772 / 8.32
    assert "| 2027E | 15.85 | 48.7x |" in out  # 772 / 15.85


def test_render_forward_pe_negative_mean():
    """亏损预期（均值为负）不出前瞻PE 数值。"""
    data = ff.parse_worth_page(PAGE)
    data["np"] = [["2026", "5", "-0.55", "-0.48", "-0.41", "11.63"]]
    out = ff.render_stock("430047", data, 30.0, "2026-01-01 00:00")
    assert "| 2026E | -0.48 | - |" in out


def test_render_matrix_drops_empty_columns():
    data = ff.parse_worth_page(PAGE)
    _, rows = data["matrix"]
    for r in rows:  # 末列在所有数据行置空 → 整列（含表头）剔除
        r[-1] = ""
    out = ff.render_stock("002851", data, None, "2026-01-01 00:00")
    assert "预测2027（平均）" not in out
    assert "预测2026（平均）" in out


# ─────────────────────────────────────────────────────────────────────────
# CLI（main，网络层用假 fetch_worth_html 拦截）
# ─────────────────────────────────────────────────────────────────────────


def test_main_single_success(monkeypatch, capsys):
    monkeypatch.setattr(ff, "fetch_worth_html", lambda code, timeout: PAGE)
    rc = ff.main(["002851", "--mcap", "772"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "麦格米特" in out
    assert "| 2026E | 8.32 | 92.8x |" in out
    assert "完成：1 只全部成功。" in out


def test_main_no_coverage_is_success(monkeypatch, capsys):
    monkeypatch.setattr(ff, "fetch_worth_html", lambda code, timeout: PAGE_NO_COVERAGE)
    rc = ff.main(["831305"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "未解析到业绩预测表格" in out


def test_main_partial_failure(monkeypatch, capsys):
    monkeypatch.setattr(ff, "fetch_worth_html", lambda code, timeout: PAGE)
    rc = ff.main(["--interval", "0", "002851,00700"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "1 只成功 / 1 只失败" in out
    assert "- 00700：港股暂不支持" in out


def test_main_fetch_failure_lists_reason(monkeypatch, capsys):
    def boom(code, timeout):
        raise ff.FetchForecastError("重试 3 次后仍失败：超时")

    monkeypatch.setattr(ff, "fetch_worth_html", boom)
    rc = ff.main(["600519"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "取数失败：重试 3 次后仍失败：超时" in out


def test_main_mcap_rejects_multiple_codes(capsys):
    rc = ff.main(["002851,600519", "--mcap", "772"])
    assert rc == 1
    assert "--mcap 仅支持单只代码" in capsys.readouterr().err


def test_main_empty_codes(capsys):
    rc = ff.main([",,"])
    assert rc == 1
    assert "至少一个股票代码" in capsys.readouterr().err
