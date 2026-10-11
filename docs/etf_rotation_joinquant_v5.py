# -*- coding: utf-8 -*-
# ETF 轮动策略 v5（聚宽可回测版）—— 双动量 TAA 版 + 实测记录
#
# ★ 2026-10-11 聚宽实测结论（区间 2016-01-01 ~ 2026-06-30，¥100万，天频，与 v1 同口径）★
#
#   | 版本 | 年化 | 夏普 | 最大回撤 | 交易次数 | 判定 |
#   | v1（27日斜率×R²+z混合，hold1，buffer2） | 18.46% | 0.669 | 23.91% | 199 | 基线 |
#   | v5 初版（hold2 50/50，r252 闸门 vs 国债） | 13.02% | 0.516 | 21.03% | 74 | 弱于 v1 |
#   | E1（改 hold1，其余同初版） | 14.89% | 0.501 | 23.86% | 22 | 仍弱 |
#   | E2（E1+合成动量闸门 vs 国债合成） | 13.84% | 0.461 | 32.88% | 28 | 否定 |
#   | E3（E1+MOP 零轨闸门 r252>0）＝本文件现行形态 | 15.94% | 0.559 | 32.92% | 17 | v5 家族最优，仍弱于 v1 |
#   | E4（E3+13612W 快复合信号） | 11.49% | 0.351 | 35.70% | 45 | 鞭打，否定 |
#   | E5（E3+Faber 10月SMA刹车） | ~11.2% | 0.388 | 33.00% | — | 否定 |
#
#   结论：本版（慢速 TAA 家族）所有有名模型变体均未能跑赢 v1。
#   v1 的优势来源经本组对照反证为：① 27 日级快速得分闸门（急跌后 2-4 周内逃出，
#   12 个月尺度的闸门在 2018/2021-22/2024-25 全部太慢）；② 国债/黄金在池内按同一
#   快速窗口参与排名竞争（熊市自然接管），而非"防守腿+慢闸门"的两段式结构；
#   ③ 更快的复合信号（13612W）与破位刹车（10月SMA）在 A 股"急跌-V反"结构下全部
#   死于鞭打。详见 docs/etf_rotation_joinquant.md 第十一节实验记录。
#   本文件保留为「慢速 TAA 家族最优形态」存档（E3），供后续对照；生产建议用
#   docs/etf_rotation_joinquant_v6.py（v1 订正版）。
#
# 设计（全部规则有出处，未做网格调参）：
#   1) 相对动量（选谁）：score = mean(r_63d, r_126d, r_252d)，3/6/12 月原始收益等权
#      （窗口=动量学界定见区；多期限等权=Hurst-Ooi-Pedersen 2017 期限分散）。
#   2) 绝对动量（买不买）：MOP(2012) 形式 r_252 > 0；不合格槽位 → 国债兜底
#      （GEM 防守腿）。E2 实测否决了"门槛=国债同期收益"：股灾时国债恰在高点，
#      门槛最高，把策略锁死在债券里错过反弹起点。
#   3) 持仓 1 只（GEM/ADM 形式；v5 初版 hold2 摊薄实测 −1.9pp/年）。
#   4) 老仓粘性：仍为前一半（5 取 3）且闸门未破 → 不换（Clenow 排名缓冲思想）。
#   5) 每周最后交易日 14:50 调仓（与 v1 同节奏，隔离变量；学术默认月频留作 A/B）。
#
# 信号口径：attribute_history 不含当日 → 信号基于 T-1 收盘价，T 日 14:50 下单、按当日收盘价成交。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-01 至今，基准 000300.XSHG，初始资金 1000000，频率「天」。
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
    set_option('use_real_price', True)      # 真实价格（动态复权）
    set_option('avoid_future_data', True)   # 防未来函数
    set_slippage(PriceRelatedSlippage(0.002))  # 单边 0.10% 滑点（保守口径）
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=0.0002, close_commission=0.0002,
                             close_today_commission=0, min_commission=5), type='fund')
    log.set_level('system', 'error')

    # --- 候选池：v1 原班 5 只风险资产（跨资产、低相关；全部 2014 年前上市）---
    g.candidates = [
        '159915.XSHE',  # 创业板100（A股成长）
        '510180.XSHG',  # 上证180（A股价值/蓝筹）
        '510900.XSHG',  # 恒生国企ETF（港股/中国资产）
        '513100.XSHG',  # 纳指100（海外科技/美元资产）
        '518880.XSHG',  # 黄金ETF（商品/避险）
    ]
    g.defensive = '511010.XSHG'  # 国债ETF：防守腿/现金替代（不排名、不作门槛基准）

    g.name_map = {
        '159915.XSHE': '创业板', '510180.XSHG': '价值', '510900.XSHG': '港股',
        '513100.XSHG': '纳指', '518880.XSHG': '黄金', '511010.XSHG': '国债',
    }

    # --- 信号参数（全部有出处，见文件头注释；不构成调参）---
    g.lookbacks = (63, 126, 252)  # 3/6/12 个月（交易日），相对动量多期限等权
    g.hold_num = 1                # 持仓数：E1 实测 hold1 > hold2（−1.9pp/年摊薄否定）
    g.weight_band = 0.05          # 权重漂移容忍 ±5pp：执行层节费容忍带，非信号参数

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


# ============================ 动量计算 ============================

def get_closes(etf, n):
    """取复权收盘价序列；数据不足返回 None（前复权避免分红拆分污染动量）。"""
    df = attribute_history(etf, n, '1d', ['close'], fq='pre', skip_paused=True, df=True)
    if df is None or len(df) < n:
        return None
    return df['close'].values


def calc_score(etf):
    """单只候选的动量分：3/6/12 个月原始区间收益的等权平均 + 12 个月收益（闸门用）。

    返回 dict：score(合成动量分)、ret_252(绝对动量闸门用的 12 个月收益)；
    数据不足返回 None。
    """
    n = max(g.lookbacks) + 1
    closes = get_closes(etf, n)
    if closes is None:
        return None
    rets = {}
    for lb in g.lookbacks:
        rets[lb] = float(closes[-1] / closes[-1 - lb] - 1.0)
    score = (rets[63] + rets[126] + rets[252]) / 3.0
    return {'score': score, 'ret_252': rets[252]}


# ============================ 调仓 ============================

def rebalance(context):
    # --- 每周最后一个交易日才调仓，其余日子直接跳过 ---
    if not is_week_end(context):
        return

    # --- 1. 候选池打分 ---
    info = {}
    for etf in g.candidates:
        m = calc_score(etf)
        if m is not None:
            info[etf] = m
    if not info:
        log.info('[轮动] 候选池数据不足，跳过本轮')
        return

    # --- 2. 闸门：MOP 零轨（r_252 > 0 才可持有；防守腿不作门槛基准，E2 已否决）---
    gate = 0.0

    # --- 3. 排名：按 score 降序（相对动量决定"持有哪个"）---
    ranked = sorted(list(info.keys()), key=lambda e: info[e]['score'], reverse=True)
    rank_of = {}
    for i, e in enumerate(ranked):
        rank_of[e] = i + 1
    keep_rank = (len(g.candidates) + 1) // 2  # 前一半：5 只候选 → 前 3

    # --- 4. 老仓粘性（先判保留）：老仓仍在前一半且闸门未破 → 不换 ---
    current = [e for e in context.portfolio.positions.keys()
               if context.portfolio.positions[e].total_amount > 0]
    kept = []
    for e in current:
        if e == g.defensive:
            continue  # 防守腿是占位，不参与粘性判断
        if e in rank_of and rank_of[e] <= keep_rank and info[e]['ret_252'] > gate:
            kept.append(e)

    # --- 5. 按名次补满 hold_num：老仓优先，其后从合格候选依次递补 ---
    target = list(kept)
    for e in ranked:
        if len(target) >= g.hold_num:
            break
        if e in target:
            continue
        if info[e]['ret_252'] > gate:  # 闸门：MOP 零轨绝对动量
            target.append(e)

    # --- 6. 空出的槽位由防守腿按份兜底（GEM：股票不赢现金等价物就持防守腿）---
    weights = {}
    for e in target:
        weights[e] = weights.get(e, 0.0) + 1.0 / g.hold_num
    n_bond = g.hold_num - len(target)
    if n_bond > 0:
        weights[g.defensive] = weights.get(g.defensive, 0.0) + n_bond / g.hold_num

    # --- 7. record 信号供复盘（候选得分）---
    curves = {}
    for e in ranked:
        curves[g.name_map.get(e, e[:6])] = round(info[e]['score'] * 100, 2)
    if curves:
        record(**curves)

    log.info('[%s 轮动] 排名=%s | 目标=%s' % (
        context.current_dt.date(),
        ' > '.join('%s:%+.1f%%' % (g.name_map.get(e, e[:6]), info[e]['score'] * 100)
                   for e in ranked),
        ' / '.join('%s:%.0f%%' % (g.name_map.get(e, e[:6]), w * 100)
                   for e, w in weights.items()) or '空仓'))

    # --- 8. 执行调仓 ---
    total_value = context.portfolio.total_value
    # 8.1 卖出不在目标中的持仓
    for etf in list(context.portfolio.positions.keys()):
        if etf not in weights:
            order_target(etf, 0)
    # 8.2 买入/权重维护：漂移在 ±weight_band 内的持仓不动，省摩擦成本
    for etf, w in weights.items():
        target_value = total_value * w
        if etf in context.portfolio.positions:
            pos = context.portfolio.positions[etf]
            cur_value = pos.total_amount * pos.price
        else:
            cur_value = 0
        if total_value > 0 and abs(cur_value - target_value) / total_value < g.weight_band:
            continue  # 权重漂移在容忍带内，跳过
        order_target_value(etf, target_value)
