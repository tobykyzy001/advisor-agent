"""统一行情取数 CLI（fetch_quotes.py）的单元测试。

测试对象是自包含脚本 src/workspace-init/fetch_quotes.py（纯标准库、直连 tushare
REST API）。通过 importlib 把脚本当作模块导入，保证测试的正是「分发出去的同一份真源」。

全程离线：网络层用假 opener / ts_post_retry 替身拦截，不发起任何真实请求；
CLI 集成用例经 main(argv) 走完整参数解析路径，行情库一律指向 tmp_path。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

# 把自包含脚本作为模块导入（脚本路径固定，纯标准库、可脱离 quantify 运行）
SCRIPT = Path(__file__).resolve().parents[1] / "src" / "workspace-init" / "fetch_quotes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_quotes", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["fetch_quotes"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


fq = _load_module()


# ─────────────────────────────────────────────────────────────────────────
# 构造辅助：行情行 / 假 opener / 假 API
# ─────────────────────────────────────────────────────────────────────────


def _bar(d: date, c: float = 10.0, vol: float = 100.0, pct_chg: float = 0.0) -> dict:
    return {"trade_date": d.strftime("%Y%m%d"), "open": c, "high": c, "low": c,
            "close": c, "vol": vol, "pct_chg": pct_chg}


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """替身 opener：open() 返回固定 JSON 响应，或抛出给定异常。"""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def open(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "timeout": timeout,
                           "body": json.loads(req.data.decode("utf-8"))})
        if isinstance(self.payload, Exception):
            raise self.payload
        return _FakeResp(json.dumps(self.payload).encode("utf-8"))


def _api_payload(rows: list[dict], fields: str) -> dict:
    cols = fields.split(",")
    items = [[r.get(c) for c in cols] for r in rows]
    return {"code": 0, "msg": "", "data": {"fields": cols, "items": items}}


def _fake_api(daily_rows: list[dict] | None = None,
              basic_rows: list[dict] | None = None):
    """返回按 api_name 分发的 ts_post_retry 替身，附带调用记录。"""
    calls: list[tuple[str, dict]] = []

    def fake(api_name, token, params, fields):
        calls.append((api_name, dict(params)))
        if api_name == "daily_basic":
            return list(basic_rows or [])
        return list(daily_rows or [])

    return fake, calls


# ─────────────────────────────────────────────────────────────────────────
# token 解析（--token > 环境变量 TUSHARE_TOKEN > 工作区 .env）
# ─────────────────────────────────────────────────────────────────────────


def test_load_env_token_plain(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=sk-xxx\nTUSHARE_TOKEN=abc123\n", encoding="utf-8")
    assert fq.load_env_token(env) == "abc123"      # 其它键忽略，首个命中生效


def test_load_env_token_quoted_and_export(tmp_path):
    env = tmp_path / ".env"
    env.write_text("export TUSHARE_TOKEN=\"abc 123\"\n", encoding="utf-8")
    assert fq.load_env_token(env) == "abc 123"     # export 前缀 + 成对引号


def test_load_env_token_inline_comment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TUSHARE_TOKEN=abc123 # 行内注释\n", encoding="utf-8")
    assert fq.load_env_token(env) == "abc123"


def test_load_env_token_missing_or_empty(tmp_path):
    assert fq.load_env_token(tmp_path / "nope.env") is None
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nTUSHARE_TOKEN=\n", encoding="utf-8")
    assert fq.load_env_token(env) is None


def test_resolve_token_priority(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("TUSHARE_TOKEN=file-token\n", encoding="utf-8")
    assert fq.resolve_token(None, env) == "file-token"          # 文件兜底
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    assert fq.resolve_token(None, env) == "env-token"           # 环境变量 > 文件
    assert fq.resolve_token("   ", env) == "env-token"          # 空白参数视为未提供
    assert fq.resolve_token("cli-token", env) == "cli-token"    # 命令行最高


def test_resolve_token_missing_exits_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(SystemExit) as ei:
        fq.resolve_token(None, tmp_path / ".env")
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "未找到 tushare token" in err
    assert "TUSHARE_TOKEN" in err


# ─────────────────────────────────────────────────────────────────────────
# 代码规范化与标的清单解析
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expect", [
    ("600519", "600519.SH"),
    ("000333", "000333.SZ"),
    ("833171", "833171.BJ"),
    ("430047", "430047.BJ"),
    ("920118", "920118.BJ"),
    ("00700", "00700.HK"),
    ("600519.SH", "600519.SH"),
    ("00700.hk", "00700.HK"),
    ("  600519  ", "600519.SH"),
    ("", ""),
])
def test_normalize_code(raw, expect):
    assert fq.normalize_code(raw) == expect


def test_load_watchlist_codes(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("- ts_code: 600519\n  name: 贵州茅台\n"
                  "- ts_code: 00700\n  name: 腾讯\n", encoding="utf-8")
    assert fq.load_watchlist_codes(wl) == ["600519.SH", "00700.HK"]


def test_load_watchlist_codes_missing(tmp_path):
    with pytest.raises(SystemExit):
        fq.load_watchlist_codes(tmp_path / "nope.yaml")


def test_resolve_codes_dedupe_and_normalize():
    args = SimpleNamespace(codes="600519, 000333，600519", watchlist=None)
    assert fq.resolve_codes(args) == ["600519.SH", "000333.SZ"]  # 中英文逗号 + 去重保序


def test_resolve_codes_conflict_or_empty(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("- ts_code: 600519\n", encoding="utf-8")
    with pytest.raises(SystemExit):   # --codes 与 --watchlist 只能二选一
        fq.resolve_codes(SimpleNamespace(codes="600519", watchlist=str(wl)))
    with pytest.raises(SystemExit):   # 两者都没给
        fq.resolve_codes(SimpleNamespace(codes=None, watchlist=None))
    with pytest.raises(SystemExit):   # 清单为空
        fq.resolve_codes(SimpleNamespace(codes="", watchlist=None))


# ─────────────────────────────────────────────────────────────────────────
# 本地 CSV 行情库（store_status / upsert_store 幂等合并）
# ─────────────────────────────────────────────────────────────────────────


def test_store_status_missing(tmp_path):
    assert fq.store_status(tmp_path, "A.SH") == (0, None)


def test_upsert_store_creates_csv(tmp_path):
    merged = fq.upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 1.5),
                                                 _bar(date(2025, 1, 3), 1.6)]})
    fp = tmp_path / "A.SH.csv"
    assert fp.read_text(encoding="utf-8").splitlines()[0] == \
        "trade_date,open,high,low,close,vol,pct_chg,rt"
    assert [r["trade_date"] for r in merged["A.SH"]] == ["20250102", "20250103"]
    assert fq.store_status(tmp_path, "A.SH") == (2, "20250103")


def test_upsert_store_idempotent(tmp_path):
    fq.upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 1.5)]})
    first = (tmp_path / "A.SH.csv").read_text(encoding="utf-8")
    merged = fq.upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 1.5)]})
    assert (tmp_path / "A.SH.csv").read_text(encoding="utf-8") == first
    assert len(merged["A.SH"]) == 1


def test_upsert_store_overwrite_same_day(tmp_path):
    fq.upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 1.5)]})
    fq.upsert_store(tmp_path, {"A.SH": [
        {"trade_date": "20250102", "open": 9, "high": 9, "low": 9, "close": 9.9, "vol": 999},
        _bar(date(2025, 1, 3), 1.6),
    ]})
    rows = fq._read_store_rows(fq.store_csv_path(tmp_path, "A.SH"))
    assert [r["trade_date"] for r in rows] == ["20250102", "20250103"]
    assert float(rows[0]["close"]) == 9.9      # 同日新值覆盖旧值
    assert fq.store_status(tmp_path, "A.SH") == (2, "20250103")


def test_next_date_str():
    assert fq._next_date_str("20250201") == "20250202"
    assert fq._next_date_str("2025-02-01") == "20250202"
    assert fq._next_date_str("bad") == "bad"   # 解析失败原样返回


# ─────────────────────────────────────────────────────────────────────────
# REST 调用层：daily_api_name / ts_post / ts_post_retry
# ─────────────────────────────────────────────────────────────────────────


def test_daily_api_name():
    assert fq.daily_api_name("00700.HK") == "hk_daily"
    assert fq.daily_api_name("600519.SH") == "daily"
    assert fq.daily_api_name("833171.BJ") == "daily"


def test_ts_post_success(monkeypatch):
    opener = _FakeOpener(_api_payload([{"ts_code": "A.SH", "close": 1.5}], "ts_code,close"))
    monkeypatch.setattr(fq, "_OPENER", opener)
    rows = fq.ts_post("daily", "tok", {"ts_code": "A.SH"}, "ts_code,close")
    assert rows == [{"ts_code": "A.SH", "close": 1.5}]
    body = opener.calls[0]["body"]
    assert body["api_name"] == "daily"
    assert body["token"] == "tok"
    assert body["params"] == {"ts_code": "A.SH"}


def test_ts_post_business_error_permanent(monkeypatch):
    monkeypatch.setattr(fq, "_OPENER", _FakeOpener({"code": 1, "msg": "抱歉，您还没有权限"}))
    with pytest.raises(fq.TushareApiError) as ei:
        fq.ts_post("daily", "tok", {}, "")
    assert ei.value.permanent is True


def test_ts_post_business_error_transient(monkeypatch):
    monkeypatch.setattr(fq, "_OPENER", _FakeOpener({"code": 1, "msg": "每分钟最多访问 500 次"}))
    with pytest.raises(fq.TushareApiError) as ei:
        fq.ts_post("daily", "tok", {}, "")
    assert ei.value.permanent is False


def test_ts_post_http_error(monkeypatch):
    err = urllib.error.HTTPError("http://api.tushare.pro", 502, "Bad Gateway", None, None)
    monkeypatch.setattr(fq, "_OPENER", _FakeOpener(err))
    with pytest.raises(fq.TushareApiError, match="502"):
        fq.ts_post("daily", "tok", {}, "")


def test_ts_post_retry_permanent_no_retry(monkeypatch):
    calls: list[int] = []

    def fake(*a, **k):
        calls.append(1)
        raise fq.TushareApiError("没有权限", permanent=True)

    monkeypatch.setattr(fq, "ts_post", fake)
    monkeypatch.setattr(fq, "_RETRY_DELAYS", [0.0])
    with pytest.raises(fq.TushareApiError):
        fq.ts_post_retry("daily", "t", {}, "")
    assert len(calls) == 1                 # 永久错误不重试，直接抛出


def test_ts_post_retry_transient_then_ok(monkeypatch):
    state = {"n": 0}

    def fake(*a, **k):
        state["n"] += 1
        if state["n"] < 3:
            raise fq.TushareApiError("每分钟访问过多")   # 限流类 → 可重试
        return [{"close": 1.0}]

    monkeypatch.setattr(fq, "ts_post", fake)
    monkeypatch.setattr(fq, "_RETRY_DELAYS", [0.0, 0.0])
    assert fq.ts_post_retry("daily", "t", {}, "") == [{"close": 1.0}]
    assert state["n"] == 3                 # 失败两次、第三次成功


def test_ts_post_retry_exhausted(monkeypatch):
    def fake(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(fq, "ts_post", fake)
    monkeypatch.setattr(fq, "_RETRY_DELAYS", [0.0])
    with pytest.raises(fq.TushareApiError, match="重试"):
        fq.ts_post_retry("daily", "t", {}, "")


# ─────────────────────────────────────────────────────────────────────────
# 刷库模式（cmd_sync，经 main 走完整 CLI 路径；API 打桩、不联网）
# ─────────────────────────────────────────────────────────────────────────


def test_sync_full_fetch_new_code(tmp_path, capsys, monkeypatch):
    """新票 → 全量：按 --full-days 自然日窗口取数，幂等入库并打印汇总摘要。"""
    rows = [_bar(date.today() - timedelta(days=i), 10.0 + i) for i in range(3)]
    fake, calls = _fake_api(daily_rows=rows)
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    store = tmp_path / "store"
    rc = fq.main(["--codes", "600519", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert [c[0] for c in calls] == ["daily"]
    params = calls[0][1]
    assert params["ts_code"] == "600519.SH"
    assert params["start_date"] == (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    assert params["end_date"] == date.today().strftime("%Y%m%d")
    assert fq.store_status(store, "600519.SH")[0] == 3
    out = capsys.readouterr().out
    assert "增量 0 / 全量 1 / 免取 0" in out
    assert "共 +3 根 K 线" in out          # 汇总式入库摘要（不逐票罗列）
    assert "[warn] 600519.SH 全量取数后仅 3 根（min-bars=30）" in out  # 异常逐只：不足 min-bars
    assert "全部成功" in out


def test_sync_incremental(tmp_path, capsys, monkeypatch):
    """库内 30 根且末根=昨天 → 增量补今天一根（区间 = 末根+1 → 今天）。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [_bar(today - timedelta(days=30 - i), 10.0)
                                          for i in range(30)]})
    fake, calls = _fake_api(daily_rows=[_bar(today, 10.5)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert calls[0][1]["start_date"] == today.strftime("%Y%m%d")
    assert fq.store_status(store, "600519.SH") == (31, today.strftime("%Y%m%d"))
    out = capsys.readouterr().out
    assert "增量 1 / 全量 0 / 免取 0" in out
    assert "1 只有新数据、共 +1 根 K 线" in out   # 汇总式：只数 + 根数，不逐票罗列


def test_sync_all_fresh_no_api(tmp_path, capsys, monkeypatch):
    """库内已含今天 K 线且根数达标 → 免取，零 API 调用。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [_bar(today - timedelta(days=29 - i), 10.0)
                                          for i in range(30)]})
    fake, calls = _fake_api()
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert calls == []
    assert "无需取数" in capsys.readouterr().out


def test_sync_min_bars_triggers_full(tmp_path, capsys, monkeypatch):
    """库内根数不足 --min-bars → 即使末根是今天也判全量重取。"""
    store = tmp_path / "store"
    fq.upsert_store(store, {"600519.SH": [_bar(date.today(), 10.0)]})
    fake, _ = _fake_api(daily_rows=[_bar(date.today(), 10.0)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t", "--store", str(store),
                  "--interval", "0", "--min-bars", "30"])
    assert rc == 0
    assert "增量 0 / 全量 1 / 免取 0" in capsys.readouterr().out


def test_sync_partial_failure(tmp_path, capsys, monkeypatch):
    """部分标的取数失败 → rc=1 打印失败清单；成功标的照常入库，失败标的不动库。"""
    store = tmp_path / "store"

    def fake(api_name, token, params, fields):
        if params["ts_code"] == "BAD.SH":
            raise fq.TushareApiError("抱歉，您还没有权限")
        return [_bar(date.today(), 10.0)]

    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH,BAD.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "1 只成功 / 1 只失败" in out
    assert "BAD.SH" in out
    assert fq.store_status(store, "600519.SH")[0] == 1
    assert fq.store_status(store, "BAD.SH") == (0, None)


def test_sync_full_range_zero_rows(tmp_path, capsys, monkeypatch):
    """全量区间 API 返回 0 根 → 判失败并提示检查代码。"""
    fake, _ = _fake_api(daily_rows=[])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "000000.SZ", "--token", "t",
                  "--store", str(tmp_path / "s"), "--interval", "0"])
    assert rc == 1
    assert "全量区间 API 返回 0 根" in capsys.readouterr().out


def test_sync_store_unwritable_exit_3(tmp_path, capsys, monkeypatch):
    """行情库目录被同名文件占住 → 写回 OSError → rc=3。"""
    blocker = tmp_path / "store"
    blocker.write_text("占位文件", encoding="utf-8")
    fake, _ = _fake_api(daily_rows=[_bar(date.today(), 10.0)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(blocker), "--interval", "0"])
    assert rc == 3
    assert "写回失败" in capsys.readouterr().out


def test_sync_token_missing_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(SystemExit) as ei:
        fq.main(["--codes", "600519.SH", "--env", str(tmp_path / "no.env")])
    assert ei.value.code == 2
    assert "未找到 tushare token" in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────────────
# 快照模式（cmd_snapshot，经 main 打桩 API）
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_fresh_store_zero_price_api(tmp_path, capsys, monkeypatch):
    """库内已含今天 K 线 → 价格零 API（直接用库内末根），仅 daily_basic 一次。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [_bar(today, 1888.0)]})
    basic = [{"ts_code": "600519.SH", "trade_date": today.strftime("%Y%m%d"),
              "close": 1888.0, "turnover_rate": 0.5, "pe": 20.0, "pe_ttm": 19.0,
              "pb": 8.0, "dv_ttm": 2.5, "total_mv": 100.0, "circ_mv": 100.0}]
    fake, calls = _fake_api(basic_rows=basic)
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--snapshot", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert [c[0] for c in calls] == ["daily_basic"]
    out = capsys.readouterr().out
    assert "1,888.00" in out
    assert "PE(TTM)：19.00" in out


def test_snapshot_pulls_and_writes_back(tmp_path, capsys, monkeypatch):
    """库内无数据 → 拉近 15 自然日日线，打印收盘，顺手增量写回行情库。"""
    store = tmp_path / "store"
    today = date.today()
    daily = [_bar(today - timedelta(days=1), 10.0), _bar(today, 10.5)]
    fake, calls = _fake_api(daily_rows=daily, basic_rows=[{"pe_ttm": 15.0, "pb": 1.5}])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--snapshot", "600519", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    apis = [c[0] for c in calls]
    assert apis[0] == "daily"
    assert "daily_basic" in apis
    assert calls[0][1]["start_date"] == (date.today() - timedelta(days=15)).strftime("%Y%m%d")
    assert fq.store_status(store, "600519.SH") == (2, today.strftime("%Y%m%d"))
    assert "10.50" in capsys.readouterr().out


def test_snapshot_hk_no_basic(tmp_path, capsys, monkeypatch):
    """港股走 hk_daily，不调 daily_basic，估值字段如实标注不可用。"""
    fake, calls = _fake_api(daily_rows=[_bar(date.today(), 300.0)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--snapshot", "00700", "--token", "t",
                  "--store", str(tmp_path / "s"), "--interval", "0"])
    assert rc == 0
    assert [c[0] for c in calls] == ["hk_daily"]
    out = capsys.readouterr().out
    assert "300.00" in out
    assert "估值字段不可用" in out


def test_snapshot_failure_lists_code(tmp_path, capsys, monkeypatch):
    """取数失败 → rc=1，收尾列出失败代码。"""
    def fake(*a, **k):
        raise fq.TushareApiError("网络异常")

    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--snapshot", "000000.SZ", "--token", "t",
                  "--store", str(tmp_path / "s"), "--interval", "0"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "取数失败" in out
    assert "000000.SZ" in out


# ─────────────────────────────────────────────────────────────────────────
# pct_chg 缺口检测与刷库回补（旧版六列库自动升级）
# ─────────────────────────────────────────────────────────────────────────


def _write_legacy_six_col_store(store: Path, code: str, n: int = 130) -> None:
    """手写旧版六列库（无 pct_chg 列），模拟历史遗留行情库。"""
    import csv as _csv
    fp = store / f"{code}.csv"
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["trade_date", "open", "high", "low", "close", "vol"])
        for i in range(n):
            w.writerow([(date.today() - timedelta(days=n - 1 - i)).strftime("%Y%m%d"),
                        10, 10, 10, 10 + i * 0.1, 100])


def test_store_pct_gap_legacy_six_col(tmp_path):
    """旧版六列库（表头无 pct_chg）→ 判有缺口。"""
    _write_legacy_six_col_store(tmp_path, "600519.SH")
    assert fq.store_pct_gap(tmp_path, "600519.SH") is True


def test_store_pct_gap_complete(tmp_path):
    """七列完整库（每行都有 pct_chg）→ 无缺口。"""
    fq.upsert_store(tmp_path, {"600519.SH": [
        _bar(date(2025, 1, 1), 10.0, pct_chg=0.0),
        _bar(date(2025, 1, 2), 10.5, pct_chg=5.0),
    ]})
    assert fq.store_pct_gap(tmp_path, "600519.SH") is False


def test_store_pct_gap_partial_missing(tmp_path):
    """有 pct_chg 列但个别行为空 → 同样判缺口（脏数据不凑合）。"""
    import csv as _csv
    fq.upsert_store(tmp_path, {"600519.SH": [_bar(date(2025, 1, 1), 10.0, pct_chg=0.0)]})
    fp = tmp_path / "600519.SH.csv"
    with fp.open("a", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["20250102", 10, 10, 10, 10.5, 100, ""])   # pct_chg 空值
    assert fq.store_pct_gap(tmp_path, "600519.SH") is True


def test_store_pct_gap_missing_file(tmp_path):
    """库内无该标的 → 不判缺口（由根数判定走全量）。"""
    assert fq.store_pct_gap(tmp_path, "NOPE.SH") is False


def test_sync_refills_pct_gap_from_store_first_date(tmp_path, capsys, monkeypatch):
    """旧版六列库刷库 → 自动按「库内首日 → 今天」重取回补，缺口行被带涨跌幅的新行覆盖。"""
    store = tmp_path / "store"
    _write_legacy_six_col_store(store, "600519.SH", n=130)
    today = date.today()
    # API 回的新行带 pct_chg（七列）
    refill = [_bar(today - timedelta(days=130 - i), 10.0 + i * 0.1, pct_chg=1.0)
              for i in range(131)]
    fake, calls = _fake_api(daily_rows=refill)
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0", "--min-bars", "30"])
    assert rc == 0
    # 重取区间起点 = 库内首日（不是末根+1，也不是 full-days 窗口截断）
    first_date = (today - timedelta(days=129)).strftime("%Y%m%d")
    assert calls[0][1]["start_date"] == first_date
    out = capsys.readouterr().out
    assert "pct_chg 缺口" in out
    # 回补后库内无缺口、行数按去重合并
    assert fq.store_pct_gap(store, "600519.SH") is False
    assert fq.store_status(store, "600519.SH")[0] == 131


def test_sync_no_gap_no_refill(tmp_path, capsys, monkeypatch):
    """七列完整库 → 不触发缺口回补，正常增量。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [
        _bar(today - timedelta(days=30 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    fake, calls = _fake_api(daily_rows=[_bar(today, 10.5, pct_chg=0.5)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert calls[0][1]["start_date"] == today.strftime("%Y%m%d")   # 正常增量区间
    assert "pct_chg 缺口" not in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────
# --realtime：盘中/近收盘实时叠加（腾讯/新浪公开源解析、临时K线、覆盖升级）
# ─────────────────────────────────────────────────────────────────────────

# 腾讯接口真实返回格式样例（GBK 文本，v_<symbol>="字段~字段~…"；截取自实测响应）
_TENCENT_TEXT = (
    'v_sh600519="1~贵州茅台~600519~1270.53~1288.00~1287.00~24248~11292~12944~1270.17~1~'
    '1270.09~5~1270.08~7~1270.03~2~1270.02~5~1270.52~1~1270.54~1~1270.55~4~1270.56~7~'
    '1270.60~1~~20261013145050~-17.47~-1.36~1288.48~1268.11~1270.53/24248/3097321805~'
    '24248~309732~0.19~19.50~~1288.48~1268.11~1.58~15882.66~15882.66~6.32~1416.80~1159.20~'
    '0.80~6~1277.37~17.84~19.29~~~0.06~309732.1805~0.0000~0~   A~GP-A~-5.83~2.83~4.09~'
    '32.41~27.30~1539.98~1151.01~1.43~-3.46~4.92~1250081601~1250081601~17.65~-8.67~'
    '1250081601~~~-7.07~-0.09~~CNY~0~___D__F__N~1271.00~-8~";\n'
)

# 新浪接口真实返回格式样例（GBK 文本，var hq_str_<symbol>="字段,字段,…";成交量单位为股）
_SINA_TEXT = (
    'var hq_str_sh600519="贵州茅台,1287.000,1288.000,1270.550,1288.480,1268.110,'
    '1270.180,1270.520,2425369,3098084131.000,500,1270.180,400,1270.170,100,1270.090,'
    '400,1270.080,100,1270.030,100,1270.520,700,1270.560,100,1270.600,100,1270.630,'
    '100,1270.640,2026-10-13,14:50:53,00,";\n'
)


def test_rt_symbol():
    assert fq._rt_symbol("600519.SH") == "sh600519"
    assert fq._rt_symbol("000333.SZ") == "sz000333"
    assert fq._rt_symbol("833171.BJ") == "bj833171"
    assert fq._rt_symbol("510300.SH") == "sh510300"
    assert fq._rt_symbol("00700.HK") is None      # 港股无实时源，如实跳过
    assert fq._rt_symbol("") is None


def test_norm_tencent():
    q = fq._norm_tencent("sh600519", _TENCENT_TEXT.split('"')[1].split("~"))
    assert q["name"] == "贵州茅台"
    assert q["price"] == 1270.53 and q["prev_close"] == 1288.00 and q["open"] == 1287.00
    assert q["high"] == 1288.48 and q["low"] == 1268.11
    assert q["vol"] == 24248.0                  # 腾讯成交量原生单位：手
    assert q["amount"] == 309732.0              # 成交额原生单位：万元
    assert q["turnover"] == 0.19
    assert q["trade_date"] == "20261013" and q["trade_time"] == "14:50:50"


def test_norm_sina_unit_conversion():
    q = fq._norm_sina("sh600519", _SINA_TEXT.split('"')[1].split(","))
    assert q["name"] == "贵州茅台"
    assert q["price"] == 1270.55 and q["prev_close"] == 1288.00 and q["open"] == 1287.00
    assert abs(q["vol"] - 24253.69) < 0.01      # 股 → 手（/100）
    assert abs(q["amount"] - 309808.4131) < 1   # 元 → 万元（/10000）
    assert q["trade_date"] == "20261013" and q["trade_time"] == "14:50:53"


def test_norm_source_short_row_rejected():
    """字段数不足（越界/脏行）→ 归一失败返回 None，不猜数据。"""
    assert fq._norm_tencent("sh600519", ["1", "贵州茅台"]) is None
    assert fq._norm_sina("sh600519", ["贵州茅台", "1.0"]) is None


def test_fetch_realtime_tencent_then_sina(monkeypatch):
    """腾讯整体不可达 → 新浪兜底；请求 URL 用实时源符号（sh600519 而非 600519.SH）。"""
    seen_urls = []

    def fake_get(url, headers=None):
        seen_urls.append(url)
        if "qt.gtimg.cn" in url:
            raise OSError("腾讯不可达")
        assert headers and headers.get("Referer")     # 新浪需 Referer 头
        return _SINA_TEXT

    monkeypatch.setattr(fq, "_http_get_text", fake_get)
    quotes, missing = fq.fetch_realtime(["600519.SH", "00700.HK"])
    assert missing == []                              # 港股无符号，不进未取得清单
    assert set(quotes) == {"600519.SH"}
    assert quotes["600519.SH"]["source"] == "新浪"
    assert quotes["600519.SH"]["price"] == 1270.55
    # 两个源的 URL 都必须携带实时源符号，而非 ts_code
    assert "q=sh600519" in seen_urls[0]
    assert "list=sh600519" in seen_urls[1]


def test_fetch_realtime_both_sources_fail(monkeypatch):
    """两源均失败 → 如实进未取得清单。"""
    def fake_get(url, headers=None):
        raise OSError("网络不可达")

    monkeypatch.setattr(fq, "_http_get_text", fake_get)
    quotes, missing = fq.fetch_realtime(["600519.SH"])
    assert quotes == {} and missing == ["600519.SH"]


def _live_quote(price=10.5, prev=10.0, vol=1234.0, tdate=None, **kw):
    today = tdate or date.today().strftime("%Y%m%d")
    q = {"symbol": "sh600519", "name": "测试", "source": "腾讯", "price": price,
         "prev_close": prev, "open": kw.get("open", 10.1), "high": kw.get("high", 10.6),
         "low": kw.get("low", 9.9), "vol": vol, "amount": 12000.0,
         "turnover": kw.get("turnover", 0.5), "trade_date": today,
         "trade_time": "14:52:31"}
    q.update({k: v for k, v in kw.items()
              if k in ("open", "high", "low", "turnover")})
    return q


def test_rt_bar_valid():
    today = date.today().strftime("%Y%m%d")
    bar = fq._rt_bar(_live_quote(price=10.5, prev=10.0, open=10.1, high=10.6, low=9.9), today)
    assert bar["trade_date"] == today
    assert bar["close"] == 10.5 and bar["open"] == 10.1
    assert bar["high"] == 10.6 and bar["low"] == 9.9
    assert bar["vol"] == 1234.0
    assert bar["pct_chg"] == "5.0000"               # (10.5/10-1)*100
    assert bar["rt"] == "1"                         # 临时K线标记


def test_rt_bar_defensive_clamps():
    """开盘价为 0 / 最高最低缺失 → 用现价兜底并夹逼，不写脏值。"""
    today = date.today().strftime("%Y%m%d")
    bar = fq._rt_bar(_live_quote(open=0.0, high=0.0, low=0.0), today)
    assert bar["open"] == 10.5 and bar["high"] == 10.5 and bar["low"] == 10.5


def test_rt_bar_fail_closed():
    today = date.today().strftime("%Y%m%d")
    # 报价日期非今天（休市/未开盘/隔夜残留）→ 不造K线
    assert fq._rt_bar(_live_quote(tdate="19990101"), today) is None
    # 停牌零成交 → 不造K线
    assert fq._rt_bar(_live_quote(vol=0.0), today) is None
    # 现价/昨收异常 → 不造K线
    assert fq._rt_bar(_live_quote(price=0.0), today) is None
    assert fq._rt_bar(_live_quote(prev=0.0), today) is None


def test_store_last_bar_is_rt(tmp_path):
    fq.upsert_store(tmp_path, {"A.SH": [_bar(date(2025, 1, 2), 10.0, pct_chg=1.0)]})
    assert fq.store_last_bar_is_rt(tmp_path, "A.SH") is False
    fq.upsert_store(tmp_path, {"A.SH": [{"trade_date": "20250103", "open": 10, "high": 10,
                                         "low": 10, "close": 10.2, "vol": 100,
                                         "pct_chg": 2.0, "rt": "1"}]})
    assert fq.store_last_bar_is_rt(tmp_path, "A.SH") is True
    assert fq.store_last_bar_is_rt(tmp_path, "NOPE.SH") is False


def test_sync_rt_bar_upgrade_to_official(tmp_path, capsys, monkeypatch):
    """库内末根是临时K线（rt=1）→ 刷库按「今天→今天」重取；tushare 已发布则覆盖升级。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [
        _bar(today - timedelta(days=29 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    fq.upsert_store(store, {"600519.SH": [
        {"trade_date": today.strftime("%Y%m%d"), "open": 10.5, "high": 10.6, "low": 10.4,
         "close": 10.55, "vol": 5000.0, "pct_chg": 0.5, "rt": "1"}]})
    fake, calls = _fake_api(daily_rows=[_bar(today, 10.6, pct_chg=0.6)])
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert calls[0][1]["start_date"] == today.strftime("%Y%m%d")   # 只重取今天
    assert calls[0][1]["end_date"] == today.strftime("%Y%m%d")
    assert "盘中临时K线（rt=1），重取今日正式K线" in capsys.readouterr().out
    rows = fq._read_store_rows(fq.store_csv_path(store, "600519.SH"))
    assert rows[-1]["trade_date"] == today.strftime("%Y%m%d")
    assert rows[-1].get("rt") != "1"          # 已被正式K线覆盖，rt 标记消失
    assert float(rows[-1]["close"]) == 10.6


def test_sync_rt_bar_kept_when_unpublished(tmp_path, capsys, monkeypatch):
    """库内末根是临时K线、tushare 尚未发布今日日线 → 重取空、临时K线原样保留。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [
        _bar(today - timedelta(days=29 - i), 10.0, pct_chg=0.1) for i in range(30)] + [
        {"trade_date": today.strftime("%Y%m%d"), "open": 10.5, "high": 10.6, "low": 10.4,
         "close": 10.55, "vol": 5000.0, "pct_chg": 0.5, "rt": "1"}]})
    fake, _ = _fake_api(daily_rows=[])               # tushare 未发布 → 空
    monkeypatch.setattr(fq, "ts_post_retry", fake)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0"])
    assert rc == 0
    assert fq.store_last_bar_is_rt(store, "600519.SH") is True   # 临时K线不动


def test_sync_realtime_patches_today_bar(tmp_path, capsys, monkeypatch):
    """--realtime：库内补到昨日、实时报价为今日 → 今日临时K线入库（rt=1）。"""
    store = tmp_path / "store"
    today = date.today()
    fq.upsert_store(store, {"600519.SH": [
        _bar(today - timedelta(days=30 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    # 库内末根=昨天（tushare 盘中最多发布到昨日）
    rows = fq._read_store_rows(fq.store_csv_path(store, "600519.SH"))
    assert rows[-1]["trade_date"] != today.strftime("%Y%m%d")
    fake, _ = _fake_api(daily_rows=[])
    monkeypatch.setattr(fq, "ts_post_retry", fake)

    def fake_rt(ts_codes):
        return ({"600519.SH": _live_quote(price=10.5, prev=10.0)}, [])

    monkeypatch.setattr(fq, "fetch_realtime", fake_rt)
    rc = fq.main(["--codes", "600519.SH", "--token", "t",
                  "--store", str(store), "--interval", "0", "--realtime"])
    assert rc == 0
    n, last = fq.store_status(store, "600519.SH")
    assert last == today.strftime("%Y%m%d") and n == 31
    rows = fq._read_store_rows(fq.store_csv_path(store, "600519.SH"))
    assert rows[-1]["rt"] == "1" and float(rows[-1]["close"]) == 10.5
    out = capsys.readouterr().out
    assert "今日临时K线入库：1 只" in out
    assert "实时时点 14:52:31" in out


def test_sync_realtime_skips_official_and_hk(tmp_path, capsys, monkeypatch):
    """今日已有正式K线 → 不补；港股无实时源 → 如实列出跳过；休市报价 → 不造K线。"""
    store = tmp_path / "store"
    today = date.today()
    # 600519 / 00700 库内末根=昨天（tushare 盘中最多发布到昨日）；000333 已有今日正式K线
    fq.upsert_store(store, {"600519.SH": [
        _bar(today - timedelta(days=30 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    fq.upsert_store(store, {"000333.SZ": [
        _bar(today - timedelta(days=29 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    fq.upsert_store(store, {"00700.HK": [
        _bar(today - timedelta(days=30 - i), 10.0, pct_chg=0.1) for i in range(30)]})
    fake, _ = _fake_api(daily_rows=[])
    monkeypatch.setattr(fq, "ts_post_retry", fake)

    called = []

    def fake_rt(ts_codes):
        called.extend(ts_codes)
        # 600519 返回隔夜残留报价（日期非今日）→ 不造K线
        return ({"600519.SH": _live_quote(tdate="19990101")}, [])

    monkeypatch.setattr(fq, "fetch_realtime", fake_rt)
    rc = fq.main(["--codes", "600519.SH,000333.SZ,00700.HK", "--token", "t",
                  "--store", str(store), "--interval", "0", "--realtime"])
    assert rc == 0
    assert called == ["600519.SH"]                 # 正式K线已到 / 港股不参与
    out = capsys.readouterr().out
    assert "港股暂不支持盘中临时K线" in out
    assert "跳过 1 只" in out and "600519.SH" in out
    assert fq.store_status(store, "600519.SH")[1] != today.strftime("%Y%m%d")


def test_snapshot_realtime_overlay(tmp_path, capsys, monkeypatch):
    """--snapshot --realtime：tushare 未发布今日 → 实时价展示 + 估值外推 + 临时K线写库。"""
    store = tmp_path / "store"
    today = date.today()
    yesterday = today - timedelta(days=1)
    fake, calls = _fake_api(
        daily_rows=[_bar(yesterday, 10.0, pct_chg=0.0)],       # tushare 只到昨天
        basic_rows=[{"ts_code": "600519.SH", "trade_date": yesterday.strftime("%Y%m%d"),
                     "close": 10.0, "turnover_rate": 0.4, "pe": 20.0, "pe_ttm": 19.0,
                     "pb": 4.0, "dv_ttm": 2.0, "total_mv": 1000.0, "circ_mv": 800.0}])
    monkeypatch.setattr(fq, "ts_post_retry", fake)

    def fake_rt(ts_codes):
        return ({"600519.SH": _live_quote(price=10.5, prev=10.0, turnover=0.42)}, [])

    monkeypatch.setattr(fq, "fetch_realtime", fake_rt)
    rc = fq.main(["--snapshot", "600519", "--token", "t",
                  "--store", str(store), "--interval", "0", "--realtime"])
    assert rc == 0
    # 估值基数取上一正式交易日（昨天），而非临时K线的今天
    basic_call = next(c for c in calls if c[0] == "daily_basic")
    assert basic_call[1]["trade_date"] == yesterday.strftime("%Y%m%d")
    out = capsys.readouterr().out
    assert "盘中实时 14:52:31，来源：腾讯" in out
    assert "最新价：10.50" in out and "+5.00%" in out
    assert "PE(TTM)：19.95*" in out                  # 19.0 × 10.5/10
    assert "PB：4.20*" in out
    assert "总市值：1,050.00万*" in out              # 1000 × 1.05
    assert "股息率(TTM)：1.90%*" in out               # 2.0 / 1.05
    assert "实时估算" in out
    # 临时K线写回行情库
    assert fq.store_status(store, "600519.SH")[1] == today.strftime("%Y%m%d")
    assert fq.store_last_bar_is_rt(store, "600519.SH") is True


def test_snapshot_realtime_official_wins(tmp_path, capsys, monkeypatch):
    """--snapshot --realtime：tushare 已发布今日正式K线 → 正式优先，不做实时叠加。"""
    store = tmp_path / "store"
    today = date.today()
    fake, _ = _fake_api(daily_rows=[_bar(today, 10.8, pct_chg=0.8)],
                        basic_rows=[{"pe_ttm": 21.0, "pb": 4.3}])
    monkeypatch.setattr(fq, "ts_post_retry", fake)

    def fake_rt(ts_codes):
        return ({"600519.SH": _live_quote(price=10.5)}, [])

    monkeypatch.setattr(fq, "fetch_realtime", fake_rt)
    rc = fq.main(["--snapshot", "600519", "--token", "t",
                  "--store", str(store), "--interval", "0", "--realtime"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "收盘：10.80" in out                      # 正式K线价格
    assert "实时估算" not in out
    assert fq.store_last_bar_is_rt(store, "600519.SH") is False


def test_snapshot_realtime_stale_quote_fallback(tmp_path, capsys, monkeypatch):
    """--snapshot --realtime 但实时报价非今日（休市）→ 按 tushare 日线展示并说明。"""
    store = tmp_path / "store"
    today = date.today()
    fake, _ = _fake_api(daily_rows=[_bar(today - timedelta(days=1), 10.0, pct_chg=0.0)],
                        basic_rows=[{"pe_ttm": 19.0}])
    monkeypatch.setattr(fq, "ts_post_retry", fake)

    def fake_rt(ts_codes):
        return ({"600519.SH": _live_quote(tdate="19990101")}, [])

    monkeypatch.setattr(fq, "fetch_realtime", fake_rt)
    rc = fq.main(["--snapshot", "600519", "--token", "t",
                  "--store", str(store), "--interval", "0", "--realtime"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "实时报价非今日" in out
    assert "收盘：10.00" in out and "tushare 日线" in out
