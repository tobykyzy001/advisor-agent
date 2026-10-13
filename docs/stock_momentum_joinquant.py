# -*- coding: utf-8 -*-
# A股全市场个股动量轮选 v1（聚宽可回测版）
#
# 方案与证据链见同目录文档《docs/stock_momentum_joinquant.md》。
#
# ⚠️ 诚实声明（2026-10-11）：本代码生成当日聚宽免费时长已尽，全部配置均未经实测，
#    头部刻意不放任何收益数字。跑完回测请把结果回填到方案文档第十节，勿先信后验。
#
# 实测记录一（2026-10-11 用户实测，backtestId=12223aadb5d7ed13eaea92be737517e2）：
#   v1 初版，¥100万，2016-01-04 起——5.5 分钟推进到 2019-08-05 积分耗尽取消（约跑完 1/3）。
#   已推进区间成绩：策略 -42.79% vs 沪深300 -1.48%（严重跑输）。
#   本次暴露两个实现 bug（下方代码已修复，现为 v1.1）：
#   1) 高价股买不进：目标金额 < 100股×现价（600519 类，2016 年约 270 元/股）时
#      order_target_value 反复「开仓数量不能小于 100」失败——而茅台恰是 2016-18
#      全市场最强的大盘动量股之一，系统性缺席把组合进一步推向小票。→ 买不起即顺延。
#   2) 冻结周误用缓冲卖出：原实现冻结期仍淘汰排名跌出缓冲的老仓（持仓曾衰减到
#      仅 5 只），违背「冻结=老仓原样保留」的技能语义 → 已改为冻结周全保留。
#   策略层诊断与后续建议见方案文档第十节：即使排除 bug 与摩擦，2016-2019 全 A
#   个股裸动量 top50 的进攻段 alpha 仍为深度负值（区间是小票三年连跌+动量失效年），
#   该形态在这段时间被证伪——支持尽快进入 v2「行业/景气前置」两段式。
#
# 策略一句话：全 A 清洗池 → 6-1 跳月动量 ÷ 波动 排名 → 趋势/反追高闸只管新买 →
# 大盘状态关（中证全指 mom60≤-5% 冻结新开仓）→ top50 等权（老仓缓冲 75 名）→
# 周频 T-1 收盘信号 / T 日 14:50 成交，纯排名滚动退出、无价格止损。
#
# 与现行 momentum-rotation 技能的差异（每条有据，详见方案文档第二/五节）：
#   1) mom20 不再作正向排名榜——A 股全市场 20 日动量实证为反转因子
#      （东吴金工 Ret20 全 A 负向；Shi-Jiang-Zhou 2015 PLOS 1 个月显著反转），
#      降级为反追高排除闸（沿用现行 偏离MA20≤28% + 近5日≤24%）；
#   2) mom120 → 6-1 跳月动量（skip-month 是动量文献共识性修正）；
#   3) 主信号除以同窗波动（夏普式风险调整，华泰金工样本外参数续效率 7.9%→33.1%）；
#   4) 池子从人工观察仓换成全 A 清洗池（ST/停牌/次新365天/北交所/日均成交额门槛），
#      可历史回测、无幸存者偏差；
#   5) 全市场版补齐：退市保护、涨跌停/一字板不可成交处理、批量取数。
#
# 消融实验（方案文档第七节）：本文件顶部「参数配置区」每个开关对应一组单变量对照——
#   G0: SCORE_MODE='mom120_raw' + 全部过滤关   （现行慢榜原样放大）
#   G1: SCORE_MODE='skip_raw'                  （跳月增量）
#   G2: SCORE_MODE='skip_vol'  ←默认           （波动调整增量）
#   G3: +趋势/反追高开、大盘关 OFF             （个股闸增量）
#   G4: +大盘关 ON = 基线 = 默认               （闸门增量与代价）
#   G5: SCORE_MODE='mom20_raw'、其余同 G4      （mom20 正向方向检验，预期弱）
#   G6: USE_PCT_MODE=True （TOP_PCT=0.10）     （因子十分位形态 vs 可交易形态）
#   G7: LOOKBACK_DAYS=252                      （6-1 vs 12-1）
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-04 ~ 2026-06-30（与仓库 ETF 族 v1–v7 同口径，便于横向对比），
#   基准 000300.XSHG，初始资金 1000000，频率「天」。
#
# 仅供研究参考，不构成投资建议。

import math

import numpy as np
import pandas as pd
from jqdata import *

# ==================== 参数配置区 ★ 消融组只改这里 ====================

# —— 信号 ——
SCORE_MODE_DEF = 'skip_vol'   # 打分口径：'skip_vol' 6-1动量÷波动（默认基线）
                              #           'skip_raw' 纯 6-1 跳月动量
                              #           'mom120_raw' 现行慢榜原样（120 日不跳月）
                              #           'mom20_raw'  快榜原样（20 日，G5 方向检验）
LOOKBACK_DAYS_DEF = 126       # 动量回看窗口（126≈半年；252=12-1 美股经典对照 G7）
SKIP_DAYS_DEF = 21            # 跳过最近 N 个交易日（剔除最近月反转污染）

# —— 组合 ——
TOP_N_DEF = 50                # 窄组合持仓数（可交易形态；默认）
KEEP_RANK_RATIO_DEF = 1.5     # 排名缓冲：老仓在全体打分榜前 ceil(1.5N) 名内保留
USE_PCT_MODE_DEF = False      # True=因子十分位形态：持仓数=ceil(合格数×TOP_PCT)
TOP_PCT_DEF = 0.10            # G6：取前 10%（用户原案的精确实现）
HOLD_CAP_DEF = 300            # pct 模式持仓数上限（再宽就不可交易了）

# —— 闸门（只管新买入；老仓留存只看排名）——
USE_TREND_FILTER_DEF = True   # 趋势关：收盘 ≥ MA120
USE_ANTI_CHASE_DEF = True     # 反追高关：偏离 MA20 ≤ 28% 且 近5日涨幅 ≤ 24%
USE_MARKET_GUARD_DEF = True   # 大盘状态关：中证全指 mom60 ≤ -5% 冻结新开仓（老仓保留）
MARKET_INDEX_DEF = '000985.XSHG'
GUARD_WIN_DEF = 60
GUARD_DOWN_DEF = -0.05

# —— 票池清洗 ——
MIN_LIST_DAYS_DEF = 365       # 上市满 N 自然日（剔除次新）
MIN_AVG_AMOUNT_DEF = 3e7      # 近20日日均成交额下限（3000万；可调 2e7/5e7）
EXCLUDE_KCB_DEF = False       # True=剔除科创板（688）

# —— 执行与成本 ——
MIN_TRADE_PCT_DEF = 0.02      # 权重漂移容忍：偏离目标 < 总资产 2% 不动（压摩擦）
DELIST_GUARD_DAYS_DEF = 30    # 剩 ≤N 自然日退市 → 强制清仓
BATCH_SIZE_DEF = 400          # 全 A 批量取数分批规模

# =====================================================================


def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    # 成本口径（方案文档第六节）：佣金万3双边 + 印花税千1 + 最低5元；滑点 0.2% 按价格比例
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
                             open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5), type='stock')
    set_slippage(PriceRelatedSlippage(0.002), type='stock')
    log.set_level('order', 'error')
    log.set_level('system', 'error')

    g.score_mode = SCORE_MODE_DEF
    g.lookback = LOOKBACK_DAYS_DEF
    g.skip = SKIP_DAYS_DEF
    g.top_n = TOP_N_DEF
    g.keep_rank = int(math.ceil(TOP_N_DEF * KEEP_RANK_RATIO_DEF))
    g.use_pct_mode = USE_PCT_MODE_DEF
    g.top_pct = TOP_PCT_DEF
    g.hold_cap = HOLD_CAP_DEF
    g.use_trend = USE_TREND_FILTER_DEF
    g.use_antichase = USE_ANTI_CHASE_DEF
    g.use_guard = USE_MARKET_GUARD_DEF
    g.market_index = MARKET_INDEX_DEF
    g.guard_win = GUARD_WIN_DEF
    g.guard_down = GUARD_DOWN_DEF
    g.min_list_days = MIN_LIST_DAYS_DEF
    g.min_avg_amount = MIN_AVG_AMOUNT_DEF
    g.exclude_kcb = EXCLUDE_KCB_DEF
    g.min_trade_pct = MIN_TRADE_PCT_DEF
    g.delist_guard_days = DELIST_GUARD_DAYS_DEF
    g.batch_size = BATCH_SIZE_DEF
    # 景气前置挂点（方案文档第九节）：v2 把这里换成景气板块成分股列表（list），
    # 引擎其余部分完全不动，与全市场基线同区间对照。
    g.custom_universe = None
    # 信号所需历史长度：取动量窗口、趋势均线、skip 三者的最大值（+1）
    g.hist_count = max(g.lookback, 120) + 1

    if g.score_mode == 'mom20_raw':
        log.warn('G5 消融：mom20 正向排名。文献口径下 A 股全市场 20 日动量为反转因子，预期显著弱于基线。')
    run_weekly(rebalance, weekday=5, time='14:50')  # 每周第 5 个交易日 14:50
    log.info('动量 v1 初始化：%s | lookback=%d skip=%d | top%d 缓冲%d | pct模式=%s | 闸门 %s%s%s' % (
        g.score_mode, g.lookback, g.skip, g.top_n, g.keep_rank, g.use_pct_mode,
        '趋势' if g.use_trend else '', ' 反追高' if g.use_antichase else '',
        ' 大盘关' if g.use_guard else ''))


# ==================== 票池清洗（每周重建，无幸存者偏差） ====================
def build_universe(context):
    if g.custom_universe:
        return list(g.custom_universe)
    df = get_all_securities(types=['stock'], date=context.current_dt.date())
    cd = get_current_data()
    today = context.current_dt.date()
    ok = []
    for code, row in df.iterrows():
        # 主板/中小板/创业板（+可选科创板）；剔除北交所（30cm 涨跌幅污染动量极值）
        if code.startswith(('4', '8')) or code.endswith('.BJSE'):
            continue
        if g.exclude_kcb and code.startswith('68'):
            continue
        # 次新股：上市未满 365 自然日（社区主流通行阈）
        if (today - row['start_date']).days < g.min_list_days:
            continue
        d = cd[code]
        if d.paused or d.is_st:
            continue
        ok.append(code)
    return ok


# ==================== 动量打分（T-1 收盘口径，前复权） ====================
def score_batch(context, batch):
    """对一批股票算得分与入场闸标记。返回 list[dict]：code/score/entry_ok/avg_amount/close。"""
    hist_c = history(g.hist_count, unit='1d', field='close',
                     security_list=batch, skip_paused=False, fq='pre', df=False)
    hist_m = history(20, unit='1d', field='money',
                     security_list=batch, skip_paused=False, fq=None, df=False)
    out = []
    for code in batch:
        try:
            c = hist_c.get(code)
            if c is None or len(c) < g.hist_count:
                continue
            c = np.asarray(c, dtype=float)
            # 流动性：近 20 日日均成交额
            m = hist_m.get(code)
            if m is None or len(m) < 10:
                continue
            avg_amount = float(np.nanmean(m))
            if not np.isfinite(avg_amount) or avg_amount < g.min_avg_amount:
                continue
            # 锚点：skip 模式需要 a[-1-skip] 与 a[-1-lookback] 都有真实价格
            px_now = c[-1]
            if not np.isfinite(px_now) or px_now <= 0:
                continue
            if g.score_mode == 'mom20_raw':
                base = c[-21]
                if not np.isfinite(base) or base <= 0:
                    continue
                score = px_now / base - 1.0
                vol = np.nan
            elif g.score_mode == 'mom120_raw':
                base = c[-121]
                if not np.isfinite(base) or base <= 0:
                    continue
                score = px_now / base - 1.0
                vol = np.nan
            else:
                a1 = c[-1 - g.skip]       # skip 窗口近端
                a0 = c[-1 - g.lookback]   # 回看窗口远端
                if not (np.isfinite(a1) and np.isfinite(a0)) or a0 <= 0:
                    continue
                mom = a1 / a0 - 1.0       # 跳月动量：不含最近 skip 日
                if g.score_mode == 'skip_raw':
                    score, vol = mom, np.nan
                else:
                    win = c[-1 - g.lookback:-g.skip]  # 同窗日收益波动
                    rets = np.diff(win) / win[:-1]
                    rets = rets[np.isfinite(rets)]
                    if len(rets) < 60:
                        score, vol = mom, np.nan  # 波动算不出 → 退回 raw（标注）
                    else:
                        vol = float(np.std(rets))
                        score = mom / vol if vol > 1e-6 else mom
            # 入场闸（只管新买入）：趋势关 + 反追高关
            entry_ok = True
            if g.use_trend:
                ma120 = np.nanmean(c[-120:])
                if not np.isfinite(ma120) or px_now < ma120:
                    entry_ok = False
            if g.use_antichase and entry_ok:
                ma20 = np.nanmean(c[-20:])
                if np.isfinite(ma20) and ma20 > 0 and px_now / ma20 - 1.0 > 0.28:
                    entry_ok = False
                r5_base = c[-6]
                if np.isfinite(r5_base) and r5_base > 0 and px_now / r5_base - 1.0 > 0.24:
                    entry_ok = False
            out.append({'code': code, 'score': score, 'entry_ok': entry_ok,
                        'avg_amount': avg_amount, 'close': px_now})
        except Exception:
            continue
    return out


# ==================== 大盘状态关 ====================
def market_guard_down(context):
    if not g.use_guard:
        return False
    try:
        idx = attribute_history(g.market_index, g.guard_win + 1, '1d', ['close'],
                                fq=None, skip_paused=True, df=True)
        if idx is None or len(idx) < g.guard_win + 1:
            return False
        mom60 = idx['close'].values[-1] / idx['close'].values[-1 - g.guard_win] - 1.0
        return mom60 <= g.guard_down
    except Exception:
        return False


# ==================== 周频调仓 ====================
def rebalance(context):
    cd = get_current_data()
    today = context.current_dt.date()

    # 0. 退市保护：临近退市的老仓优先强平（卖不出则如实留痕）
    for code in list(context.portfolio.positions.keys()):
        pos = context.portfolio.positions[code]
        if pos.total_amount <= 0:
            continue
        info = get_security_info(code)
        if info and info.end_date and (info.end_date - today).days <= g.delist_guard_days:
            d = cd[code]
            if not d.paused and d.last_price > d.low_limit:
                order_target(code, 0)
                log.info('退市保护清仓 %s（end_date %s）' % (code, info.end_date))
            else:
                log.info('退市保护：%s 临退市但停牌/跌停无法卖出，下周重试' % code)

    # 1. 池子清洗 + 批量打分
    universe = build_universe(context)
    scored = []
    for i in range(0, len(universe), g.batch_size):
        scored.extend(score_batch(context, universe[i:i + g.batch_size]))
    scored.sort(key=lambda x: (-x['score'], x['code']))
    ranked = [x['code'] for x in scored]
    rank_of = {}
    for i, c in enumerate(ranked):
        rank_of[c] = i + 1
    info_map = {x['code']: x for x in scored}

    # 2. 大盘状态关：下行冻结新开仓（老仓保留不清仓，语义同现行技能）
    frozen = market_guard_down(context)

    # 3. 目标持仓数与排名缓冲：pct 模式下缓冲随 N 联动
    if g.use_pct_mode:
        n_target = max(1, min(g.hold_cap, int(math.ceil(len(scored) * g.top_pct))))
        keep_rank = max(g.keep_rank, int(math.ceil(n_target * KEEP_RANK_RATIO_DEF)))
    else:
        n_target = g.top_n
        keep_rank = g.keep_rank

    # 老仓保留：冻结周 = 原样全保留（不新增也不按缓冲淘汰，同现行技能「冻结新增」语义）；
    # 正常周 = 仍在全体打分榜前 keep_rank 名内 → 保留（不受入场闸影响）
    current = [c for c in context.portfolio.positions.keys()
               if context.portfolio.positions[c].total_amount > 0]
    if frozen:
        kept = list(current)
    else:
        kept = [c for c in current if rank_of.get(c, 10 ** 9) <= keep_rank]
    target = list(kept)

    # 4. 补位：大盘未冻结时，按排名顺序补入通过入场闸的股票
    if not frozen:
        for c in ranked:
            if len(target) >= n_target:
                break
            if c in target:
                continue
            if info_map[c]['entry_ok']:
                target.append(c)

    weight = 1.0 / max(1, n_target)

    # 5. 卖出：不在目标中的老仓；跌停/停牌卖不出 → 保留到下周再卖（如实留痕）
    sold, sell_failed = [], []
    for code in current:
        if code in target:
            continue
        info = get_security_info(code)
        if info and info.end_date and (info.end_date - today).days <= g.delist_guard_days:
            continue  # 已在退市保护段处理
        d = cd[code]
        if d.paused:
            sell_failed.append('%s(停牌)' % code[:6])
            continue
        if d.last_price <= d.low_limit:
            sell_failed.append('%s(跌停)' % code[:6])
            continue
        order_target(code, 0)
        sold.append('%s(r%d)' % (code[:6], rank_of.get(code, 0)))

    total_value = context.portfolio.total_value  # 卖出后取总资产，买入口径一致

    # 6. 买入：涨停/一字/停牌买不进 或 单手都买不起（目标金额 < 100 股×现价，
    #    如 600519 类高价股）→ 顺延给排名榜中下一名可买的合格标的
    def buyable(c):
        d = cd[c]
        return (not d.paused) and (not d.is_st) and d.last_price < d.high_limit \
            and d.day_open < d.high_limit

    slot_value = total_value * weight

    def can_open(c):
        return buyable(c) and slot_value >= 100 * cd[c].last_price

    held = set([c for c in context.portfolio.positions.keys()
                if context.portfolio.positions[c].total_amount > 0])
    reserved = set(target)  # 已占用名额，顺延不重复
    bought, buy_skipped = [], []
    for code in target:
        pick = code
        if code in held:
            # 老仓只维护权重：停牌/涨停买不进就本周不动（绝不能顺延再买——
            # 否则等于给已持有票重复占名额，造成超配）
            if not buyable(pick):
                continue
        elif not can_open(pick):
            # 顺延：在全体打分榜 keep_rank 名内找下一名（新买仍需过入场闸 + 买得起）
            pick = None
            for c in ranked:
                if rank_of[c] > keep_rank:
                    break
                if c in reserved or c in held:
                    continue
                if not info_map[c]['entry_ok']:
                    continue
                if can_open(c):
                    pick = c
                    break
            if pick is None:
                buy_skipped.append('%s(不可成交且无顺延)' % code[:6])
                continue
            reserved.add(pick)
        cur_value = 0
        if pick in context.portfolio.positions:
            p = context.portfolio.positions[pick]
            cur_value = p.total_amount * p.price
        if abs(cur_value - total_value * weight) <= total_value * g.min_trade_pct and cur_value > 0:
            continue  # 权重漂移容忍带内，不动
        order_target_value(pick, total_value * weight)
        if pick not in held:
            bought.append('%s(r%d)' % (pick[:6], rank_of.get(pick, 0)))

    # 7. 日志与记录
    top5 = ' '.join('%s:%.3f' % (x['code'][:6], x['score']) for x in scored[:5])
    log.info('%s 池%d 打分%d 目标%d/%d %s | top5 %s' % (
        today, len(universe), len(scored), len(target), n_target,
        '冻结' if frozen else '正常', top5))
    if sold:
        log.info('  卖出 %d: %s' % (len(sold), ' '.join(sold)))
    if bought:
        log.info('  买入 %d: %s' % (len(bought), ' '.join(bought)))
    if sell_failed:
        log.info('  卖不出顺延: %s' % ' '.join(sell_failed))
    if buy_skipped:
        log.info('  买不进: %s' % ' '.join(buy_skipped))
    n_held = sum(1 for p in context.portfolio.positions.values() if p.total_amount > 0)
    record(持仓数=n_held, 冻结=int(frozen), 池内=float(len(scored)))
