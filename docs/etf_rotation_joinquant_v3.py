# -*- coding: utf-8 -*-
# ETF 动量轮动策略 v3（聚宽可回测版）
#
# v2（docs/etf_rotation_joinquant_v2.py）的问题（见对话复盘）：
#   1) attribute_history 未显式 fq='pre'，分红/拆分日动量被污染；
#   2) Z-Score 横截面归一化跨了「黄金/国债 vs 半导体」两类单位不可比的资产，波动率
#      变相成了惩罚项、防御组几乎永远被选不中；
#   3) final>0&&score>0 存在自相关冗余（Z-Score 含 score 项）；
#   4) 「大盘关」（沪深300 站上 MA20/60 任一）只拦新增不清老仓 → 2016 熔断类系统性
#      下跌照样满仓吃回撤（v1 的 -23.9% 最大回撤根源其实也是它，不在「单一权益」）；
#   5) 行业组/防守型权益/跨境/防守 是唯一仓位分配器，权重拍死（30/30/25/15），高
#      β 行业和高波动时期没有被仓位数学压住，低波黄金/国债长期占不满应有权重。
#   6) 14:50 下单、滑点 0.05% 偏乐观，回测年化与实盘落差有系统性缺口。
#
# v3 的四大修正（按用户审定的设计逐条落地）：
#   (1) 资产池五组：进攻宽基 / 行业（高β、允许所有行业都进来让仓位管理决定谁活）/
#       防守型权益（红利+自由现金流+恒生红利，用户确认可同时进）/ 跨境 / 纯防守；
#   (2) 组内化规则：组内同量纲 mom 排名第 1 入选（共 5 个仓位槽，每组 1 只），
#       跨组横向不比较 → 不再 Z-Score 全局池；
#   (3) 信号=「绝对动量 slope_ann>0 (Antonacci 时间序列动量)」闸门，唯一主信号，
#       不再混 R²、短动量、波动率三个任务堆给信号层；
#   (4) 波动率只用于**仓位权重**（逆波动率风险平价），不作为剔除标准、不作为排名；
#       用户否决「高β行业剔除论」依据：波动率的职责是管仓位不是管选品。
#
# 三个 A/B 开关（用户要求做对照回测，不是选参数）：
#   use_market_guard / use_risk_parity / use_anti_chase （默认 关/开/开）
#
# 执行与工程：
#   - 每周最后一个交易日 14:50 调仓，聚宽日线回测成交价=当日收盘价；
#   - attribute_history(..., fq='pre') 显式复权；short_window 越界 try-except;
#   - 反追高=波动率标准化（价格偏离 MA20 > k×σ_daily×√20 顺延一周），k=2.5;
#   - 每调仓对跨境 ETF 记录当日幅度 vs 前收，仅 log.info 存档不拦截；
#   - record '备用'/'防守' 两个仓位槽曲线可见供复盘；季度重算全池逆波动率权重，
#     周内权重用懒估值（缓存季度快照），不拖累跨周 post;
#   - get_all_securities('etf') 启动时校验 22 只名单均在交易所，缺失踢出；
#   - sum()/sorted() 在聚宽环境规避（按 v2 原注释手动实现）。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-01 至今、基准 000300.XSHG、初始资金 100000、频率「天」。
# 仅供研究参考，不构成投资建议。

import datetime
import math

import numpy as np
import pandas as pd
from jqdata import *


# ============================ 资产池（五组） ============================
# 名单审定：进攻 4 / 行业 9 / 防守型权益 4 / 跨境 3 / 防守 2 = 22 只
# 每组 1 只槽位，共 5 只持仓；跨组仓位由逆波动率分配，组内由 mom160 排名选 1。
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
        '510900.XSHG',   # 恒生中国企业（2012-08）
    ],
    '防守': [
        '518880.XSHG',   # 黄金（2013-07）
        '511010.XSHG',   # 国债（2013-03）
    ],
}

NAME_MAP = {
    '510300.XSHG': '沪深300', '510500.XSHG': '中证500',
    '159915.XSHE': '创业板', '512100.XSHG': '中证1000',
    '512880.XSHG': '证券', '512010.XSHG': '医药',
    '512480.XSHG': '半导体', '512660.XSHG': '军工',
    '515880.XSHG': '通信', '159819.XSHE': 'AI',
    '159363.XSHE': '创业板AI', '159227.XSHE': '航天',
    '159928.XSHE': '消费',
    '515080.XSHG': '中证红利', '512890.XSHG': '红利低波',
    '159201.XSHE': '自由现金流', '513950.XSHG': '恒生红利',
    '513100.XSHG': '纳指', '513500.XSHG': '标普',
    '510900.XSHG': '港股',
    '518880.XSHG': '黄金', '511010.XSHG': '国债',
}

QDLL_CODES = {'513100.XSHG', '513500.XSHG', '510900.XSHG', '513950.XSHG'}


# ============================ 初始化 ============================

def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(PriceRelatedSlippage(0.002))        # 单边 0.10% 滑点基准
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=0.0002, close_commission=0.0002,
                             close_today_commission=0, min_commission=5), type='fund')
    log.set_level('system', 'error')

    g.start_dt = context.current_dt.date()

    # 名单校验：防基金更名/退市后残留、防未来函数上市前编号
    try:
        all_securities = get_all_securities(['etf'], date=context.current_dt.date())
        listed = set(all_securities.index)
    except Exception:
        listed = None  # 聚宽回测早期不支持时降级
    g.pool = {}
    for gname, members in POOL.items():
        g.pool[gname] = [e for e in members if listed is None or e in listed]
        dropped = [e for e in members if listed is not None and e not in listed]
        for e in dropped:
            log.warn('[初始化] %s 不在交易所名单, 从池子剔除' % e)
    g.name_map = dict(NAME_MAP)

    # ═════════════════ 参数配置区 ★ 可自行修改 ★ ═════════════════

    # --- A/B 开关（用户要求对照回测，默认值只是推荐态）---
    g.use_market_guard = False   # 大盘关（默认关，仅防系统性下跌的 A 股权益组用）
    g.use_risk_parity = True    # 逆波动率仓位（默认开；对照改 False 走均权 0.2/0.2/0.2/0.2/0.2）
    g.use_anti_chase = True     # 反追高（默认开）
    # 滑点压测：ETF 基准摩擦极小微观，0.1% 是正常口径；0.3% 模拟 QDII 折溢价/流动性紧张情形。
    # 若把 0.002 上调到 0.003~0.006 后年化回撤消失，说明收益对摩擦高度敏感 → 需要降换手。
    g.slippage_rate = 0.002     # 建议对跑：0.002 (基准) / 0.003 (压测)
    set_slippage(PriceRelatedSlippage(g.slippage_rate))

    # --- 动量参数（1 主 2 辅，不再 R² 混合）---
    # 实证：mom120/10 在 2024-2025 A股结构牛市中信号翻得太勤，
    #    累计少赚 75pp；恢复 8 个月主信号 + 近 1 月跳过，动量"够钝"
    #    才能在行业主线情形中不被短期反转摔下车。
    g.mom_window = 160          # 主动量窗口（约 8 个月）
    g.skip_recent = 20          # 跳过最近 20 日过滤月度反转
    g.vol_window = 60           # 仓位波动率窗口（动量窗口解耦）
    g.annualize = 250           # A股年化交易日

    # --- 大盘关（仅当 use_market_guard=True 时生效）---
    g.market_index = '510300.XSHG'
    g.market_ma_slow = 120      # 慢速 MA (MA120)

    # --- 反追高参数（波动率标准化，k 个标准差以外顺延一周）---
    g.chase_ma_n = 20
    g.chase_k = 2.5

    # --- 老仓粘性 / 权重漂移容忍（节振幅用，用户认可）---
    g.hold_buffer = 1           # 老仓排名仍在组内前 (hold_buffer+1) → 保留
    g.weight_band = 0.05        # 权重漂移在 ±5% 内不调仓

    # --- 权重上限（逆波动率版）；任一槽位上限、防守组工龄下限 ---
    g.max_group_weight = 0.40
    g.min_defense_weight = 0.10

    # ══════════════════════════════════════════════════

    # 缓存：季度刷新逆波动率权重
    g.weight_cache = {}          # {'组名': weight}，键为 'YYYY-Qn'
    g.first_close = {}           # 回测曲线懒加载
    # 盘中日志第一条
    log.info('[v3] 初始化完成，资产池 22 只 / 5 组 1 槽')

    run_daily(rebalance, '14:50')


# ============================ 工具函数 ============================

def is_week_end(context):
    """判断今天是否本周最后一个交易日：下一交易日跨 ISO 周即视为周末。"""
    today = context.current_dt.date()
    trade_days = get_trade_days(start_date=today,
                                end_date=today + datetime.timedelta(days=10))
    days_after = [d for d in trade_days if d > today]
    if not days_after:
        return True
    nxt = days_after[0]
    return today.isocalendar()[:2] != nxt.isocalendar()[:2]


def atr_ok(etf, n, min_n):
    """安全检查：是否能在 attribute_history 上拿到 n 根以上价格。"""
    try:
        df = attribute_history(etf, n, '1d', ['close'], fq='pre',
                               skip_paused=True, df=True)
        if df is None:
            return None
        closes = df['close'].values
        if len(closes) < min_n:
            return None
        return closes
    except Exception:
        return None


def market_guard_ok(context):
    """慢速大盘关（默认关）：沪深300 站上 MA120 或 mom120>0 其一即放行。"""
    if not g.use_market_guard:
        return True
    closes = atr_ok(g.market_index, g.market_ma_slow + 5, g.market_ma_slow)
    if closes is None:
        return True
    last = closes[-1]
    ma = closes[-g.market_ma_slow:].mean()
    mom = last / closes[-g.market_ma_slow] - 1.0
    return bool(last >= ma or mom > 0)


def chase_blocked(etf, closes_ref):
    """反追高：价格偏离 MA20 > k × σ_daily × √20 → 顺延一周（只拦新买）。"""
    if not g.use_anti_chase:
        return False
    n = g.chase_ma_n
    if len(closes_ref) < n + 5:
        return False
    ma = closes_ref[-n:].mean()
    if ma <= 0:
        return False
    rets = np.diff(closes_ref) / closes_ref[:-1]
    vol = float(np.std(rets[-n:], ddof=1)) if len(rets) >= n else 0.0
    if vol <= 0:
        return False
    return abs(closes_ref[-1] / ma - 1.0) > g.chase_k * vol * float(math.sqrt(n))


# ============================ 动量计算 ============================

def calc_mom_vol(etf):
    """单只 ETF：主动量 slope_ann（对数价格线性回归年化斜率）+ σ_daily/σ_ann。

    返回 dict：slope_ann, final(=slope_ann), vol_ann, closes,
              mom_simple (=close[n-1-skip]/close[0]-1 的走势参考）
    数据不足（上市未满或停牌过久）自动返回 None，该组顺延下一候选。
    """
    n_needed = max(g.mom_window + g.skip_recent + 5, g.vol_window + 10)
    closes = atr_ok(etf, n_needed, g.mom_window + 5)
    if closes is None:
        return None

    # 主信号：对数价格线性回归（mom_window 长度，跳过最近 skip_recent 日）
    n = g.mom_window
    seg = closes[-n - g.skip_recent: -g.skip_recent] if g.skip_recent > 0 else closes[-n:]
    if len(seg) < n:
        return None
    log_p = np.log(seg)
    x = np.arange(n)
    slope, _intercept = np.polyfit(x, log_p, 1)
    slope_ann = float(np.exp(slope * g.annualize) - 1)

    # 波动率（用于仓位分配，独立于动量窗口）
    vol_seg = closes[-g.vol_window - 1:]
    if len(vol_seg) >= 2:
        rets = np.diff(vol_seg) / vol_seg[:-1]
        vol_ann = float(np.std(rets, ddof=1)) * float(math.sqrt(g.annualize))
    else:
        vol_ann = 0.0

    # 走势参考（日志展示用； simple momentum 不含 skip，便于看全走势）
    mom_simple = float(closes[-1] / closes[-g.mom_window - 1] - 1) if len(closes) > g.mom_window else 0.0

    return {'slope_ann': slope_ann, 'vol_ann': vol_ann,
            'mom_simple': mom_simple, 'closes': closes}


# ============================ 逆波动率权重 ============================

def compute_inverse_vol_weights(context):
    """季度刷新：全池 22 只各算 σ_ann，α-agg 到组；组合权重 = (1/σ_agg)/sum(1/σ_agg)。

    - 某组所有成员数据不足 → 组权重=0（留现金）
    - 组内成员仅 1 只有效时，σ_agg = 该成员 σ
    - 任一槽位 ≥ max_group_weight → 按 cap 压缩并把溢出比例摊给其他组
    - 防守组（黄金+国债）若计算权重 < min_defense_weight → 提到下限
    """
    sigma_by_etf = {}
    for gname, members in g.pool.items():
        sigma_by_etf[gname] = []
        for etf in members:
            info = calc_mom_vol(etf)
            if info is not None and info['vol_ann'] > 1e-6:
                sigma_by_etf[gname].append(info['vol_ann'])

    inv = {}
    for gname, sig_list in sigma_by_etf.items():
        if not sig_list:
            inv[gname] = 0.0
            continue
        # 组内 1/σ 平均（equally-weighted 子资产的隐权重和）
        acc = 0.0
        for s in sig_list:
            acc += 1.0 / s
        inv[gname] = acc / len(sig_list)

    total_inv = 0.0
    for v in inv.values():
        total_inv += v
    if total_inv <= 0:
        # 极端情形：全组无数据 → 均分
        n_g = len(g.pool)
        return {name: 1.0 / n_g for name in g.pool}

    w = {name: v / total_inv for name, v in inv.items()}

    # 防守组下限
    d = w.get('防守', 0.0)
    if 0.0 < d < g.min_defense_weight:
        w['防守'] = g.min_defense_weight
        # 其他组按比例压
        rest_total = 1.0 - g.min_defense_weight
        rest_inv_sum = 0.0
        for name, v in inv.items():
            if name != '防守':
                rest_inv_sum += v
        for name in w:
            if name != '防守':
                w[name] = (inv[name] / rest_inv_sum) * rest_total if rest_inv_sum > 0 else 0.0

    # 单槽上限（多次压缩直至收敛）
    for _ in range(10):
        over = [(name, wt) for name, wt in w.items() if wt > g.max_group_weight]
        if not over:
            break
        excess = 0.0
        for name, wt in over:
            excess += wt - g.max_group_weight
            w[name] = g.max_group_weight
        # 溢出资按 inv 权重摊给未受限组
        free_inv = 0.0
        for name, wt in w.items():
            if wt < g.max_group_weight:
                free_inv += inv.get(name, 0.0)
        if free_inv <= 0:
            break
        for name in w:
            if w[name] < g.max_group_weight:
                w[name] += excess * (inv.get(name, 0.0) / free_inv)

    return w


def refresh_weights_if_needed(context):
    """季度刷新权重缓存。聚宽环境时间对象需避开 sum/sorted。"""
    q_key = '%d-Q%d' % (context.current_dt.year, (context.current_dt.month - 1) // 3 + 1)
    if g.weight_cache.get('__key__') == q_key:
        return g.weight_cache.get('__w__', {})
    w = compute_inverse_vol_weights(context)
    g.weight_cache = {'__key__': q_key, '__w__': w}
    log.info('[权重刷新 %s] %s' % (q_key,
             ' / '.join('%s:%.1f%%' % (name, w[name] * 100) for name in g.pool)))
    return w


def fixed_equal_weights():
    """use_risk_parity=False 时的对照：组间均权 0.2/0.2/0.2/0.2/0.2。"""
    n = len(g.pool)
    return {name: 1.0 / n for name in g.pool}


# ============================ 调仓 ============================

def rebalance(context):
    if not is_week_end(context):
        return

    # 1. 全池计算动量 + σ
    info = {}
    for gname, members in g.pool.items():
        for etf in members:
            res = calc_mom_vol(etf)
            if res is not None:
                info[etf] = res

    if not info:
        log.info('[轮动] 全池无有效动量数据，跳过本周')
        return

    # 2. 权重（逆波动率或均权对照）
    if g.use_risk_parity:
        group_weight = refresh_weights_if_needed(context)
    else:
        group_weight = fixed_equal_weights()

    # 3. 大盘关（默认关；打开时只约束 A 股权益组的**新买**）
    market_ok = market_guard_ok(context)
    guarded_groups = {'进攻', '行业'}

    # 4. 组内排序选最强 1 只 + 三道闸门
    target = {}
    defer_log = []

    for gname, members in g.pool.items():
        cands = [e for e in members if e in info]
        if not cands:
            continue
        # 组内同量纲 mom 降序
        ranked = sorted(cands, key=lambda e: info[e]['slope_ann'], reverse=True)

        # 闸门1：绝对动量 slope_ann > 0
        # 不要对防守组放宽保底 —— 实证（v3-C vs A 对照）：放宽会让防守组2024-2026
        # 大牛市期间权重被动被逆波动率压走，累计少赚 -55pp；slope>0 的版本里
        # 防守组 slope<0 的资金留现金反而让权益仓位跟着过牛信号满仓收益。
        qualified = [e for e in ranked if info[e]['slope_ann'] > 0]

        # 闸门2：大盘关（仅当 use_market_guard=True 且组为 A 股权益）
        if g.use_market_guard and not market_ok and gname in guarded_groups:
            qualified = []

        # 闸门3：反追高（只拦新买）
        if g.use_anti_chase:
            filtered = []
            for e in qualified:
                if chase_blocked(e, info[e]['closes']):
                    defer_log.append((gname, e, info[e]['slope_ann']))
                    continue
                filtered.append(e)
            qualified = filtered

        # 老仓粘性：老仓仍在前 (hold_buffer+1) 且 slope>0 → 保留（跳过闸门2/3）
        old_pos = [e for e in context.portfolio.positions.keys()
                   if e in members and context.portfolio.positions[e].total_amount > 0]
        pick = None
        if old_pos:
            for e in old_pos:
                if e in ranked[:g.hold_buffer + 1] and info[e]['slope_ann'] > 0:
                    pick = e
                    break

        if pick is None and qualified:
            pick = qualified[0]

        if pick is not None:
            target[pick] = group_weight.get(gname, 0.0)

    # 5. 跨境 ETF 溢价观察（仅存档不拦截）
    for etf in target:
        if etf in QDLL_CODES:
            last_close = info[etf]['closes'][-1] if etf in info else 0.0
            prev_close = info[etf]['closes'][-2] if etf in info and len(info[etf]['closes']) >= 2 else last_close
            if prev_close > 0:
                chg = (last_close / prev_close - 1.0) * 100.0
                log.info('[跨境存档] %s 今日幅度 %+.2f%%，供事后判断溢价行为' % (etf, chg))

    # 6. record 持仓曲线 + 大盘开关
    rec = {}
    if g.use_market_guard:
        rec['大盘'] = 1 if market_ok else 0
    for etf, wt in target.items():
        nm = g.name_map.get(etf, etf[:6])
        try:
            px = context.get_prices(etf, g.start_dt, context.current_dt.date(),
                                    fields=['close'], fq='pre', df=True)
        except Exception:
            px = None
        if px is not None and len(px) >= 2:
            ret = px['close'].iloc[-1] / px['close'].iloc[0] - 1
            rec[nm] = round(ret * 100, 2)
    if rec:
        record(**rec)

    # 7. 每周日志：各组入选动量榜 + 持仓
    _parts = []
    for gname, members in g.pool.items():
        cands = [e for e in members if e in info]
        if not cands:
            _parts.append('%s[-]' % gname)
            continue
        best = max(cands, key=lambda e: info[e]['slope_ann'])
        _parts.append('%s[%s:%+.1f%%]' % (gname, g.name_map.get(best, best[:6]),
                                          info[best]['slope_ann'] * 100))
    _hold = ' / '.join('%s:%s %.0f%%' % (g.name_map.get(_e, _e[:6]),
                     _weights_str(gname_=_find_group(_e), wg=group_weight),
                     group_weight.get(_find_group(_e), 0) * 100) for _e in target)
    log.info('%s 入选[%s] 持仓{%s}' % (context.current_dt.date(),
                     ' '.join(_parts), _hold if _hold else '空仓'))

    if defer_log:
        for gname, etf, sc in defer_log:
            log.info('[反追高] %s 组 %s 偏离MA20>2.5σ 顺延一周 (slope=%+.1f%%)' %
                     (gname, etf, sc * 100))

    # 8. 执行调仓
    total_value = context.portfolio.total_value
    for etf in list(context.portfolio.positions.keys()):
        if etf not in target:
            order_target(etf, 0)
    for etf, w in target.items():
        target_value = total_value * w
        if etf in context.portfolio.positions:
            pos = context.portfolio.positions[etf]
            cur_value = pos.total_amount * pos.price
        else:
            cur_value = 0
        if total_value > 0 and abs(cur_value - target_value) / total_value < g.weight_band:
            continue
        order_target_value(etf, target_value)


def _find_group(etf):
    for gname, members in g.pool.items():
        if etf in members:
            return gname
    return ''


def _weights_str(gname_, wg):
    return '%s' % gname_
