"""中期动量轮动策略：自包含单文件脚本（纯标准库，可下载即跑，不依赖 quantify 包）。

这是「观察仓中期动量选股」的**唯一可执行真源**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/momentum_strategy.py` 提供给目标工作区里的
agent 下载执行（与 workspace-init/init_workspace.py、w_bottom_screen.py 同一分发模式）。

设计约束（与 w_bottom_screen.py 一致）：
- 纯标准库（dataclass / json / argparse / datetime / pathlib / re / statistics / os），零第三方依赖。
- 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。
- 脚本本身**不调 tushare MCP**（MCP 只在 agent 会话内可用）；取数由 agent 完成并回填 JSON。

────────────────────────────────────────────────────────────────────────────────
策略口径（长短期双榜 + 老仓粘性，详细见 .agents/skills/momentum-rotation/SKILL.md）
────────────────────────────────────────────────────────────────────────────────

动量：对每只股票算三档区间涨幅 mom = close[-1] / close[-N] - 1：
  mom20  = 20 交易日涨幅（短期强度，选股快榜）
  mom120 = 120 交易日涨幅（中期强度，选股慢榜）
  mom60  = 60 交易日涨幅（仅用于判断大盘状态，不参与选股排名）

大盘状态（fail-closed 的市场过滤器）：
  取池内全部股票 mom60 的「中位数」：
    中位数 ≥ +5%  → 上行（正常开仓）
    中位数 ≤ -5%  → 下行（冻结新增，已有持仓原样保留）
    之间          → 震荡（正常开仓）

选股顺序：
  第一步 过滤（三道关全过才算「合格」）：
     - 趋势关：close ≥ MA120（收盘站在 120 日均线上方）
     - 反追高关：close 偏离 MA20 ≤ 28% 且 近 5 日涨幅 ≤ 24%
     - 数据关：过滤后无合格标的则按兜底规则处理
  第二步 双轨排名：对合格股票分别按 mom20 降序排快榜、按 mom120 降序排慢榜
     （同分按 ts_code 字典序，保证确定）。
  第三步 老仓优先（buffer16）：现有持仓只要在快榜或慢榜任一榜中仍居前 16，直接保留。
  第四步 补满 8 仓：老仓保留完未满 8 只，按「快榜前 4 → 慢榜前 4 → 快榜续扫 → 慢榜续扫」
     顺序补位，去重，直到凑满 max_positions=8 只（等权，每只 1/8=12.5%）。
  第五步 兜底：无合格标的时——有老仓则继续持有老仓（参数 keep_on_empty=True，
     可改 False 切「全部退现金」），无老仓则空仓退出（100% 现金）。

持仓约束：
  - 等权：单一标的权重 = 1 / max_positions。
  - 退出机制：**纯滚动**。持仓期间没有任何价格型止损在跑——股票跌出快/慢榜前
    buffer_rank（16）名缓冲 → 下次调仓被移出目标组合 → 生成 zero_out 清仓单。
    跌破 MA120 只是失去「新买入资格」，已持仓的仍靠前 16 名缓冲决定去留。
  - 流动性：单票下单量 ≤ 信号日成交量 × max_trade_vol_ratio（默认 10%）。
  - 节奏：信号日（T）收盘算信号，按 T 日收盘价成交；回滚开关见 STRATEGY_MOMENTUM_DECISION_MODE。

fail-closed（数据安全）：
  - 池内历史数据覆盖率 < coverage_min（默认 95%）→ 不出信号（no_signal）。
  - 某只股票历史不足 121 交易日（需算 mom120/MA120）→ 该只剔除并在报告标注。
  - 若过滤后一只合格标的都没有 → 按兜底规则退出现金（不留半仓凑合）。

一键回滚开关（环境变量 STRATEGY_MOMENTUM_DECISION_MODE=decision_runs）：
  这是「一键降级」开关：当需要只产出决策、不落状态时（如链路故障或仅做研究核对），
  设置 STRATEGY_MOMENTUM_DECISION_MODE=decision_runs 后，脚本跳过「回写持仓状态 state.json」
  这一步，只打印决策快照（排名/过滤/目标持仓的确定性结果），避免在不确定状态下
  写入会被下一轮当作「老仓」依据的状态。详见 `--data` 输出差异。
────────────────────────────────────────────────────────────────────────────────

两层式工作流（三段式，与 copy-trade / w-bottom-screener 一致）：
 1) python momentum_strategy.py --watchlist output/watchlist/watchlist.yaml --plan
        —— 打印观察仓标的清单 + 待取数清单（含每只 ts_code、需要的交易日窗口）。
        池子大于 --shard-size（默认 10 只）时自动按分片打印，也可用 --shards N 显式指定片数。
 2) agent 取数（行情数据体积大，禁止进入主会话上下文）：
    - 小池子（单片）：主会话逐只调 mcp__tushareMcp__daily 取不早于 start_date 的日线，
      整理成 JSON 写到 output/momentum/quotes.json，格式：
        {"600519.SH": [{"trade_date":"20250102","open":..,"high":..,"low":..,"close":..,"vol":..}, ...], ...}
    - 大池子（分片）：每个分片派一个独立子代理（subagent）并行取数，子代理把本片 JSON
      直接写到 output/momentum/quotes/shard_<k>.json（不同写同一文件），主会话只收一行回执。
      取数前先清空 quotes 目录，避免上轮残留分片混入。
 3) python momentum_strategy.py --watchlist output/watchlist/watchlist.yaml \
        --state output/momentum/state.json --data output/momentum/quotes.json   # 单片小池子
    python momentum_strategy.py --watchlist output/watchlist/watchlist.yaml \
        --state output/momentum/state.json --data output/momentum/quotes   # 分片目录模式
        —— 跑选股排名/过滤/调仓，产出组合信号与持仓（T 日收盘价成交），
           并回写持仓状态 state.json；报告落在 output/momentum/plan_<时间戳>.md。

已有持仓通过 `--state` 传入（上一轮信号输出会自动回写 state.json；首次运行无 state 则视为空仓）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import median

# ─────────────────────────────────────────────────────────────────────────
# 领域结构（标准库 dataclass，等价于 quantify.data.schema 的 Bar / Series）
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Bar:
    date: date
    close: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0


@dataclass
class Series:
    symbol: str
    bars: list[Bar] = field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]


@dataclass
class StrategyParams:
    """策略全部可调参数（默认值对应「中期动量」口径）。"""

    win_short: int = 20            # mom20 窗口（短期强度，1 个月）
    win_mid: int = 120             # mom120 窗口（中期强度，半年）
    win_market: int = 60           # mom60 窗口（仅大盘状态，1 个季度）
    ma_trend: int = 120            # 趋势关均线（MA120）
    ma_short: int = 20             # 反追高关均线（MA20）
    ma_dev_max: float = 0.28       # 反追高：现价偏离 MA20 上限 28%
    rush_max: float = 0.24         # 反追高：近 5 日涨幅上限 24%
    rush_window: int = 5           # 反追高：近 N 日
    market_up: float = 0.05        # 大盘上行阈值（mom60 中位数 ≥ +5%）
    market_down: float = -0.05     # 大盘下行阈值（mom60 中位数 ≤ -5%）
    buffer_rank: int = 16          # 老仓粘性：快/慢任一榜前 16 保留
    max_positions: int = 8         # 最大持仓数（等权）
    fast_top: int = 4              # 补仓：快榜前 N
    slow_top: int = 4              # 补仓：慢榜前 N
    max_trade_vol_ratio: float = 0.10  # 流动性：单票下单量 ≤ 信号日成交量 × 10%
    coverage_min: float = 0.95     # 覆盖率下限（低于则 fail-closed 不出信号）
    history_min: int = 121         # 每只股票最低历史交易日（mom120+1 可用）
    hold_on_empty: bool = True     # 兜底：无合格标的时是否继续持有老仓（False=全退现金）


@dataclass
class WatchItem:
    ts_code: str
    name: str = ""
    market: str = "A"
    note: str = ""


@dataclass
class Decision:
    """单只股票的动量决策中间结构。"""

    ts_code: str
    name: str = ""
    mom20: float = 0.0
    mom120: float = 0.0
    mom60: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    ma120: float = 0.0
    close: float = 0.0
    dev_ma20: float = 0.0      # 现价 / MA20 - 1
    rush5: float = 0.0         # 近 5 日涨幅
    volume: float = 0.0        # 信号日成交量（流动性约束用）
    has_history: bool = False  # 历史是否达标（≥ history_min）
    qualified: bool = False    # 三关过滤是否合格
    filter_reasons: list[str] = field(default_factory=list)
    rank_fast: int = 0         # 快榜排名（0 表示未上榜/不合格）
    rank_slow: int = 0         # 慢榜排名

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "mom20": round(self.mom20, 4),
            "mom120": round(self.mom120, 4),
            "mom60": round(self.mom60, 4),
            "ma120": round(self.ma120, 2),
            "close": round(self.close, 2),
            "dev_ma20": round(self.dev_ma20, 4),
            "rush5": round(self.rush5, 4),
            "volume": round(self.volume, 0),
            "has_history": self.has_history,
            "qualified": self.qualified,
            "filter_reasons": self.filter_reasons,
            "rank_fast": self.rank_fast,
            "rank_slow": self.rank_slow,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 观察仓清单读取（极简 YAML 子集解析，与 w_bottom_screen.py 一致）
# ═══════════════════════════════════════════════════════════════════════════

_ITEM_KEY = re.compile(r"^\s*-\s+ts_code:\s*(.+?)\s*$")
# 子键行：任意缩进（≥1 空格）都收——标准格式为 2 空格（与 PyYAML safe_dump 同风格，
# 量化核心 save_watchlist 写出的清单可直接读），也宽容兼容旧 4 空格手编格式。
_KEY = re.compile(r"^\s+(\w+):\s*(.*)$")


def load_watchlist(path: Path | None = None) -> list[WatchItem]:
    p = path or Path("output/watchlist/watchlist.yaml")
    if not p.exists():
        raise FileNotFoundError(
            f"观察仓清单不存在：{p}。请先运行 workspace-init 技能初始化工作区"
            f"（`python src/workspace-init/init_workspace.py`），或在投研工具设置里检查默认工作区。"
        )
    lines = p.read_text(encoding="utf-8").splitlines()
    items: list[WatchItem] = []
    cur: dict[str, str] | None = None
    for ln in lines:
        m = _ITEM_KEY.match(ln)
        if m:
            if cur is not None:
                items.append(WatchItem(
                    ts_code=cur.get("ts_code", "").strip(),
                    name=cur.get("name", "").strip(),
                    market=cur.get("market", "A").strip() or "A",
                    note=cur.get("note", "").strip(),
                ))
            cur = {"ts_code": m.group(1).strip().strip('"').strip("'")}
            continue
        km = _KEY.match(ln)
        if km and cur is not None:
            cur[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    if cur is not None:
        items.append(WatchItem(
            ts_code=cur.get("ts_code", "").strip(),
            name=cur.get("name", "").strip(),
            market=cur.get("market", "A").strip() or "A",
            note=cur.get("note", "").strip(),
        ))
    return [it for it in items if it.ts_code]


# ═══════════════════════════════════════════════════════════════════════════
# tushare 日线行 → Series（清洗 + 排序）
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
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def rows_to_series(symbol: str, rows: list[dict]) -> Series:
    bars: list[Bar] = []
    for row in rows:
        c = _to_float(row.get("close"))
        d = _to_date(row.get("trade_date"))
        if c is None or d is None:
            continue  # 缺收盘价或日期为脏行
        vol = _to_float(row.get("vol")) or 0.0
        bars.append(Bar(
            date=d,
            close=c,
            open=_to_float(row.get("open")) or 0.0,
            high=_to_float(row.get("high")) or 0.0,
            low=_to_float(row.get("low")) or 0.0,
            volume=vol,
        ))
    bars.sort(key=lambda b: b.date)
    return Series(symbol=symbol, bars=bars)


# ═══════════════════════════════════════════════════════════════════════════
# 动量 / 均线 / 状态纯函数（可独立单测）
# ═══════════════════════════════════════════════════════════════════════════


def mom(closes: list[float], window: int) -> float | None:
    """区间涨幅：close[-1] / close[-1-window] - 1。数据不足返回 None。"""
    if len(closes) < window + 1:
        return None
    base = closes[-1 - window]
    if not base:
        return None
    return closes[-1] / base - 1.0


def ma(closes: list[float], window: int) -> float | None:
    """简单移动平均。数据不足返回 None。"""
    if len(closes) < window or window <= 0:
        return None
    seg = closes[-window:]
    return sum(seg) / len(seg)


def compute_metrics(series: Series, p: StrategyParams) -> Decision:
    """对单只股票计算全部动量/均线指标。数据不足时 has_history=False。"""
    d = Decision(ts_code=series.symbol)
    closes = series.closes
    if len(closes) < p.history_min:
        d.filter_reasons.append(f"历史仅 {len(closes)} 日，不足 {p.history_min}")
        return d
    d.has_history = True

    m20, m120, m60 = mom(closes, p.win_short), mom(closes, p.win_mid), mom(closes, p.win_market)
    a20, a120 = ma(closes, p.ma_short), ma(closes, p.ma_trend)
    d.close = closes[-1]
    d.mom20 = m20 if m20 is not None else 0.0
    d.mom120 = m120 if m120 is not None else 0.0
    d.mom60 = m60 if m60 is not None else 0.0
    d.ma20 = a20 if a20 is not None else 0.0
    d.ma120 = a120 if a120 is not None else 0.0
    d.volume = series.bars[-1].volume
    if a20:
        d.dev_ma20 = d.close / a20 - 1.0
    if len(closes) >= p.rush_window + 1 and closes[-1 - p.rush_window]:
        d.rush5 = d.close / closes[-1 - p.rush_window] - 1.0
    return d


def _filter(d: Decision, p: StrategyParams) -> None:
    """三道过滤关（趋势关 / 反追高关 / 数据关）。结果写回 d.qualified。"""
    reasons: list[str] = []
    if not d.has_history:
        reasons.append("历史数据不足")
    if d.ma120 and d.close < d.ma120:
        reasons.append(f"收盘 {d.close:.2f} 在 MA120({d.ma120:.2f}) 下方")
    if d.dev_ma20 > p.ma_dev_max:
        reasons.append(f"偏离 MA20 {d.dev_ma20:.2%} 超上限 {p.ma_dev_max:.2%}")
    if d.rush5 > p.rush_max:
        reasons.append(f"近 {p.rush_window} 日涨幅 {d.rush5:.2%} 超上限 {p.rush_max:.2%}")
    d.filter_reasons = reasons
    d.qualified = not reasons


def market_regime(decisions: list[Decision], p: StrategyParams) -> str:
    """大盘状态：池内全部股票 mom60 中位数（仅考虑 has_history 的标的）。"""
    valid = [d.mom60 for d in decisions if d.has_history]
    if not valid:
        return "unknown"
    m = median(valid)
    if m >= p.market_up:
        return "up"
    if m <= p.market_down:
        return "down"
    return "range"


def rank_fast_slow(decisions: list[Decision]) -> tuple[dict[str, int], dict[str, int]]:
    """分别给快榜（mom20 降序）、慢榜（mom120 降序）编号，同分按 ts_code 字典序。

    返回两个 dict：ts_code → 排名（1-based）。只给合格标的排名，其他置 0。
    """
    qd = [d for d in decisions if d.qualified]
    fast_sorted = sorted(qd, key=lambda d: (-d.mom20, d.ts_code))
    slow_sorted = sorted(qd, key=lambda d: (-d.mom120, d.ts_code))
    rank_fast = {d.ts_code: i + 1 for i, d in enumerate(fast_sorted)}
    rank_slow = {d.ts_code: i + 1 for i, d in enumerate(slow_sorted)}
    for d in decisions:
        d.rank_fast = rank_fast.get(d.ts_code, 0)
        d.rank_slow = rank_slow.get(d.ts_code, 0)
    return rank_fast, rank_slow


def build_target(decisions: list[Decision], current: set[str], p: StrategyParams) -> list[str]:
    """按策略四步法产出目标持仓（ts_code 列表，等权无顺序要求返回稳定顺序）。"""
    rank_fast, rank_slow = rank_fast_slow(decisions)

    fast_top = sorted(
        [d.ts_code for d in decisions if 0 < d.rank_fast],
        key=lambda s: (rank_fast[s], s),
    )
    slow_top = sorted(
        [d.ts_code for d in decisions if 0 < d.rank_slow],
        key=lambda s: (rank_slow[s], s),
    )

    target: list[str] = []
    seen: set[str] = set()

    # 第三步：老仓在快/慢任一榜 ≤ buffer_rank 直接保留（按 ts_code 字典序遍历，保证结果确定）
    for code in sorted(current):
        keep_fast = code in rank_fast and rank_fast[code] <= p.buffer_rank
        keep_slow = code in rank_slow and rank_slow[code] <= p.buffer_rank
        if keep_fast or keep_slow:
            target.append(code)
            seen.add(code)

    # 第四步：快榜前 fast_top → 慢榜前 slow_top → 快榜续扫 → 慢榜续扫
    def _fill(seq: list[str]) -> bool:
        for code in seq:
            if len(target) >= p.max_positions:
                return True
            if code in seen:
                continue
            target.append(code)
            seen.add(code)
        return len(target) >= p.max_positions

    if _fill(fast_top[: p.fast_top]):
        return target
    if _fill(slow_top[: p.slow_top]):
        return target
    if _fill(fast_top):
        return target
    _fill(slow_top)
    return target


# ═══════════════════════════════════════════════════════════════════════════
# 持仓状态读写（output/momentum/state.json）
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
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 分片取数（大池子多 agent 并行）与行情载入
# ═══════════════════════════════════════════════════════════════════════════

#: 每个取数分片的默认标的数：单片 MCP 取数上下文控制在子代理可承受范围内
SHARD_SIZE_DEFAULT = 10


def shard_groups(codes: list[str], shards: int | None = None,
                 shard_size: int = SHARD_SIZE_DEFAULT) -> list[list[str]]:
    """把 ts_code 清单切成若干片。

    指定 shards（片数）时按片数均分；否则按 shard_size（每片只数）切。
    保持原清单顺序，保证各片子代理取数范围确定、互不重叠。
    """
    if not codes:
        return []
    if shards and shards > 0:
        size = max(1, -(-len(codes) // shards))  # 向上取整
    else:
        size = max(1, shard_size)
    return [codes[i:i + size] for i in range(0, len(codes), size)]


def load_quotes(data_path: Path) -> tuple[dict[str, list], list[str]]:
    """读取回填行情：单个 JSON 文件，或一个目录（合并目录下全部 *.json 分片）。

    目录模式用于多 agent 分片并行取数：每个子代理把本分片写到 quotes/shard_<k>.json，
    本函数按文件名排序逐片合并；同一 ts_code 跨片重复时保留行数较多者。
    返回 (raw, notes)：raw 为 {ts_code: [bar, ...]}，notes 为合并提示（重复/覆盖等）。
    文件坏 JSON / 顶层非 dict 直接抛 ValueError（指明文件名，便于重派该分片）。
    """
    if not data_path.exists():
        raise FileNotFoundError(str(data_path))
    files = [data_path] if data_path.is_file() else sorted(data_path.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"目录下没有任何 .json 分片：{data_path}")

    raw: dict[str, list] = {}
    notes: list[str] = []
    for fp in files:
        try:
            chunk = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"分片文件不是合法 JSON：{fp}（{e}）") from e
        if not isinstance(chunk, dict):
            raise ValueError(f"分片文件顶层必须是 {{\"ts_code\": [...]}} 的对象：{fp}")
        for ts, rows in chunk.items():
            if not isinstance(rows, list):
                notes.append(f"{ts} 在 {fp.name} 中的数据不是数组，已跳过")
                continue
            if ts in raw:
                if len(rows) > len(raw[ts]):
                    notes.append(f"{ts} 在 {fp.name} 重复出现，保留行数较多者（{len(rows)} 行）")
                    raw[ts] = rows
                else:
                    notes.append(f"{ts} 在 {fp.name} 重复出现，已跳过（保留先出现的 {len(raw[ts])} 行）")
            else:
                raw[ts] = rows
    return raw, notes


# ═══════════════════════════════════════════════════════════════════════════
# 主流程（--plan / --data）
# ═══════════════════════════════════════════════════════════════════════════


def _plan(watchlist_path: Path, shards: int | None = None,
          shard_size: int = SHARD_SIZE_DEFAULT) -> int:
    try:
        items = load_watchlist(watchlist_path)
    except FileNotFoundError as e:
        print(f"[warning] {e}")
        return 1
    if not items:
        print("观察仓清单为空，请先编辑 output/watchlist/watchlist.yaml。")
        return 1
    print("观察仓标的池（%d 只）：" % len(items))
    for it in items:
        print(f"  - {it.ts_code}\t{it.name}\t{it.market}\t{it.note}")
    print()

    codes = [it.ts_code for it in items]
    groups = shard_groups(codes, shards, shard_size)

    if len(groups) <= 1:
        # 小池子：单片直取（数据量可控，主会话一次取齐）
        print("池子较小（单片），主会话逐只调用 mcp__tushareMcp__daily 取近 250 交易日日线")
        print("（start_date 应不晚于 today-250，以覆盖 mom120=120 与 MA120=120 的历史窗口）：")
        for code in codes:
            print(f"  ts_code={code}")
        print()
        print("取数字段保留：trade_date, open, high, low, close, vol。")
        print("整理成 JSON 保存为 output/momentum/quotes.json，格式：")
        print('  {"600519.SH": [{"trade_date":"20250102","open":..,"high":..,')
        print('                "low":..,"close":..,"vol":..}, ...], ...}')
        print()
        print("完成后运行：")
        print("  python momentum_strategy.py --watchlist output/watchlist/watchlist.yaml")
        print("      --state output/momentum/state.json --data output/momentum/quotes.json")
        return 0

    # 大池子：分片模式，多 agent 并行取数 + 落盘旁路，行情数据不进主会话
    print(f"池子较大，分 {len(groups)} 片取数（每片 {len(groups[0])} 只左右）。取数纪律：")
    print("- 行情数据体积大，禁止进入主会话上下文（不回显、不粘贴、不汇总明细）。")
    print("- 取数前先清空 output/momentum/quotes/ 目录，避免上轮残留分片混入。")
    print("- 每个分片派一个独立子代理（subagent）并行取数：子代理对片内每只 ts_code 调")
    print("  mcp__tushareMcp__daily 取近 250 交易日日线（start_date 不晚于 today-250），")
    print("  字段保留 trade_date/open/high/low/close/vol，把本片 JSON 直接写入对应分片文件；")
    print("  回复主会话只需一行回执（如「shard 1：完成 10/10，失败 []」），不得粘贴行情。")
    print()
    for k, grp in enumerate(groups, 1):
        print(f"shard {k}/{len(groups)} → output/momentum/quotes/shard_{k}.json"
              f"（{len(grp)} 只）：{' '.join(grp)}")
    print()
    print("全部分片回齐后运行（--data 指向目录，脚本自动合并全部 *.json 分片）：")
    print("  python momentum_strategy.py --watchlist output/watchlist/watchlist.yaml")
    print("      --state output/momentum/state.json --data output/momentum/quotes")
    return 0


def _data(watchlist_path: Path, data_path: Path, state_path: Path | None, p: StrategyParams,
          write_plan: bool = True) -> int:
    try:
        raw, merge_notes = load_quotes(data_path)
    except FileNotFoundError as e:
        print(f"数据不存在：{e}，请先完成取数（--plan 后按提示回填）。")
        return 1
    except ValueError as e:
        print(f"[error] {e}")
        print("请重新取数补全该分片后再运行。")
        return 1
    for note in merge_notes:
        print(f"[merge] {note}")
    if not raw:
        print("数据为空（所有分片均无标的），请先完成取数。")
        return 1

    name_map: dict[str, str] = {}
    try:
        name_map = {it.ts_code: it.name for it in load_watchlist(watchlist_path)}
    except FileNotFoundError:
        pass

    # 覆盖池：数据文件里出现的全部 ts_code（用于覆盖率统计）
    universe = list(raw.keys())
    series_list = [rows_to_series(ts, rows) for ts, rows in raw.items()]

    # 覆盖率 fail-closed
    total = len(universe)
    covered = sum(1 for s in series_list if len(s.bars) >= p.history_min)
    coverage = covered / total if total else 0.0
    no_signal = coverage < p.coverage_min

    decisions = [compute_metrics(s, p) for s in series_list]
    for d in decisions:
        if d.has_history:
            d.name = name_map.get(d.ts_code, "")
        _filter(d, p)
    regime = market_regime(decisions, p)

    # 老仓读取
    state = load_state(state_path) if state_path else {"positions": [], "cash_pct": 1.0}
    current = {h["ts_code"] for h in state.get("positions", [])}

    # ── 目标持仓决策（顺序严格，与策略五步法一致） ─────────────────────────
    # 优先级：fail-closed(数据关) > 大盘下行冻结 > 正常选股 > 兜底
    if no_signal:
        # 数据覆盖率不足：不出新信号，老仓按 hold_on_empty 决定去留
        target = sorted(current) if p.hold_on_empty else []
        signal = "no_signal"
    elif regime == "down":
        # 大盘明显下行：冻结新增，已有持仓原样保留（不清仓）
        target = sorted(current)
        signal = "frozen"
    else:
        # 上行/震荡：正常跑四步选股（老仓 buffer16 优先 → 快4慢4补满到 8）
        target = build_target(decisions, current, p)
        signal = "signal"
        # 第五步兜底：过滤完一只合格标的都没有时，有老仓则继续持有老仓，无则空仓
        if not target and current and p.hold_on_empty:
            target = sorted(current)

    weight = 1.0 / p.max_positions if p.max_positions else 0.0

    # 信号日 = 数据中出现的最后一个交易日期（成交 = 该日收盘价）
    last_dates = [s.bars[-1].date for s in series_list if s.bars]
    signal_date = max(last_dates).isoformat() if last_dates else ""

    # 组装报告
    lines: list[str] = []
    lines.append("# 中期动量轮动策略 · 组合信号")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 信号日：{signal_date}（按 T 日收盘价成交）")
    lines.append(f"- 观察池标的数：{total}")
    lines.append(f"- 数据覆盖率：{coverage:.1%}")
    lines.append(f"- 大盘状态（mom60 中位数）：{regime}" + ("（冻结新增）" if regime == "down" else ""))
    lines.append(f"- 信号：{signal}")
    lines.append(f"- 目标持仓：{len(target)} / {p.max_positions}（每只权重 {weight:.2%}，现金 {(1 - len(target)*weight):.2%}）")
    lines.append("")

    if no_signal:
        lines.append("> ⚠️ 数据覆盖率低于下限，策略 fail-closed，本日不出新信号。")
        if target:
            lines.append("> 按兜底规则继续保留老仓原样；不新增、不清仓。")
        else:
            lines.append("> 无老仓可保留，空仓等待。")

    # 目标持仓表
    dm = {d.ts_code: d for d in decisions}
    if target:
        lines.append("")
        lines.append("## 本次目标持仓（T 日收盘价成交）")
        lines.append("")
        lines.append("| 代码 | 名称 | 快榜 | 慢榜 | mom20 | mom120 | 现价 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for code in target:
            d = dm.get(code)
            if d is None:
                continue
            tag = "老仓" if code in current else "新增"
            lines.append(
                f"| {code} | {d.name} | {d.rank_fast} | {d.rank_slow} | {d.mom20:.1%} | "
                f"{d.mom120:.1%} | {d.close:.2f} | {tag} |"
            )
        lines.append("")
        lines.append("> 持仓期间无价格止损，退出靠排名跌出前 16 名缓冲被动滚动（zero_out）。")
        lines.append("")
    else:
        lines.append("")
        lines.append("> 本次无目标持仓，空仓现金。")
        lines.append("")

    # 现金流/流动性提示（信号日成交量 10% 约束）
    lines.append(f"> 流动性约束：单票下单量 ≤ 信号日成交量 × {p.max_trade_vol_ratio:.0%}。")
    lines.append("")

    # 合格标的明细（过滤表）
    lines.append("## 逐只动量与过滤")
    lines.append("")
    lines.append("| 代码 | 名称 | mom20 | mom120 | mom60 | MA120 | 现价 | 偏离MA20 | 近5日 | 合格 | 快榜 | 慢榜 | 过滤原因 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in sorted(decisions, key=lambda x: x.ts_code):
        if not d.has_history:
            lines.append(f"| {d.ts_code} | {d.name} | - | - | - | - | - | - | - | 否 | - | - | {'；'.join(d.filter_reasons)} |")
            continue
        lines.append(
            f"| {d.ts_code} | {d.name} | {d.mom20:.1%} | {d.mom120:.1%} | {d.mom60:.1%} | {d.ma120:.2f} "
            f"| {d.close:.2f} | {d.dev_ma20:.1%} | {d.rush5:.1%} | {'是' if d.qualified else '否'} "
            f"| {d.rank_fast or '-'} | {d.rank_slow or '-'} | {'；'.join(d.filter_reasons) or '-'} |"
        )

    out_path = data_path.parent / f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # 回写 state.json（含新目标持仓与等权权重）
    new_positions: list[dict] = []
    for code in target:
        new_positions.append({
            "ts_code": code,
            "weight": weight,
        })
    new_state = {
        "as_of": signal_date,
        "cash_pct": round(max(0.0, 1.0 - len(target) * weight), 4),
        "signal": signal,
        "regime": regime,
        "target": target,
        "positions": new_positions,
    }
    if state_path and write_plan:
        save_state(state_path, new_state)

    print("\n".join(lines))
    print(f"\n报告已保存：{out_path}")
    if not write_plan:
        print("[降级] STRATEGY_MOMENTUM_DECISION_MODE=decision_runs：仅产出决策快照，未回写持仓状态。")
    elif state_path:
        print(f"持仓状态已回写：{state_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="momentum_strategy")
    ap.add_argument("--watchlist", default="output/watchlist/watchlist.yaml",
                    help="观察仓标的池 YAML 路径")
    ap.add_argument("--plan", action="store_true", help="输出观察仓清单与待取数清单")
    ap.add_argument("--shards", type=int, default=None,
                    help="取数分片数（仅 --plan 有效）；缺省时池子超过 --shard-size 只自动分片")
    ap.add_argument("--shard-size", type=int, default=SHARD_SIZE_DEFAULT,
                    help=f"每片标的数（默认 {SHARD_SIZE_DEFAULT}），配合 --plan 自动分片")
    ap.add_argument("--data", help="回填的日线 JSON 路径（单文件），或分片目录（合并目录下全部 *.json）")
    ap.add_argument("--state", default=None, help="持仓状态 JSON 路径（默认同名 .state.json）")
    ap.add_argument("--max-positions", type=int, default=8)
    ap.add_argument("--win-short", type=int, default=20)
    ap.add_argument("--win-mid", type=int, default=120)
    ap.add_argument("--win-market", type=int, default=60)
    ap.add_argument("--ma-dev-max", type=float, default=0.28)
    ap.add_argument("--rush-max", type=float, default=0.24)
    ap.add_argument("--market-up", type=float, default=0.05)
    ap.add_argument("--market-down", type=float, default=-0.05)
    ap.add_argument("--buffer-rank", type=int, default=16)
    ap.add_argument("--fast-top", type=int, default=4)
    ap.add_argument("--slow-top", type=int, default=4)
    ap.add_argument("--max-trade-vol-ratio", type=float, default=0.10)
    ap.add_argument("--coverage-min", type=float, default=0.95)
    ap.add_argument("--history-min", type=int, default=121)
    ap.add_argument("--hold-on-empty", choices=["true", "false"], default="true")
    args = ap.parse_args(argv)

    # 一键回滚/降级开关（环境变量 STRATEGY_MOMENTUM_DECISION_MODE=decision_runs）：
    # 置为 decision_runs 时，脚本只产出「决策快照」（排名/过滤/目标持仓的确定性结果），
    # 不回写持仓状态 state.json——用于研究核对或执行链故障时一键降级，避免误写下一轮的「老仓」依据。
    write_plan = os.getenv("STRATEGY_MOMENTUM_DECISION_MODE", "").strip() != "decision_runs"

    if args.plan:
        return _plan(Path(args.watchlist), shards=args.shards, shard_size=args.shard_size)

    if args.data:
        p = StrategyParams(
            win_short=args.win_short,
            win_mid=args.win_mid,
            win_market=args.win_market,
            ma_dev_max=args.ma_dev_max,
            rush_max=args.rush_max,
            market_up=args.market_up,
            market_down=args.market_down,
            buffer_rank=args.buffer_rank,
            max_positions=args.max_positions,
            fast_top=args.fast_top,
            slow_top=args.slow_top,
            max_trade_vol_ratio=args.max_trade_vol_ratio,
            coverage_min=args.coverage_min,
            history_min=args.history_min,
            hold_on_empty=args.hold_on_empty == "true",
        )
        state_path = Path(args.state) if args.state else Path("output/momentum/state.json")
        return _data(Path(args.watchlist), Path(args.data), state_path, p, write_plan=write_plan)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())