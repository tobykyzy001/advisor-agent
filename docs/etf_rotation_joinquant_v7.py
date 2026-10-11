# -*- coding: utf-8 -*-
# ETF 轮动策略 v7（聚宽可回测版）—— 51 池日频多仓版（当前推荐基线）
#
# 渊源：聚宽主帖"ETF动量策略"（post/78032，作者 A-资产轮动-865）的用户审定改良版。
# 2026-10-11 聚宽实测（严格区间 2016-01-01 ~ 2026-06-30，¥100000，佣金万 1 每边 + 滑点万 5 往返）：
#
#   | 配置 | 收益 | 年化 | 夏普 | 最大回撤 | 备注 |
#   | 本版 K=4（前4/缓冲6） | 764.25% | ~22.8% | 1.123 | 21.49% | ★ 收益+夏普最优 |
#   | 本版 K=4 滑点改 0（用户实测 2026-10-11） | 806.17% | 24.16% | 1.156 | 21.45% | 摩擦敏感性实测：往返万 5 滑点 + 佣金万 1 全程仅 -1.3pp/年、-42pp 累计、回撤几乎不变； |
#   |   └ 年化换手约 109 笔（平均 2 个多交易日一仓）——1.5K 排名缓冲把"日频信号"压成了低频成交 |
#   | 本版 K=10（前10/缓冲15）（改 HOLDINGS_NUM_DEF=10） | 284.25% ×2次一致 | ~13.7% | 0.912 | 15.35% | 低波版 |
#   | 主帖原版 K=1+分钟止损+分钟量过滤 @0.1%滑点 | 1335.39% ×2次一致 | ~28.9% | 0.868 | 29.73% | 收益更高但回撤/夏普差 |
#   | 主帖原版同码 2700.50% | 孤值不可复现（权重排除） | — | 1.167 | 28.27% | 孤值成因未定案： |
#   |   └ 最大嫌疑是首次发射时编辑器/服务端代码快照竞争；分钟量过滤嫌疑已排除——后两次含该 |
#   | v1（本仓库基线，¥100万） | 461.3% | 18.46% | 0.669 | 23.91% | 被 K4 全面反超 |
#   | v6（v1 订正版） | 396.2% | ~16.5% | 0.603 | 24.96% | 同左 |
#   2020-2025 区间对照：主帖秀 1256.7%/56.5%/1.607/28.3%（万1滑点+止损+单吊）；
#   主帖原码换 0.1% 滑点 791.7%/1.270；本版 K10 157.1%/1.006/15.5%。
#
# 相对主帖原版的改动（每条有审定，非调参）：
#   1) 删除分钟级 -5% 硬止损（实盘做不到该成交；退出纯靠动量轮换——跌出缓冲名次自然换走）；
#   2) 满仓单吊 → 动量前 K 名等权（K=4 回测最优；K=10 是低波端点；keep_rank=ceil(1.5K)
#      沿用 Clenow 买前20%/卖跌出前30% 的排名缓冲，压换手）；
#   3) 删除分钟成交量过滤（日频回测下"今日盘中量"语义不成立，且它让回测依赖大量分钟级
#      取数；删除后本版两次重复回测逐位一致，可复现）；
#   4) 信号统一 T-1 收盘、T 日 14:00 执行（不拼盘中价）；
#   5) 费率现代口径：佣金万 1 每边、滑点万 5 往返（聚宽 PriceRelatedSlippage 按±值/2 计；
#      原版佣金万 2 + 滑点 0.0001）。
#
# 不动的核心（验证结论：真实有效）：24 日加权(linspace1→2)对数回归斜率年化 × R²、
# R²≥0.4 过滤、近3日单日跌幅>3.5% 剔除、score∈(0,5)、51 只池、日频、无合格标的时
# 按槽位切货币 ETF（511880）。
#
# 遗留风险（诚实声明）：
#   a) 51 池是 2026 年回头看组的——沙特/机器人/卫星/纳指科技/恒生创新药等在回测
#      起点不存在、或事后才知道是赢家，池子本身带前瞻偏差；降低方法是用景气研究
#      定期维护池子成分（例如仓库 prosperity-analysis 的输出），而非固定这份名单。
#   b) 沙特/豆粕/白银/德国/巴西类品种实盘冲击成本与 QDII 溢价远大于回测模型，
#      K=4 满仓 25% 单品种时尤其要人工核溢价（>2% 建议暂缓）。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-01 至今，基准 000300.XSHG，初始资金 100000，频率「天」。
#
# 仅供研究参考，不构成投资建议。

import math

import numpy as np
import pandas as pd
from jqdata import *

# ================== 资产池（51 只，与主帖一致；应定期用景气研究复核）==================
ETF_POOL_DEF = [
    # 境外
    "159941.XSHE",  # 纳指ETF
    "159509.XSHE",  # 纳指科技ETF
    "513500.XSHG",  # 标普500ETF
    "513520.XSHG",  # 日经ETF
    "513030.XSHG",  # 德国ETF
    "513080.XSHG",  # 法国ETF
    "159100.XSHE",  # 巴西ETF
    "159329.XSHE",  # 沙特ETF
    # 商品
    "518880.XSHG",  # 黄金ETF
    "159980.XSHE",  # 有色ETF
    "161226.XSHE",  # 白银ETF
    "159985.XSHE",  # 豆粕ETF
    "159981.XSHE",  # 能源化工ETF
    "501018.XSHG",  # 南方原油LOF
    # 债券
    "511090.XSHG",  # 30年国债ETF
    # 国内
    "513130.XSHG",  # 恒生科技ETF
    "520500.XSHG",  # 恒生创新药ETF
    "513970.XSHG",  # 消费ETF
    "513690.XSHG",  # 港股红利ETF
    "159915.XSHE",  # 创业板ETF
    "563300.XSHG",  # 中证2000ETF
    "563360.XSHG",  # 中证A500ETF
    "510410.XSHG",  # 资源ETF
    "515210.XSHG",  # 钢铁ETF
    "562800.XSHG",  # 稀有金属ETF
    "159928.XSHE",  # 中证消费ETF
    "512690.XSHG",  # 中证酒ETF
    "159992.XSHE",  # 创新药ETF
    "588220.XSHG",  # 科创100ETF
    "159819.XSHE",  # 人工智能ETF
    "159851.XSHE",  # 金融科技ETF
    "159326.XSHE",  # 电网设备ETF
    "515030.XSHG",  # 新能源车ETF
    "516160.XSHG",  # 新能源ETF
    "512710.XSHG",  # 军工ETF
    "515220.XSHG",  # 煤炭ETF
    "512880.XSHG",  # 证券ETF
    "159378.XSHE",  # 通用航空ETF
    "159206.XSHE",  # 卫星ETF
    "516510.XSHG",  # 云计算ETF
    "515050.XSHG",  # 5GETF
    "512170.XSHG",  # 医疗ETF
    "159870.XSHE",  # 化工ETF
    "159611.XSHE",  # 电力ETF
    "159995.XSHE",  # 芯片ETF
    "515790.XSHG",  # 光伏ETF
    "159755.XSHE",  # 电池ETF
    "515000.XSHG",  # 科技ETF
    "562500.XSHG",  # 机器人ETF
]

# ============== 策略参数 ==============
LOOKBACK_DAYS_DEF = 24        # 动量回归窗口（主帖 24 日，不动）
HOLDINGS_NUM_DEF = 4          # 持仓数 K：实测 K=4 收益/夏普最优；K=10 为低波端点
MIN_MONEY_DEF = 5000          # 最小交易金额
DEFENSIVE_ETF_DEF = "511880.XSHG"  # 防御：货币ETF

LOSS_DEF = 0.965              # 近3日单日跌幅过滤（主帖 3.5%，不动）
USE_R2_FILTER_DEF = True
R2_MIN_THRESHOLD_DEF = 0.4    # R² 下限（主帖，不动）
MIN_SCORE_THRESHOLD_DEF = 0.0
MAX_SCORE_THRESHOLD_DEF = 5.0


def initialize(context):
    set_benchmark("000300.XSHG")
    set_option('use_real_price', True)
    set_option("avoid_future_data", True)
    set_slippage(PriceRelatedSlippage(0.0005), type="fund")  # 滑点 0.0005：单边约万 2.5、往返万 5
    set_order_cost(
        OrderCost(
            open_tax=0, close_tax=0,
            open_commission=0.0001, close_commission=0.0001,  # 佣金万 1
            close_today_commission=0, min_commission=0,
        ),
        type="fund",
    )
    log.set_level('order', 'error')
    log.set_level('system', 'error')

    g.etf_pool = ETF_POOL_DEF
    g.lookback_days = LOOKBACK_DAYS_DEF
    g.holdings_num = HOLDINGS_NUM_DEF
    g.keep_rank = int(math.ceil(g.holdings_num * 1.5))  # 老仓排名缓冲：买前K，卖跌出前1.5K
    g.defensive_etf = DEFENSIVE_ETF_DEF
    g.min_money = MIN_MONEY_DEF
    g.loss = LOSS_DEF
    g.use_r2_filter = USE_R2_FILTER_DEF
    g.r2_min_threshold = R2_MIN_THRESHOLD_DEF
    g.min_score_threshold = MIN_SCORE_THRESHOLD_DEF
    g.max_score_threshold = MAX_SCORE_THRESHOLD_DEF

    # 日频 14:00 一次调仓（先卖后买在同一函数内完成）
    run_daily(rebalance, time='14:00')
    log.info("v7 初始化：池 %d 只 | 窗口 %d 日 | 持前 %d 缓冲 %d | 无硬止损" % (
        len(g.etf_pool), g.lookback_days, g.holdings_num, g.keep_rank))


# ==================== 动量指标计算（T-1 收盘口径）====================
def calculate_momentum_metrics(context, etf):
    """24 日加权对数回归斜率年化 × R² + 近3日跌幅过滤（主帖口径，数据仅用 T-1 收盘）。"""
    try:
        lookback = g.lookback_days + 20
        prices = attribute_history(etf, lookback, '1d', ['close'], fq='pre', skip_paused=True, df=True)
        if prices is None or len(prices) < g.lookback_days + 4:
            return None
        price_series = prices["close"].values
        if price_series[-1] <= 0:
            return None

        # 近3日单日跌幅过滤
        day1_ratio = price_series[-1] / price_series[-2]
        day2_ratio = price_series[-2] / price_series[-3]
        day3_ratio = price_series[-3] / price_series[-4]
        if min(day1_ratio, day2_ratio, day3_ratio) < g.loss:
            return None

        # 24 日加权（linspace(1,2)）对数线性回归
        recent = price_series[-(g.lookback_days + 1):]
        y = np.log(recent)
        x = np.arange(len(y))
        weights = np.linspace(1, 2, len(y))
        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_returns = math.exp(slope * 250) - 1

        ss_res = np.sum(weights * (y - (slope * x + intercept)) ** 2)
        ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot else 0

        if g.use_r2_filter and not (g.r2_min_threshold <= r_squared <= 1):
            return None

        score = annualized_returns * r_squared
        return {'etf': etf, 'annualized_returns': annualized_returns,
                'r_squared': r_squared, 'score': score}
    except Exception:
        return None


# ==================== 调仓（日频，先卖后买）====================
def rebalance(context):
    # --- 1. 全池打分（上市前/停牌/数据不足自动跳过）---
    metrics = []
    for etf in g.etf_pool:
        m = calculate_momentum_metrics(context, etf)
        if m is not None and 0 < m['score'] < g.max_score_threshold:
            metrics.append(m)
    metrics.sort(key=lambda x: x['score'], reverse=True)
    ranked = [m['etf'] for m in metrics]
    rank_of = {}
    for i, e in enumerate(ranked):
        rank_of[e] = i + 1

    # --- 2. 目标持仓：老仓仍在前 keep_rank 名 → 保留；空缺从前 K 名递补；空槽货基兜底 ---
    current = [e for e in context.portfolio.positions.keys()
               if context.portfolio.positions[e].total_amount > 0 and e != g.defensive_etf]
    kept = [e for e in current if e in rank_of and rank_of[e] <= g.keep_rank]
    target = list(kept)
    for e in ranked:
        if len(target) >= g.holdings_num:
            break
        if e not in target:
            target.append(e)

    weights = {}
    for e in target:
        weights[e] = weights.get(e, 0.0) + 1.0 / g.holdings_num
    n_def = g.holdings_num - len(target)
    if n_def > 0:
        weights[g.defensive_etf] = weights.get(g.defensive_etf, 0.0) + n_def / g.holdings_num

    # --- 3. 卖出不在目标中的持仓 ---
    for etf in list(context.portfolio.positions.keys()):
        if etf not in weights:
            order_target(etf, 0)

    # --- 4. 买入/权重维护：漂移不足总资产 5% 不动（主帖口径）---
    total_value = context.portfolio.total_value
    for etf, w in weights.items():
        target_value = total_value * w
        cur_value = 0
        if etf in context.portfolio.positions:
            pos = context.portfolio.positions[etf]
            cur_value = pos.total_amount * pos.price
        if abs(cur_value - target_value) <= total_value * 0.05 and cur_value > 0:
            continue
        if target_value >= g.min_money:
            order_target_value(etf, target_value)

    # --- 5. 日志：前五名 + 目标 ---
    log.info("%s 前五:%s | 持仓:%s" % (
        context.current_dt.date(),
        ' '.join('%s:%.3f' % (m['etf'][:6], m['score']) for m in metrics[:5]),
        ' '.join('%s:%.0f%%' % (e[:6], w * 100) for e, w in weights.items())))
