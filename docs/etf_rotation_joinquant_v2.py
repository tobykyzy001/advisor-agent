# -*- coding: utf-8 -*-
# ETF 轮动策略 v2（聚宽可回测版）—— 分组轮动 + 全谱资产池
#
# v1 回测结论（2016-01 ~ 2026-10）：年化 18.46%、最大回撤 23.91%（集中于 2016 熔断震荡期）、
#   夏普 0.669、盈亏比 2.64 —— 轮动逻辑有效，但「单仓 100% + 单一权益仓位」导致波动过高。
# v2 升级（方案文档见 docs/etf_rotation_joinquant.md）：
#   1) 资产池从 6 只扩到 14 只：宽基(价值/成长/中盘/小盘) + 行业(证券/医药/军工/半导体/消费)
#      + 跨境(纳指/标普/港股) + 防守(黄金/国债) —— 行业/指数/债券/商品混合轮动；
#   2) 分组轮动：行业组/宽基组/跨境组/防守组 各持组内最强 1 只（最多 4 只持仓），
#      组内比动量、组间仓位独立 —— 某组全弱则该组自动空仓，天然降回撤（v1 的 23.9% 回撤
#      主要来自单一权益仓 100% 承受系统性下跌）；
#   3) 权重漂移容忍 ±5%：老仓权重漂移在容忍带内不动，省无谓调仓费；
#   4) 保留 v1 三道闸门：绝对动量(final>0且score>0) / 大盘关(只限A股权益组新增) / 老仓排名容差。
#
# 信号口径：attribute_history 不含当日 → 信号基于 T-1 收盘价，T 日 14:50 下单、按当日收盘价成交。
# 晚上市的 ETF 由「数据不足自动跳过」兜底（如 512480 半导体 2019-06 上市、
# 512660 军工 2016-08 上市，在各自满 60 个交易日数据前自动不参与，不影响其余标的）。
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

    # 回测起始日（各 ETF 累计收益以此为起点，见周统计懒加载）
    g.start_dt = context.current_dt.date()
    g.etf_hold_weeks = {}          # 每支 ETF 被持有的调仓周数（回测末看哪支被用得最多）
    # first_close 懒加载：ETF 首次进入动量表时（说明已上市、有数据）才取一次全历史首价，
    # 故晚上市 ETF（如 512480）也能取到其上市日收盘价作为累计收益起点，不漏掉
    g.first_close = {}             # 每支 ETF 上市后的首个收盘价（见周统计里的懒加载）

    # --- 资产池：分组定义（每组独立轮动，各持组内最强 1 只）---
    # 行业组：高beta进攻；宽基组：A股核心beta+大小盘风格；跨境组：海外分散；防守组：避险压舱
    g.groups = {
        '行业': [
            '512880.XSHG',  # 证券（2016-07 上市）
            '512010.XSHG',  # 医药（2013-12 上市）
            '512660.XSHG',  # 军工（2016-08 上市）
            '512480.XSHG',  # 半导体（2019-06 上市）
            '159928.XSHE',  # 消费（2013-09 上市）
        ],
        '宽基': [
            '510180.XSHG',  # 上证180（价值/蓝筹，2006-05）
            '159915.XSHE',  # 创业板100（成长，2011-12）
            '510500.XSHG',  # 中证500（中盘，2013-03）
            '512100.XSHG',  # 中证1000（小盘，2016-11 满60日后自动参与）
        ],
        '跨境': [
            '513100.XSHG',  # 纳指100（2013-05）
            '513500.XSHG',  # 标普500（2013-12）
            '510900.XSHG',  # 恒生国企（2012-08）
        ],
        '防守': [
            '518880.XSHG',  # 黄金（2013-07）
            '511010.XSHG',  # 国债（2013-03）
        ],
    }

    # record 曲线名 / 日志用中文名
    g.name_map = {
        '512880.XSHG': '证券', '512010.XSHG': '医药', '512660.XSHG': '军工',
        '512480.XSHG': '半导体', '159928.XSHE': '消费',
        '510180.XSHG': '价值', '159915.XSHE': '创业板', '510500.XSHG': '中证500',
        '512100.XSHG': '中证1000',
        '513100.XSHG': '纳指', '513500.XSHG': '标普', '510900.XSHG': '港股',
        '518880.XSHG': '黄金', '511010.XSHG': '国债',
    }

    # ═══════════════════ 参数配置区 ★ 可自行修改 ★ ═══════════════════

    # --- 大盘状态关（只约束「A股权益类」组：行业+宽基的新买入）---
    # 跨境/黄金/国债与A股大盘状态相关性低，各自的绝对动量就是它们的闸门；
    # 若用A股大盘关一刀切，会在A股走弱时错失黄金（恰是轮动的避险去向）。
    g.use_market_guard = True
    g.market_index = '000300.XSHG'   # 大盘代理指数
    g.market_ma = (20, 60)           # 大盘均线组：任一站上即视为可买A股权益
    g.guarded_groups = {'行业', '宽基'}

    # --- 动量参数 ---
    g.regression_window = 27         # 主信号回归窗口（交易日，取最后 N 根）
    g.short_window = 20              # 短动量窗口（mom20）
    g.vol_window = 60                # 波动率窗口（取最后 N 根算波动）
    g.w_long = 0.6                   # 主信号权重（年化回归斜率×R²）
    g.w_short = 0.3                  # 短动量权重
    g.w_vol = 0.1                    # 波动率惩罚权重（越大越规避高波动标的）
    # 三权重之和应接近 1；改动后无需手动归一化（程序自动归一）

    # --- 组合规则 ---
    g.hold_buffer = 1                  # 组内排名容差：老仓仍在组内前 (hold_buffer+1) 名且分数>0 → 保留
    g.weight_band = 0.05               # 权重漂移容忍 ±5%：带内不调仓（省摩擦成本）
    # 组权重（总和=1，程序自动归一化）；某组空仓时该组权重留作现金（天然降杠杆）
    # 聚宽环境的 sum()/dict.values() 行为异常（sum 原样返回 dict_values，不调和），
    # 故完全不调用 sum/.values()，改用 + 直接相加后归一化
    w_industry = 0.30
    w_broad = 0.30
    w_cross = 0.25
    w_defense = 0.15
    _s = w_industry + w_broad + w_cross + w_defense
    g.group_weights = {'行业': w_industry / _s, '宽基': w_broad / _s,
                       '跨境': w_cross / _s, '防守': w_defense / _s}

    # ═══════════════════════════════════════════════════════════════

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


def is_last_trading_day(context):
    """判断今天是否为回测最后一天：之后无下一个交易日即为结束。"""
    today = context.current_dt.date()
    future = get_trade_days(start_date=today + datetime.timedelta(days=1),
                            end_date=today + datetime.timedelta(days=6))
    return len(future) == 0


# ============================ 动量打分 ============================

def calc_momentum_score(etf):
    """计算单只 ETF 的三维动量指标。

    返回 dict：slope_ann(年化回归收益)、r2(拟合优度)、score(主信号=slope_ann*r2)、
    mom_short(短动量)、vol(年化波动率)；数据不足返回 None（晚上市 ETF 自动跳过）。
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
            'mom_short': mom_short, 'vol': vol, 'close': closes[-1]}


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

    # --- 1. 全池打分（晚上市 ETF 数据不足自动返回 None 跳过）---
    all_etfs = [e for grp in g.groups.values() for e in grp]
    rows = []
    for etf in all_etfs:
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

    # --- 3. 大盘状态关（只冻结 A股权益组的新买入，不强清老仓）---
    market_ok = market_guard_ok(context)

    # --- 4. 分组轮动：每组选最强 1 只 ---
    target = {}          # etf → 目标市值权重
    group_picks = {}     # 组名 → 选中的 etf（record 用）
    for gname, members in g.groups.items():
        pool = [e for e in members if e in df.index]
        if not pool:
            group_picks[gname] = None
            continue
        gdf = df.loc[pool].sort_values(by='final', ascending=False)

        # 闸门1：绝对动量（final>0 且 score>0）
        qualified = gdf[(gdf['final'] > 0) & (gdf['score'] > 0)]
        # 闸门2：大盘关（只拦 A股权益组；清空 qualified 即本组空仓）
        if g.use_market_guard and not market_ok and gname in g.guarded_groups:
            qualified = qualified.iloc[0:0]

        # 闸门3：老仓粘性 —— 组内老仓仍在前 (hold_buffer+1) 名、分数>0 → 保留老仓
        old_pos = [e for e in context.portfolio.positions.keys()
                   if e in pool and context.portfolio.positions[e].total_amount > 0]
        pick = None
        if old_pos:
            buffer_rank = g.hold_buffer + 1
            top_buf = set(gdf.index[:buffer_rank])
            for e in old_pos:
                if e in top_buf and gdf.loc[e, 'final'] > 0 and gdf.loc[e, 'score'] > 0:
                    pick = e
                    break
        # 老仓跌出带或无老仓 → 取合格榜第 1 名（可能为空 → 该组空仓）
        if pick is None and len(qualified) > 0:
            pick = qualified.index[0]

        group_picks[gname] = pick
        if pick is not None:
            target[pick] = g.group_weights[gname]

    # --- 累计记录每支目标 ETF 被持有的周数（初始化时已设为 {}，此处不重置）---
    for etf in target:
        if etf not in g.etf_hold_weeks:
            g.etf_hold_weeks[etf] = 0
        g.etf_hold_weeks[etf] += 1

    # --- 5. 信号记录 / 日志供复盘 ---
    # 5.1 记录当前持有标的的累计收益曲线（聚宽图形界面可直接看，不消耗积分；单日内约10条上限）。
    #     持仓≤4只，加「大盘」开关共≤5条，不会超限。曲线终点 = 该标的自回测起累计收益%。
    rec = {'大盘': 1 if market_ok else 0}
    for etf in target:
        nm = g.name_map.get(etf, etf[:6])
        try:
            px = context.get_prices(etf, g.start_dt, context.current_dt.date(),
                                    fields=['close'], fq='pre', df=True)
        except Exception:
            px = None
        if px is not None and len(px) >= 2:
            ret = px['close'].iloc[-1] / px['close'].iloc[0] - 1
            rec[nm] = round(ret * 100, 2)
    record(**rec)

    # --- 每周统计：全池ETF累计收益(低→高)+ 当前持仓★标记，方便找优/短板 ---
    # 懒加载首价：ETF 首次进入动量表时（已上市满 60 交易日），取一次自回测起第一个收盘价
    for _etf in df.index:
        if _etf not in g.first_close:
            try:
                _px = context.get_prices(_etf, g.start_dt, context.current_dt.date(),
                                         fields=['close'], df=True, fq='pre')
                _vals = _px['close'].dropna()
                g.first_close[_etf] = float(_vals.iloc[0]) if len(_vals) > 0 else None
            except Exception:
                g.first_close[_etf] = None
    _rets = {}
    for _etf in df.index:
        _fc = g.first_close.get(_etf)
        if _fc and _fc > 0:
            _recent = df.loc[_etf, 'close']
            if _recent and _recent > 0:
                _rets[_etf] = _recent / _fc - 1
    if _rets:
        # 手动升序排序（≤14 个元素；聚宽下 sorted/sum/min/max 返回原值、不可用）
        _items = list(_rets.items())
        for _i in range(1, len(_items)):
            _item = _items[_i]
            _j = _i - 1
            while _j >= 0 and _items[_j][1] > _item[1]:
                _items[_j + 1] = _items[_j]
                _j -= 1
            _items[_j + 1] = _item
        # _items 已升序（最劣→最优），当前持仓标★
        _parts = []
        for _e, _r in _items:
            _tag = '★' if _e in target else ''
            _parts.append('%s%s:%+.0f%%' % (g.name_map.get(_e, _e[:6]), _tag, _r * 100))
        _hold = ' / '.join('%s:%+.0f%%' % (g.name_map.get(_e, _e[:6]), _rets[_e] * 100)
                           for _e in target)
        log.info('[%s] 累计收益(低→高) %s | 本周持仓{%s}' % (
            context.current_dt.date(), '  '.join(_parts), _hold))

    # --- 6. 执行调仓 ---
    total_value = context.portfolio.total_value
    # 6.1 卖出不在目标中的持仓（组切换 / 组空仓）
    for etf in list(context.portfolio.positions.keys()):
        if etf not in target:
            order_target(etf, 0)
    # 6.2 买入/权重维护：漂移在 ±weight_band 内的持仓不动，省摩擦成本
    for etf, w in target.items():
        target_value = total_value * w
        if etf in context.portfolio.positions:
            pos = context.portfolio.positions[etf]
            cur_value = pos.total_amount * pos.price
        else:
            cur_value = 0
        if total_value > 0 and abs(cur_value - target_value) / total_value < g.weight_band:
            continue  # 权重漂移在容忍带内，跳过
        order_target_value(etf, target_value)
    # 调仓过程不再刷日志（省积分）；策略意图从回测曲线与各ETF持有曲线读取
