"""ETF 动量轮动策略：自包含单文件脚本（纯标准库，可下载即跑，不依赖 quantify 包）。

这是「ETF 池动量轮动」的**唯一可执行真源**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/etf_rotation_strategy.py` 提供给目标工作区里的
agent 下载执行（与 momentum_strategy.py、w_bottom_screen.py 同一分发模式）。

设计约束（与 momentum_strategy.py 一致）：
- 纯标准库（dataclass / json / argparse / datetime / pathlib / re / math / csv），零第三方依赖。
- 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。
- 脚本本身**不取行情**：取数统一走 fetch_quotes.py（直连 tushare REST API，增量刷库）；
  本脚本只从本地 CSV 行情库读数计算。

────────────────────────────────────────────────────────────────────────────────
策略口径（24 日加权斜率×R² + 前 K 等权 + 排名缓冲；详细见 .agents/skills/etf-rotation/SKILL.md）
────────────────────────────────────────────────────────────────────────────────

溯源与实测（聚宽 2026-10-11，2016-01~2026-06，佣金万1+滑点万5往返）：
  信号 = 24 日加权(linspace 1→2)对数线性回归斜率年化 × R²（聚宽主帖"ETF动量策略"口径，
  经用户审定改良：去分钟级止损、去分钟成交量过滤、满仓单吊改前 K 等权）：
    K=4（缓冲 6）  764.25% / 夏普 1.123 / 回撤 21.49%（重复跑逐位一致，可复现）
    K=10（缓冲 15）284.25% / 夏普 0.912 / 回撤 15.35%
  滑点敏感性实测：往返万 5 滑点全程仅 -1.3pp/年（零滑点 806.17%/夏普 1.156）。
  退出纯靠排名滚动，无价格止损。

选股顺序（严格四步）：
  1) 过滤：R² ≥ 0.4、近 3 日单日跌幅均优于 -3.5%、得分 ∈ (0, 5) 才算「合格」；
  2) 排名：合格池按 score 降序（同分按 ts_code 字典序，结果确定）；
  3) 老仓优先（缓冲）：现有持仓只要在合格榜仍居前 buffer=ceil(1.5K) 名，直接保留；
  4) 补满 K 仓：不足 K 只从合格榜依次递补、去重，凑满 K 只（等权，每只 1/K）；
     空出的槽位由防御资产（511880 货币ETF，不参与排名）按份兜底。

fail-closed（数据安全）：
  - 库内无数据 / 最后交易日距今超过 10 自然日 → 该标的为缺口，fail-closed 不出信号；
  - pct_chg 缺列或断链（无法前复权）→ fail-closed 提示先刷库；
  - 池内历史 ≥ history_min 的覆盖率 < coverage_min → 不出信号（no_signal）；
  - 无合格标的：有老仓则继续持有老仓，无老仓则全部退防御。

一键回滚开关（环境变量 STRATEGY_ETF_ROTATION_DECISION_MODE=decision_runs）：
  只产决策快照、不回写持仓状态（研究核对或执行链故障时用，输出行带 [降级] 提示）。

复权口径（与 momentum_strategy.py 一致）：
  行情库每行带 pct_chg（tushare 日涨跌幅，除权日为除权口径），策略端从最新一根往前
  复利连乘得到前复权序列（adj[i] = adj[i+1] / (1 + pct_chg[i+1]/100)，锚定最新真实收盘价）；
  动量与三日跌幅一律基于前复权价计算（含分红的国债/红利类 ETF 不被除息跳空扭曲）。

本地 CSV 行情库（与 momentum_strategy.py / w_bottom_screen.py 共用 output/quotes-store/）：
  每只标的一份 output/quotes-store/<ts_code>.csv，列
  trade_date,open,high,low,close,vol,pct_chg（pct_chg 为前复权计算原料）。

两层式工作流（取数与计算分离）：
 1) python fetch_quotes.py --watchlist output/etf-rotation/pool.yaml --min-bars 45 --full-days 120
       —— 刷库：对照库内最后交易日增量补到最新（新票/不足 45 根的全量取近 120 自然日），
       幂等合并写回 output/quotes-store/。
 2) python etf_rotation_strategy.py --pool output/etf-rotation/pool.yaml \
       --state output/etf-rotation/state.json
       —— 直接读库跑过滤/排名/缓冲/兜底，产出目标持仓（等权 + 防御槽），并回写
       state.json；报告落在 output/etf-rotation/plan_<时间戳>.md。
       标准输出只打印摘要（目标持仓/调仓变动/防御槽/提醒）——「逐只动量与过滤」
       全池明细只写报告文件、不进会话（51 只时逐只长表是纯 token 损耗）。
     （--plan 仅作诊断：打印每只的库内根数/最后交易日/待补区间，不取数。）

QDII / 小众品种溢价提醒：
  池文件 note 字段含 "QDII" 的标的（纳指/标普/日经/德国/法国/巴西/沙特/恒生系/原油等），
  实盘折溢价与流动性冲击远大于回测模型，入选持仓时报告会显式提示人工核溢价（>2% 暂缓）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

#: 数据门禁：库内最后交易日距今超过该自然日数视为数据过期（与 momentum_strategy.py 一致）
MAX_STALE_DAYS = 10


# ─────────────────────────────────────────────────────────────────────────
# 领域结构（与 momentum_strategy.py 同款，纯标准库 dataclass）
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Bar:
    date: date
    close: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    pct_chg: float | None = None   # 日涨跌幅（%，除权日为除权口径）；None=缺失（fail-closed）
    adj_close: float = 0.0  # 前复权收盘价（由 pct_chg 复利连乘回推，锚定最新真实收盘价）


@dataclass
class Series:
    symbol: str
    bars: list[Bar] = field(default_factory=list)
    adj_ok: bool = False  # 前复权链路是否完好（pct_chg 全程无缺失、无脏值）

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def adj_closes(self) -> list[float]:
        return [b.adj_close for b in self.bars]


@dataclass
class StrategyParams:
    """策略全部可调参数（默认值对应 v7 实测口径）。"""

    win_slope: int = 24          # 动量回归窗口（主帖 24 日，实测有效，不动）
    history_min: int = 45        # 每只最低历史交易日（24+20 余量 + 3日过滤）
    r2_min: float = 0.4          # R² 下限（主帖参数）
    loss_limit: float = 0.965    # 近3日单日跌幅下限（主帖 3.5%）
    score_min: float = 0.0       # 得分下限（>0 才有绝对动量）
    score_max: float = 5.0       # 得分上限（主帖 ∈(0,5)）
    k: int = 4                   # 持仓数：K=4 实测收益/夏普最优；K=10 为低波端点
    buffer_ratio: float = 1.5    # 老仓缓冲 = ceil(K × 1.5)（Clenow 买前20%/卖跌出前30% 口径）
    coverage_min: float = 0.90   # 池内历史达标覆盖率下限
    defensive_code: str = "511880.XSHG"  # 防御资产：货币ETF（不参与排名）
    annual_days: int = 250       # 年化交易日


@dataclass
class PoolItem:
    ts_code: str
    name: str = ""
    note: str = ""


@dataclass
class Decision:
    """单只 ETF 的动量决策中间结构。"""

    ts_code: str
    name: str = ""
    slope_ann: float = 0.0       # 年化回归收益 e^(slope*250)-1
    r2: float = 0.0              # 加权拟合优度
    score: float = 0.0           # 综合得分 slope_ann × R²
    close: float = 0.0           # 最新真实收盘价
    volume: float = 0.0          # 信号日成交量
    min_day3: float = 0.0        # 近3日单日涨幅最小值（跌幅过滤凭据）
    qdii: bool = False           # 跨境/QDII 标记（溢价人工核）
    has_history: bool = False    # 历史是否达标（≥ history_min）
    qualified: bool = False      # 三道过滤是否合格
    filter_reasons: list[str] = field(default_factory=list)
    rank: int = 0                # 合格榜排名（0 表示未上榜）

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "slope_ann": round(self.slope_ann, 4),
            "r2": round(self.r2, 4),
            "score": round(self.score, 4),
            "close": round(self.close, 3),
            "min_day3": round(self.min_day3, 4),
            "qdii": self.qdii,
            "has_history": self.has_history,
            "qualified": self.qualified,
            "filter_reasons": self.filter_reasons,
            "rank": self.rank,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 池清单读取（极简 YAML 子集解析，与 momentum_strategy.py 同款）
# ═══════════════════════════════════════════════════════════════════════════

_ITEM_KEY = re.compile(r"^\s*-\s+ts_code:\s*(.+?)\s*$")
_KEY = re.compile(r"^\s+(\w+):\s*(.*)$")


def load_pool(path: Path) -> list[PoolItem]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"ETF 池清单不存在：{p}。请先跑 etf-rotation 技能的 pool-build 动作：\n"
            f"  从目录资产 .agents/skills/etf-rotation/references/etf-map.yaml 按在跟景气板块"
            f"映射生成当期池 {p}\n"
            f"（面板/分发环境：目录经宿主静态端点 /plugins/advisor-agent/assets/etf-rotation/etf-map.yaml"
            f" 下载；兜底：无在跟板块时先只收目录常驻层 layer: base 全收生成最小池）"
        )
    lines = p.read_text(encoding="utf-8").splitlines()
    items: list[PoolItem] = []
    cur: dict[str, str] | None = None
    for ln in lines:
        m = _ITEM_KEY.match(ln)
        if m:
            if cur is not None:
                items.append(_mk(cur))
            cur = {"ts_code": m.group(1).strip().strip('"').strip("'")}
            continue
        km = _KEY.match(ln)
        if km and cur is not None:
            cur[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    if cur is not None:
        items.append(_mk(cur))
    return [it for it in items if it.ts_code]


def _mk(cur: dict[str, str]) -> PoolItem:
    return PoolItem(
        ts_code=cur.get("ts_code", "").strip(),
        name=cur.get("name", "").strip(),
        note=cur.get("note", "").strip(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# tushare 日线行 → Series（清洗 + 前复权；与 momentum_strategy.py 同口径）
# ═══════════════════════════════════════════════════════════════════════════

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


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def rows_to_series(symbol: str, rows: list[dict]) -> Series:
    bars: list[Bar] = []
    for row in rows:
        c = _to_float(row.get("close"))
        d = _to_date(row.get("trade_date"))
        if c is None or d is None:
            continue  # 缺收盘价或日期为脏行
        bars.append(Bar(
            date=d,
            close=c,
            open=_to_float(row.get("open")) or 0.0,
            high=_to_float(row.get("high")) or 0.0,
            low=_to_float(row.get("low")) or 0.0,
            volume=_to_float(row.get("vol")) or 0.0,
            pct_chg=_to_float(row.get("pct_chg")),  # 缺失保持 None，不冒充平盘 0.0
        ))
    bars.sort(key=lambda b: b.date)
    series = Series(symbol=symbol, bars=bars)
    series.adj_ok = apply_forward_adjust(series)
    return series


def apply_forward_adjust(series: Series) -> bool:
    """就地给 series.bars 计算前复权收盘价 adj_close（按日期升序）。

    算法与 momentum_strategy.py 一致：从最后一根往前复利连乘
    adj[i] = adj[i+1] / (1 + pct_chg[i+1] / 100)；任何一根 pct_chg 缺失或为脏值返回 False。
    分红类 ETF（30年国债/红利等）除息跳空被还原，动量不被扭曲。
    """
    bars = series.bars
    if not bars:
        return False
    if any(b.pct_chg is None for b in bars):
        return False
    bars[-1].adj_close = bars[-1].close
    for i in range(len(bars) - 2, -1, -1):
        pc = bars[i + 1].pct_chg
        if pc is None:
            return False
        r = 1.0 + pc / 100.0
        if r <= 0:
            return False
        bars[i].adj_close = bars[i + 1].adj_close / r
    return True


# ═══════════════════════════════════════════════════════════════════════════
# 动量核心（24 日加权对数回归斜率×R²；纯标准库实现，可独立单测）
# ═══════════════════════════════════════════════════════════════════════════

def weighted_log_slope_r2(closes: list[float], window: int, annual_days: int = 250) -> tuple[float, float] | None:
    """对最近 window+1 根前复权收盘价取对数，做权重 linspace(1,2) 的一元线性回归。

    返回 (年化回归收益, 加权 R²)；数据不足或退化返回 None。
    年化回归收益 = e^(slope * annual_days) - 1；score = 年化收益 × R² 由调用方合成。
    """
    seg = closes[-(window + 1):]
    if len(seg) < window + 1 or any(c <= 0 for c in seg):
        return None
    y = [math.log(c) for c in seg]
    n = len(y)
    x = list(range(n))
    w = [1.0 + i * (1.0 / (n - 1)) for i in range(n)]  # linspace(1, 2, n)，近端权重更大

    sw = sum(w)
    xbar = sum(wi * xi for wi, xi in zip(w, x)) / sw
    ybar = sum(wi * yi for wi, yi in zip(w, y)) / sw
    sxx = sum(wi * (xi - xbar) ** 2 for wi, xi in zip(w, x))
    if sxx <= 0:
        return None
    sxy = sum(wi * (xi - xbar) * (yi - ybar) for wi, xi, yi in zip(w, x, y))
    slope = sxy / sxx
    intercept = ybar - slope * xbar

    ss_res = sum(wi * (yi - (slope * xi + intercept)) ** 2 for wi, xi, yi in zip(w, x, y))
    ss_tot = sum(wi * (yi - ybar) ** 2 for wi, yi in zip(w, y))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    slope_ann = math.exp(slope * annual_days) - 1.0
    return slope_ann, r2


def min_day3(closes: list[float]) -> float | None:
    """近 3 个交易日的单日涨幅最小值（最近4根的前复权价比）；不足返回 None。"""
    if len(closes) < 4:
        return None
    r1 = closes[-1] / closes[-2]
    r2 = closes[-2] / closes[-3]
    r3 = closes[-3] / closes[-4]
    return min(r1, r2, r3)


def compute_metrics(series: Series, p: StrategyParams) -> Decision:
    """单只 ETF：加权回归动量 + R² + 近3日过滤凭据。"""
    d = Decision(ts_code=series.symbol)
    bars = series.bars
    d.has_history = len(bars) >= p.history_min
    if not d.has_history:
        d.filter_reasons.append(f"历史仅 {len(bars)} 根 < {p.history_min}（上市未满门槛，暂不参与）")
        return d

    adj = series.adj_closes
    d.close = bars[-1].close
    d.volume = bars[-1].volume

    res = weighted_log_slope_r2(adj, p.win_slope, p.annual_days)
    if res is None:
        d.filter_reasons.append("数据不足/退化，无法回归")
        return d
    d.slope_ann, d.r2 = res
    d.score = d.slope_ann * d.r2

    m3 = min_day3(adj)
    d.min_day3 = m3 if m3 is not None else 0.0

    # 三道过滤（主帖口径，均可回测背书）
    if d.r2 < p.r2_min:
        d.filter_reasons.append(f"R² {d.r2:.2f} < {p.r2_min}（趋势平滑度不足）")
    if m3 is None:
        d.filter_reasons.append("近3日数据不足")
    elif m3 < p.loss_limit:
        d.filter_reasons.append(f"近3日单日最大跌幅 {(m3 - 1) * 100:.1f}% 超 -3.5%")
    if not (p.score_min < d.score < p.score_max):
        d.filter_reasons.append(f"得分 {d.score:.3f} 不在 ({p.score_min}, {p.score_max})")

    d.qualified = not d.filter_reasons
    return d


def rank_decisions(decisions: list[Decision]) -> list[Decision]:
    """对合格决策按 score 降序排名（同分按 ts_code 字典序，保证确定）。"""
    eligible = [d for d in decisions if d.qualified]
    eligible.sort(key=lambda d: (-d.score, d.ts_code))
    for i, d in enumerate(eligible, start=1):
        d.rank = i
    return eligible


def build_target(eligible: list[Decision], current: set[str], p: StrategyParams) -> list[str]:
    """老仓靠前 buffer 保留 → 从合格榜递补到 K 只。"""
    buffer = math.ceil(p.k * p.buffer_ratio)
    rank_of = {d.ts_code: d.rank for d in eligible}
    order = [d.ts_code for d in eligible]

    kept = [e for e in current if rank_of.get(e, 10 ** 9) <= buffer]
    target = list(kept)
    for e in order:
        if len(target) >= p.k:
            break
        if e not in target:
            target.append(e)
    return target


# ═══════════════════════════════════════════════════════════════════════════
# 持仓状态（state.json）
# ═══════════════════════════════════════════════════════════════════════════

def load_state(path: Path) -> dict:
    """读取持仓状态；无文件返回空结构。"""
    if not path or not path.exists():
        return {"positions": [], "as_of": "", "cash_pct": 1.0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"positions": [], "as_of": "", "cash_pct": 1.0}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 本地 CSV 行情库（每标的一份；与 momentum_strategy.py 共用 output/quotes-store/）
# ═══════════════════════════════════════════════════════════════════════════

CSV_FIELDS = ["trade_date", "open", "high", "low", "close", "vol", "pct_chg"]


def store_csv_path(store_dir: Path, ts_code: str) -> Path:
    return store_dir / f"{ts_code}.csv"


def _read_store_rows(fp: Path) -> list[dict]:
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
    d = _to_date(trade_date)
    if d is None:
        return trade_date
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def read_store(store_dir: Path, ts_code: str) -> list[dict]:
    fp = store_csv_path(store_dir, ts_code)
    if not fp.exists():
        return []
    rows = _read_store_rows(fp)
    return sorted(rows, key=lambda r: r["trade_date"])


# ═══════════════════════════════════════════════════════════════════════════
# 主流程（--plan 仅诊断 / 默认直接读库计算）
# ═══════════════════════════════════════════════════════════════════════════

def _plan(pool_path: Path, store_dir: Path, history_min: int) -> int:
    try:
        items = load_pool(pool_path)
    except FileNotFoundError as e:
        print(f"[warning] {e}")
        return 1
    if not items:
        print(f"ETF 池清单为空，请先填充 {pool_path}。")
        return 1
    print(f"ETF 池（{len(items)} 只）：")
    for it in items:
        print(f"  - {it.ts_code}\t{it.name}\t{it.note}")
    print()

    today = date.today().strftime("%Y%m%d")
    print(f"本地行情库：{store_dir}（每只一份 CSV，由 fetch_quotes.py 刷库写回）")
    for it in items:
        n, last = store_status(store_dir, it.ts_code)
        if n >= history_min and last:
            start = _next_date_str(last)
            if start > today:
                print(f"  {it.ts_code}  免取（库内已到 {last}，共 {n} 根）")
            else:
                print(f"  {it.ts_code}  待增量 {start} → {today}（库内 {n} 根，最新 {last}）")
        elif last:
            print(f"  {it.ts_code}  待全量（库内仅 {n} 根 < {history_min}，最新 {last}）")
        else:
            print(f"  {it.ts_code}  待全量（库内无数据）")
    print()
    print("刷库命令（增量自动补到最新交易日）：")
    print(f"  python fetch_quotes.py --watchlist {pool_path}"
          f" --min-bars {history_min} --full-days 120")
    print("刷库完成后直接计算（直接读库，无需 --data）：")
    print(f"  python etf_rotation_strategy.py --pool {pool_path}"
          f" --state output/etf-rotation/state.json")
    return 0


def _run(pool_path: Path, state_path: Path | None, p: StrategyParams,
         out_dir: Path, write_state: bool = True, store_dir: Path = Path("output/quotes-store")) -> int:
    try:
        items = load_pool(pool_path)
    except FileNotFoundError as e:
        print(f"[error] {e}")
        return 1
    if not items:
        print(f"[error] ETF 池清单为空，请先填充 {pool_path}。")
        return 1
    pool_codes = [it.ts_code for it in items if it.ts_code != p.defensive_code]
    name_map = {it.ts_code: it.name for it in items}
    qdii_map = {it.ts_code: ("QDII" in (it.note or "")) for it in items}

    # 数据门禁：读库 + 新鲜度校验（缺口 fail-closed）
    today = date.today()
    raw: dict[str, list] = {}
    gaps: list[str] = []
    for code in pool_codes:
        rows = read_store(store_dir, code)
        if not rows:
            gaps.append(f"{code}（库内无数据）")
            continue
        last = _to_date(rows[-1]["trade_date"])
        if last is None or (today - last).days > MAX_STALE_DAYS:
            gaps.append(f"{code}（库内最新 {rows[-1]['trade_date']}，"
                        f"距今超过 {MAX_STALE_DAYS} 自然日）")
            continue
        raw[code] = rows
    if gaps:
        print("[error] 以下 ETF 行情库数据缺失或过期，本次 fail-closed 不出信号：")
        for g in gaps:
            print(f"  - {g}")
        print(f"请先刷库：python fetch_quotes.py --watchlist {pool_path}"
              f" --min-bars {p.history_min} --full-days 120")
        return 1

    # 前复权链路校验（断链 fail-closed）
    series_list: list[Series] = []
    adj_broken: list[str] = []
    for ts, rows in raw.items():
        s = rows_to_series(ts, rows)
        if not s.adj_ok:
            adj_broken.append(ts)
            continue
        series_list.append(s)
    if adj_broken:
        print("[error] 以下 ETF 行情库缺 pct_chg 列或存在断链，无法前复权计算，本次 fail-closed：")
        for c in adj_broken:
            print(f"  - {c}")
        print(f"请先刷库回补：python fetch_quotes.py --watchlist {pool_path}"
              f" --min-bars {p.history_min} --full-days 120")
        return 1

    # 覆盖率 fail-closed（上市未满 history_min 的标的不参与并计入覆盖率）
    total = len(pool_codes)
    covered = sum(1 for s in series_list if len(s.bars) >= p.history_min)
    coverage = covered / total if total else 0.0
    no_signal = coverage < p.coverage_min

    decisions = [compute_metrics(s, p) for s in series_list]
    for d in decisions:
        d.name = name_map.get(d.ts_code, "")
        d.qdii = qdii_map.get(d.ts_code, False)
    eligible = rank_decisions(decisions)
    buffer = math.ceil(p.k * p.buffer_ratio)

    # 老仓读取
    state = load_state(state_path) if state_path else {"positions": [], "cash_pct": 1.0}
    current = {h["ts_code"] for h in state.get("positions", [])}

    # ── 目标持仓决策（严格顺序：fail-closed > 正常选股 > 兜底） ─────────────
    if no_signal:
        target = sorted(current)  # 数据关：不卖不买，老仓原样
        signal = "no_signal"
    else:
        target = build_target(eligible, current, p)
        signal = "signal"
    weight = 1.0 / p.k if p.k else 0.0
    n_def = max(0, p.k - len(target)) if signal == "signal" else 0
    equity_pct = len(target) * weight

    # 信号日 = 数据中出现的最后一个交易日（T 日收盘算信号，实盘 T 日尾盘执行）
    last_dates = [s.bars[-1].date for s in series_list if s.bars]
    signal_date = max(last_dates).isoformat() if last_dates else ""

    # ── 组装报告：摘要（lines）随标准输出打印；「逐只动量与过滤」明细只写 md ──
    dm = {d.ts_code: d for d in decisions}
    lines: list[str] = []
    detail_lines: list[str] = []
    lines.append("# ETF 动量轮动 · 组合信号")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 信号日：{signal_date}（T 日收盘算信号，实盘 T 日尾盘执行）")
    lines.append(f"- ETF 池：{total} 只（防御槽：{p.defensive_code} 货币ETF，不参与排名）")
    lines.append(f"- 数据覆盖率：{coverage:.1%}（下限 {p.coverage_min:.0%}）")
    lines.append("- 复权口径：前复权（pct_chg 复利连乘；动量/三日跌幅均为前复权价计算，"
                 "现价为真实收盘价）")
    lines.append(f"- 信号：{signal}｜持仓数 K={p.k}（缓冲 buffer={buffer} 名）")
    lines.append(f"- 目标：权益 {len(target)} 只 × {weight:.2%} + 防御槽 {n_def} × {weight:.2%}"
                 f"（权益仓位 {equity_pct:.2%}，防御 {n_def * weight:.2%}）")
    lines.append("")

    if no_signal:
        lines.append("> ⚠️ 数据覆盖率低于下限，策略 fail-closed，本日不出新信号。")
        if target:
            lines.append("> 按数据关规则老仓原样保留；不新增、不清仓。")
        else:
            lines.append("> 无老仓可保留，等待数据修复后再开新仓。")

    # 目标持仓表
    if target:
        lines.append("## 本次目标持仓（等权）")
        lines.append("")
        lines.append("| 代码 | 名称 | 排名 | 得分 | 年化回归 | R² | 现价 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for code in target:
            d = dm.get(code)
            if d is None:
                continue
            tag = "老仓" if code in current else "新增"
            if d.qdii:
                tag += "/QDII核溢价"
            lines.append(
                f"| {code} | {d.name} | {d.rank} | {d.score:.3f} | {d.slope_ann:.1%} "
                f"| {d.r2:.2f} | {d.close:.3f} | {tag} |"
            )
        lines.append("")
        lines.append(f"> 退出纯靠排名滚动：跌出合格榜前 {buffer} 名缓冲 → 下次调仓轮出，无价格止损。")
        lines.append("")
    else:
        lines.append("")
        lines.append("> 全池无合格标的：权益槽全部空出。")
        lines.append("")

    # 防御槽
    if n_def > 0:
        lines.append(f"## 防御槽：{p.defensive_code}（货币ETF）× {n_def} 份（{n_def * weight:.2%}）")
        lines.append("")
        lines.append("> 合格标的不足 K 只时，空槽由货币ETF兜底（不参与排名、无闸门，是地板）。")
        lines.append("")

    # 调仓变动（老仓被剔除是核心决策信息，显式列出及原因）
    removed = sorted(current - set(target))
    added = sorted(set(target) - current)
    if removed or added:
        lines.append("## 本次调仓变动")
        lines.append("")
        if removed:
            lines.append(f"**剔除（轮出，zero_out）：** {len(removed)} 只")
            lines.append("")
            lines.append("| 代码 | 名称 | 排名 | 剔除说明 |")
            lines.append("|---|---|---|---|")
            for code in removed:
                d = dm.get(code)
                nm = d.name if d else "-"
                rk = d.rank if d else "-"
                if no_signal:
                    why = "数据覆盖率不足 fail-closed，按数据关规则处理"
                elif d is None:
                    why = "不在本期 ETF 池（已移出清单或库内无数据）"
                elif not d.qualified:
                    why = "过滤不合格：" + "；".join(d.filter_reasons)
                else:
                    why = f"合格榜第 {d.rank} 名，跌出前 {buffer} 名缓冲"
                lines.append(f"| {code} | {nm} | {rk} | {why} |")
            lines.append("")
        if added:
            names = "、".join(
                f"{c}（{dm[c].name}）" if c in dm and dm[c].name else c for c in added
            )
            lines.append(f"**新增（开仓）：** {len(added)} 只——{names}。")
            lines.append("")
    else:
        lines.append("> 与上期目标持仓一致，本次无调仓。")
        lines.append("")

    # QDII 人工核溢价提示（入选持仓中带标记的）
    held_qdii = [c for c in target if dm.get(c) and dm[c].qdii]
    if held_qdii:
        _names = "、".join(f"{c}（{dm[c].name}）" for c in held_qdii)
        lines.append(f"> ⚠️ QDII/小众品种溢价提示：{_names} 实盘折溢价与流动性冲击远大于回测模型，"
                     "实盘 >2% 溢价建议暂缓；回测照价成交是模型盲区。")
        lines.append("")

    # 逐只动量与过滤明细（只写 md 报告，不进会话）
    detail_lines.append("## 逐只动量与过滤（全池）")
    detail_lines.append("")
    detail_lines.append("| 代码 | 名称 | 得分 | 年化回归 | R² | 近3日最弱日 | 现价 | 合格 | 排名 | 过滤原因 |")
    detail_lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for d in sorted(decisions, key=lambda x: (-x.score, x.ts_code)):
        if not d.has_history:
            detail_lines.append(
                f"| {d.ts_code} | {d.name} | - | - | - | - | - | 否 | - | {'；'.join(d.filter_reasons)} |")
            continue
        detail_lines.append(
            f"| {d.ts_code} | {d.name} | {d.score:.3f} | {d.slope_ann:.1%} | {d.r2:.2f} "
            f"| {(d.min_day3 - 1) * 100:+.1f}% | {d.close:.3f} | {'是' if d.qualified else '否'} "
            f"| {d.rank or '-'} | {'；'.join(d.filter_reasons) or '-'} |"
        )

    out_path = out_dir / f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines + detail_lines), encoding="utf-8")

    # 回写 state.json
    new_positions = [{"ts_code": code, "weight": weight} for code in target]
    new_state = {
        "as_of": signal_date,
        "signal": signal,
        "k": p.k,
        "buffer": buffer,
        "target": target,
        "defensive": {"ts_code": p.defensive_code, "slots": n_def, "weight": n_def * weight},
        "equity_pct": round(equity_pct, 4),
        "cash_pct": round(max(0.0, 1.0 - equity_pct), 4),
        "positions": new_positions,
    }
    if state_path and write_state:
        save_state(state_path, new_state)

    # 标准输出只打摘要
    print("\n".join(lines))
    print(f"\n「逐只动量与过滤」明细（全池 {len(decisions)} 只）只写入报告文件、未打印到标准输出"
          f"（避免长表占满会话上下文）。")
    print(f"报告已保存：{out_path}")
    if not write_state:
        print("[降级] STRATEGY_ETF_ROTATION_DECISION_MODE=decision_runs：仅产出决策快照，未回写持仓状态。")
    elif state_path:
        print(f"持仓状态已回写：{state_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="etf_rotation_strategy")
    ap.add_argument("--pool", default="output/etf-rotation/pool.yaml",
                    help="ETF 池清单（极简 YAML，ts_code/name/note）")
    ap.add_argument("--state", default="output/etf-rotation/state.json",
                    help="持仓状态文件（老仓缓冲凭据；--write-state false 时只读不写）")
    ap.add_argument("--out-dir", default="output/etf-rotation", help="报告输出目录")
    ap.add_argument("--store-dir", default="output/quotes-store", help="本地 CSV 行情库目录")
    ap.add_argument("--k", type=int, default=None, help="持仓数（覆盖默认 4）")
    ap.add_argument("--buffer-ratio", type=float, default=None, help="缓冲倍数（默认 1.5K）")
    ap.add_argument("--win-slope", type=int, default=None, help="动量回归窗口（默认 24）")
    ap.add_argument("--r2-min", type=float, default=None, help="R² 下限（默认 0.4）")
    ap.add_argument("--loss-limit", type=float, default=None, help="近3日单日跌幅下限（默认 0.965）")
    ap.add_argument("--coverage-min", type=float, default=None, help="覆盖率下限（默认 0.90）")
    ap.add_argument("--history-min", type=int, default=None, help="最低历史交易日（默认 45）")
    ap.add_argument("--defensive", default=None, help="防御资产代码（默认 511880.XSHG 货币ETF）")
    ap.add_argument("--plan", action="store_true", help="仅诊断库内数据状态，不计算")
    args = ap.parse_args(argv)

    p = StrategyParams()
    if args.k is not None:
        p.k = args.k
    if args.buffer_ratio is not None:
        p.buffer_ratio = args.buffer_ratio
    if args.win_slope is not None:
        p.win_slope = args.win_slope
    if args.r2_min is not None:
        p.r2_min = args.r2_min
    if args.loss_limit is not None:
        p.loss_limit = args.loss_limit
    if args.coverage_min is not None:
        p.coverage_min = args.coverage_min
    if args.history_min is not None:
        p.history_min = args.history_min
    if args.defensive:
        p.defensive_code = args.defensive

    pool_path = Path(args.pool)
    store_dir = Path(args.store_dir)

    if args.plan:
        return _plan(pool_path, store_dir, p.history_min)

    # 降级开关：只产决策快照，不回写状态
    degraded = os.environ.get("STRATEGY_ETF_ROTATION_DECISION_MODE") == "decision_runs"
    return _run(pool_path, Path(args.state), p, Path(args.out_dir),
                write_state=not degraded, store_dir=store_dir)


if __name__ == "__main__":
    raise SystemExit(main())
