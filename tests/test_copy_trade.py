"""copy-trade 技能脚本的单元测试（纯确定性逻辑，不联网）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".agents/skills/copy-trade/scripts"))

from fetch_homework import parse_messages, _clean_body, _clean_speaker  # noqa: E402
from symbol_map import resolve_alias, load_map, _parse_override  # noqa: E402
from timeline import extract_events, _which_action, split_themes  # noqa: E402
from backtest import resolve_signals, FullSignal  # noqa: E402


SAMPLE_HTML = """
<meta charset="utf-8">
2026-08-14 13:49:41 【<b>十倍之路 <a>只看Ta</a></b>】 【群主】 铜冠铜箔 ，我择机低吃，感觉也有点意思<br>
2026-08-14 13:50:20 【<b>十倍之路 <a>只看Ta</a></b>】 【群主】手里锐捷+ 有研硅 +铜管，然后还有子弹留着。仅供参考<br>
2026-08-17 09:32:49 【<b>十倍之路 <a>只看Ta</a></b>】 【群主】 铜冠铜箔 先止盈了<br>
"""


def test_clean_speaker():
    assert _clean_speaker("十倍之路  只看Ta") == "十倍之路"
    assert _clean_body(" ![]( 环境狗，也是没办法") == "环境狗，也是没办法"


def test_parse_messages():
    msgs = parse_messages(SAMPLE_HTML)
    assert len(msgs) == 3
    assert msgs[0]["时间戳"] == "2026-08-14 13:49:41"
    assert msgs[0]["是否群主"] is True
    # 正文不应残留时间戳
    assert not msgs[0]["正文"].startswith("2026")


def test_which_action():
    assert _which_action("低吃") == "买入"
    assert _which_action("先止盈了") == "卖出"
    assert _which_action("锁仓没动") == "持有"
    assert _which_action("随便聊聊") == ""


def test_extract_events():
    msgs = parse_messages(SAMPLE_HTML)
    evs = extract_events(msgs)
    # 第1条"低吃"(买入)、第2条"留着"非动作词(跳过)、第3条"止盈"(卖出)
    assert len(evs) == 2
    assert evs[0].action == "买入"
    assert evs[1].action == "卖出"


def test_resolve_alias():
    m = load_map()
    assert resolve_alias("硅微粉", m) == ["688300.SH"]
    assert resolve_alias("600519", m) == ["600519.SH"]
    assert resolve_alias("折叠屏", m) == []   # 待确认


def test_parse_override():
    txt = "硅微粉:\n  - 688300.SH\n冷液: 301018.SZ\n"
    d = _parse_override(txt)
    assert d["硅微粉"] == ["688300.SH"]
    assert d["冷液"] == ["301018.SZ"]


def test_resolve_signals_dedup():
    # 同标的连续同向只留最新，方向翻转各自保留
    msgs = parse_messages(SAMPLE_HTML)
    amap = load_map()
    sigs = resolve_signals(msgs, amap)
    tc_entries = [s for s in sigs if s.ts_code == "301559.SZ"]
    assert len(tc_entries) == 2
    assert tc_entries[0].action == "买入"
    assert tc_entries[1].action == "卖出"
    assert tc_entries[1].date > tc_entries[0].date


def test_split_themes():
    hits = split_themes("手里折叠屏+铜箔+冷液+硅微粉", {"硅微粉", "冷液", "铜箔", "折叠屏"})
    assert "硅微粉" in hits and "冷液" in hits and "铜箔" in hits