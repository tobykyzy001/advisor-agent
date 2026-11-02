# -*- coding: utf-8 -*-
# 沪深300「弱市次日正T」做T增强策略（聚宽回测版）
#
# 由来：2026-10 本仓库基于 tushare 直连数据做的 10 年回测研究。
#   数据：000300.SH 日线 2015-10-08 ~ 2026-10-15（2678 交易日）+ 30 分钟线辅助验证。
#   市场微结构基线（近10年，T+1制度性效应）：
#     日内 close/open 均值 +6.3bp/日（t=+3.1）、隔夜 open/昨收 均值 -4.7bp/日（t=-3.7）
#     → A股底仓做T的天然姿势是【正T】（开盘买、收盘卖等份额底仓），反T必亏。
#   边际分布：前一日收盘 < MA20 时，次日日内 +9.3bp（夏普 0.98）；MA20 上方仅 +3.5bp。
#     即日内漂移集中在「弱市次日」——这就是本策略仅有的信号。
#
# 策略规则（T+1 合规，底仓股数每日收盘复原）：
#   1) 长期持有 510300 底仓（数量恒定不动）；
#   2) 信号：昨收 < 20 日均线（无条件版本——已验证：加「低开/刚下穿」等过滤全是负优化
#      —— 过滤越少收益越高，唯一统计为负的子集是「高开>1%」日，仅39天，微观可剔除）；
#   3) 信号日一轮正T：09:30 用现金腿买入 510300，14:55 卖出【同股数】底仓
#      （卖出的是昨日之前的持仓，符合 T+1；当日买入份额自动滚入底仓，日末股数不变）；
#   4) 现金准备金空仓闲置（在聚宽回测中泊 511880 已被实测为负优化，负摩擦吃掉全部超额；
#      实盘若要利息请离场外理财，勿在日回测里来回倒腾货基）。
#
# 本地回测对照（净额口径，全摆 w=1，买入持有=年化2.35%）：
#   叠加层纯alpha：@3bp 年化 +7.1%/夏普0.67/R²0.87；@5bp +4.6%/0.45/0.69；@10bp 亏损。
#   组合（持有+做T，指数价 @3bp）：10 年年化 8.26%、夏普 0.43、回撤 -51.9%。
#   用 510300 ETF 实际成交价重算：年化 5.78%（ETF 折溢价/开盘价噪声吃掉约 1/3 超额，
#   聚宽回测天然含此折扣，请与「ETF口径 5.78%」对齐预期，勿与指数口径 8.26% 比）。
#   参数敏感性：MA10~20 是平台区；检验段（2021-07 后）夏普约减半为训练段一半（策略在衰减）；
#   2022/2023 年 alpha 为负的年份存在，属统计现象，非参数失效。
#
# 聚宽实测校准（2026-10-16，分钟级 2016-10-17~2026-10-15，本地同参数复刻吻合）：
#   BASE70%(w0.43)+货基腿ON+T腿5bp → 组合年化 +0.35%（JQ 实测；本地复刻 +0.96%）
#   拆解：货基双腿摩擦 -3.6%/年（关掉回到 +4.55%）；降费+提摆动后 base60% +6.44%、
#   base50%(全摆w=1.0) +7.64%/夏普0.49 —— 成本与摆动比例是命门，不是策略失效。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-10-17 ~ 2026-10-15，基准 000300.XSHG，初始资金 1,000,000，
#   频率【分钟】（09:30 买腿 / 14:55 卖腿依赖分钟级撮合；日线频率下该时间语义不存在）。
#
# 仅供研究参考，不构成投资建议。

from jqdata import *


# ================== 参数（用户可调）==================
ETF = "510300.XSHG"      # 标的：华泰柏瑞沪深300ETF（日成交数十亿，冲击成本最低）
MMF = "511880.XSHG"      # 现金停泊：银华日利货币ETF（场内 T+0 可回转）
BENCH = "000300.XSHG"    # 基准：沪深300指数
MA_N = 20                # 弱市判定均线（10~20 为平台区，勿过度优化）
BASE_RATIO = 0.60        # 底仓占比（w=(1-BASE)/BASE：0.6→w0.67；改 0.5 则 w=1.0 全摆，alpha贡献拉满）
USE_MMF = False          # 现金腿停泊货币ETF。【聚宽实测：True 为负优化——信号日货基来回双边计费，
                         # 30%仓位×5bp×~120信号日≈-3.6%/年，把超额吃成负的；保持 False 现金闲置】
SWING_CAP = 0.50         # 单日做T腿市值不超过总资金该比例（防现金不足时裸露杠杆）
COMMISSION = 0.00005     # ETF 佣金 万0.5/边（按你的券商实际改；若有最低5元收费，改 min_commission=5）
SLIPPAGE = 0.0001        # 冲击滑点 万1/边（510300 盘口厚，万2偏保守；实测组合对滑点高度敏感）
SETTLE_MIN = 5000        # 买腿现金低于该额度放弃当日做T（避免碎单）


def initialize(context):
    """聚宽初始化：基准、费率、定时函数"""
    set_benchmark(BENCH)
    set_option("use_real_price", True)       # 真实收盘价撮合（回测透明）
    set_option("avoid_future_data", True)    # 禁止未来函数（与本地回测口径一致）
    # ETF 无印花税；佣金佣金按实际券商修改
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=COMMISSION, close_commission=COMMISSION,
                             close_today_commission=0, min_commission=0),
                   type="fund")
    set_slippage(PriceRelatedSlippage(SLIPPAGE))

    g.signal = False          # 今日是否做T
    g.leg_shares = 0          # 今日买入的做T腿股数（14:55 等量卖出）
    g.base_shares = None      # 底仓股数（校验用；None=底仓尚未建成）
    g.t_profit = 0.0          # 累计做T腿盈亏（含费）
    g.t_days = 0              # 交易天数
    g.init_done = False       # 底仓是否下过单

    run_daily(prepare, time="before_open", reference_security=BENCH)
    run_daily(t_buy, time="09:30", reference_security=ETF)
    run_daily(t_sell, time="14:55", reference_security=ETF)
    run_daily(report, time="after_close", reference_security=BENCH)
    log.set_level("order", "error")  # 成交回报日志降噪


def prepare(context):
    """开盘前：建仓（仅首日）+ 用昨日收盘与 MA20 判定今日信号（无未来函数）"""
    pv = context.portfolio
    if not g.init_done:
        # 首交易日先买底仓（9:30 成交），余下现金泊货基
        order_target_value(ETF, pv.total_value * BASE_RATIO)
        if USE_MMF:
            order_target_value(MMF, pv.available_cash * 0.998)
        g.init_done = True
        log.info("建仓底仓 510300 目标占比 %.0f%%，建仓日不做T（T+1 可卖股数为0）" % (BASE_RATIO * 100))

    h = attribute_history(ETF, MA_N, "1d", ["close"], skip_paused=True, fq="pre", df=True)
    if len(h) < MA_N:
        log.warn("MA20 数据不足（前 %d 日），今日不做T" % len(h))
        g.signal = False
        return
    prev_close = h["close"][-1]
    ma = h["close"].mean()
    cd = get_current_data()[ETF]
    g.signal = bool(prev_close < ma and not cd.paused)
    log.info("信号判定：昨收 %.3f %s MA20(%.3f)，今日%s" %
             (prev_close, "<" if prev_close < ma else ">=", ma, "做T" if g.signal else "休息"))


def t_buy(context):
    """09:30 买腿：信号日先卸货基、再买入 510300，记录腿股数与占用现金"""
    if g.base_shares is None:
        # 底仓刚成交的首日：记录底仓股数，今日不做T（T+1 下无可卖老股）
        g.base_shares = context.portfolio.positions[ETF].total_amount
        log.info("底仓建成 %d 股，自次日起可做T" % g.base_shares)
        return
    if not g.signal:
        return
    cd = get_current_data()[ETF]
    if cd.paused:
        log.warn("510300 停牌，放弃今日做T")
        return
    if USE_MMF:
        order_target_value(MMF, 0)  # 卸货基释放买腿现金
    pv = context.portfolio
    buy_value = min(pv.available_cash * 0.998, pv.total_value * SWING_CAP)
    if buy_value < SETTLE_MIN:
        log.warn("可用现金 %.0f 不足以做T，放弃" % pv.available_cash)
        return
    before = pv.positions[ETF].total_amount
    cash0 = pv.available_cash
    order_value(ETF, buy_value)
    g.leg_shares = pv.positions[ETF].total_amount - before
    g.leg_outlay = cash0 - pv.available_cash  # 买腿实际占用现金（含费）
    log.info("买腿：%.0f 元 → %d 股" % (buy_value, g.leg_shares))


def t_sell(context):
    """14:55 卖腿：卖出【与早盘同股数】的底仓（T+1 老股），现金回泊货基，结转做T盈亏"""
    if g.leg_shares <= 0:
        return
    cd = get_current_data()[ETF]
    if cd.paused:
        log.error("14:55 停牌无法卖腿！做T腿留存过夜，底仓闭环破环，请人工核查")
        return
    before_close = context.portfolio.positions[ETF].total_amount
    cash1 = context.portfolio.available_cash
    order(ETF, -g.leg_shares)
    cash2 = context.portfolio.available_cash
    g.t_profit += (cash2 - cash1) - g.leg_outlay
    g.t_days += 1
    log.info("卖腿 %d 股，本轮做T盈亏 %.0f 元（累计 %.0f）" %
             (g.leg_shares, (cash2 - cash1) - g.leg_outlay, g.t_profit))
    log.info("收盘校验：%d → %d 股（底仓 %d）" % (before_close, before_close - g.leg_shares, g.base_shares))
    g.leg_shares = 0
    if USE_MMF:
        order_value(MMF, context.portfolio.available_cash * 0.998)


def report(context):
    """收盘后校验：底仓股数必须恒定；输出累计指标"""
    cur = context.portfolio.positions[ETF].total_amount
    if g.base_shares is not None and cur != g.base_shares:
        log.error("底仓股数异常：今日 %d vs 底仓 %d（有未平仓做T腿或分红股）" % (cur, g.base_shares))
    shrunk = (context.portfolio.total_value / context.portfolio.starting_cash - 1) * 100
    log.info("日终：净值 %.3f（累计 %+0.2f%%），底仓 %d 股，做T %d 天，做T累计盈亏 %.0f 元" %
             (context.portfolio.total_value / context.portfolio.starting_cash, shrunk,
              cur, g.t_days, g.t_profit))
