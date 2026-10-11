# -*- coding: utf-8 -*-
# ETF 轮动策略（聚宽可回测版）
#
# 策略思路（方案文档见 docs/etf_rotation_joinquant.md）：
#   跨资产 ETF 池（A股成长/价值 + 美股 + 港股 + 黄金 + 国债），
#   主信号 = 年化回归斜率 × R²（趋势速度×质量），辅信号 = mom20 短动量 + 波动率惩罚，
#   三道闸门：绝对动量（分数>0）、大盘状态关（沪深300均线，只冻结中国权益类新增）、
#   排名容差（老仓粘性）。每周最后一个交易日 14:50 调仓，持最强 1 只；全部分数≤0 空仓。
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


# ============================ 初始化 ============================

def initialize(context):
    # --- 回测基础设定 ---
    set_benchmark('000300.XSHG')            # 基准：沪深300
    set_option('use_real_price', True)      # 真实价格（动态复权），跨境/黄金/债券ETF必须开
    set_option('avoid_future_data', True)   # 防未来函数
    set_slippage(PriceRelatedSlippage(0.001))  # 比例滑点：成交价±0.05%（买卖各半）
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=0.0002, close_commission=0.0002,
                             close_today_commission=0, min_commission=5), type='fund')
    log.set_level('system', 'error')

    # --- 资产池（跨资产、低相关；全部 2014 年前上市，支持 2016 年起长回测）---
    g.etf_pool = [
        '159915.XSHE',  # 创业板100（A股成长/科技）
        '510180.XSHG',  # 上证180（A股价值/蓝筹）
        '510900.XSHG',  # 恒生国企ETF（港股/中国资产，上交所）
        '513100.XSHG',  # 纳指100（海外科技/美元资产）
        '518880.XSHG',  # 黄金ETF（商品/避险）
        '511010.XSHG',  # 国债ETF（债券防守）
    ]
    # record 曲线用的中文名
    g.name_map = {
        '159915.XSHE': '创业板', '510180.XSHG': '价值', '510900.XSHG': '港股',
        '513100.XSHG': '纳指', '518880.XSHG': '黄金', '511010.XSHG': '国债',
    }

    # --- 大盘状态关：只约束「中国权益类」的新买入 ---
    # 纳指/黄金/国债与A股大盘状态相关性低，各自的绝对动量就是它们的闸门；
    # 若用A股大盘关一刀切，会在A股走弱时错失黄金（恰是轮动的避险去向）。
    g.guarded_etfs = {'159915.XSHE', '510180.XSHG', '510900.XSHG'}
    g.use_market_guard = True
    g.market_index = '000300.XSHG'   # 大盘代理指数
    g.market_ma = (20, 60)           # 大盘均线组：任一站上即视为可买中国权益

    # --- 动量参数 ---
    g.regression_window = 27   # 回归动量窗口（交易日）
    g.short_window = 20        # 短动量窗口
    g.vol_window = 60          # 波动率窗口
    g.w_long = 0.6             # 主信号权重（年化回归斜率×R²）
    g.w_short = 0.3            # 短动量权重
    g.w_vol = 0.1              # 波动率惩罚权重

    # --- 组合规则 ---
    g.hold_num = 1             # 持仓数量（1=集中轮动；2-3 等权可降回撤）
    g.hold_buffer = 2          # 排名容差：老仓仍在前 (hold_buffer+hold_num-1) 名内不换仓

    # --- 定时任务：每日检查、函数内部只在「本周最后一个交易日」调仓 ---
    run_daily(rebalance, '14:50')


def is_week_end(context):
    """判断今天是否本周最后一个交易日：下一交易日跨周（ISO 周号变化）则为是。"""
    today = context.current_dt.date()
    trade_days = get_trade_days(start_date=today,
                                end_date=today + datetime.timedelta(days=10))
    days_after = [d for d in trade_days if d > today]
    if not days_after:
        return True  # 数据尽头，视为周末
    nxt = days_after[0]
    return today.isocalendar()[:2] != nxt.isocalendar()[:2]


# ============================ 动量打分 ============================

def calc_momentum_score(etf):
    """计算单只 ETF 的三维动量指标。

    返回 dict：slope_ann(年化回归收益)、r2(拟合优度)、score(主信号=slope_ann*r2)、
    mom_short(短动量)、vol(年化波动率)；数据不足返回 None。
    """
    df = attribute_history(etf, g.vol_window, '1d', ['close'], skip_paused=True, df=True)
    if df is None or len(df) < g.vol_window:
        return None
    closes = df['close'].values

    # --- 主信号：对数价格线性回归 → 年化斜率 × R² ---
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

    # --- 辅信号：短动量 + 年化波动率 ---
    mom_short = float(closes[-1] / closes[-g.short_window - 1] - 1)
    rets = np.diff(closes) / closes[:-1]
    vol = float(np.std(rets, ddof=1)) * float(np.sqrt(250))

    return {'code': etf, 'slope_ann': slope_ann, 'r2': r2, 'score': score,
            'mom_short': mom_short, 'vol': vol}


def market_guard_ok(context):
    """大盘状态关：沪深300 收盘 ≥ MA20 或 ≥ MA60（任一站上即通过）。"""
    if not g.use_market_guard:
        return True
    df = attribute_history(g.market_index, g.market_ma[1], '1d', ['close'], skip_paused=True, df=True)
    if df is None or len(df) < g.market_ma[1]:
        return True  # 数据不足不拦截（保守放行）
    closes = df['close'].values
    last = closes[-1]
    ma20 = closes[-g.market_ma[0]:].mean()
    ma60 = closes[-g.market_ma[1]:].mean()
    return bool(last >= ma20 or last >= ma60)


# ============================ 调仓 ============================

def rebalance(context):
    # --- 每周最后一个交易日才调仓，其余日子直接跳过 ---
    if not is_week_end(context):
        return

    # --- 1. 全池打分 ---
    rows = []
    for etf in g.etf_pool:
        m = calc_momentum_score(etf)
        if m is not None:
            rows.append(m)
    if not rows:
        log.info('[轮动] 全池数据不足，跳过本轮')
        return
    df = pd.DataFrame(rows).set_index('code')

    # --- 2. Z-Score 横截面标准化 + 合成评分 ---
    def zscore(s):
        std = s.std(ddof=1)
        return (s - s.mean()) / std if std > 1e-9 else s * 0.0

    df['final'] = (g.w_long * zscore(df['score'])
                   + g.w_short * zscore(df['mom_short'])
                   - g.w_vol * zscore(df['vol']))
    df = df.sort_values(by='final', ascending=False)  # final 降序

    # --- 3. 闸门1：绝对动量（final>0 且主信号 score>0）---
    qualified = df[(df['final'] > 0) & (df['score'] > 0)]

    # --- 4. 闸门2：大盘状态关（只冻结中国权益类的新买入，不强清老仓）---
    market_ok = market_guard_ok(context)
    if g.use_market_guard and not market_ok:
        qualified = qualified[~qualified.index.isin(g.guarded_etfs)]

    # --- 5. 老仓粘性（闸门3：排名容差）---
    # 老仓仍在 final 榜前 (hold_buffer+hold_num-1) 名、且自身 final>0、score>0 → 保留；
    # 大盘关只冻结新增，老仓去留一律由排名缓冲决定（与 momentum-rotation 技能口径一致）。
    current = list(context.portfolio.positions.keys())
    buffer_rank = g.hold_buffer + g.hold_num - 1
    top_buffer = set(df.index[:buffer_rank])
    kept = [c for c in current
            if c in top_buffer and df.loc[c, 'final'] > 0 and df.loc[c, 'score'] > 0]

    # --- 6. 老仓优先，从合格榜补满 hold_num ---
    additions = [c for c in qualified.index if c not in kept]
    additions = additions[:max(g.hold_num - len(kept), 0)]
    target = kept + additions

    # --- 7. record 信号供复盘（大盘开关 + 各标的 final 评分）---
    # 聚宽 record() 同一天只有最后一次调用生效 → 全部曲线合并成一次调用
    curves = {'大盘': 1 if market_ok else 0}
    for etf, row in df.iterrows():
        curves[g.name_map.get(etf, etf[:6])] = round(row['final'], 2)
    record(**curves)

    # --- 8. 执行调仓 ---
    # 8.1 卖出不在目标中的持仓
    for etf in list(context.portfolio.positions.keys()):
        if etf not in target:
            score_str = ('final=%.3f' % df.loc[etf, 'final']) if etf in df.index else '无数据'
            order_target(etf, 0)
            log.info('[轮动] 卖出 %s（%s）' % (etf, score_str))
    # 8.2 等权买入/再平衡目标持仓
    if target:
        per_value = context.portfolio.total_value / len(target)
        for etf in target:
            order_target_value(etf, per_value)
        log.info('[轮动] 目标持仓 %s（等权 %.0f 元/只）' % (str(target), per_value))
    else:
        log.info('[轮动] 无合格标的（绝对动量或大盘关拦截）→ 空仓持币')
