"""行情取数 CLI：自包含单文件脚本（纯标准库，直连 tushare pro REST API）。

这是全工作区**统一的行情取数入口**，随插件包分发，投研工具面板在投递指令前经宿主
/provision 端点预置到目标工作区 scripts/ 供 agent 直接执行
（与 w_bottom_screen.py / momentum_strategy.py 同一分发模式）。

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
  3) 盘中/近收盘实时叠加（--realtime，可与刷库 / 快照叠加）：
     python fetch_quotes.py --watchlist output/watchlist/watchlist.yaml --realtime
     —— tushare 日线收盘后才发布（约 15:00–17 点陆续），盘中刷库最多补到昨日；
        加 --realtime 在正常取数之后追加一步：用公开实时报价（腾讯优先、新浪兜底，
        纯标准库直连、免 token）给库内尚无今日正式K线的标的补一根「今日临时K线」，
        w_bottom / momentum / etf_rotation 读库即用上近收盘价。规则（fail-closed）：
        休市 / 停牌零成交 / 未开盘不造K线；港股暂无实时源，如实列出跳过；临时K线带
        rt=1 标记，收盘后 tushare 正式日线发布，下次刷库自动覆盖升级为正式K线。

本地 CSV 行情库（与 w_bottom_screen.py / momentum_strategy.py 共享 output/quotes-store/）：
- 每只标的一份 CSV：<store>/<ts_code>.csv，列 trade_date,open,high,low,close,vol,pct_chg,rt
  （rt 为盘中临时K线标记：1=实时临时、空=tushare 正式日线；策略脚本按列名读取，
  自动忽略 rt 列）。
- pct_chg 为 tushare 日涨跌幅（%）：除权日按除权口径（基于除权昨收），是动量策略
  前复权计算的原料；旧版六列库（无 pct_chg）在刷库时自动按缺口区间回补。
  临时K线的 pct_chg 按实时源昨收推算（除权日实时源昨收即除权口径昨收，口径一致）。
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

#: CSV 列固定：与 w_bottom_screen.py / momentum_strategy.py 共用同一库结构。
#: pct_chg（日涨跌幅 %，除权日为除权口径）供动量策略复利连乘构造前复权序列；
#: rt 为盘中临时K线标记（--realtime 写入：1=实时临时、空=tushare 正式日线），
#: 策略脚本按列名读取各自所需字段、自动忽略 rt 列。
CSV_FIELDS = ["trade_date", "open", "high", "low", "close", "vol", "pct_chg", "rt"]

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


def store_pct_gap(store_dir: Path, ts_code: str) -> bool:
    """库内该标的的 pct_chg 列是否有缺口（需重取回补）。

    判定口径：CSV 内存在**任一行**缺 pct_chg 即视为有缺口——旧版六列库整列缺失、
    或历史某段增量行未带涨跌幅，都会被检出。整列缺失时用表头识别（表头里没有
    pct_chg 列 → 全部行都缺）；有列但个别行为空同样算缺口（脏数据不凑合）。
    库内无数据的标的返回 False（由根数判定走全量，无需在此处理）。
    """
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return False
    with fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "pct_chg" not in (reader.fieldnames or []):
            return True   # 旧版六列表头：整列缺失
        for r in reader:
            if not (r.get("pct_chg") or "").strip():
                return True   # 有列但个别行缺值（脏行不凑合）
    return False


def store_first_date(store_dir: Path, ts_code: str) -> str | None:
    """库内该标的的首个交易日（YYYYMMDD）；无数据返回 None。"""
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return None
    rows = _read_store_rows(fp)
    if not rows:
        return None
    return min(r["trade_date"] for r in rows)


def store_last_bar_is_rt(store_dir: Path, ts_code: str) -> bool:
    """库内最后一根（按日期最大）是否为盘中临时K线（rt=1）。"""
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return False
    rows = _read_store_rows(fp)
    if not rows:
        return False
    return max(rows, key=lambda r: r["trade_date"]).get("rt") == "1"


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


#: 日线取数字段（多取 pre_close 供快照推算涨跌幅；pct_chg 写库保留供前复权计算）
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
# 实时行情（--realtime：盘中/近收盘用公开实时报价补今日临时K线，免 token）
# ═══════════════════════════════════════════════════════════════════════════

#: 公开实时报价源（纯标准库直连，GBK 文本，批量一次请求；与 tushare 取数相互独立）：
#: - 腾讯：https://qt.gtimg.cn/q=sh600519,sz000333,...（成交量单位：手；成交额：万元）
#: - 新浪：https://hq.sinajs.cn/list=sh600519,...（需 Referer 头；成交量单位：股；成交额：元）
_RT_TENCENT_URL = "https://qt.gtimg.cn/q="
_RT_SINA_URL = "https://hq.sinajs.cn/list="
_RT_BATCH = 40            # 单次请求的标的数上限（控制 URL 长度）
_RT_TIMEOUT = 10

_RT_TENCENT_ROW = re.compile(r'v_(\w+)="([^"]*)"')
_RT_SINA_ROW = re.compile(r'hq_str_(\w+)="([^"]*)"')


def _rt_symbol(ts_code: str) -> str | None:
    """ts_code → 实时源符号（600519.SH → sh600519、833171.BJ → bj833171）。

    港股（.HK）与未知后缀无对应实时源，返回 None（调用方如实跳过，不猜数据）。
    """
    raw = (ts_code or "").upper()
    for suffix, prefix in ((".SH", "sh"), (".SZ", "sz"), (".BJ", "bj")):
        if raw.endswith(suffix):
            return prefix + raw[: -len(suffix)]
    return None


def _f(seq: list[str], i: int) -> float:
    """按位取数值字段，越界/非数值返回 0.0。"""
    try:
        return float(seq[i])
    except (IndexError, ValueError):
        return 0.0


def _norm_tencent(symbol: str, f: list[str]) -> dict | None:
    """腾讯报价字段归一：f[3] 现价 / f[4] 昨收 / f[5] 今开 / f[30] 时间戳
    （YYYYMMDDHHMMSS）/ f[33] 最高 / f[34] 最低 / f[36] 成交量（手）/ f[37] 成交额（万元）
    / f[38] 换手率（%）。"""
    ts = f[30] if len(f) > 30 else ""
    if len(ts) < 14:
        return None
    return {"symbol": symbol, "name": f[1] if len(f) > 1 else "", "source": "腾讯",
            "price": _f(f, 3), "prev_close": _f(f, 4), "open": _f(f, 5),
            "high": _f(f, 33), "low": _f(f, 34),
            "vol": _f(f, 36), "amount": _f(f, 37), "turnover": _f(f, 38),
            "trade_date": ts[:8], "trade_time": f"{ts[8:10]}:{ts[10:12]}:{ts[12:14]}"}


def _norm_sina(symbol: str, f: list[str]) -> dict | None:
    """新浪报价字段归一：f[1] 今开 / f[2] 昨收 / f[3] 现价 / f[4] 最高 / f[5] 最低 /
    f[8] 成交量（股→折手）/ f[9] 成交额（元→折万元）/ f[30] 日期 / f[31] 时间。"""
    if len(f) < 32:
        return None
    d = f[30].replace("-", "")
    if len(d) != 8 or not d.isdigit():
        return None
    return {"symbol": symbol, "name": f[0], "source": "新浪",
            "price": _f(f, 3), "prev_close": _f(f, 2), "open": _f(f, 1),
            "high": _f(f, 4), "low": _f(f, 5),
            "vol": _f(f, 8) / 100.0, "amount": _f(f, 9) / 10000.0, "turnover": 0.0,
            "trade_date": d, "trade_time": f[31]}


def _http_get_text(url: str, headers: dict | None = None) -> str:
    """GET 一个文本接口（GBK 解码；沿用关闭系统代理的直连 opener）。"""
    req = urllib.request.Request(url, headers=headers or {})
    with _OPENER.open(req, timeout=_RT_TIMEOUT) as resp:
        return resp.read().decode("gbk", errors="replace")


def fetch_realtime(ts_codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """批量拉实时报价：腾讯优先、新浪兜底（腾讯没拿到的标的再走新浪）。

    返回 ({ts_code: 归一行情}, 未取得报价的 ts_code 清单)；两类源均单位归一到
    成交量（手）/ 成交额（万元）。逐源整体失败（网络不可达）自动换源，单标的
    在两个源都拿不到 → 进未取得清单，由调用方如实报告。
    """
    quotes: dict[str, dict] = {}
    missing = [c for c in ts_codes if _rt_symbol(c)]
    # (URL 前缀, 请求头, 行正则, 字段分隔符, 归一函数)：腾讯优先、新浪兜底
    for url, headers, row_re, sep, norm in (
        (_RT_TENCENT_URL, None, _RT_TENCENT_ROW, "~", _norm_tencent),
        (_RT_SINA_URL, {"Referer": "https://finance.sina.com.cn"}, _RT_SINA_ROW, ",", _norm_sina),
    ):
        if not missing:
            break
        for i in range(0, len(missing), _RT_BATCH):
            batch = missing[i:i + _RT_BATCH]
            try:
                text = _http_get_text(url + ",".join(_rt_symbol(c) for c in batch), headers)
            except (urllib.error.URLError, TimeoutError, OSError):
                continue   # 该源本次请求失败：留给下一个源兜底
            sym_by_code = {c: _rt_symbol(c) for c in batch}
            got = {m.group(1): m.group(2) for m in row_re.finditer(text)}
            for c in batch:
                sym = sym_by_code[c]
                if sym in got:
                    q = norm(sym, got[sym].split(sep))
                    if q:
                        quotes[c] = q
        missing = [c for c in missing if c not in quotes]
    return quotes, [c for c in ts_codes if c not in quotes and _rt_symbol(c)]


def _rt_bar(q: dict, today: str) -> dict | None:
    """实时报价 → 今日临时K线行（CSV 口径 + rt=1 标记）。

    fail-closed：报价日期非今天（休市 / 未开盘 / 隔夜残留）、停牌零成交、
    现价或昨收异常 → 返回 None，绝不造K线。pct_chg 按实时源昨收推算——
    除权日实时源的昨收即除权口径昨收，与 tushare 日涨跌幅口径一致。
    """
    if q["trade_date"] != today or q["price"] <= 0 or q["prev_close"] <= 0 or q["vol"] <= 0:
        return None
    price = q["price"]
    pct = (price / q["prev_close"] - 1.0) * 100.0
    return {"trade_date": today,
            "open": q["open"] if q["open"] > 0 else price,
            "high": max(q["high"], price) if q["high"] > 0 else price,
            "low": min(q["low"], price) if q["low"] > 0 else price,
            "close": price,
            "vol": round(q["vol"], 2),
            "pct_chg": f"{pct:.4f}",
            "rt": "1"}


def realtime_patch(store_dir: Path, codes: list[str], skip: set[str] | None = None) -> int:
    """--realtime 收尾步骤：给库内尚无今日正式K线的标的补/刷新今日临时K线。

    规则（fail-closed，不造数据）：
    - 只处理 A 股（沪深北）代码；港股等无实时源的如实列出跳过；
    - 只补「库内已有历史」的标的（tushare 侧失败/未入库的空库不凭一根临时K线建库）；
    - 库内最后交易日已是今天且为正式K线 → 无需临时；已是今天的临时K线 → 用更新的
      实时报价覆盖刷新（同日幂等）；
    - 实时报价日期非今天（休市/未开盘）或停牌零成交 → 跳过并如实说明。
    返回写入临时K线的标的数。
    """
    today = date.today().strftime("%Y%m%d")
    skip = skip or set()
    unsupported = [c for c in codes if _rt_symbol(c) is None]
    if unsupported:
        print(f"[realtime] 无实时源（港股暂不支持盘中临时K线），跳过：{'、'.join(unsupported)}")
    cand: list[str] = []
    for c in codes:
        if _rt_symbol(c) is None or c in skip:
            continue
        n, last = store_status(store_dir, c)
        if not n or not last:
            continue
        if last == today and not store_last_bar_is_rt(store_dir, c):
            continue   # 已有今日正式K线
        if last > today:
            continue   # 库内日期异常超前，不动
        cand.append(c)
    if not cand:
        print("[realtime] 无需补今日临时K线（库内均已是今日正式K线，或休市/无历史数据）。")
        return 0

    quotes, _ = fetch_realtime(cand)
    patched: list[str] = []
    skipped: list[str] = []
    raw: dict[str, list] = {}
    for c in cand:
        bar = _rt_bar(quotes[c], today) if c in quotes else None
        if bar:
            raw[c] = [bar]
            patched.append(c)
        else:
            skipped.append(c)
    try:
        if raw:
            upsert_store(store_dir, raw)
    except OSError as e:
        print(f"[error] 临时K线写回行情库失败：{e}")
        return 0
    if patched:
        stamp = max(quotes[c]["trade_time"] for c in patched)
        print(f"[realtime] 今日临时K线入库：{len(patched)} 只（实时时点 {stamp}；"
              f"收盘后 tushare 发布正式日线，下次刷库自动覆盖升级）。")
    if skipped:
        print(f"[realtime] 跳过 {len(skipped)} 只（休市/未开盘/停牌零成交/实时源未返回，"
              f"不造K线）：{'、'.join(skipped)}")
    return len(patched)


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
    # 另：库内 pct_chg 列存在缺口（旧版六列库 / 增量时段未回补）的标的重取
    # 「库内首日 → 今天」整段（覆盖全部历史行，幂等合并后缺口闭合），
    # 避免动量策略的前复权计算出现断链。
    # 另：库内末根是盘中临时K线（rt=1，--realtime 写入）→ 按「今天 → 今天」重取，
    # tushare 收盘后发布正式日线时自动覆盖升级；未发布则 API 空、库不动。
    fetch_ranges: dict[str, tuple[str, str]] = {}
    full_codes: list[str] = []
    fresh_codes: list[str] = []
    gap_codes: list[str] = []
    rt_upgrade: list[str] = []
    for code in codes:
        n, last = store_status(store_dir, code)
        if n and last and store_pct_gap(store_dir, code):
            # pct_chg 有缺口：重取库内首日 → 今天（库内首日早于 full-days 窗口时
            # 以库内首日为准，保证全部历史行都被带涨跌幅的新行覆盖）
            first = store_first_date(store_dir, code)
            start = min(first, (date.today() - timedelta(days=args.full_days)).strftime("%Y%m%d"))
            fetch_ranges[code] = (start, today)
            gap_codes.append(code)
            continue
        if n >= args.min_bars and last:
            start = _next_date_str(last)
            if start > today:
                if store_last_bar_is_rt(store_dir, code):
                    fetch_ranges[code] = (today, today)
                    rt_upgrade.append(code)
                else:
                    fresh_codes.append(code)
            else:
                fetch_ranges[code] = (start, today)
        else:
            full_codes.append(code)

    print(f"本地行情库：{store_dir}（每只一份 CSV，幂等合并写回）")
    print(f"标的 {len(codes)} 只：增量 {len(fetch_ranges)} / 全量 {len(full_codes)}"
          f" / 免取 {len(fresh_codes)}；今天={today}，min-bars={args.min_bars}，"
          f"full-days={args.full_days}")
    if gap_codes:
        print(f"其中 {len(gap_codes)} 只存在 pct_chg 缺口（旧版六列库或历史行未含涨跌幅），"
              f"已排入重取回补：{'、'.join(gap_codes)}")
    if rt_upgrade:
        print(f"其中 {len(rt_upgrade)} 只库内末根为盘中临时K线（rt=1），重取今日正式K线"
              f"尝试覆盖升级（未发布则保持临时K线不动）。")
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

    # --realtime：tushare 收盘后才发布日线，盘中/近收盘用公开实时报价补今日临时K线，
    # 策略脚本读库即用上近收盘价（tushare 侧失败的标的不参与，避免在残缺历史上叠临时K线）
    if args.realtime:
        realtime_patch(store_dir, codes, skip={c for c, _ in failures})

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


def _scale(v, ratio: float):
    """估值基数按价格比外推（PE/PB/市值等比、股息率传倒数比）；None/异常透传 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v) * ratio
    except (TypeError, ValueError):
        return None


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
    # --realtime：先批量拉一次实时报价（免 token），仅保留「今日 + 有成交」的有效报价
    # 供逐只叠加；港股无实时源、报价非今日（休市/未开盘/停牌）如实说明后按日线展示。
    rt_quotes: dict[str, dict] = {}
    if getattr(args, "realtime", False):
        rt_codes = [c for c in codes if _rt_symbol(c)]
        hk_codes = [c for c in codes if not _rt_symbol(c)]
        if hk_codes:
            print(f"[realtime] 无实时源（港股暂不支持盘中实时），按 tushare 日线展示："
                  f"{'、'.join(hk_codes)}")
        if rt_codes:
            got_all, rt_missing = fetch_realtime(rt_codes)
            rt_quotes = {c: q for c, q in got_all.items()
                         if q["trade_date"] == today and q["price"] > 0 and q["vol"] > 0}
            stale = [c for c in got_all if c not in rt_quotes]
            if rt_missing:
                print(f"[realtime] 实时源未返回（按 tushare 日线展示）：{'、'.join(rt_missing)}")
            if stale:
                print(f"[realtime] 实时报价非今日（休市/未开盘/停牌零成交，按 tushare 日线"
                      f"展示）：{'、'.join(stale)}")
    for code in codes:
        try:
            live = rt_quotes.get(code)
            n, last = store_status(store_dir, code)
            bar: dict | None = None
            official_rows: list[dict] = []
            if live is None:
                # 库内已最新（含今天）→ 直接用库内最后一根，零 API 拿价格
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
            else:
                # 有今日实时报价：拉近 15 日日线取正式K线与上一交易日估值基数（顺带
                # 把库内临时K线升级为正式K线）；tushare 已发布今日正式K线则正式优先，
                # 未发布则叠加实时价成临时K线（rt=1）写回行情库。
                throttle.wait()
                official_rows = ts_post_retry(
                    daily_api_name(code), token,
                    {"ts_code": code,
                     "start_date": (date.today() - timedelta(days=15)).strftime("%Y%m%d"),
                     "end_date": today},
                    _DAILY_FIELDS,
                ) or []
                official = official_rows[-1] if official_rows else None
                extra: list[dict] = []
                if official is not None and str(official.get("trade_date")) == today:
                    bar = official
                else:
                    rt_b = _rt_bar(live, today)
                    if rt_b is not None:
                        bar = rt_b
                        extra = [rt_b]
                    else:
                        bar = official      # 实时报价异常兜底：退回正式末根
                merged[code] = list(official_rows) + extra
            if bar is None:
                failures.append(code)
                print(f"===== {code} =====\n  无行情数据（代码可能有误或已退市）")
                continue

            # 估值字段（仅 A 股：daily_basic 不覆盖港股）。盘中临时K线当日 daily_basic
            # 未生成 → 基数取上一正式交易日，展示时按实时价/昨收线性外推。
            basic_date = str(bar["trade_date"])
            if bar.get("rt") == "1" and official_rows:
                basic_date = str(official_rows[-1]["trade_date"])
            basic: dict | None = None
            basic_err = ""
            if daily_api_name(code) != "hk_daily":
                try:
                    throttle.wait()
                    got = ts_post_retry(
                        "daily_basic", token,
                        {"ts_code": code, "trade_date": basic_date},
                        _BASIC_FIELDS,
                    )
                    basic = got[0] if got else None
                except TushareApiError as e:
                    basic_err = str(e)

            is_rt = bar.get("rt") == "1"
            name = name_map.get(code, "") or (live.get("name", "") if is_rt else "")
            print(f"===== {code}" + (f" {name}" if name else "") + " =====")
            td = _to_date(bar.get("trade_date"))
            if is_rt and live is not None:
                print(f"  日期：{td.isoformat() if td else bar.get('trade_date')}"
                      f"（盘中实时 {live['trade_time']}，来源：{live['source']}；"
                      f"临时K线已写行情库，收盘后刷库自动覆盖）")
                print(f"  最新价：{_fmt(bar.get('close'))}  涨跌幅：{_fmt_pct(bar.get('pct_chg'))}"
                      f"  成交量：{_fmt(bar.get('vol'))} 手")
            else:
                date_label = ("（盘中临时K线 rt=1，收盘后刷库自动覆盖为正式K线）" if is_rt
                              else "（数据时点：tushare 日线）")
                print(f"  日期：{td.isoformat() if td else bar.get('trade_date')}{date_label}")
                pct = bar.get("pct_chg")
                if pct in (None, "") and bar.get("pre_close") not in (None, ""):
                    try:
                        pct = (float(bar["close"]) / float(bar["pre_close"]) - 1) * 100
                    except (TypeError, ValueError, ZeroDivisionError):
                        pct = None
                print(f"  收盘：{_fmt(bar.get('close'))}  涨跌幅：{_fmt_pct(pct)}"
                      f"  成交量：{_fmt(bar.get('vol'))} 手")
            if basic:
                if is_rt and live is not None and live["prev_close"] > 0:
                    r = live["price"] / live["prev_close"]
                    print(f"  换手率：{_fmt(live.get('turnover') or None, '%')}"
                          f"  PE(TTM)：{_fmt(_scale(basic.get('pe_ttm'), r))}*"
                          f"  PE(静)：{_fmt(_scale(basic.get('pe'), r))}*"
                          f"  PB：{_fmt(_scale(basic.get('pb'), r))}*")
                    print(f"  总市值：{_fmt(_scale(basic.get('total_mv'), r), '万')}*"
                          f"  流通市值：{_fmt(_scale(basic.get('circ_mv'), r), '万')}*"
                          f"  股息率(TTM)：{_fmt(_scale(basic.get('dv_ttm'), 1.0 / r), '%')}*")
                    print("  （* 实时估算：上一交易日 daily_basic 按实时价/昨收外推）")
                else:
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
    ap.add_argument("--realtime", action="store_true",
                    help="盘中/近收盘实时叠加（免 token）：用公开实时报价（腾讯优先、"
                         "新浪兜底）给库内补今日临时K线（rt=1），策略读库即用近收盘价；"
                         "收盘后 tushare 发布正式日线，下次刷库自动覆盖。可叠加于刷库/快照")
    ap.add_argument("--env", default=".env", help=".env 文件路径（默认工作区 ./.env）")
    ap.add_argument("--interval", type=float, default=_INTERVAL_DEFAULT,
                    help=f"相邻 API 请求间隔秒数（默认 {_INTERVAL_DEFAULT}，防限流）")
    args = ap.parse_args(argv)

    if args.snapshot:
        return cmd_snapshot(args)
    return cmd_sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
