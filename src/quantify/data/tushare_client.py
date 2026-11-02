"""tushare REST 轻量客户端（研究用）。

与 `src/workspace-init/fetch_quotes.py` 同源协议：POST api.tushare.pro，显式关闭系统
代理直连。区别是本模块面向「多接口、长区间」的研究取数，提供按日期分段拉取
（tushare 多数接口单次有 300/3000/4000 条上限，长区间必须切片）与保序去重。

token 优先级：显式参数 > 环境变量 TUSHARE_TOKEN > 工作区 .env。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

TUSHARE_API = "https://api.tushare.pro"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ENV_KEY_RE = re.compile(r"^(?:export\s+)?TUSHARE_TOKEN\s*=\s*(.*)$")
_PERM_HINTS = ("权限", "没有权限", "不在您", "开通", "抱歉，您还没有")


class TushareError(RuntimeError):
    """tushare 接口业务错误。"""


def resolve_token(token: str | None = None, env_path: str | Path = ".env") -> str:
    """按 参数 > 环境变量 > .env 的顺序解析 token；缺失时抛出 TushareError。"""
    if token and token.strip():
        return token.strip()
    env_token = (os.getenv("TUSHARE_TOKEN") or "").strip()
    if env_token:
        return env_token
    path = Path(env_path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _ENV_KEY_RE.match(line.strip())
            if not m:
                continue
            val = m.group(1).strip()
            if not val.startswith(('"', "'")):
                val = val.split("#", 1)[0].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if val:
                return val
    raise TushareError(
        "未找到 tushare token：请设置环境变量 TUSHARE_TOKEN、在工作区 .env 写入 "
        "TUSHARE_TOKEN=...，或传 --token。"
    )


def query(api_name: str, params: dict, fields: str, token: str,
          timeout: int = 30) -> list[dict]:
    """调用一次 tushare 接口，返回 dict 行列表（按 trade_date 升序）。"""
    body = {"api_name": api_name, "token": token, "params": params, "fields": fields}
    req = urllib.request.Request(
        TUSHARE_API, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TushareError(f"HTTP {e.code}：{e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TushareError(f"网络错误：{e}") from e
    if payload.get("code") != 0:
        msg = str(payload.get("msg") or "未知错误")
        if any(h in msg for h in _PERM_HINTS):
            raise TushareError(f"{api_name} 无权限：{msg}")
        raise TushareError(f"{api_name} 失败：{msg}")
    data = payload.get("data") or {}
    cols = data.get("fields") or []
    rows = [dict(zip(cols, row)) for row in (data.get("items") or [])]
    if rows and "trade_date" in rows[0]:
        rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    return rows


def _to_date(v: str | date) -> date:
    if isinstance(v, date):
        return v
    s = str(v).replace("-", "")
    return datetime.strptime(s[:8], "%Y%m%d").date()


def query_chunked(api_name: str, params: dict, fields: str, token: str,
                  start: str | date, end: str | date, chunk_days: int,
                  throttle: float = 0.15) -> list[dict]:
    """按固定天数切片拉取长区间，合并后按行去重并保持日期升序。

    tushare 多数接口单次返回条数有上限（如 300/3000/4000），长区间直接请求会被
    截断且不报错；切片是唯一可靠做法。
    """
    cur, last = _to_date(start), _to_date(end)
    if chunk_days < 1:
        raise ValueError("chunk_days 必须 >= 1")
    merged: dict[str, dict] = {}
    while cur <= last:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), last)
        p = dict(params)
        p["start_date"] = cur.strftime("%Y%m%d")
        p["end_date"] = chunk_end.strftime("%Y%m%d")
        for row in query(api_name, p, fields, token):
            merged[json.dumps(row, sort_keys=True, ensure_ascii=False)] = row
        cur = chunk_end + timedelta(days=1)
        if throttle > 0:
            time.sleep(throttle)
    rows = list(merged.values())
    if rows and "trade_date" in rows[0]:
        rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    return rows
