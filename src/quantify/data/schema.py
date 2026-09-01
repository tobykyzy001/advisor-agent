"""数据领域模型（pydantic schema）。

设计原则：所有下游模块（估值/分析/组合/Agent）只依赖这里的模型，
不直接接触 akshare/tushare 的 DataFrame 原始结构，便于切换数据源。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class DailyBar(BaseModel):
    """单根日线 K 线（用于技术形态识别，如 W底/放量）。"""

    date: date                                   # 交易日
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0                          # 成交量（股/手，口径由数据源保证一致）

    @property
    def is_up(self) -> bool:
        """是否阳线（收盘 > 开盘）。"""
        return self.close > self.open


class DailySeries(BaseModel):
    """一个标的按日期升序排列的日线序列。"""

    symbol: str
    market: str = "A"
    bars: list[DailyBar] = Field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def lows(self) -> list[float]:
        return [b.low for b in self.bars]


class Quote(BaseModel):
    """单个标的最新行情快照。"""

    symbol: str
    name: str
    market: str = "A"                 # A | HK
    price: float
    previous_close: float = 0.0
    change_pct: float = 0.0
    market_cap: float = 0.0           # 总市值（亿元）
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    dividend_yield: Optional[float] = None
    as_of: date = Field(default_factory=date.today)


class Financials(BaseModel):
    """公司最新基本面指标（用于估值）。"""

    symbol: str
    name: str = ""
    market: str = "A"
    roe_pct: Optional[float] = None          # 净资产收益率 %（近年）
    roe_trend: list[float] = Field(default_factory=list)  # 近5年ROE
    revenue_growth_pct: Optional[float] = None
    profit_growth_pct: Optional[float] = None
    net_profit: Optional[float] = None       # 归母净利润（元）
    book_value: Optional[float] = None       # 每股净资产（元）
    eps: Optional[float] = None              # 每股收益（元）
    industry: str = ""
    as_of: date = Field(default_factory=date.today)


class StockBundle(BaseModel):
    """一次研究所需的全部输入：行情 + 基本面。"""

    quote: Quote
    financials: Financials = Field(default_factory=Financials)
    # 可选：近N年每股收益序列，用于DCF/成长估算
    eps_history: list[float] = Field(default_factory=list)
    dividend_history: list[float] = Field(default_factory=list)

    @property
    def symbol(self) -> str:
        return self.quote.symbol
