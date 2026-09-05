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


def _bar(d: date, c: float = 10.0, vol: float = 100.0) -> dict:
    return {"trade_date": d.strftime("%Y%m%d"), "open": c, "high": c, "low": c,
            "close": c, "vol": vol}


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
    assert fp.read_text(encoding="utf-8").splitlines()[0] == "trade_date,open,high,low,close,vol"
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
    """新票 → 全量：按 --full-days 自然日窗口取数，幂等入库并打印一行摘要。"""
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
    assert "入库 3 根" in out
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
    assert "入库 1 根" in out


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
