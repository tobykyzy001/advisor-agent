"""行情取数 CLI：自包含单文件脚本（纯标准库，直连 tushare pro REST API）。

这是全工作区**统一的行情取数入口**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py` 提供给目标工作区里的
agent 下载执行（与 w_bottom_screen.py / momentum_strategy.py 同一分发模式）。

设计约束：
- 纯标准库（argparse / csv / json / os / re / time / urllib.request），零第三方依赖。
- **不依赖 MCP 桥**：直接 POST http://api.tushare.pro（JSON 协议），确定性代码取数，
  行情数据不经过任何 LLM 上下文。
- 显式 ProxyHandler({}) 关闭系统代理直连（与 stock-valuation 的 fetch_snapshot.py 同款，
  避免本机代理对数据域名不可达）。

两种用法：
  1) 刷库（默认，--codes 或 --watchlist 二选一）：
     python fetch_quotes.py --watchlist output/watchlist/watchlist.yaml
         [--min-bars 30] [--full-days 90] [--store output/quotes-store]
     —— 对照本地 CSV 行情库 output/quotes-store/ 给每只定区间：
       免取（库内已含今天 K 线）/ 增量（库内最后日期+1 → 今天）/ 全量（新票或库内
       根数不足 --min-bars，取近 --full-days 自然日），逐只调 API 拉日线后**幂等合并**
       写回 CSV（按 trade_date 去重、新行覆盖同日旧行）。输出为**汇总式**：只报
       成功/失败与入库根数汇总，逐票「入库 N 根」明细与 K 线一律不打印（大池子
       逐行刷屏只会白耗 agent 会话 token），仅异常（取数失败 / 全量后仍不足
       min-bars）逐只列出。
  2) 快照（--snapshot）：
     python fetch_quotes.py --snapshot 600519.SH[,000333.SZ]
     —— 打印每只最新收盘 / 涨跌幅 / 换手 / PE / PB / 市值 / 股息率（小数据，可进会话），
       顺手把拉到的日线增量写回行情库。供估值 / 持仓复核 / 论据核查等即取即用场景。

本地 CSV 行情库（与 w_bottom_screen.py / momentum_strategy.py 共享 output/quotes-store/）：
- 每只标的一份 CSV：<store>/<ts_code>.csv，列 trade_date,open,high,low,close,vol。
- 幂等合并：同一批增量重复合并结果不变，中途失败重跑无副作用。

token 来源（优先级）：--token 参数 > 环境变量 TUSHARE_TOKEN > 工作区 .env 里的
TUSHARE_TOKEN=…（支持引号与 export 前缀）。tushare pro token 见 https://tushare.pro。

退出码：0=成功；1=部分标的失败（已打印失败清单）；2=token 缺失；3=网络/接口持续失败。
输出仅供研究参考，不构成投资建议；数据口径以 tushare 接口返回为准。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

TUSHARE_API = "http://api.tushare.pro"

#: CSV 列固定：与 w_bottom_screen.py / momentum_strategy.py 共用同一库结构
CSV_FIELDS = ["trade_date", "open", "high", "low", "close", "vol"]

#: 日线接口：A股（沪深北）走 daily，港股走 hk_daily（按 ts_code 后缀分流）
_HK_SUFFIX = ".HK"

#: 限速：默认相邻两次 API 请求的间隔秒数（免费档约 200 次/分钟，0.35s 是安全值）
_INTERVAL_DEFAULT = 0.35

#: 重试退避（秒）：网络错误 / 限流时逐次等待后重试
_RETRY_DELAYS = [2.0, 10.0, 30.0]

#: 接口报错文案里出现这些词 → 判定权限/积分问题，重试无意义，直接失败该只
_PERM_HINTS = ("权限", "没有权限", "不在您", "开通", "抱歉，您还没有")


class TushareApiError(RuntimeError):
    """tushare 接口业务错误（code != 0）。permanent=True 表示重试无意义。"""

    def __init__(self, msg: str, permanent: bool = False) -> None:
        super().__init__(msg)
        self.permanent = permanent


# ═══════════════════════════════════════════════════════════════════════════
# token 解析（--token > 环境变量 TUSHARE_TOKEN > 工作区 .env）
# ═══════════════════════════════════════════════════════════════════════════

_ENV_KEY_RE = re.compile(r"^(?:export\s+)?TUSHARE_TOKEN\s*=\s*(.*)$")


def load_env_token(env_path: Path) -> str | None:
    """从 .env 文件解析 TUSHARE_TOKEN（支持引号包裹与行内注释）。"""
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _ENV_KEY_RE.match(line.strip())
        if not m:
            continue
        val = m.group(1).strip()
        # 去掉行内注释（仅对未加引号的值生效）
        if not (val.startswith('"') or val.startswith("'")):
            val = val.split("#", 1)[0].strip()
        # 去掉成对引号
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if val:
            return val
    return None


def resolve_token(cli_token: str | None, env_file: Path) -> str:
    """按优先级解析 token；找不到则抛出带指引的错误。"""
    if cli_token and cli_token.strip():
        return cli_token.strip()
    env_val = os.getenv("TUSHARE_TOKEN", "").strip()
    if env_val:
        return env_val
    file_val = load_env_token(env_file)
    if file_val:
        return file_val
    print("[error] 未找到 tushare token。三种配置方式任选其一：",
          file=sys.stderr)
    print("  1) 命令行参数：--token <你的 tushare pro token>", file=sys.stderr)
    print("  2) 环境变量：set TUSHARE_TOKEN=<token>（Windows）"
          " / export TUSHARE_TOKEN=<token>（类 Unix）", file=sys.stderr)
    print(f"  3) 工作区 .env 文件（{env_file}）里写一行：TUSHARE_TOKEN=<token>",
          file=sys.stderr)
    print("  token 在 tushare pro 个人主页获取：https://tushare.pro/user/token",
          file=sys.stderr)
    raise SystemExit(2)


# ═══════════════════════════════════════════════════════════════════════════
# 代码规范化与观察仓清单读取（与 manage_watchlist.py / w_bottom_screen.py 同口径）
# ═══════════════════════════════════════════════════════════════════════════


def normalize_code(raw: str) -> str:
    """把用户输入的代码规范化为 tushare 格式。

    600519 → 600519.SH；000333 → 000333.SZ；833171/43xxxx → .BJ；920xxx（北交所
    920 号段）→ .BJ；5 位数字 → .HK；已带后缀（.SH/.SZ/.BJ/.HK）原样保留。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "." in s:
        return s.upper()
    if not s.isdigit():
        return s
    if len(s) == 5:
        return f"{s}.HK"
    # 北交所：8/4 开头与 920 号段（与 manage_watchlist.py 同口径，920 判定先于沪市 9 开头）
    if s[:3] == "920" or s.startswith(("4", "8")):
        return f"{s}.BJ"
    if s.startswith(("6", "5", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


_ITEM_KEY = re.compile(r"^\s*-\s+ts_code:\s*(.+?)\s*$")
_KEY = re.compile(r"^\s+(\w+):\s*(.*)$")


def load_watchlist_codes(path: Path) -> list[str]:
    """读取 watchlist.yaml 的 ts_code 清单（极简解析，与 w_bottom_screen.py 同款）。"""
    if not path.exists():
        raise SystemExit(f"[error] 观察仓清单不存在：{path}。请先初始化工作区或创建该文件。")
    codes: list[str] = []
    cur: dict[str, str] | None = None
    items: list[dict[str, str]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = _ITEM_KEY.match(ln)
        if m:
            if cur is not None:
                items.append(cur)
            cur = {"ts_code": m.group(1).strip().strip('"').strip("'")}
            continue
        km = _KEY.match(ln)
        if km and cur is not None:
            cur[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    if cur is not None:
        items.append(cur)
    for d in items:
        code = normalize_code(d.get("ts_code", ""))
        if code:
            codes.append(code)
    return codes


def resolve_codes(args: argparse.Namespace) -> list[str]:
    """取数标的清单：--codes 或 --watchlist 二选一。"""
    if args.codes and args.watchlist:
        raise SystemExit("[error] --codes 与 --watchlist 只能二选一。")
    if args.codes:
        out = [normalize_code(x) for x in re.split(r"[,，\s]+", args.codes) if x.strip()]
    elif args.watchlist:
        out = load_watchlist_codes(Path(args.watchlist))
    else:
        raise SystemExit("[error] 需要指定标的来源：--codes 600519.SH,000333.SZ 或 --watchlist <路径>。")
    # 去重且保序
    seen: set[str] = set()
    uniq = [c for c in out if not (c in seen or seen.add(c))]
    if not uniq:
        raise SystemExit("[error] 标的清单为空。")
    return uniq


# ═══════════════════════════════════════════════════════════════════════════
# 本地 CSV 行情库（每标的一份，增量合并，幂等；与两个策略脚本同结构）
# ═══════════════════════════════════════════════════════════════════════════


def store_csv_path(store_dir: Path, ts_code: str) -> Path:
    """库内某标的的 CSV 路径（文件名即 ts_code）。"""
    return store_dir / f"{ts_code}.csv"


def _read_store_rows(fp: Path) -> list[dict]:
    """读一份 CSV 为 dict 行（只保留 CSV_FIELDS 中存在的非空字段）。"""
    rows: list[dict] = []
    with fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            td = (r.get("trade_date") or "").strip()
            if not td:
                continue
            row = {"trade_date": td}
            for k in CSV_FIELDS[1:]:
                v = (r.get(k) or "").strip()
                if v:
                    row[k] = v
            rows.append(row)
    return rows


def store_status(store_dir: Path, ts_code: str) -> tuple[int, str | None]:
    """库内该标的的 (行数, 最后交易日)；无文件或空文件返回 (0, None)。"""
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return 0, None
    rows = _read_store_rows(fp)
    if not rows:
        return 0, None
    return len(rows), max(r["trade_date"] for r in rows)


def _next_date_str(trade_date: str) -> str:
    """'20250201'/'2025-02-01' → 次日 '20250202'（解析失败则原样返回）。"""
    d = _to_date(trade_date)
    if d is None:
        return trade_date
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def upsert_store(store_dir: Path, raw: dict[str, list]) -> dict[str, list]:
    """把增量行情合并进 CSV 库并写回，返回 {ts_code: 全量行（按日期升序）}。

    按 trade_date 去重、新行覆盖同日旧行——幂等：同一批增量重复合并结果不变；
    仅当出现新日期或新建文件时才真正写盘。
    """
    merged: dict[str, list] = {}
    for ts, rows in raw.items():
        fp = store_csv_path(store_dir, ts)
        by_date: dict[str, dict] = {}
        if fp.exists():
            for r in _read_store_rows(fp):
                by_date[r["trade_date"]] = r
        for row in rows or []:
            td = str(row.get("trade_date", "")).strip()
            if not td:
                continue
            rec = {"trade_date": td}
            for k in CSV_FIELDS[1:]:
                if k in row and row[k] is not None:
                    rec[k] = row[k]
            by_date[td] = rec
        dates = sorted(by_date)
        if not dates:
            merged[ts] = []
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for d in dates:
                w.writerow(by_date[d])
        merged[ts] = [by_date[d] for d in dates]
    return merged


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        if len(s) >= 8 and s[:8].isdigit():
            return datetime.strptime(s[:8], "%Y%m%d").date()
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# tushare REST API（直连、限速、重试）
# ═══════════════════════════════════════════════════════════════════════════

# 关闭系统代理的 opener：本机代理可能对数据域名不可达，直连更稳（同 fetch_snapshot.py）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ts_post(api_name: str, token: str, params: dict, fields: str,
            timeout: int = 30) -> list[dict]:
    """调用 tushare pro REST 接口一次，返回 dict 行列表。

    协议：POST http://api.tushare.pro，JSON body {api_name, token, params, fields}；
    返回 {code, msg, data:{fields, items}}，code != 0 视为业务错误。
    """
    body = {"api_name": api_name, "token": token, "params": params, "fields": fields}
    req = urllib.request.Request(
        TUSHARE_API,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TushareApiError(f"HTTP {e.code}：{e.reason}") from e
    if payload.get("code") != 0:
        msg = str(payload.get("msg") or "未知错误")
        if any(h in msg for h in _PERM_HINTS):
            raise TushareApiError(msg, permanent=True)
        raise TushareApiError(msg)
    data = payload.get("data") or {}
    cols = data.get("fields") or []
    return [dict(zip(cols, row)) for row in (data.get("items") or [])]


def ts_post_retry(api_name: str, token: str, params: dict, fields: str) -> list[dict]:
    """带重试的接口调用：网络错误/限流退避重试，权限类错误直接抛出。"""
    last_err: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return ts_post(api_name, token, params, fields)
        except TushareApiError as e:
            if e.permanent:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
        if attempt < len(_RETRY_DELAYS):
            wait = _RETRY_DELAYS[attempt]
            print(f"  [retry] {api_name} 调用失败（{last_err}），{wait:.0f}s 后第 "
                  f"{attempt + 1}/{len(_RETRY_DELAYS)} 次重试…", flush=True)
            time.sleep(wait)
    raise TushareApiError(f"重试 {len(_RETRY_DELAYS)} 次后仍失败：{last_err}")


def daily_api_name(ts_code: str) -> str:
    """日线接口分流：港股走 hk_daily，其余（沪深北）走 daily。"""
    return "hk_daily" if ts_code.upper().endswith(_HK_SUFFIX) else "daily"


#: 日线取数字段（多取 pre_close/pct_chg 供快照用；写库仍只写 CSV_FIELDS 六列）
_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol"

#: 每日指标字段（快照模式的估值部分；daily_basic 仅覆盖 A 股）
_BASIC_FIELDS = "ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,dv_ttm,total_mv,circ_mv"


class _Throttle:
    """极简限速器：相邻请求至少间隔 interval 秒。"""

    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, interval)
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# 模式一：刷库（增量 / 全量 / 免取，幂等合并写回）
# ═══════════════════════════════════════════════════════════════════════════


def cmd_sync(args: argparse.Namespace) -> int:
    codes = resolve_codes(args)
    store_dir = Path(args.store)
    token = resolve_token(args.token, Path(args.env))
    throttle = _Throttle(args.interval)
    today = date.today().strftime("%Y%m%d")

    # 对照库内状态分三类：增量（补尾巴）/ 全量（新票或根数不足）/ 免取（已到今天）
    fetch_ranges: dict[str, tuple[str, str]] = {}
    full_codes: list[str] = []
    fresh_codes: list[str] = []
    for code in codes:
        n, last = store_status(store_dir, code)
        if n >= args.min_bars and last:
            start = _next_date_str(last)
            if start > today:
                fresh_codes.append(code)
            else:
                fetch_ranges[code] = (start, today)
        else:
            full_codes.append(code)

    print(f"本地行情库：{store_dir}（每只一份 CSV，幂等合并写回）")
    print(f"标的 {len(codes)} 只：增量 {len(fetch_ranges)} / 全量 {len(full_codes)}"
          f" / 免取 {len(fresh_codes)}；今天={today}，min-bars={args.min_bars}，"
          f"full-days={args.full_days}")
    if not fetch_ranges and not full_codes:
        print("全部标的库内均已最新，无需取数。")
        return 0

    failures: list[tuple[str, str]] = []
    merged: dict[str, list] = {}

    def _fetch_one(code: str, start: str, end: str, is_full: bool) -> bool:
        """单只取数 + 合并进 merged；返回是否成功。"""
        try:
            throttle.wait()
            rows = ts_post_retry(
                daily_api_name(code), token,
                {"ts_code": code, "start_date": start, "end_date": end},
                _DAILY_FIELDS,
            )
        except TushareApiError as e:
            failures.append((code, str(e)))
            print(f"  {code} 取数失败：{e}")
            return False
        if is_full and not rows:
            failures.append((code, "全量区间 API 返回 0 根（代码可能有误或已退市）"))
            print(f"  {code} 全量区间 API 返回 0 根，请检查代码是否正确。")
            return False
        merged[code] = rows
        return True

    for code in codes:
        if code in fresh_codes:
            continue
        if code in fetch_ranges:
            s, e = fetch_ranges[code]
            _fetch_one(code, s, e, is_full=False)
        else:
            start = (date.today() - timedelta(days=args.full_days)).strftime("%Y%m%d")
            _fetch_one(code, start, today, is_full=True)

    # 幂等合并写回（免取与失败标的不动库）
    if merged:
        try:
            after = upsert_store(store_dir, merged)
        except OSError as e:
            print(f"[error] 行情库写回失败：{e}")
            return 3
        # 汇总式输出：逐票「入库 N 根」整屏刷进 agent 会话上下文是纯 token 损耗，
        # 正常标的只报一行汇总，仅异常（全量后仍不足 min-bars）逐只列出。
        with_new = [c for c in merged if merged[c]]
        total_new = sum(len(merged[c]) for c in with_new)
        if with_new:
            print(f"入库汇总：{len(with_new)} 只有新数据、共 +{total_new} 根 K 线；"
                  f"其余 {len(merged) - len(with_new)} 只库内无新数据。")
        else:
            print("入库汇总：增量区间无新数据（库内均已是最新交易日）。")
        for code in full_codes:
            n_bars = len(after.get(code) or [])
            if n_bars < args.min_bars:
                print(f"  [warn] {code} 全量取数后仅 {n_bars} 根（min-bars="
                      f"{args.min_bars}），历史不足将被策略计算自动排除。")

    if failures:
        print(f"\n完成：{len(codes) - len(failures)} 只成功 / {len(failures)} 只失败：")
        for code, msg in failures:
            print(f"  - {code}：{msg}")
        return 1
    print(f"\n完成：{len(codes)} 只全部成功。")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# 模式二：快照（最新行情 + 估值字段，小数据可进会话；顺手增量写库）
# ═══════════════════════════════════════════════════════════════════════════


def _fmt(v, suffix: str = "") -> str:
    """数值格式化：None/空 → '-'，其余保留两位并拼后缀。"""
    if v is None or v == "":
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.2f}{suffix}"


def _fmt_pct(v) -> str:
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def cmd_snapshot(args: argparse.Namespace) -> int:
    codes = [normalize_code(x) for x in re.split(r"[,，\s]+", args.snapshot) if x.strip()]
    if not codes:
        raise SystemExit("[error] --snapshot 需要至少一个代码，如 --snapshot 600519.SH")
    store_dir = Path(args.store)
    token = resolve_token(args.token, Path(args.env))
    throttle = _Throttle(args.interval)
    today = date.today().strftime("%Y%m%d")
    # 名称尽量从观察仓带出（若清单里有）
    name_map: dict[str, str] = {}
    try:
        if args.watchlist and Path(args.watchlist).exists():
            p = Path(args.watchlist)
            cur: dict[str, str] | None = None
            for ln in p.read_text(encoding="utf-8").splitlines():
                m = _ITEM_KEY.match(ln)
                if m:
                    if cur:
                        name_map[cur.get("ts_code", "")] = cur.get("name", "")
                    cur = {"ts_code": normalize_code(m.group(1).strip())}
                    continue
                km = _KEY.match(ln)
                if km and cur is not None:
                    cur[km.group(1)] = km.group(2).strip().strip('"').strip("'")
            if cur:
                name_map[cur.get("ts_code", "")] = cur.get("name", "")
    except OSError:
        pass

    failures: list[str] = []
    merged: dict[str, list] = {}
    for code in codes:
        try:
            # 库内已最新（含今天）→ 直接用库内最后一根，零 API 拿价格
            n, last = store_status(store_dir, code)
            bar: dict | None = None
            if n and last and _next_date_str(last) > today:
                rows = _read_store_rows(store_csv_path(store_dir, code))
                bar = rows[-1] if rows else None
            else:
                throttle.wait()
                rows = ts_post_retry(
                    daily_api_name(code), token,
                    {"ts_code": code,
                     "start_date": (date.today() - timedelta(days=15)).strftime("%Y%m%d"),
                     "end_date": today},
                    _DAILY_FIELDS,
                )
                merged[code] = rows
                bar = rows[-1] if rows else None
            if bar is None:
                failures.append(code)
                print(f"===== {code} =====\n  无行情数据（代码可能有误或已退市）")
                continue

            # 估值字段（仅 A 股：daily_basic 不覆盖港股）
            basic: dict | None = None
            basic_err = ""
            if daily_api_name(code) != "hk_daily":
                try:
                    throttle.wait()
                    got = ts_post_retry(
                        "daily_basic", token,
                        {"ts_code": code, "trade_date": str(bar["trade_date"])},
                        _BASIC_FIELDS,
                    )
                    basic = got[0] if got else None
                except TushareApiError as e:
                    basic_err = str(e)

            name = name_map.get(code, "")
            print(f"===== {code}" + (f" {name}" if name else "") + " =====")
            td = _to_date(bar.get("trade_date"))
            print(f"  日期：{td.isoformat() if td else bar.get('trade_date')}"
                  "（数据时点：tushare 日线）")
            pct = bar.get("pct_chg")
            if pct in (None, "") and bar.get("pre_close") not in (None, ""):
                try:
                    pct = (float(bar["close"]) / float(bar["pre_close"]) - 1) * 100
                except (TypeError, ValueError, ZeroDivisionError):
                    pct = None
            print(f"  收盘：{_fmt(bar.get('close'))}  涨跌幅：{_fmt_pct(pct)}"
                  f"  成交量：{_fmt(bar.get('vol'))} 手")
            if basic:
                print(f"  换手率：{_fmt(basic.get('turnover_rate'), '%')}"
                      f"  PE(TTM)：{_fmt(basic.get('pe_ttm'))}"
                      f"  PE(静)：{_fmt(basic.get('pe'))}"
                      f"  PB：{_fmt(basic.get('pb'))}")
                print(f"  总市值：{_fmt(basic.get('total_mv'), '万')}"
                      f"  流通市值：{_fmt(basic.get('circ_mv'), '万')}"
                      f"  股息率(TTM)：{_fmt(basic.get('dv_ttm'), '%')}")
            elif basic_err:
                print(f"  估值字段不可用：{basic_err}")
            else:
                print("  估值字段不可用（daily_basic 仅覆盖 A 股，港股暂无）。")
        except TushareApiError as e:
            failures.append(code)
            print(f"===== {code} =====\n  取数失败：{e}")

    # 顺手把拉到的日线增量写回行情库（幂等，失败不影响快照输出）
    if merged:
        try:
            upsert_store(store_dir, merged)
        except OSError as e:
            print(f"[warning] 行情库写回失败（不影响本次快照）：{e}")

    if failures:
        print(f"\n快照完成：{len(codes) - len(failures)} 只成功 / {len(failures)} 只失败"
              f"（{' '.join(failures)}）。")
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fetch_quotes",
        description="统一行情取数 CLI：直连 tushare pro，增量刷库 / 估值快照，"
                    "本地 CSV 行情库 output/quotes-store/ 幂等合并写回。",
    )
    ap.add_argument("--codes", help="标的清单（逗号分隔，如 600519.SH,000333.SZ；"
                                     "支持简写 600519 / 00700）")
    ap.add_argument("--watchlist", default=None,
                    help="观察仓清单 YAML 路径（与 --codes 二选一）")
    ap.add_argument("--snapshot", default=None,
                    help="快照模式：打印指定标的的最新行情与估值字段（逗号分隔）")
    ap.add_argument("--store", default="output/quotes-store",
                    help="本地 CSV 行情库目录（默认 output/quotes-store，三技能共享）")
    ap.add_argument("--min-bars", type=int, default=30,
                    help="库内根数低于该值判全量重取（默认 30；动量场景传 121）")
    ap.add_argument("--full-days", type=int, default=90,
                    help="全量取数回看自然日窗口（默认 90；动量场景传 420）")
    ap.add_argument("--token", default=None, help="tushare pro token（优先级最高）")
    ap.add_argument("--env", default=".env", help=".env 文件路径（默认工作区 ./.env）")
    ap.add_argument("--interval", type=float, default=_INTERVAL_DEFAULT,
                    help=f"相邻 API 请求间隔秒数（默认 {_INTERVAL_DEFAULT}，防限流）")
    args = ap.parse_args(argv)

    if args.snapshot:
        return cmd_snapshot(args)
    return cmd_sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
