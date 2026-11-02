"""tushare REST 客户端的分段/去重测试（不联网）。"""
from __future__ import annotations

import pytest

from quantify.data import tushare_client as tc


def test_resolve_token_priority(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TUSHARE_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert tc.resolve_token(None, env) == "file-token"
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    assert tc.resolve_token(None, env) == "env-token"
    assert tc.resolve_token("cli-token", env) == "cli-token"


def test_resolve_token_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(tc.TushareError, match="未找到 tushare token"):
        tc.resolve_token(None, tmp_path / "nope.env")


def test_query_chunked_slices_and_dedupes(monkeypatch):
    calls: list[dict] = []

    def fake_query(api_name, params, fields, token, timeout=30):
        calls.append(dict(params))
        d = params["start_date"]
        # 故意让相邻切片返回同一天，验证去重
        return [{"trade_date": d, "close": 1.0},
                {"trade_date": params["end_date"], "close": 2.0}]

    monkeypatch.setattr(tc, "query", fake_query)
    rows = tc.query_chunked("daily", {}, "trade_date,close", "t",
                            "20200101", "20200110", chunk_days=4, throttle=0)
    assert [c["start_date"] for c in calls] == ["20200101", "20200105", "20200109"]
    assert [r["trade_date"] for r in rows] == ["20200101", "20200104", "20200105",
                                               "20200108", "20200109", "20200110"]


def test_query_chunked_bad_chunk():
    with pytest.raises(ValueError):
        tc.query_chunked("daily", {}, "", "t", "20200101", "20200110", chunk_days=0)
