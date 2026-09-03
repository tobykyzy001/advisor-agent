"""中期动量轮动策略的单元测试。

测试对象是自包含脚本 src/workspace-init/momentum_strategy.py 里的纯函数
（不依赖 quantify 包、不联网、不调 MCP）。通过 importlib 把脚本当作模块导入，
保证测试的正是「分发出去的同一份算法真源」。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# 把自包含脚本作为模块导入（脚本路径固定，纯标准库、可脱离 quantify 运行）
SCRIPT = Path(__file__).resolve().parents[1] / "src" / "workspace-init" / "momentum_strategy.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("momentum_strategy", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["momentum_strategy"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


m = _load_module()
StrategyParams = m.StrategyParams
Decision = m.Decision
mom = m.mom
ma = m.ma
compute_metrics = m.compute_metrics
_filter = m._filter
market_regime = m.market_regime
rank_fast_slow = m.rank_fast_slow
build_target = m.build_target
rows_to_series = m.rows_to_series
Series = m.Series


@pytest.fixture
def params() -> StrategyParams:
    return StrategyParams()


# ─────────────────────────────────────────────────────────────────────────
# 基础区间动量 / 均线
# ─────────────────────────────────────────────────────────────────────────


def test_mom_basic():
    # close[-1]=220, close[-1-20]=200 → 10%
    closes = [100.0] * 10 + [200.0] + [210.0] * 19 + [220.0]
    assert mom(closes, 20) == pytest.approx(0.10, rel=1e-6)


def test_mom_insufficient_data():
    assert mom([1.0, 2.0, 3.0], 20) is None


def test_mom_zero_base():
    closes = [0.0] * 21
    assert mom(closes, 20) is None


def test_ma_basic():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ma(closes, 3) == pytest.approx(4.0)  # (3+4+5)/3


def test_ma_insufficient():
    assert ma([1.0, 2.0], 3) is None


# ─────────────────────────────────────────────────────────────────────────
# 构造确定性日线序列的辅助
# ─────────────────────────────────────────────────────────────────────────


def _bar(d: date, c: float, vol: float = 100.0) -> dict:
    return {"trade_date": d.strftime("%Y%m%d"), "open": c, "high": c, "low": c,
            "close": c, "vol": vol}


def _make_series(symbol: str, n: int, start: float = 100.0, step: float = 1.0,
                 start_date: date = date(2024, 1, 1)) -> Series:
    """生成 n 根逐日 +step 的确定性序列（日期用「每根 +1 天」模拟交易日，足够算指标）。"""
    rows = []
    d = start_date
    for i in range(n):
        c = start + i * step
        rows.append(_bar(d, c))
        d = d + timedelta(days=1)
    return rows_to_series(symbol, rows)


# ─────────────────────────────────────────────────────────────────────────
# 指标计算 / 历史门槛
# ─────────────────────────────────────────────────────────────────────────


def test_history_threshold():
    """历史不足 121 交易日 → has_history=False。"""
    s = _make_series("A.SH", 120)  # 只有 120 根
    d = compute_metrics(s, StrategyParams())
    assert d.has_history is False
    assert "不足" in "；".join(d.filter_reasons)


def test_compute_metrics_uptrend():
    """单调上涨序列：mom 全为正，现价高于 MA120，偏离 MA20、近5日均为正。"""
    s = _make_series("B.SH", 130, start=100.0, step=1.0)  # 最后收盘=229
    d = compute_metrics(s, StrategyParams())
    assert d.has_history is True
    assert d.mom20 > 0
    assert d.mom120 > 0
    assert d.mom60 > 0
    assert d.close == pytest.approx(229.0)
    assert d.dev_ma20 > 0
    assert d.rush5 > 0


# ─────────────────────────────────────────────────────────────────────────
# 三道过滤关
# ─────────────────────────────────────────────────────────────────────────


def test_filter_trend_below_ma120():
    """趋势关：收盘跌破 MA120 → 不合格。"""
    # 先涨后暴跌，使现价跌破 MA120
    rows = []
    d0 = date(2024, 1, 1)
    for i in range(125):
        rows.append(_bar(d0 + timedelta(days=i), 100.0 + i))       # 一路涨到 224
    for i in range(5):
        rows.append(_bar(d0 + timedelta(days=125 + i), 1.0))       # 暴跌到底
    s = rows_to_series("C.SH", rows)
    dec = compute_metrics(s, StrategyParams())
    m._filter(dec, StrategyParams())
    assert dec.qualified is False
    assert any("MA120" in r for r in dec.filter_reasons)


def test_filter_rush_too_fast():
    """反追高关：近5日涨幅 > 24% → 不合格。"""
    s = _make_series("D.SH", 130, start=100.0, step=0.0)  # 平稳
    # 手动改造最后几根，制造急涨
    bars = s.bars
    for i in range(5):
        bars[-1 - i].close = 100.0 + (5 - i) * 10.0  # 近5日从100涨到~150 → rush5≈50%
    dec = compute_metrics(s, StrategyParams())
    m._filter(dec, StrategyParams())
    assert dec.qualified is False
    assert any("涨幅" in r or "24" in r for r in dec.filter_reasons)


def test_filter_dev_ma20_too_much():
    """反追高关：现价偏离 MA20 > 28% → 否决（近5日不涨但一次性跳高）。"""
    rows = []
    d0 = date(2024, 1, 1)
    for i in range(125):
        rows.append(_bar(d0 + timedelta(days=i), 100.0))  # 长期 100 平稳
    rows[-1] = _bar(d0 + timedelta(days=124), 150.0)  # 最后一天跳到 150
    s = rows_to_series("E.SH", rows)
    dec = compute_metrics(s, StrategyParams())
    m._filter(dec, StrategyParams())
    assert dec.qualified is False
    assert any("MA20" in r for r in dec.filter_reasons)


def test_filter_pass():
    """平稳微升序列：三道关全过。"""
    s = _make_series("F.SH", 130, start=100.0, step=0.2)  # 温和上涨
    dec = compute_metrics(s, StrategyParams())
    m._filter(dec, StrategyParams())
    assert dec.qualified is True
    assert dec.filter_reasons == []


# ─────────────────────────────────────────────────────────────────────────
# 大盘状态（mom60 中位数）
# ─────────────────────────────────────────────────────────────────────────


def test_market_regime_up():
    p = StrategyParams()
    decs = []
    for sym, step in [("A", 2.0), ("B", 1.5), ("C", 1.0)]:
        d = compute_metrics(_make_series(f"{sym}.SH", 130, step=step), p)
        d.mom60 = 0.15 if sym == "A" else 0.12 if sym == "B" else 0.05
        d.has_history = True
        decs.append(d)
    # 中位数 = 0.12 ≥ +5% → up
    assert market_regime(decs, p) == "up"


def test_market_regime_down():
    p = StrategyParams()
    decs = []
    for i in range(3):
        d = compute_metrics(_make_series(f"{chr(65+i)}.SH", 130, step=0.0), p)
        d.mom60 = -0.10 * (i + 1)  # -0.10, -0.20, -0.30
        d.has_history = True
        decs.append(d)
    assert market_regime(decs, p) == "down"


def test_market_regime_range():
    p = StrategyParams()
    decs = []
    for i in range(3):
        d = compute_metrics(_make_series(f"{chr(65+i)}.SH", 130, step=0.0), p)
        d.mom60 = 0.01 * (i + 1)  # 0.01, 0.02, 0.03 → 中位数 0.02
        d.has_history = True
        decs.append(d)
    assert market_regime(decs, p) == "range"


def test_market_regime_no_history():
    p = StrategyParams()
    d = compute_metrics(_make_series("X.SH", 10, step=0.0), p)
    assert market_regime([d], p) == "unknown"


# ─────────────────────────────────────────────────────────────────────────
# 双轨排名 + buffer16 + 快4慢4 补仓
# ─────────────────────────────────────────────────────────────────────────


def _mk_decision(ts, mom20, mom120, qualified=True) -> Decision:
    d = Decision(ts_code=ts, mom20=mom20, mom120=mom120, qualified=qualified,
                 has_history=True)
    return d


def test_rank_ties_by_ticker():
    """同分按 ts_code 字典序。"""
    d1 = _mk_decision("B.SH", 0.10, 0.05)
    d2 = _mk_decision("A.SH", 0.10, 0.05)
    d3 = _mk_decision("C.SH", 0.05, 0.10)
    rf, rs = rank_fast_slow([d1, d2, d3])
    # 快榜 mom20：A=B=0.10 同分 → A 在前；C=0.05 在后
    assert rf["A.SH"] == 1
    assert rf["B.SH"] == 2
    assert rf["C.SH"] == 3
    # 慢榜 mom120：C 最大
    assert rs["C.SH"] == 1
    assert rs["A.SH"] == 2
    assert rs["B.SH"] == 3


def test_build_target_fast4_slow4():
    """8 只合格：应按快4 + 慢4 补满（无老仓）。"""
    decs = [
        # 快榜强者（mom20 大）：F1~F5
        _mk_decision("F1.SH", 0.50, 0.02),
        _mk_decision("F2.SH", 0.45, 0.03),
        _mk_decision("F3.SH", 0.40, 0.01),
        _mk_decision("F4.SH", 0.35, 0.04),
        # 慢榜强者（mom120 大）：S1..S4
        _mk_decision("S1.SH", 0.01, 0.90),
        _mk_decision("S2.SH", 0.02, 0.80),
        _mk_decision("S3.SH", 0.03, 0.70),
        _mk_decision("S4.SH", 0.04, 0.60),
    ]
    target = build_target(decs, set(), StrategyParams())
    # 8 只全选
    assert len(target) == 8
    # 前4 = 快榜 F1..F4；后4 = 慢榜 S1..S4
    assert target[:4] == ["F1.SH", "F2.SH", "F3.SH", "F4.SH"]
    assert target[4:] == ["S1.SH", "S2.SH", "S3.SH", "S4.SH"]


def test_build_target_buffer_keeps_old():
    """老仓排名第 15（≤16）应被保留，即便不在前 8。"""
    decs = [_mk_decision(f"T{i:02d}.SH", 0.9 - i * 0.01, 0.9 - i * 0.01) for i in range(20)]
    # T00（0.90）最强，T13（0.77）第 14 名，仍在 buffer 16 内
    current = {"T13.SH"}
    target = build_target(decs, current, StrategyParams())
    assert "T13.SH" in target
    assert len(target) == 8


def test_build_target_buffer_drops_deep_old():
    """老仓落到第 17 名（>16）不入前两榜缓冲 → 被换掉。"""
    decs = [_mk_decision(f"T{i:02d}.SH", 0.9 - i * 0.01, 0.9 - i * 0.01) for i in range(20)]
    current = {"T17.SH"}  # 第 18 名，超 buffer16
    target = build_target(decs, current, StrategyParams())
    assert "T17.SH" not in target


def test_build_target_empty_pool():
    assert build_target([], set(), StrategyParams()) == []


# ─────────────────────────────────────────────────────────────────────────
# tushare 行 → Series 适配
# ─────────────────────────────────────────────────────────────────────────


def test_rows_to_series_dirty_and_sort():
    rows = [
        {"trade_date": "20250110", "close": 10.5, "open": 10, "high": 11, "low": 9, "vol": 100},
        {"trade_date": "20250109", "close": 10.0, "open": 10, "high": 11, "low": 9, "vol": 120},
        {"trade_date": "20250108", "close": None, "open": 10, "high": 11, "low": 9, "vol": 100},
    ]
    s = rows_to_series("G.SH", rows)
    assert len(s.bars) == 2  # 脏行被跳过
    assert s.bars[0].date.isoformat() == "2025-01-09"  # 升序


def test_rows_to_series_close_required():
    """缺 close 的行（即使其它 OHLC 全）也应被跳过——收盘价是策略硬依赖。"""
    s = rows_to_series("H.SH", [{"trade_date": "20250101", "open": 10, "high": 11, "low": 9, "vol": 100}])
    assert len(s.bars) == 0


# ─────────────────────────────────────────────────────────────────────────
# 分片取数（大池子多 agent 并行）：shard_groups / load_quotes
# ─────────────────────────────────────────────────────────────────────────

shard_groups = m.shard_groups
load_quotes = m.load_quotes


def test_shard_groups_default_size():
    """82 只按默认每片 10 只 → 9 片，末片 2 只，顺序与池子一致。"""
    codes = [f"{i:06d}.SH" for i in range(82)]
    groups = shard_groups(codes)
    assert len(groups) == 9
    assert groups[0] == codes[:10]
    assert groups[-1] == codes[-2:]


def test_shard_groups_explicit_shards():
    """显式指定片数 → 按片数均分（向上取整）。"""
    codes = [str(i) for i in range(10)]
    assert shard_groups(codes, shards=3) == [["0", "1", "2", "3"], ["4", "5", "6", "7"], ["8", "9"]]


def test_shard_groups_tiny_pool():
    """小池子始终单片（片数多于标的数也不拆碎）。"""
    assert shard_groups(["a"], shards=8) == [["a"]]
    assert shard_groups([], shards=8) == []


def test_load_quotes_single_file(tmp_path):
    """单文件模式：兼容小池子直取的 quotes.json。"""
    fp = tmp_path / "quotes.json"
    fp.write_text('{"A.SH": [{"close": 1}], "B.SH": [{"close": 2}]}', encoding="utf-8")
    raw, notes = load_quotes(fp)
    assert set(raw) == {"A.SH", "B.SH"}
    assert notes == []


def test_load_quotes_dir_merges_shards(tmp_path):
    """目录模式：合并全部 *.json 分片；跨片重复的 ts_code 保留行数较多者。"""
    (tmp_path / "shard_2.json").write_text('{"A.SH": [{"close": 1}]}', encoding="utf-8")
    (tmp_path / "shard_1.json").write_text(
        '{"A.SH": [{"close": 1}, {"close": 1}, {"close": 1}], "B.SH": [{"close": 2}]}',
        encoding="utf-8",
    )
    raw, notes = load_quotes(tmp_path)
    assert len(raw["A.SH"]) == 3  # shard_1 行数多，覆盖 shard_2 的单行版本
    assert len(raw["B.SH"]) == 1
    assert any("重复出现" in n for n in notes)


def test_load_quotes_skips_non_list_value(tmp_path):
    """分片里某标的的值不是数组 → 跳过并提示，不炸掉整批。"""
    tmp_path.joinpath("shard_1.json").write_text(
        '{"C.SH": "oops", "A.SH": [{"close": 1}]}', encoding="utf-8"
    )
    raw, notes = load_quotes(tmp_path)
    assert list(raw) == ["A.SH"]
    assert any("不是数组" in n for n in notes)


def test_load_quotes_bad_shard_fails_closed(tmp_path):
    """坏 JSON 分片 → ValueError 指明文件名，让主 agent 能重派该分片。"""
    tmp_path.joinpath("shard_3.json").write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError, match="shard_3.json"):
        load_quotes(tmp_path)


def test_load_quotes_empty_dir_fails(tmp_path):
    """目录下没有任何分片 → FileNotFoundError（视为未取数）。"""
    with pytest.raises(FileNotFoundError):
        load_quotes(tmp_path)


# ─────────────────────────────────────────────────────────────────────────
# 大盘状态关开关（--market-guard）：下行默认冻结，可关闭照常选股（端到端）
# ─────────────────────────────────────────────────────────────────────────


def _decline_rebound_rows(n: int, d0: date = date(2024, 1, 1)) -> list[dict]:
    """构造「大盘下行但个股仍合格」的确定性行情：先平缓 → 中段尖峰 → 深跌 → 尾盘温和反弹。

    收盘曲线：60 根 100 → 10 根 108（尖峰）→ 50 根 88 → 10 根 86→95 温和反弹。
    验证口径（n=130 时）：mom60 = 95/108-1 ≈ -12%（< -5%，大盘判 down）；
    MA120 ≈ 94.875 < 收盘价 95（趋势关过）；偏离 MA20 ≈ 6.4%、近 5 日涨幅 ≈ 5.6%（反追高关过）。
    """
    closes = [100.0] * 60 + [108.0] * 10 + [88.0] * 50 + [86.0 + i for i in range(10)]
    assert len(closes) == n, "本用例固定 130 根窗口"
    return [_bar(d0 + timedelta(days=i), c) for i, c in enumerate(closes)]


def _run_strategy(tmp_path, extra_args: list[str]):
    """以「3 只同形态合格股 + 老仓 OLD.SH」跑一轮完整策略，返回 (rc, 回写后的 state)。"""
    codes = ["N1.SH", "N2.SH", "OLD.SH"]
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "\n".join(f"- ts_code: {c}\n  name: 测试" for c in codes) + "\n", encoding="utf-8"
    )
    rows = _decline_rebound_rows(130)
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({c: rows for c in codes}), encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "as_of": "2024-06-01", "cash_pct": 0.875, "signal": "signal", "regime": "up",
        "target": ["OLD.SH"], "positions": [{"ts_code": "OLD.SH", "weight": 0.125}],
    }), encoding="utf-8")
    rc = m.main([
        "--watchlist", str(watchlist), "--state", str(state), "--data", str(quotes),
        "--no-store",  # 既有用例隔离本地行情库，保持纯 JSON 全量语义
        *extra_args,
    ])
    return rc, json.loads(state.read_text(encoding="utf-8"))


def test_market_guard_default_freezes_on_down(tmp_path):
    """默认开启：大盘下行 → signal=frozen，目标持仓只剩老仓，不新增。"""
    rc, state = _run_strategy(tmp_path, [])
    assert rc == 0
    assert state["regime"] == "down"          # 前提：行情确属下行
    assert state["signal"] == "frozen"
    assert state["target"] == ["OLD.SH"]      # 冻结新增，老仓原样保留


def test_market_guard_off_runs_on_down(tmp_path):
    """--market-guard false：大盘仍判 down，但照常选股出新组合（老仓缓冲照常生效）。"""
    rc, state = _run_strategy(tmp_path, ["--market-guard", "false"])
    assert rc == 0
    assert state["regime"] == "down"          # 状态照常计算展示
    assert state["signal"] == "signal"
    assert set(state["target"]) == {"N1.SH", "N2.SH", "OLD.SH"}


# ─────────────────────────────────────────────────────────────────────────
# 本地 CSV 行情库（增量取数 + 幂等合并）：store_status / upsert_store / 全流程
# ─────────────────────────────────────────────────────────────────────────

store_status = m.store_status
upsert_store = m.upsert_store


def test_store_status_missing(tmp_path):
    """库内无此标的 → (0, None)。"""
    assert store_status(tmp_path, "A.SH") == (0, None)


def test_upsert_store_creates_csv_and_merges(tmp_path):
    """首批增量：新建 CSV（含表头、按日期升序），merged 回读全量，回执提示入库。"""
    raw = {"A.SH": [_bar(date(2025, 1, 2), 1.5), _bar(date(2025, 1, 3), 1.6)]}
    merged, notes = upsert_store(tmp_path, raw)
    fp = tmp_path / "A.SH.csv"
    assert fp.exists()
    assert fp.read_text(encoding="utf-8").splitlines()[0] == "trade_date,open,high,low,close,vol"
    assert [r["trade_date"] for r in merged["A.SH"]] == ["20250102", "20250103"]
    assert store_status(tmp_path, "A.SH") == (2, "20250103")
    assert any("入库" in n for n in notes)


def test_upsert_store_idempotent(tmp_path):
    """幂等：同批增量重复合并 → 行数不变、文件字节一致、无新回执。"""
    raw = {"A.SH": [_bar(date(2025, 1, 2), 1.5), _bar(date(2025, 1, 3), 1.6)]}
    upsert_store(tmp_path, raw)
    first = (tmp_path / "A.SH.csv").read_text(encoding="utf-8")
    merged, notes = upsert_store(tmp_path, raw)
    assert (tmp_path / "A.SH.csv").read_text(encoding="utf-8") == first
    assert len(merged["A.SH"]) == 2
    assert store_status(tmp_path, "A.SH") == (2, "20250103")
    assert notes == []


def test_upsert_store_increment_and_overwrite(tmp_path):
    """增量合并：新日期追加、同日新值覆盖旧值。"""
    upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 1.5)]})
    inc = [
        {"trade_date": "20250102", "close": 9.9, "open": 9.0, "high": 9.0,
         "low": 9.0, "vol": 999},                       # 同日覆盖
        _bar(date(2025, 1, 3), 1.6),                    # 新增
    ]
    merged, _ = upsert_store(tmp_path, {"A.SH": inc})
    rows = merged["A.SH"]
    assert [r["trade_date"] for r in rows] == ["20250102", "20250103"]
    assert rows[0]["close"] == 9.9
    assert store_status(tmp_path, "A.SH") == (2, "20250103")


def _write_watchlist(tmp_path, codes) -> Path:
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("\n".join(f"- ts_code: {c}\n  name: 测试" for c in codes) + "\n",
                  encoding="utf-8")
    return wl


def test_plan_incremental_full_and_fresh(tmp_path, capsys):
    """--plan 按库内状态把标的分为：增量补数（带显式区间）/ 全量 / 免取。"""
    codes = ["OLD.SH", "NEW.SH", "FRESH.SH"]
    wl = _write_watchlist(tmp_path, codes)
    store = tmp_path / "store"
    # OLD.SH：121 根老数据 → 增量，start = 最后日期(2024-04-30) +1
    upsert_store(store, {"OLD.SH": [_bar(date(2024, 1, 1) + timedelta(days=i), 100.0)
                                    for i in range(121)]})
    # FRESH.SH：121 根且末根是今天 → 免取
    today = date.today()
    upsert_store(store, {"FRESH.SH": [_bar(today - timedelta(days=(120 - i)), 100.0)
                                      for i in range(121)]})
    rc = m.main(["--watchlist", str(wl), "--plan", "--store", str(store)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "增量补数 1 只 / 全量 1 只 / 库内已最新免取 1 只" in out
    assert "ts_code=OLD.SH start_date=20240501" in out
    assert "ts_code=NEW.SH（全量" in out
    assert "FRESH.SH" in out.split("免取")[0] or "FRESH.SH" in out


def test_plan_all_fresh_no_fetch(tmp_path, capsys):
    """全部免取：--plan 提示无需取数。"""
    wl = _write_watchlist(tmp_path, ["F1.SH"])
    store = tmp_path / "store"
    today = date.today()
    upsert_store(store, {"F1.SH": [_bar(today - timedelta(days=(120 - i)), 100.0)
                                   for i in range(121)]})
    rc = m.main(["--watchlist", str(wl), "--plan", "--store", str(store)])
    assert rc == 0
    assert "无需取数" in capsys.readouterr().out


def test_e2e_store_roundtrip_increment(tmp_path):
    """端到端：首轮全量落库 → 二轮只喂一根新 K 线 → 库 +1 行、信号日滚到新日期。"""
    codes = ["N1.SH", "N2.SH", "N3.SH"]
    watchlist = _write_watchlist(tmp_path, codes)
    store = tmp_path / "store"
    state = tmp_path / "state.json"
    rows = _decline_rebound_rows(130)   # 2024-01-01 起 130 根，末日 2024-05-09
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({c: rows for c in codes}), encoding="utf-8")
    rc = m.main(["--watchlist", str(watchlist), "--state", str(state),
                 "--data", str(quotes), "--store", str(store)])
    assert rc == 0
    for c in codes:
        assert store_status(store, c) == (130, "20240509")

    inc = tmp_path / "inc.json"
    inc.write_text(json.dumps({c: [_bar(date(2024, 5, 10), 96.0)] for c in codes}),
                   encoding="utf-8")
    rc = m.main(["--watchlist", str(watchlist), "--state", str(state),
                 "--data", str(inc), "--store", str(store)])
    assert rc == 0
    assert store_status(store, "N1.SH") == (131, "20240510")
    st = json.loads(state.read_text(encoding="utf-8"))
    assert st["as_of"] == "2024-05-10"


def test_e2e_fresh_codes_join_via_store(tmp_path):
    """「免取」标的不出现在增量分片里，也应从行情库回读参与计算，不静默缺席。"""
    codes = ["F1.SH", "F2.SH"]
    watchlist = _write_watchlist(tmp_path, codes)
    store = tmp_path / "store"
    today = date.today()
    # 两只库内均已最新（130 根、末根=今天）→ --plan 会判「免取」，增量分片为空 {}
    rows = [_bar(today - timedelta(days=(129 - i)), 100.0 + i * 0.2) for i in range(130)]
    upsert_store(store, {c: rows for c in codes})
    inc = tmp_path / "inc.json"
    inc.write_text("{}", encoding="utf-8")
    state = tmp_path / "state.json"
    rc = m.main(["--watchlist", str(watchlist), "--state", str(state),
                 "--data", str(inc), "--store", str(store)])
    assert rc == 0
    st = json.loads(state.read_text(encoding="utf-8"))
    assert st["as_of"] == today.isoformat()   # 库内末根即今天
    assert set(st["target"]) == set(codes)    # 两只都参与选股（温和上涨全合格）