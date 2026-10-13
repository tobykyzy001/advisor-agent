# -*- coding: utf-8 -*-
# A股个股动量轮选 v3（聚宽可回测版）—— 行业前置两段式 + 仓位阀
#
# 方案与证据链见《docs/stock_momentum_joinquant.md》第十节 10.4（诊断与动机）。
#
# ⚠️ 诚实声明（2026-10-11）：生成当日未经实测。既有实测链：
#   v1.1 全 A 裸动量：十年 ≈ −63%（证伪，10.3）；v2 行业前置月选：全程 −10.3% vs 基准
#   +33.5%（IR −0.174，前置层真实有效但救不回满仓 beta，10.4）。v2 诊断结论：
#   「收益 = 行业动量超额的牛市段 − 满仓 beta 的熊市段」，最大死因是**没有仓位阀**
#   （冻结语义=只冻新买不减仓，2018 年与基准 −26.3% 逐点同步满仓扛熊）。
#
# v3 相对 v2 的两处改动（其余全部不变，含两个 A/B 退回开关）：
#   A. 冻结减仓（仓位阀）：大盘状态关触发的冻结周，目标仓位降至 DEFENSE_RATIO（默认 30%），
#      解冻当周按正常逻辑满仓补回——替代 v2 的「冻结=原样满仓扛」。2018/2022–2023
#      这类系统年预期各减亏 15–25pp；代价是反弹初期最长一周的踏空窗口
#      （Daniel-Moskowitz 2016 提示的动量崩溃段风险，代价低于满仓）。DEFENSE_RATIO=1.0
#      退回 v2 语义做对照。
#   B. 行业绝对动量闸：入选行业聚合分必须 > IND_MIN_SCORE（默认 0.0），不足 K 个则
#      少入选、仓位自动收缩（池子为空 → 老仓按正常排名规则整体轮出，不是 fail-open
#      保留——这正是熊市自动降仓的来源）。搬移仓库 ETF 族 v5/E3 实测验证过的
#      「得分>0 才持有」组件。IND_MIN_SCORE=None 退回纯相对排名（v2 行为）。
#
# 「行业池为空」的两种成因分开处理（v2 合并处理是 bug）：
#   取数失败（get_industry 异常）→ fail-closed，仅保留老仓、不开新仓、不清仓；
#   闸 B 过滤为空（数据正常、无行业得分>0）→ 真信号，老仓按排名规则轮出降仓。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-01 ~ 2026-06-30、基准 000300.XSHG、初始资金 100000000、频率「天」，
#   与 v2（10.4）同口径对照。
#
# 仅供研究参考，不构成投资建议。

import math

import numpy as np
import pandas as pd
from jqdata import *

# ==================== 参数配置区 ★ 消融组只改这里 ====================

# —— 行业前置（v2 核心开关）——
USE_INDUSTRY_FILTER_DEF = True   # False = 退化为 v1.1 全 A 基线（对照复核对用）
IND_TOP_K_DEF = 4                # 持有动量 top-K 个申万一级行业（文档口径 3~5）
IND_AGG_DEF = 'median'           # 行业得分聚合：'median'(默认) / 'mean'
IND_MIN_MEMBERS_DEF = 5          # 行业内有效得分股票数下限，不足不参与行业排名
IND_FREQ_DEF = 'monthly'         # 行业集刷新频率：'monthly'(默认,每月首周刷新,月内不动)
                                 #                 /'weekly'(每周重排,可作 A/B)
IND_MIN_SCORE_DEF = 0.0          # 行业绝对动量闸：聚合分 > 阈值才入选，不足 K 个则仓位
                                 # 自动收缩（None=关闭退回 v2 纯相对排名）

# —— 信号（与 v1.1 完全一致）——
SCORE_MODE_DEF = 'skip_vol'   # 'skip_vol' 6-1动量÷波动（默认基线；'skip_raw'/'mom120_raw'/'mom20_raw' 为消融对照）
LOOKBACK_DAYS_DEF = 126
SKIP_DAYS_DEF = 21

# —— 组合 ——
TOP_N_DEF = 50
KEEP_RANK_RATIO_DEF = 1.5
USE_PCT_MODE_DEF = False
TOP_PCT_DEF = 0.10
HOLD_CAP_DEF = 300

# —— 闸门（只管新买入；老仓留存只看排名）——
USE_TREND_FILTER_DEF = True
USE_ANTI_CHASE_DEF = True
USE_MARKET_GUARD_DEF = True
MARKET_INDEX_DEF = '000985.XSHG'
GUARD_WIN_DEF = 60
GUARD_DOWN_DEF = -0.05
DEFENSE_RATIO_DEF = 0.3          # 冻结减仓（仓位阀）：冻结周目标仓位降到该比例，
                                 # 解冻当周满仓补回（1.0=退回 v2「冻结=满仓扛」语义）

# —— 票池清洗 ——
MIN_LIST_DAYS_DEF = 365
MIN_AVG_AMOUNT_DEF = 3e7
EXCLUDE_KCB_DEF = False

# —— 执行与成本 ——
MIN_TRADE_PCT_DEF = 0.02
DELIST_GUARD_DAYS_DEF = 30
BATCH_SIZE_DEF = 400

# =====================================================================


def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
                             open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5), type='stock')
    set_slippage(PriceRelatedSlippage(0.002), type='stock')
    log.set_level('order', 'error')
    log.set_level('system', 'error')

    g.use_industry_filter = USE_INDUSTRY_FILTER_DEF
    g.ind_top_k = IND_TOP_K_DEF
    g.ind_agg = IND_AGG_DEF
    g.ind_min_members = IND_MIN_MEMBERS_DEF
    g.ind_freq = IND_FREQ_DEF       # 行业集刷新频率：monthly=每月首周刷新 / weekly=每周重排
    g.ind_month = None              # 本月行业集水印（'YYYY-MM'），月内不重复重排
    g.ind_min_score = IND_MIN_SCORE_DEF   # 行业绝对动量闸（None=关闭）
    g.defense_ratio = DEFENSE_RATIO_DEF   # 冻结减仓比例（1.0=不减仓，退回 v2 语义）
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
    g.last_top_inds = []          # 行业取数失败时的存续行业集（274 语义：不静默改池）
    g.hist_count = max(g.lookback, 120) + 1

    run_weekly(rebalance, weekday=5, time='14:50')  # 每周第 5 个交易日 14:50
    log.info('动量 v3 初始化：行业前置=%s top%d(%s) 闸%s | 防守仓位 %.0f%% | %s %d/%d | top%d 缓冲%d' % (
        g.use_industry_filter, g.ind_top_k, g.ind_agg,
        str(g.ind_min_score), g.defense_ratio * 100, g.score_mode,
        g.lookback, g.skip, g.top_n, g.keep_rank))


# ==================== 票池清洗（每周重建，无幸存者偏差） ====================
def build_universe(context):
    df = get_all_securities(types=['stock'], date=context.current_dt.date())
    cd = get_current_data()
    today = context.current_dt.date()
    ok = []
    for code, row in df.iterrows():
        if code.startswith(('4', '8')) or code.endswith('.BJSE'):
            continue
        if g.exclude_kcb and code.startswith('68'):
            continue
        if (today - row['start_date']).days < g.min_list_days:
            continue
        d = cd[code]
        if d.paused or d.is_st:
            continue
        ok.append(code)
    return ok


# ==================== 动量打分（T-1 收盘口径，前复权；与 v1.1 一致） ====================
def score_batch(context, batch):
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
            m = hist_m.get(code)
            if m is None or len(m) < 10:
                continue
            avg_amount = float(np.nanmean(m))
            if not np.isfinite(avg_amount) or avg_amount < g.min_avg_amount:
                continue
            px_now = c[-1]
            if not np.isfinite(px_now) or px_now <= 0:
                continue
            if g.score_mode == 'mom20_raw':
                base = c[-21]
                if not np.isfinite(base) or base <= 0:
                    continue
                score = px_now / base - 1.0
            elif g.score_mode == 'mom120_raw':
                base = c[-121]
                if not np.isfinite(base) or base <= 0:
                    continue
                score = px_now / base - 1.0
            else:
                a1 = c[-1 - g.skip]
                a0 = c[-1 - g.lookback]
                if not (np.isfinite(a1) and np.isfinite(a0)) or a0 <= 0:
                    continue
                mom = a1 / a0 - 1.0
                if g.score_mode == 'skip_raw':
                    score = mom
                else:
                    win = c[-1 - g.lookback:-g.skip]
                    rets = np.diff(win) / win[:-1]
                    rets = rets[np.isfinite(rets)]
                    if len(rets) < 60:
                        score = mom
                    else:
                        vol = float(np.std(rets))
                        score = mom / vol if vol > 1e-6 else mom
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
                        'avg_amount': avg_amount, 'close': px_now,
                        'ind_code': '', 'ind_name': ''})
        except Exception:
            continue
    return out


# ==================== 行业前置：得分按申万一级聚合 → top-K 行业 ====================
def annotate_industry(context, scored):
    """给每条打分记录标注申万一级行业（每周都要：行业集固定、成分归属随个股变化）。

    返回 True/False；失败不改动记录（ind_code 保持空串），由调用方兜底。
    """
    try:
        ind_map = get_industry([x['code'] for x in scored], date=context.current_dt.date())
    except Exception as e:
        log.info('get_industry 取数失败：%s' % e)
        return False
    for x in scored:
        sw = ind_map.get(x['code'], {}).get('sw_l1', {}) or {}
        x['ind_code'], x['ind_name'] = sw.get('industry_code', ''), sw.get('industry_name', '')
    return True


def rank_industries(scored):
    """按行业内得分聚合排名，返回 top-K 行业 [(ind_code, ind_name, agg, n_members)]，降序。

    绝对动量闸（B）：ind_min_score 设定时，只有聚合分 > 阈值才具备入选资格——
    不足 K 个则少入选（仓位自动收缩），一个都不够则返回空表（深熊自动降仓信号）。
    """
    groups = {}
    for x in scored:
        if x['ind_code']:
            groups.setdefault(x['ind_code'], {'name': x['ind_name'], 'scores': []})['scores'].append(x['score'])
    ranked_inds = []
    for ic, ginfo in groups.items():
        if len(ginfo['scores']) < g.ind_min_members:
            continue
        arr = np.asarray(ginfo['scores'], dtype=float)
        agg = float(np.median(arr)) if g.ind_agg == 'median' else float(np.mean(arr))
        ranked_inds.append((ic, ginfo['name'], agg, len(arr)))
    ranked_inds.sort(key=lambda t: (-t[2], t[0]))
    qualified = ranked_inds
    if g.ind_min_score is not None:
        qualified = [t for t in ranked_inds if t[2] > g.ind_min_score]
        log.info('  行业绝对动量闸(agg>%.2f)：%d/%d 行业过关' % (
            g.ind_min_score, len(qualified), len(ranked_inds)))
    top = qualified[:g.ind_top_k]
    for ic, iname, agg, n in top:
        log.info('  行业入选 %s %s agg=%.3f (n=%d)' % (ic, iname, agg, n))
    return top


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

    # 1. 全 A 清洗 + 批量打分（行业聚合与个股排名共用这份产出）
    universe = build_universe(context)
    scored = []
    for i in range(0, len(universe), g.batch_size):
        scored.extend(score_batch(context, universe[i:i + g.batch_size]))

    # 1b. 行业前置：行业集按月刷新（每月首周重排，月内不动；个股行业归属每周仍然新取）。
    # 「行业池为空」两种成因分开：取数失败 → fail-closed 保留老仓；
    # 闸 B 过滤为空（真信号）→ 老仓按正常排名规则轮出、自动降仓。
    if g.use_industry_filter:
        ind_fail = False
        ok = annotate_industry(context, scored)
        if ok:
            this_month = today.strftime('%Y-%m')
            need_refresh = (g.ind_freq == 'weekly' or g.ind_month != this_month
                            or g.ind_month is None)
            if need_refresh:
                top_inds = rank_industries(scored)
                # 刷新结果照常落档——哪怕闸 B 过滤后不足 K 甚至为空（空仓信号），
                # 不许拿着上月的行业集混过系统性下跌
                g.last_top_inds = top_inds
                g.ind_month = this_month
                log.info('  %s 行业集刷新（%s）：%s' % (
                    this_month, g.ind_freq,
                    ' '.join('%s %.3f' % (t[1], t[2]) for t in top_inds) or '（无过关行业）'))
            else:
                top_inds = g.last_top_inds
        else:
            ind_fail = True
            top_inds = g.last_top_inds
            log.info('  行业分类取数失败，沿用存续行业集：%s' % [t[1] for t in top_inds])
        indset = {t[0] for t in top_inds}
        pool = [x for x in scored if x['ind_code'] in indset]
        if not pool:
            log.info('  ⚠️ 行业池为空（%s）：%s' % (
                '行业分类取数失败' if ind_fail else '绝对动量闸：无行业过关',
                '仅保留老仓、不开新仓、不清仓' if ind_fail else '老仓按正常排名规则轮出、自动降仓'))
    else:
        ind_fail, top_inds, pool = False, [], scored

    pool.sort(key=lambda x: (-x['score'], x['code']))
    pool_ranked = [x['code'] for x in pool]
    prank_of = {}
    for i, c in enumerate(pool_ranked):
        prank_of[c] = i + 1
    info_map = {x['code']: x for x in scored}

    # 2. 大盘状态关：下行冻结新开仓（老仓原样全保留）
    frozen = market_guard_down(context)

    # 3. 目标持仓数与排名缓冲：pct 模式下缓冲随 N 联动
    if g.use_pct_mode:
        n_target = max(1, min(g.hold_cap, int(math.ceil(len(pool) * g.top_pct))))
        keep_rank = max(g.keep_rank, int(math.ceil(n_target * KEEP_RANK_RATIO_DEF)))
    else:
        n_target = g.top_n
        keep_rank = g.keep_rank

    # 老仓保留：冻结周 / 行业数据故障 = 原样全保留（冻结周仓位由防守比例降为 30%，见第 6 步）；
    # 正常周 = 仍在池内排名前 keep_rank 名内 → 保留
    # （行业跌出 top-K 或闸 B 空仓的老仓不在池内 → 视为排名无穷大 → 轮出，这就是行业换仓/降仓）
    current = [c for c in context.portfolio.positions.keys()
               if context.portfolio.positions[c].total_amount > 0]
    if frozen or (g.use_industry_filter and ind_fail and not pool):
        kept = list(current)
    else:
        kept = [c for c in current if prank_of.get(c, 10 ** 9) <= keep_rank]
    target = list(kept)

    # 4. 补位：未冻结且池非空时，按池内排名补入过入场闸的股票
    if not frozen and pool:
        for c in pool_ranked:
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
            continue
        d = cd[code]
        if code not in info_map:
            tag = '%s(掉出打分池)' % code[:6]
        elif code not in prank_of:
            tag = '%s(行业轮出)' % code[:6]
        else:
            tag = '%s(r%d)' % (code[:6], prank_of[code])
        if d.paused:
            sell_failed.append('%s(停牌)' % code[:6])
            continue
        if d.last_price <= d.low_limit:
            sell_failed.append('%s(跌停)' % code[:6])
            continue
        order_target(code, 0)
        sold.append(tag)

    total_value = context.portfolio.total_value

    # 6. 买入：涨停/一字/停牌买不进 或 单手都买不起 → 顺延给池内排名下一名
    def buyable(c):
        d = cd[c]
        return (not d.paused) and (not d.is_st) and d.last_price < d.high_limit \
            and d.day_open < d.high_limit

    # 仓位阀：冻结周目标仓位降到 defense_ratio（减仓经买入段的 order_target_value 向下调整实现，
    # 解冻当周 defense 恢复 1.0 按正常逻辑满仓补回）；正常周 = 全额单票权重
    slot_value = total_value * weight * (g.defense_ratio if frozen else 1.0)

    def can_open(c):
        return buyable(c) and slot_value >= 100 * cd[c].last_price

    held = set([c for c in context.portfolio.positions.keys()
                if context.portfolio.positions[c].total_amount > 0])
    reserved = set(target)
    bought, buy_skipped, trimmed = [], [], []
    # 冻结态切换周（正常↔冻结）强制重平衡到目标仓位，绕过漂移容忍带——
    # 否则单票 0.7×2% 的减/补仓量落在 2% 带内，仓位阀永远不动（v3 自审 bug）
    regime_changed = frozen != getattr(g, 'last_frozen', False)
    for code in target:
        pick = code
        if code in held:
            # 老仓：防守减仓 / 权重维护；停牌/跌停无法成交就本周不动（绝不顺延再买防超配）
            d = cd[pick]
            if d.paused or d.last_price <= d.low_limit:
                continue
            if not frozen and not buyable(pick):
                continue
        elif not frozen and not can_open(pick):
            pick = None
            for c in pool_ranked:
                if prank_of[c] > keep_rank:
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
        elif frozen:
            continue  # 冻结周不开新仓
        cur_value = 0
        if pick in context.portfolio.positions:
            p = context.portfolio.positions[pick]
            cur_value = p.total_amount * p.price
        if (not regime_changed and cur_value > 0
                and abs(cur_value - slot_value) <= total_value * g.min_trade_pct):
            continue
        order_target_value(pick, slot_value)
        if pick in held and slot_value < cur_value:
            trimmed.append(pick[:6])
        elif pick not in held:
            bought.append('%s(r%d)' % (pick[:6], prank_of.get(pick, 0)))

    # 7. 日志与记录
    ind_txt = ' '.join('%s' % t[1] for t in top_inds) if top_inds else ('全A' if not g.use_industry_filter else '空')
    top5 = ' '.join('%s:%.3f' % (x['code'][:6], x['score']) for x in pool[:5])
    status = '冻结→仓位%.0f%%' % (g.defense_ratio * 100) if frozen else '正常'
    log.info('%s 行业[%s] 池%d(全A%d) 目标%d/%d %s | top5 %s' % (
        today, ind_txt, len(pool), len(scored), len(target), n_target, status, top5))
    if trimmed:
        log.info('  防守减仓 %d 只至 %.0f%% 仓' % (len(trimmed), g.defense_ratio * 100))
    if sold:
        log.info('  卖出 %d: %s' % (len(sold), ' '.join(sold)))
    if bought:
        log.info('  买入 %d: %s' % (len(bought), ' '.join(bought)))
    if sell_failed:
        log.info('  卖不出顺延: %s' % ' '.join(sell_failed))
    if buy_skipped:
        log.info('  买不进: %s' % ' '.join(buy_skipped))
    n_held = sum(1 for p in context.portfolio.positions.values() if p.total_amount > 0)
    invested = sum(p.total_amount * p.price for p in context.portfolio.positions.values()
                   if p.total_amount > 0)
    record(持仓数=n_held, 仓位比=float(invested / max(1.0, total_value)),
           冻结=int(frozen), 池内=float(len(pool)))
    g.last_frozen = frozen
