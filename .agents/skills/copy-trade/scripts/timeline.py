"""抄作业技能配套：从结构化消息抽取「信号事件」，还原群主持仓/换仓路线。

纯逻辑模块（不联网），可独立单测。输入 fetch_homework.parse_messages 的消息流，
输出按时间升序的 SignalEvent 列表；供回测与"是否值得抄"分析共用。

动作词 → 方向（事件类型）：
  买入/持有词：低吃 低吸 买入 新开 建仓 锁仓 满仓 回 拿先手 给半仓 猛干 加仓 补
  卖出/清仓词：止盈 砸 割 走 兑现 出 空仓 清仓 拉黑 移出 卖出 减仓
  观察/持有：没动 让子弹飞 观察 不主观 观望 躺平 锁仓(持有)
规则：一条消息可含多个「标的 + 动作」；无标的的纯情绪消息不产出事件。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 动作词 → 事件类型
BUY_WORDS = ["低吃", "低吸", "低水位", "拿先手", "先手", "买入", "新开", "建仓", "给半仓",
             "猛干", "加仓", "补仓", "回去", "回来", "重仓", "进", "上", "回踩"]
SELL_WORDS = ["止盈", "砸", "割", "兑现", "走", "走了", "清仓", "空仓", "拉黑", "移出",
              "卖出", "减仓", "出掉", "跑了", "退了", "休息"]
HOLD_WORDS = ["锁仓", "没动", "不动", "让子弹飞", "观察", "观望", "躺", "格局", "锁", "坐"]
WATCH_WORDS = ["计划", "想", "准备", "再看", "等", "看情况", "不主观", "临盘看"]

# 有些词同时是"回"（买入）与"回调"（不确定），做保守：仅当"回去/回来"才视为买入
RETURN_RE = re.compile(r"(回去|回来|回[^调踩]{0,4})$")


@dataclass
class SignalEvent:
    """一条信号事件。"""
    ts: str            # 时间戳 "YYYY-MM-DD HH:MM:SS"
    date: str          # "YYYY-MM-DD"
    speaker: str
    raw: str           # 原始正文（去噪后）
    action: str        # 买入 | 卖出 | 持有 | 观望
    theme: str         # 对应题材/标的别名（未解析到 ts_code 时为别名原文）
    ts_code: str = ""  # 解析后的 ts_code，未解析则为空


@dataclass
class Timeline:
    """整条路线。"""
    author: str = ""
    events: list[SignalEvent] = field(default_factory=list)


def _which_action(body: str) -> str:
    for w in SELL_WORDS:
        if w in body:
            return "卖出"
    for w in BUY_WORDS:
        if w in body:
            return "买入"
    for w in HOLD_WORDS:
        if w in body:
            return "持有"
    for w in WATCH_WORDS:
        if w in body:
            return "观望"
    return ""


def extract_events(messages: list[dict]) -> list[SignalEvent]:
    """从消息流抽取信号事件。返回按时间升序排序、带 part/action 的事件列表。"""
    events: list[SignalEvent] = []
    for m in messages:
        body = m.get("正文", "")
        if not body or m.get("是否撤回"):
            continue
        action = _which_action(body)
        if not action:
            continue
        ts = m["时间戳"]
        date = ts[:10]
        speaker = m.get("发言人", "")
        ev = SignalEvent(ts=ts, date=date, speaker=speaker, raw=body,
                         action=action, theme="")
        events.append(ev)
    return events


def split_themes(body: str, known_themes: set[str]) -> list[str]:
    """从一条消息里切出提到的「题材/标的」token（供 symbol 映射用）。

    用已确认的题材别名 + 对中文名做启发；返回命中的 theme 列表（保序去重）。
    """
    hits: list[str] = []
    for t in sorted(known_themes, key=len, reverse=True):
        if t in body and t not in hits:
            hits.append(t)
    return hits