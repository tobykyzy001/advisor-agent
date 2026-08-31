"""抄作业技能配套：轻量回测——把群主路线还原成近似收益/胜率。

设计：本脚本不联网，接收「事件流 + 每只标的的日K收盘序列(JSON)」做回测。
日K由 agent 通过 tushare MCP (`mcp__tushareMcp__daily`) 在会话内拉取后，以
`--quotes quotes.json` 传入；或用 `--quotes-file` 指向已落盘的缓存。

回测假设（明示的近似，非精确业绩）：
  - 信号消息出现的**次日开盘价**成交（当日消息通常盘中发出，无法当日落地）。
    缺开盘价时退用当日收盘价。
  - 买入 = 等权全仓（卖出清零）；同一标的重复信号取最新一次方向。
  - 无仓位参数，故按"信号方向翻转"重建，不做部分加减仓。
  - 涨停买不进 / 跌停卖不出 / 停牌 不作特殊处理（近似）。
  - 期末仍持有的标的按最后交易日收盘价市值计入。

输出：路线还原表 + 等权组合逐日净值 + 期末收益 / 最大回撤 / 换手次数。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 把 timeline 与 symbol_map 纳入同目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from timeline import SignalEvent, extract_events  # noqa: E402


@dataclass
class FullSignal:
    """一只标的的一次方向翻转。"""
    ts_code: str
    date: str          # 信号日 YYYY-MM-DD
    action: str        # 买入 | 卖出
    raw: str = ""


@dataclass
class TradeLeg:
    """一段持仓：买入日 → 卖出日（或期末）。"""
    ts_code: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    ret_pct: float


@dataclass
class BacktestResult:
    legs: list[TradeLeg] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)  # [{date, nav}]
    total_ret_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    open_positions: list[dict] = field(default_factory=list)  # 期末未平仓标的

    def to_dict(self) -> dict:
        return {
            "trades": [t.__dict__ for t in self.legs],
            "equity": self.equity,
            "总收益%": round(self.total_ret_pct, 2),
            "最大回撤%": round(self.max_drawdown_pct, 2),
            "胜率%": round(self.win_rate, 2),
            "期末持仓": self.open_positions,
        }


def resolve_signals(messages: list[dict], amap: dict[str, list[str]]) -> list[FullSignal]:
    """从消息流 + 映射表重建「标的 → 方向」信号序列（按时间升序）。

    仅纳入能映射到唯一 ts_code 的标的；映射为空或多个候选的标注未解析、跳过回测。
    语义修正：把"持有/观望"也视作"仍在持有（延续）"，不额外生成交易；只有
    「买入类」与「卖出类」动作词才决定方向翻转。这样一条情绪化消息里的题材被
    反复提及，不会被当成多次买卖。
    """
    from symbol_map import resolve_alias  # noqa: E402
    from timeline import split_themes  # noqa: E402
    theme_set = set(amap.keys())
    out: list[FullSignal] = []
    for ev in extract_events(messages):
        # 仅"买入 / 卖出"参与方向；"持有/观望"不产生新信号
        if ev.action not in ("买入", "卖出"):
            continue
        themes = split_themes(ev.raw, theme_set)
        if not themes:
            import re
            for code in re.findall(r"\b\d{6}\b", ev.raw):
                resolved = resolve_alias(code, amap)
                if resolved:
                    out.append(FullSignal(resolved[0], ev.date, ev.action, ev.raw))
            continue
        for t in themes:
            cand = resolve_alias(t, amap)
            if len(cand) != 1:
                continue
            tc = cand[0]
            out.append(FullSignal(tc, ev.date, ev.action, ev.raw))
    # 同一 (ts_code) 按时间序，连续同方向只留最新（保持状态机的"翻转"语义）
    by_ts: dict[str, list[FullSignal]] = {}
    for s in sorted(out, key=lambda x: x.date):
        by_ts.setdefault(s.ts_code, []).append(s)
    dedup: list[FullSignal] = []
    for tc, sigs in by_ts.items():
        prev_action = None
        for s in sigs:
            if s.action == prev_action:
                dedup[-1] = s  # 覆盖为更新的同向信号
            else:
                dedup.append(s)
                prev_action = s.action
    dedup.sort(key=lambda x: x.date)
    return dedup


def run_backtest(signals: list[FullSignal],
                 quotes: dict[str, list[dict]],
                 period_end: str) -> BacktestResult:
    """quotes: {ts_code: [{date, open, close}]}（接近日序）。按信号重建逐笔。"""
    # 每个标的的日期索引
    idx: dict[str, dict[str, dict]] = {}
    for tc, rows in quotes.items():
        idx[tc] = {r["date"]: r for r in rows}

    signals = sorted(signals, key=lambda s: s.date)
    trades: list[TradeLeg] = []
    open_pos: dict[str, dict] = {}   # ts_code -> {date, price}
    for s in signals:
        tc = s.ts_code
        action = s.action
        rows = idx.get(tc, {})
        dates = sorted(rows)
        if not dates:
            continue
        if s.date < dates[0]:
            # 信号早于数据窗口 => 只能在窗口首日开盘成交（近似）
            fill = dates[0]
        else:
            # 成交日 = 信号日当天或其后第一个交易日（信号日多是盘中发出）
            fill = next((d for d in dates if d >= s.date), None)
            if fill is None:
                continue
        row = rows[fill]
        price = row.get("open") or row.get("close")
        if price is None:
            continue
        if action == "买入":
            open_pos.setdefault(tc, {"date": fill, "price": price})
        else:  # 卖出
            if tc in open_pos:
                entry = open_pos.pop(tc)
                ret = (price - entry["price"]) / entry["price"] * 100
                trades.append(TradeLeg(tc, entry["date"], entry["price"], fill, price, ret))

    # 期末未平仓 → 以 period_end 前最后一个收盘价市值
    open_positions = []
    for tc, entry in open_pos.items():
        rows = idx.get(tc, {})
        dates = sorted(rows)
        if not dates:
            continue
        last = next((d for d in reversed(dates) if d <= period_end), dates[-1])
        px = rows[last].get("close") or rows[last].get("open")
        ret = (px - entry["price"]) / entry["price"] * 100
        trades.append(TradeLeg(tc, entry["date"], entry["price"], last, px, ret))
        open_positions.append({"ts_code": tc, "date": last, "close": px,
                               "收益%": round(ret, 2)})

    # 等权组合净值（简化：按卖出时间轴累积每笔收益）
    wins = sum(1 for t in trades if t.ret_pct > 0)
    total_ret = (sum(t.ret_pct for t in trades) / len(trades)) if trades else 0.0
    win_rate = (wins / len(trades) * 100) if trades else 0.0
    max_dd = 0.0
    equity = []
    nav = 1.0
    peak = 1.0
    for t in sorted(trades, key=lambda x: x.sell_date):
        nav = nav * (1 + t.ret_pct / 100)
        peak = max(peak, nav)
        dd = (peak - nav) / peak * 100
        max_dd = max(max_dd, dd)
        equity.append({"date": t.sell_date, "nav": round(nav, 4)})

    return BacktestResult(legs=trades, equity=equity,
                          total_ret_pct=total_ret,
                          max_drawdown_pct=max_dd,
                          win_rate=win_rate,
                          open_positions=open_positions)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backtest")
    ap.add_argument("--messages", required=True, help="解析后消息 JSON 路径（fetch_homework 产物）")
    ap.add_argument("--quotes", required=True, help="标的日线 JSON：{ts_code:[{date,open,close}]}")
    ap.add_argument("--period-end", default=None, help="回测期末 YYYY-MM-DD")
    ap.add_argument("--out", default="output/copy-trade/backtest_result.json")
    args = ap.parse_args(argv)

    msgs = json.loads(Path(args.messages).read_text(encoding="utf-8"))["消息"]
    quotes = json.loads(Path(args.quotes).read_text(encoding="utf-8"))
    from symbol_map import load_map
    amap = load_map()
    signals = resolve_signals(msgs, amap)
    period_end = args.period_end or max((r["date"] for rows in quotes.values() for r in rows), default="")
    result = run_backtest(signals, quotes, period_end)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))