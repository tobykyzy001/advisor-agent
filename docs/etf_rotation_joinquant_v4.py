# -*- coding: utf-8 -*-
# ETF 轮动策略 v4 —— v1 召回版 + 扩池到 22 只
#
# 用户对三轮 v3 (A/B/C) 的洞察：
#   - v1 年化 18.46% 的根源不是「信号更准」，而是「满仓权益近 10 年」；
#   - v3 用五组轮动+逆波动率把仓位压到 ~50%，年化随之减半（9.94%）；
#   - 用户提议：直接 v1 多放几只 ETF，看「v1 的属性能不能搬到大池子」；
#
# v4 完全保留 v1 的信号引擎（回归斜率×R² + mom20 + vol 惩罚 + Z-Score 混合），
# 只动资产池（22 只 5 组）和老仓粘性参数。其余和 v1 一字不差。
# 这样回测 v4 vs v1 差 = 「扩池」的边际效果。
#
# 信号口径：attribute_history 不含当日 → 信号基于 T-1 收盘价，T 日 14:50 下单、按当日收盘价成交。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-01 至今，基准 000300.XSHG，初始资金 100000，频率「天」。
#
# 仅供研究参考，不构成投资建议。

import datetime

import numpy as np
import pandas as pd
from jqdata import *


# ============================ 资产池（5 组 22 只） ============================
POOL = {
    '进攻': [
        '510300.XSHG',   # 沪深300（2012-05）
        '510500.XSHG',   # 中证500（2013-03）
        '159915.XSHE',   # 创业板（2011-12）
        '512100.XSHG',   # 中证1000（2016-11）
    ],
    '行业': [
        '512880.XSHG',   # 证券（2016-07）
        '512010.XSHG',   # 医药（2013-12）
        '512480.XSHG',   # 半导体（2019-06）
        '512660.XSHG',   # 军工（2016-08）
        '515880.XSHG',   # 通信（2019-08）
        '159819.XSHE',   # 人工智能（2021-01）
        '159363.XSHE',   # 创业板AI（2025-07）
        '159227.XSHE',   # 航空航天（2025-06）
        '159928.XSHE',   # 消费（2013-09）
    ],
    '防守型权益': [
        '515080.XSHG',   # 中证红利（2019-08）
        '512890.XSHG',   # 红利低波（2018-08）
        '159201.XSHE',   # 自由现金流（2024-06）
        '513950.XSHG',   # 恒生红利（2023-06）
    ],
    '跨境': [
        '513100.XSHG',   # 纳指100（2013-05）
        '513500.XSHG',   # 标普500（2013-12）
        '510900.XSHG',   # 恒生国企（2012-08）
    ],
    '防守': [
        '518880.XSHG',   # 黄金（2013-07）
        '511010.XSHG',   # 国债（2013-03）
    ],
}

NAME_MAP = {
    '510300.XSHG': '沪深300', '510500.XSHG': '中证500',
    '159915.XSHE': '创业板',   '512100.XSHG': '中证1000',
    '512880.XSHG': '证券',     '512010.XSHG': '医药',
    '512480.XSHG': '半导体',   '512660.XSHG': '军工',
    '515880.XSHG': '通信',     '159819.XSHE': 'AI',
    '159363.XSHE': '创业板AI', '159227.XSHE': '航天',
    '159928.XSHE': '消费',
    '515080.XSHG': '中证红利', '512890.XSHG': '红利低波',
    '159201.XSHE': '中证现金流','513950.XSHG': '恒生红利',
    '513100.XSHG': '纳指',     '513500.XSHG': '标普',
    '510900.XSHG': '港股',
    '518880.XSHG': '黄金',     '511010.XSHG': '国债',
}


# ============================ 初始化 ============================

def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(PriceRelatedSlippage(0.002))
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=0.0002, close_commission=0.0002,
                             close_today_commission=0, min_commission=5), type='fund')
    log.set_level('system', 'error')

    # 名单校验：防止基金更名/退市
    try:
        all_securities = get_all_securities(['etf'], date=context.current_dt.date())
        listed = set(all_securities.index)
    except Exception:
        listed = None
    g.pool = []
    for gname, members in POOL.items():
        for etf in members:
            if listed is None or etf in listed:
                g.pool.append(etf)
    g.name_map = dict(NAME_MAP)

    # ═══════════════ 与 v1 完全一致的信号参数 ═══════════════
    g.regression_window = 27
    g.short_window = 20
    g.vol_window = 60
    g.w_long = 0.6
    g.w_short = 0.3
    g.w_vol = 0.1

    # ═══════════════ 仓位参数（v1 默认 1 只）═══════════════
    g.hold_num = 1             # 持仓数量：v1=1
    g.hold_buffer = 2          # v1 的默认老仓粘性=2（前 buffer+hold_num-1 名内不换仓）

    # ═══════════════ 大盘状态关 ═══════════════
    # v1 的话使用关，只冻 A 股权益；v4 改写为「只关 A股进攻 + A股行业」(跨境不受阻，
    # 防守权益类本来生意即是低波)。
    g.use_market_guard = True
    g.market_index = '000300.XSHG'
    g.market_ma = (20, 60)
    g.guarded_etfs = {
        '510300.XSHG', '510500.XSHG', '159915.XSHE', '512100.XSHG',  # 进攻
        '512880.XSHG', '512010.XSHG', '512480.XSHG', '512660.XSHG',
        '515880.XSHG', '159819.XSHE', '159363.XSHE', '159227.XSHE',
        '159928.XSHE',                                                  # 行业
    }

    run_daily(rebalance, '14:50')


# ============================ 工具函数 ============================

def is_week_end(context):
    today = context.current_dt.date()
    trade_days = get_trade_days(start_date=today,
                                end_date=today + datetime.timedelta(days=10))
    days_after = [d for d in trade_days if d > today]
    if not days_after:
        return True
    nxt = days_after[0]
    return today.isocalendar()[:2] != nxt.isocalendar()[:2]


# ============================ 动量打分 ============================

def calc_momentum_score(etf):
    """v1 公式：主信号 = 年化回归斜率 × R²；辅信号 = mom20 - vol惩罚"""
    df = attribute_history(etf, g.vol_window, '1d', ['close'],
                           fq='pre', skip_paused=True, df=True)
    if df is None or len(df) < g.vol_window:
        return None
    closes = df['close'].values
    if len(closes) < g.short_window + 5:
        return None

    # 主信号：对数价格线性回归 → 年化斜率 × R²
    n = g.regression_window
    seg = closes[-n:]
    log_p = np.log(seg)
    x = np.arange(n)
    slope, intercept = np.polyfit(x, log_p, 1)
    slope_ann = float(np.exp(slope * 250) - 1)
    y_hat = slope * x + intercept
    ss_res = float(np.sum((log_p - y_hat) ** 2))
    ss_tot = float(np.sum((log_p - np.mean(log_p)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    score = slope_ann * r2

    # 辅信号：短动量 + 年化波动率
    mom_short = float(closes[-1] / closes[-g.short_window - 1] - 1)
    rets = np.diff(closes) / closes[:-1]
    vol = float(np.std(rets, ddof=1)) * float(np.sqrt(250))

    return {'code': etf, 'slope_ann': slope_ann, 'r2': r2, 'score': score,
            'mom_short': mom_short, 'vol': vol}


def market_guard_ok(context):
    """大盘状态关：沪深300 收盘 ≥ MA20 或 ≥ MA60（任一站上即通过）。"""
    if not g.use_market_guard:
        return True
    df = attribute_history(g.market_index, g.market_ma[1], '1d', ['close'],
                           fq='pre', skip_paused=True, df=True)
    if df is None or len(df) < g.market_ma[1]:
        return True
    closes = df['close'].values
    last = closes[-1]
    ma20 = closes[-g.market_ma[0]:].mean()
    ma60 = closes[-g.market_ma[1]:].mean()
    return bool(last >= ma20 or last >= ma60)


# ============================ 调仓 ============================

def rebalance(context):
    if not is_week_end(context):
        return

    # --- 1. 全池打分 ---
    rows = []
    for etf in g.pool:
        m = calc_momentum_score(etf)
        if m is not None:
            rows.append(m)
    if not rows:
        log.info('[轮动] 全池数据不足，跳过本轮')
        return
    df = pd.DataFrame(rows).set_index('code')

    # --- 2. Z-Score 横截面标准化 + 合成评分（v1 公式）---
    def zscore(s):
        std = s.std(ddof=1)
        return (s - s.mean()) / std if std > 1e-9 else s * 0.0

    df['final'] = (g.w_long * zscore(df['score'])
                   + g.w_short * zscore(df['mom_short'])
                   - g.w_vol * zscore(df['vol']))
    df = df.sort_values(by='final', ascending=False)

    # --- 3. 闸门1：绝对动量（final>0 且主信号 score>0）---
    qualified = df[(df['final'] > 0) & (df['score'] > 0)]

    # --- 4. 闸门2：大盘状态关（只拦 A股进攻+行业组的新买）---
    market_ok = market_guard_ok(context)
    if g.use_market_guard and not market_ok:
        qualified = qualified[~qualified.index.isin(g.guarded_etfs)]

    # --- 5. 老仓粘性（闸门3：排名容差）---
    current = list(context.portfolio.positions.keys())
    buffer_rank = g.hold_buffer + g.hold_num - 1
    top_buffer = set(df.index[:buffer_rank])
    kept = [c for c in current
            if c in top_buffer and df.loc[c, 'final'] > 0 and df.loc[c, 'score'] > 0]

    # --- 6. 老仓优先，从合格榜补满 hold_num ---
    additions = [c for c in qualified.index if c not in kept]
    additions = additions[:max(g.hold_num - len(kept), 0)]
    target = kept + additions

    # --- 7. record 持仓信号曲线 ---
    curves = {'大盘': 1 if market_ok else 0}
    for etf, row in df.iterrows():
        curves[g.name_map.get(etf, etf[:6])] = round(row['final'], 2)
    record(**curves)

    # --- 8. 执行调仓 ---
    total_value = context.portfolio.total_value
    for etf in list(context.portfolio.positions.keys()):
        if etf not in target:
            order_target(etf, 0)
    if target:
        per_value = total_value / len(target)
        for etf in target:
            order_target_value(etf, per_value)
