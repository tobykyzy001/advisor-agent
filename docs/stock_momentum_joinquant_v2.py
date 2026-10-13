# -*- coding: utf-8 -*-
# A股个股动量轮选 v2（聚宽可回测版）—— 行业前置两段式
#
# 方案与证据链见《docs/stock_momentum_joinquant.md》第九节/第十节。
#
# ⚠️ 诚实声明（2026-10-11）：生成当日未经实测。前两轮实测结论（10.3）：
#    「全 A 个股裸动量 top50」在 2016–2026 全区间被证伪（前半 −42.8% vs 基准 −1.5%；
#    后半 −34.8% vs 基准 +65.4%，信息比率 −0.57）。判定：观察池版的正贡献只能来自
#    池子的预筛选——前置选股是必要条件。本版即验证该假说的回测载体。
#
# 设计原则（对照纯度）：
#   引擎（打分/双闸/大盘关/缓冲/等权/顺延/成本）与 v1.1 完全一致、一行不动，
#   唯一变量 = 股票池来源：v1.1 全 A 清洗池 → v2 申万一级行业动量 top-K 成分股池。
#   USE_INDUSTRY_FILTER=False 时行为退化为 v1.1 全 A 基线（同文件可复核对，防实现漂移）。
#
# 两段式：
#   第一段（行业层动量，月频）：每股 skip_vol 得分（与 v1.1 同一引擎产出）按申万一级
#     行业取中位数，31 个行业排名取 top IND_TOP_K（默认 4），**每月首周刷新一次、
#     月内不动**（行业动量的经典频率是月度——海通/华泰研报均月频口径，周切行业只会
#     多交鞭打费；IND_FREQ='weekly' 可切回周频做 A/B）——行业层 1–3 个月动量在 A 股
#     显著更强（Su 2011 EMFT；海通 2018 行业系列 7；华泰 2018 系列 4；本仓文档 3.1）。
#   第二段（个股层）：只在 top-K 行业成分股池内跑 v1.1 引擎（top50 等权/缓冲75/
#     趋势+反追高只管新买/大盘关冻结/纯排名退出/退市保护/涨跌停顺延）。
#   退出语义：行业跌出 top-K → 成分股整体失去保留资格（行业轮动换仓）；
#     冻结周（中证全指 mom60≤-5%）照常全保留。
#
# 已知近似与声明：
#   a) 行业分类用 get_industry(codes, date=当日) 逐周取，避免全期静态映射的未来函数；
#      但「公司→行业」的分类学本身是现行口径回溯应用（SW 2021 改版等），属近似，
#      已尽力消除、无法完全剔除。
#   b) 行业动量未用行业指数行情，而是成分股得分的中位数——机制上更贴近
#      「行业动量经成分股传导」（Su 2011），且不依赖申万指数行情可得性。
#
# 使用方法：聚宽「我的策略」→ 新建策略 → 粘贴本代码 →
#   回测区间 2016-01-04 ~ 2026-06-30、基准 000300.XSHG、初始资金 100000000、频率「天」，
#   与 v1.1（10.2）同口径对照。中间的消融开关全在顶部配置区。
#
# v2.1（2026-10-11，行业窗口消融）：新增配置项 IND_LOOKBACK_DAYS_DEF / IND_SKIP_DAYS_DEF。
# v2.2（2026-10-11，组合宽度 + 个股绝对动量闸消融，用户提案）：
#   TOP_N_DEF 50→8（对齐现行技能的 8 仓结构，buffer 随 1.5N=12）+
#   USE_ABS_MOM_ENTRY_DEF=True（新买入必须 score>0，池内正动量不足 8 只时自然空槽降仓
#   ——个股层的绝对动量阀，比 v3 的滞后市场闸少一层独立市场状态延迟）；
#   对照基准 = v2 基线（−10.30%，top50 无绝对动量闸），行业窗复原 126/21（v2.1 的 60/5
#   变量已单独测过）。改 TOP_N_DEF=50 + USE_ABS_MOM_ENTRY_DEF=False 即回 v2 基线。
# v2.3（2026-10-12，全A指数动量全退闸，用户提案，已实测 → 10.8）：
#   −19.50%/全程（vs v2.2 −23.85%），但机制=仓位近似砍半（Beta 0.495、263/536 周场外）。
#   判定：滞后二值闸家族全部测完，最好成绩≈半仓，不产生真实超额。
# v2.4（2026-10-12，波动率目标连续量仓位管理——「涨一波跌一波」的文献正解）：
#   复原 v2.2 全部设置（含 'hold'/-0.05），新增 USE_VOL_TARGET_DEF=True：
#   每日收盘快照组合净值→周度按近 21 日实现波动缩放敞口（min(1, 0.18/实现波动)）——
#   无开关、无跳变；动量崩溃时策略波动率先跳、仓位自动收缩（Barroso & Santa-Clara
#   2015 JFE 的核心机制）。组内对照基准 = v2.2（−23.85%，10.7）。
#   关闭波动率目标（USE_VOL_TARGET_DEF=False）即复原 v2.2。
# v2.5（2026-10-12，行业粒度三级）：IND_LEVEL_DEF='sw_l3'（默认开启；sw_l1 一级=基线）。
# v2.6（2026-10-12，全正动量行业版，用户提案：999+闸0.0）——**已弃**：中途取消
#   （2020-02），已有区间全期劣于 v2.2（2017 −22.1 vs −14.8；2019 +61.2 vs +91.5）。
#   死因：行情正常的月份过关三级行业几十上百个、池子≈全市场，行业前置层消失，
#   退化为全 A 正侧取妖股（v1 被 −63% 证伪的形态套了个壳）。
# v2.7（2026-10-12，代表团制，用户提案「优中选优」形态）：
#   三级行业动量 top 4 → **每行业先内部优中选优**（过闸+动量最高前 CREAM_PER_IND=4 名）
#   → 代表团 ≤16 只（全过闸）→ 团内按动量取 top8、缓冲 12。
#   行业分散由结构写死（各行业先遣代表再比武、第一名链吃不满 8 仓)；
#   行业动量为负的行业连一个代表都出不来 → 熊市代表团自然变瘦甚至为空 → 自动降仓，
#   不再依赖任何滞后市场闸。改 CREAM_PER_IND_DEF=None 退回成分股合池大排名（v2~v2.6）。
#
# ⚠️ 重大勘误（2026-10-12 自审发现）：**v2.4/v2.6 实跑的「波动率目标版」实际未生效**——
#   下单与漂移判断误用 total*weight 而非 slot_value，敞口乘子从未进入订单；那两版
#   曲线等效于对应的「无波动目标」基线，日志/record 里的「敞口xx%」都只是计算后未用
#   的显示量。本次已修复为 slot_value 全链路下单（v2.2 等不启用波动目标的版本
#   因 vol_scale=1.0 行为不受影响；v3 为独立新文件不受此 bug 影响，结果仍有效）。
#
# v2.8（2026-10-12，技能仿真模式，用户提案：原样模拟现行 momentum-rotation 工具）：
#   SIMULATE_SKILL_DEF=True 时完全复刻技能规则——快慢双榜（mom20 快榜/mom120 慢榜，
#   原始区间涨幅、不跳月不除波动）、五步法（老仓任一榜前16保留→快4→慢4→快续扫→
#   慢续扫至8仓）、大盘状态=代表团池内 mom60 中位数≤-5% 冻结新开仓（不用指数）、
#   无 score>0 闸、无波动目标、无顺延；代表团池（三级 top4 × 每行业 mom120 前 4）
#   充当「人工观察仓景气成果」的可回测代理。引擎原样、只换池子——这才回答
#   「现行工具在全市场口径下到底行不行」。
# v2.9（2026-10-12，上帝视角诊断组 ORACLE，用户提案）：ORACLE_ENABLED_DEF=True 时
#   行业集改为按年份查表（ORACLE_INDUSTRIES_DEF=申万**三级**指数当年收益 top8,
#   335 个三级指数日线复算——三级口径才区分得开 CPO/PCB/CXO 类真景气簇,hindsight！）。
#   用途只限上界诊断：ORACLE 好 = 景气信息值钱、瓶颈在自动化选板；ORACLE 也不行 =
#   个股动量引擎本身没救、收线。**绝非可交易策略，收益数字不得用于任何策略宣传。**
#   ORACLE 中途态（2026-10-12 实测,backtestId=7cfc23e22031955b18dd3821e43dc6a0,
#   取消于 2024）：**峰值 2021-09-14 累计 +742%**——2018 −5.5%(基准−26.3)、2020 +131.8%、
#   2021 +63.3%(基准−6.2)；上界诊断第一条成立=景气信息极其值钱,瓶颈只剩自动化选板
#   与裸引擎的回撤返还（2022 −16.9/2023 −18.7）。
# v2.10（2026-10-12，技能仿真解禁波动目标，用户提案）：SKILL_VOL_TARGET_DEF=True 时
#   技能仿真模式也启用连续量仓位管理（v2.8 求引擎纯度曾强制关闭,False 复原 v2.8）——
#   对 ORACLE 峰值%+742 的回撤返还段，首次真正的波动目标检验（v2.4/v2.6 曾档位接错
#   未生效,已修复 slot_value 全链路）。
# v2.11（2026-10-13，宽度+哑铃防守腿，用户提案）：TOP_N_DEF 8→16（缓冲 auto=24），
#   DEFENSIVE_ASSET_DEF=510880 红利ETF + DEFENSIVE_WEIGHT_DEF=0.30——股票组合拿 70%、
#   红利腿固定 30% 每周再平衡（漂移<2%不动;ETF 免印花税/佣金万1/滑点万5）。
#   人能执行的回撤管理,替代波动目标（机械缩放人跟不上）;预计收益打七折、回撤打六折,
#   与 v2.9 ORACLE（+1337.91%/DD65.54%）同引擎同名单对照,只有宽度与哑铃两个变量。
#
# 仅供研究参考，不构成投资建议。

import math

import numpy as np
import pandas as pd
from jqdata import *

# ==================== 参数配置区 ★ 消融组只改这里 ====================

# —— 行业前置（v2 核心开关）——
USE_INDUSTRY_FILTER_DEF = True   # False = 退化为 v1.1 全 A 基线（对照复核对用）
IND_TOP_K_DEF = 4                # 入选行业数（v2.7 代表团制 = 4；v2.6 的 999 全正行业版实测
                                 # 中途劣于 v2.2 已弃——「全正≈全市场」稀释了行业前置的「窄」）
IND_MIN_SCORE_DEF = None         # 行业绝对动量闸（None=关闭；0.0 = v2.6 已弃版本才用）
CREAM_PER_IND_DEF = 4            # v2.7 代表团制：每个入选行业取「过闸（趋势/反追高/score>0）
                                 # 且动量最高」的前 N 名 → 代表团 ≈ 4×4=16 只,
                                 # 再在代表团内按动量取 TOP_N(8)、缓冲 1.5N(12)。
                                 # 行业分散是结构写死的（各行业先遣代表再比武）,
                                 # 行业动量为负的行业连一个代表都出不来 → 熊市自然空槽。
                                 # None/0 = 关闭代表团制,退回成分股合池大排名（v2~v2.6 行为）
IND_AGG_DEF = 'median'           # 行业得分聚合：'median'(默认) / 'mean'
IND_MIN_MEMBERS_DEF = 5          # 行业内有效得分股票数下限，不足不参与行业排名
IND_FREQ_DEF = 'monthly'         # 行业集刷新频率：'monthly'(默认,每月首周刷新,月内不动)
                                 #                 /'weekly'(每周重排,可作 A/B)
IND_LOOKBACK_DAYS_DEF = 126      # 行业层动量窗口（v2.2 起复原 v2 基线 126；60 = v2.1 消融值已测）
IND_SKIP_DAYS_DEF = 21           # 行业层跳过近端 N 日（v2.2 起复原 v2 基线 21；5 = v2.1 消融值已测）
IND_LEVEL_DEF = 'sw_l3'          # 行业粒度（2026-10-12 用户提案上三级；'sw_l1' 一级31个=
                                 # 基线已测 / 'sw_l2' 二级约124个 / 'sw_l3' 三级约250个；
                                 # 注意：入选的三级行业可能同属一条题材链——更贴近景气簇,
                                 # 但相关性不降反升,组合波动会更大）

# —— 技能仿真（v2.8，用户提案 2026-10-12：原样模拟现行 momentum-rotation 工具）——
SIMULATE_SKILL_DEF = True   # True = 完全复刻现行技能规则（此时下方 skip_vol/除波动/跳月、
                            # score>0 闸、波动目标、keep 缓冲比 全部失效，改用技能口径）：
                            #   · 快慢双榜：mom20 快榜 / mom120 慢榜（原始区间涨幅,不跳月不除波动）
                            #   · 五步法：老仓任一榜前 16 保留 → 快4 → 慢4 → 快续扫 → 慢续扫至 8 仓
                            #   · 大盘状态：代表团池内 mom60 中位数 ±5%（不用指数）
                            #   · 趋势/反追高闸同原版；退出纯排名滚动
                            #   代表团池（三级行业 top4 × 每行业 mom120 前 4）充当「人工观察仓
                            #   景气成果」的可回测代理。False = 回退 v2.7 改良引擎行为。

# —— 上帝视角诊断组（ORACLE，2026-10-12 用户提案）——
ORACLE_ENABLED_DEF = True   # True = 行业集不再按月动量选,改为按年份查表（hindsight！）。
                            # 目的:给「景气信息本身值多少钱」做**上界诊断**——
                            #   ORACLE 组好 → 瓶颈在自动化选板,景气研究值得作为手动输入;
                            #   ORACLE 组也不行 → 个股动量引擎本身没救,整条线收工。
                            # ⚠️ 本组用 2026 年回头看的赢家名单,不是可交易策略,收益数字
                            #   不得作为任何策略宣传。False = 回退正常月选行业。
ORACLE_INDUSTRIES_DEF = {   # 申万三级指数当年收益 top8（335 个三级指数日线复算,2026-10-12;
                            # 三级口径才能区分 CPO/PCB/CXO 类真景气簇,一级桶是遮住的）
    2016: ['850623', '850324', '851112', '852032', '850322', '850372', '851241', '850615'],  # 房屋建设Ⅲ/其他化学原料/空调/超市/氯碱/聚氨酯/肉制品/耐火材料
    2017: ['851112', '851941', '850523', '851243', '850372', '850813', '850833', '850544'],  # 空调/保险Ⅲ/非金属材料Ⅲ/乳品/聚氨酯/半导体材料/光学元件/其他小金属
    2018: ['851242', '852111', '852131', '851612', '851232', '851611', '850813', '850623'],  # 调味发酵品Ⅲ/人工景区/旅游综合/水力发电/啤酒/火力发电/半导体材料/房屋建设Ⅲ
    2019: ['850813', '850822', '851233', '850372', '850833', '851112', '852111', '850323'],  # 半导体材料/印制电路板/其他酒类/聚氨酯/光学元件/空调/人工景区/无机盐
    2020: ['850152', '851233', '852131', '851242', '857421', '850353', '850823', '851232'],  # 粮油加工/其他酒类/旅游综合/调味发酵品Ⅲ/航空装备Ⅲ/改性塑料/被动元件/啤酒
    2021: ['850382', '850332', '850343', '850741', '850362', '850322', '857242', '857321'],  # 氟化工/磷肥及磷化工/粘胶/电机Ⅲ/其他橡胶制品/氯碱/化学工程/电网自动化设备
    2022: ['852112', '859511', '859632', '852131', '859512', '852111', '850523', '852121'],  # 自然景区/动力煤/油品石化贸易/旅游综合/焦煤/人工景区/非金属材料Ⅲ/酒店
    2023: ['851024', '851422', '857692', '852213', '857641', '850833', '857674', '850831'],  # 通信网络设备及器件/印刷/大众出版/通信工程及服务/游戏Ⅲ/光学元件/门户网站/面板
    2024: ['850913', '850912', '851024', '850545', '850814', '851116', '850343', '851112'],  # 商用载客车/商用载货车/通信网络设备及器件/钼/数字芯片设计/冰洗/粘胶/空调
    2025: ['851024', '850822', '857411', '850552', '850355', '850772', '851161', '850544'],  # 通信网络设备及器件/印制电路板/航天装备Ⅲ/铜/膜材料/工程机械器件/家电零部件Ⅲ/其他小金属
    2026: ['851025', '857122', '850823', '850818', '851563', '850813', '850822', '850817'],  # 通信线缆及配套/玻纤制造/被动元件/半导体设备/医疗研发外包/半导体材料/印制电路板/集成电路封测
}

# —— 信号（与 v1.1 完全一致）——
SCORE_MODE_DEF = 'skip_vol'   # 'skip_vol' 6-1动量÷波动（默认基线；'skip_raw'/'mom120_raw'/'mom20_raw' 为消融对照）
LOOKBACK_DAYS_DEF = 126
SKIP_DAYS_DEF = 21

# —— 组合 ——
TOP_N_DEF = 16              # v2.11：8→16（用户提案 2026-10-13；8 = v2.2 起基线）
KEEP_RANK_RATIO_DEF = 1.5   # 排名缓冲：老仓在池内前 ceil(1.5N) 名内保留
USE_ABS_MOM_ENTRY_DEF = True  # v2.2 新买入绝对动量闸：新买必须 score>0（池内正动量不足 8 只
                              # 时自然空槽降仓——个股层的绝对动量阀,False = v2 基线行为）
SCORE_MAX_DEF = None        # 新买入动量上限（默认 None 不限制；如设 60：新买必须 score ≤ 60，
                            # 排除连板妖股类抛物线头部——ETF v7 的 score 上限组件同源；
                            # 反追高 28%/24% 已在挡一部分,此上限是更硬的第二道,默认依赖前者）
USE_PCT_MODE_DEF = False
TOP_PCT_DEF = 0.10
HOLD_CAP_DEF = 300

# —— 哑铃防守腿（v2.11，用户提案 2026-10-13：人能执行的回撤管理，替代波动目标）——
DEFENSIVE_ASSET_DEF = '510880.XSHG'   # 防守资产：红利ETF
DEFENSIVE_WEIGHT_DEF = 0.30           # 防守腿固定权重（30%）；股票组合拿到其余 70%
                                      # 每周随调仓再平衡回原比例（漂移<总资产2%不动）；
                                      # 0 = 关闭哑铃,全部资金投入股票仓

# —— 闸门（只管新买入；老仓留存只看排名）——
USE_TREND_FILTER_DEF = True
USE_ANTI_CHASE_DEF = True
USE_MARKET_GUARD_DEF = True
MARKET_INDEX_DEF = '000985.XSHG'
GUARD_WIN_DEF = 60
# 阈值与动作（v2.4 起复原 v2.2 基线语义 -0.05 + 'hold'；v2.3 全退版已测 −19.50%，见 10.8）
GUARD_DOWN_DEF = -0.05
GUARD_MODE_DEF = 'hold'          # 'hold'=冻结新买、老仓原样保留（基线语义）；'exit'=触发全退现金

# —— 波动率目标仓位（v2.4 核心开关）——
USE_VOL_TARGET_DEF = True        # 波动率目标仓位管理：连续量、无开关跳变——按组合自身
                                 # 实现波动反向缩放敞口（Barroso & Santa-Clara 2015 JFE：
                                 # 波动管理基本消除动量崩溃、夏普近倍）。False 关闭
VOL_TARGET_DEF = 0.18            # 目标年化波动（对 v2.2 实测波动 37% 是约半仓档；A/B 试 0.16/0.24）
VOL_WIN_DEF = 21                 # 实现波动估计窗口（交易日，≈1 个月，BSC 或文献常用口径）
VOL_CAP_DEF = 1.0                # 敞口上限（1.0=只降不加杠杆）
SKILL_VOL_TARGET_DEF = True      # v2.10：技能仿真模式下也启用波动目标（用户提案——
                                 # ORACLE 峰值 +742% 但回撤返还巨大,裸引擎需要仓位阀;
                                 # False = v2.8 技能纯度基线语义,波动目标强制关）

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
    # 哑铃防守腿走 ETF 口径：免印花税、佣金万1、滑点万5往返（同 v7 口径）
    set_order_cost(OrderCost(open_tax=0, close_tax=0,
                             open_commission=0.0001, close_commission=0.0001,
                             close_today_commission=0, min_commission=0), type='fund')
    set_slippage(PriceRelatedSlippage(0.0005), type='fund')
    log.set_level('order', 'error')
    log.set_level('system', 'error')

    g.use_industry_filter = USE_INDUSTRY_FILTER_DEF
    g.ind_top_k = IND_TOP_K_DEF
    g.ind_min_score = IND_MIN_SCORE_DEF   # 行业绝对动量闸（None=关闭）
    g.cream_per_ind = CREAM_PER_IND_DEF   # 代表团制：每入选行业出前 N 名过闸代表（None/0=关闭）
    g.ind_agg = IND_AGG_DEF
    g.ind_min_members = IND_MIN_MEMBERS_DEF
    g.ind_freq = IND_FREQ_DEF       # 行业集刷新频率：monthly=每月首周刷新 / weekly=每周重排
    g.ind_month = None              # 本月行业集水印（'YYYY-MM'），月内不重复重排
    g.ind_lookback = IND_LOOKBACK_DAYS_DEF   # 行业层动量窗口（126/21 = 复原 v2 基线）
    g.ind_skip = IND_SKIP_DAYS_DEF           # 行业层跳过近端
    g.ind_level = IND_LEVEL_DEF              # 行业粒度：sw_l1/sw_l2/sw_l3（v2.5）
    g.score_mode = SCORE_MODE_DEF
    g.lookback = LOOKBACK_DAYS_DEF
    g.skip = SKIP_DAYS_DEF
    g.top_n = TOP_N_DEF
    g.keep_rank = int(math.ceil(TOP_N_DEF * KEEP_RANK_RATIO_DEF))
    g.use_pct_mode = USE_PCT_MODE_DEF
    g.top_pct = TOP_PCT_DEF
    g.hold_cap = HOLD_CAP_DEF
    g.use_abs_mom_entry = USE_ABS_MOM_ENTRY_DEF   # 新买入绝对动量闸 score>0
    g.score_max = SCORE_MAX_DEF                   # 新买入动量上限（None=不限制）
    g.use_vol_target = USE_VOL_TARGET_DEF         # 波动率目标（连续量仓位管理）
    g.simulate_skill = SIMULATE_SKILL_DEF         # 技能仿真模式（v2.8）：开启时采用
    if g.simulate_skill:                          # 技能原口径——无 score>0 闸、快慢双榜、
        g.keep_rank = max(16, int(math.ceil(g.top_n * 1.5)))  # 缓冲；v2.11 N=16 → 24
        if not SKILL_VOL_TARGET_DEF:              # 波动目标 v2.8 为求引擎纯度强制关，
            g.use_vol_target = False              # v2.10 起可经 SKILL_VOL_TARGET_DEF 打开
    g.defensive_asset = DEFENSIVE_ASSET_DEF       # 哑铃防守腿（v2.11）：红利ETF
    g.defensive_weight = DEFENSIVE_WEIGHT_DEF     # 防守腿固定权重（0=关闭）
    g.oracle_enabled = ORACLE_ENABLED_DEF         # 上帝视角诊断组：行业集按年份查表
    g.oracle_industries = ORACLE_INDUSTRIES_DEF
    g.oracle_year = None
    g.vol_target = VOL_TARGET_DEF
    g.vol_win = VOL_WIN_DEF
    g.vol_cap = VOL_CAP_DEF
    g.pv = []                # 每日净值轨迹（收盘快照），波动率目标的估计原料
    g.vol_scale = 1.0        # 当前的敞口乘子（≤1）
    g.use_trend = USE_TREND_FILTER_DEF
    g.use_antichase = USE_ANTI_CHASE_DEF
    g.use_guard = USE_MARKET_GUARD_DEF
    g.market_index = MARKET_INDEX_DEF
    g.guard_win = GUARD_WIN_DEF
    g.guard_down = GUARD_DOWN_DEF
    g.guard_mode = GUARD_MODE_DEF     # 'exit'=触发全退现金 / 'hold'=冻结新买老仓保留
    g.min_list_days = MIN_LIST_DAYS_DEF
    g.min_avg_amount = MIN_AVG_AMOUNT_DEF
    g.exclude_kcb = EXCLUDE_KCB_DEF
    g.min_trade_pct = MIN_TRADE_PCT_DEF
    g.delist_guard_days = DELIST_GUARD_DAYS_DEF
    g.batch_size = BATCH_SIZE_DEF
    g.last_top_inds = []          # 行业取数失败时的存续行业集（274 语义：不静默改池）
    g.hist_count = max(g.lookback, 120) + 1

    run_weekly(rebalance, weekday=5, time='14:50')  # 每周第 5 个交易日 14:50
    log.info('动量 v2 初始化：行业前置=%s %s top%d行业(%s) 行业窗%d/%d | %s lookback=%d skip=%d | top%d 缓冲%d | 波动目标%s | 技能仿真=%s' % (
        g.use_industry_filter, g.ind_level, g.ind_top_k, g.ind_agg, g.ind_lookback, g.ind_skip,
        g.score_mode, g.lookback, g.skip, g.top_n, g.keep_rank,
        ('%.0f%%/%dd' % (g.vol_target * 100, g.vol_win)) if g.use_vol_target else '关',
        g.simulate_skill))


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
            # 技能仿真场原料（v2.8）：三条原始区间动量（不跳月、不除波动），恒为顺手算
            def _raw_mom(n):
                b = c[-1 - n]
                return px_now / b - 1.0 if np.isfinite(b) and b > 0 else np.nan
            mom20 = _raw_mom(20)
            mom120 = _raw_mom(120)
            mom60 = _raw_mom(60)
            # 行业层动量得分（独立窗口,仅用于行业聚合,不参与个股排名;
            # v2.1 消融:60/5≈3 个月,为「行业窗口」单变量对照项——同一份 K 线顺带算,零额外取数）
            ind_score = np.nan
            is1 = c[-1] if g.ind_skip == 0 else c[-1 - g.ind_skip]
            is0 = c[-1 - g.ind_lookback]
            if np.isfinite(is1) and np.isfinite(is0) and is0 > 0:
                ind_mom = is1 / is0 - 1.0
                iwin = c[-1 - g.ind_lookback:] if g.ind_skip == 0 \
                    else c[-1 - g.ind_lookback:-g.ind_skip]
                irets = np.diff(iwin) / iwin[:-1]
                irets = irets[np.isfinite(irets)]
                if len(irets) >= 20:
                    ivol = float(np.std(irets))
                    ind_score = ind_mom / ivol if ivol > 1e-6 else ind_mom
                else:
                    ind_score = ind_mom
            out.append({'code': code, 'score': score, 'entry_ok': entry_ok,
                        'avg_amount': avg_amount, 'close': px_now,
                        'mom20': mom20, 'mom120': mom120, 'mom60': mom60,
                        'ind_score': ind_score, 'ind_code': '', 'ind_name': ''})
        except Exception:
            continue
    return out


# ==================== 行业前置：得分按申万行业聚合 → top-K 行业 ====================
def annotate_industry(context, scored):
    """给每条打分记录标注申万行业（粒度 g.ind_level：sw_l1/sw_l2/sw_l3）。

    每周都要取：行业集固定、成分归属随个股变化。
    返回 True/False；失败不改动记录（ind_code 保持空串），由调用方兜底。
    """
    try:
        ind_map = get_industry([x['code'] for x in scored], date=context.current_dt.date())
    except Exception as e:
        log.info('get_industry 取数失败：%s' % e)
        return False
    level = 'sw_l3' if g.oracle_enabled else \
        (g.ind_level if g.ind_level in ('sw_l1', 'sw_l2', 'sw_l3') else 'sw_l1')
    for x in scored:
        sw = ind_map.get(x['code'], {}).get(level, {}) or {}
        x['ind_code'], x['ind_name'] = sw.get('industry_code', ''), sw.get('industry_name', '')
    return True


def rank_industries(scored):
    """按行业内得分聚合排名，返回过关行业 [(ind_code, ind_name, agg, n_members)]，降序。

    聚合用 ind_score（行业层独立窗口 ind_lookback/ind_skip），与个股排名的 score 解耦；
    ind_score 缺失（NaN）的个股不计入聚合。
    绝对动量闸（v2.6）：ind_min_score 设定时，只有聚合分 > 阈值才具备入选资格——
    与 IND_TOP_K=999 联用即「所有动量为正的行业全入选」（无名次截断）。
    """
    groups = {}
    for x in scored:
        if x['ind_code'] and np.isfinite(x.get('ind_score', np.nan)):
            groups.setdefault(x['ind_code'], {'name': x['ind_name'], 'scores': []})['scores'].append(x['ind_score'])
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
    for ic, iname, agg, n in top[:12]:
        log.info('  行业入选 %s %s agg=%.3f (n=%d)' % (ic, iname, agg, n))
    if len(top) > 12:
        log.info('  …共 %d 个行业过关（仅打印前 12）' % len(top))
    return top


# ==================== 波动率目标：每日净值快照（收盘后） ====================
def after_trading_end(context):
    """每日收盘记录组合净值，作为实现波动率的估计原料（v2.4）。

    波动率目标用的是**策略自己的**收益波动（Barroso & Santa-Clara 2015 的口径），
    不是市场指数波动——动量崩溃时策略波动率先跳起来，仓位自动收缩，无需任何
    「顶/底」判断，这是它与二值闸（v3/v2.3 已证伪）的本质区别。
    """
    if not g.use_vol_target:
        return
    g.pv.append(context.portfolio.total_value)
    if len(g.pv) > 260:
        g.pv[:] = g.pv[-260:]


def vol_target_scale():
    """敞口乘子 = min(cap, 目标波动 / 实现波动)；数据不足窗口时返回 1.0。"""
    if not g.use_vol_target or len(g.pv) <= g.vol_win:
        return 1.0
    arr = np.asarray(g.pv[-(g.vol_win + 1):], dtype=float)
    rets = np.diff(arr) / arr[:-1]
    rets = rets[np.isfinite(rets)]
    if len(rets) < max(10, g.vol_win - 3):
        return 1.0
    vol = float(np.std(rets)) * math.sqrt(250)
    if vol <= 1e-6:
        return 1.0
    return min(g.vol_cap, g.vol_target / vol)


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


# ==================== 技能仿真（v2.8：原样复刻现行 momentum-rotation） ====================
def skill_regime_down(pool):
    """技能大盘状态关：池内全部股票 mom60 的中位数 ≤ -5% → 下行（冻结新开仓，老仓保留）。"""
    valid = [x['mom60'] for x in pool
             if x.get('mom60') is not None and np.isfinite(x['mom60'])]
    if not valid:
        return False
    return float(np.median(np.asarray(valid, dtype=float))) <= -0.05


def skill_build_target(pool, current):
    """现行技能五步法：过滤(合格) → 快榜(mom20)/慢榜(mom120)双轨排名 →
    老仓任一榜前 keep_rank 保留 → 快半 → 慢半 → 快续扫 → 慢续扫至 top_n 仓。

    keep_rank/top_n 参数化（v2.11 起支持 N=16：half=8、keep=24）；
    N=8 时严格等同现行技能（keep16、快4慢4）。"""
    half = max(1, g.top_n // 2)
    q = [x for x in pool if x['entry_ok']
         and np.isfinite(x.get('mom20', np.nan))
         and np.isfinite(x.get('mom120', np.nan))]
    fast = sorted(q, key=lambda x: (-x['mom20'], x['code']))
    slow = sorted(q, key=lambda x: (-x['mom120'], x['code']))
    rf = {x['code']: i + 1 for i, x in enumerate(fast)}
    rs = {x['code']: i + 1 for i, x in enumerate(slow)}
    target, seen = [], set()
    for code in sorted(current):
        if rf.get(code, 10 ** 9) <= g.keep_rank or rs.get(code, 10 ** 9) <= g.keep_rank:
            target.append(code)
            seen.add(code)

    def _fill(seq):
        for x in seq:
            if len(target) >= g.top_n:
                return True
            if x['code'] in seen:
                continue
            target.append(x['code'])
            seen.add(x['code'])
        return len(target) >= g.top_n

    if _fill(fast[:half]):
        return target
    if _fill(slow[:half]):
        return target
    if _fill(fast):
        return target
    _fill(slow)
    return target


# ==================== 周频调仓 ====================
def rebalance(context):
    cd = get_current_data()
    today = context.current_dt.date()

    # 0. 退市保护：临近退市的老仓优先强平（卖不出则如实留痕）
    for code in list(context.portfolio.positions.keys()):
        pos = context.portfolio.positions[code]
        if pos.total_amount <= 0:
            continue
        if code == g.defensive_asset:
            continue  # 哑铃防守腿（ETF）不走退市保护
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
    # 「行业池为空」两种成因分开（v3 实测教训）：取数失败 → fail-closed 保留老仓；
    # 绝对动量闸过滤为空（真信号）→ 老仓按正常排名规则轮出、自动降仓。
    if g.use_industry_filter:
        ind_fail = False
        ok = annotate_industry(context, scored)
        if g.oracle_enabled:
            # 上帝视角组：行业集按年份查表（hindsight），不再逐月动量排名
            if not ok:
                ind_fail = True
                top_inds = g.last_top_inds
            else:
                yr = today.year
                olist = g.oracle_industries.get(yr) or g.oracle_industries.get(yr - 1, [])
                code_set = set(olist)
                name_by = {}
                for x in scored:
                    if x['ind_code'] in code_set and x['ind_code'] not in name_by:
                        name_by[x['ind_code']] = x['ind_name']
                top_inds = [(c, name_by.get(c, c), float('nan'), 0) for c in olist]
                g.last_top_inds = top_inds
                if yr != g.oracle_year:
                    g.oracle_year = yr
                    log.info('  先知组 %d 行业(上帝视角,非策略!)：%s' % (
                        yr, ' '.join(t[1] for t in top_inds)))
        elif ok:
            this_month = today.strftime('%Y-%m')
            need_refresh = (g.ind_freq == 'weekly' or g.ind_month != this_month
                            or g.ind_month is None)
            if need_refresh:
                top_inds = rank_industries(scored)
                # 刷新结果照常落档——哪怕闸过滤后不足 K 甚至为空（空仓信号），
                # 不许拿着上月的行业集混过系统性下跌
                g.last_top_inds = top_inds
                g.ind_month = this_month
                log.info('  %s 行业集刷新（%s，入选%d）：%s' % (
                    this_month, g.ind_freq, len(top_inds),
                    ' '.join('%s %.3f' % (t[1], t[2]) for t in top_inds[:8]) or '（无过关行业）'))
            else:
                top_inds = g.last_top_inds
        else:
            ind_fail = True
            top_inds = g.last_top_inds
            log.info('  行业分类取数失败，沿用存续行业集：%s' % [t[1] for t in top_inds])
        indset = {t[0] for t in top_inds}
        pool = [x for x in scored if x['ind_code'] in indset]
        # 代表团制（v2.7）：每个入选行业内部先优中选优——取过闸且动量最高的前 N 名，
        # 4 行业×N = 代表团（默认 ≤16 只,全部过闸）,后续 top8/缓冲12 在代表团内进行。
        # 技能仿真模式（v2.8）：代表团改用技能慢榜口径选人（mom120 降序 + 双闸合格），
        # 充当「人工观察仓景气成果」的代理。
        if g.cream_per_ind:
            by_ind = {}
            for x in pool:
                by_ind.setdefault(x['ind_code'], []).append(x)
            cream = []
            for ic, xs in by_ind.items():
                if g.simulate_skill:
                    xs = [x for x in xs if x['entry_ok']
                          and np.isfinite(x.get('mom120', np.nan))]
                    xs.sort(key=lambda x: (-x['mom120'], x['code']))
                else:
                    xs.sort(key=lambda x: (-x['score'], x['code']))
                    xs = [x for x in xs
                          if x['entry_ok']
                          and (not g.use_abs_mom_entry or x['score'] > 0)
                          and (g.score_max is None or x['score'] <= g.score_max)]
                cream.extend(xs[:g.cream_per_ind])
            pool = cream
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

    # 2. 大盘状态关：技能仿真=代表团池内 mom60 中位数≤-5%（技能原口径）；
    #    其他版本=中证全指 mom60 ≤ GUARD_DOWN
    if g.simulate_skill:
        frozen = skill_regime_down(pool)
    else:
        frozen = market_guard_down(context)

    # 3. 目标持仓数与排名缓冲：pct 模式下缓冲随 N 联动
    if g.use_pct_mode:
        n_target = max(1, min(g.hold_cap, int(math.ceil(len(pool) * g.top_pct))))
        keep_rank = max(g.keep_rank, int(math.ceil(n_target * KEEP_RANK_RATIO_DEF)))
    else:
        n_target = g.top_n
        keep_rank = g.keep_rank

    current = [c for c in context.portfolio.positions.keys()
               if context.portfolio.positions[c].total_amount > 0]

    # 4. 补位闸（新买入必须全过）：入场闸 + 绝对动量闸（score>0，可关）
    #    + 动量上限（score ≤ SCORE_MAX，可关；默认 None 不启用）
    def buy_ok(x):
        return (x['entry_ok']
                and (not g.use_abs_mom_entry or x['score'] > 0)
                and (g.score_max is None or x['score'] <= g.score_max))

    if g.simulate_skill:
        # 技能仿真（v2.8）：冻结/池数据故障 → 老仓原样保留不开新仓；
        # 否则走技能五步法（skill_build_target：老仓 buffer16 → 快4 → 慢4 → 快续扫 → 慢续扫）
        if frozen or (not pool and (not g.use_industry_filter or ind_fail)):
            target = list(current)
        else:
            target = skill_build_target(pool, current)
    else:
        # 老仓保留：冻结周按 guard_mode——'exit'=全退成现金（v2.3）/'hold'=原样全保留；
        # 正常周 = 仍在池内排名前 keep_rank 名内 → 保留
        # （行业跌出 top-K 的老仓不在池内 → 视为排名无穷大 → 轮出，这就是行业换仓）
        if frozen:
            kept = [] if g.guard_mode == 'exit' else list(current)
        elif not pool and (not g.use_industry_filter or ind_fail):
            # 数据故障（整池打分失败 / 行业分类取数失败）→ 保留老仓；
            # 行业绝对动量闸过滤为空（真信号）不走这里——按下方排名规则自然轮出
            kept = list(current)
        else:
            kept = [c for c in current if prank_of.get(c, 10 ** 9) <= keep_rank]
        target = list(kept)

        # 4b. 补位：未冻结且池非空时，按池内排名补买入闸全过的股票
        if not frozen and pool:
            for c in pool_ranked:
                if len(target) >= n_target:
                    break
                if c in target:
                    continue
                if buy_ok(info_map[c]):
                    target.append(c)

    weight = 1.0 / max(1, n_target)

    # 5. 卖出：不在目标中的老仓；跌停/停牌卖不出 → 保留到下周再卖（如实留痕）
    sold, sell_failed = [], []
    for code in current:
        if code == g.defensive_asset:
            continue  # 哑铃防守腿（ETF）永远不卖,仓位由第 6 步再平衡统一维护
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

    # 波动率目标敞口（连续量）：仓位乘子每周按最新实现波动重算，
    # 直接作用于目标市值——降敞口=减仓、回升=补回，全程无开关跳变
    g.vol_scale = vol_target_scale()
    # 哑铃（v2.11）：股票组合只拿到 (1 - defensive_weight) 的资金，
    # 其余 defensive_weight 比例每周再平衡到红利ETF（见下方防守腿订单）
    equity_share = 1.0 - g.defensive_weight
    slot_value = total_value * weight * g.vol_scale * equity_share

    def can_open(c):
        return buyable(c) and slot_value >= 100 * cd[c].last_price

    held = set([c for c in context.portfolio.positions.keys()
                if context.portfolio.positions[c].total_amount > 0])
    reserved = set(target)
    bought, buy_skipped = [], []
    for code in target:
        pick = code
        if code in held:
            # 老仓只维护权重：停牌/涨停买不进就本周不动（绝不顺延再买防超配）
            if not buyable(pick):
                continue
        elif not can_open(pick):
            pick = None
            if not g.simulate_skill:
                # 顺延：在全体打分榜 keep_rank 名内找下一名（技能仿真模式不做顺延——
                # 技能原口径买不进就留槽位空着，不顺延）
                for c in pool_ranked:
                    if prank_of[c] > keep_rank:
                        break
                    if c in reserved or c in held:
                        continue
                    if not buy_ok(info_map[c]):
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
        # 注意:必须用 slot_value（含波动率目标敞口）下单——曾误用 total*weight 导致
        # 敞口乘子不进订单（v2.4/v2.6 实跑结果实质等效无波动目标版本,已立此存照）
        if abs(cur_value - slot_value) <= total_value * g.min_trade_pct and cur_value > 0:
            continue
        order_target_value(pick, slot_value)
        if pick not in held:
            bought.append('%s(r%d)' % (pick[:6], prank_of.get(pick, 0)))

    # 哑铃防守腿（v2.11）：每周再平衡回固定权重——人能执行的回撤管理（每月底核对一次即可）
    if g.defensive_weight > 0 and g.defensive_asset:
        tgt = total_value * g.defensive_weight
        cur = 0.0
        if g.defensive_asset in context.portfolio.positions:
            p = context.portfolio.positions[g.defensive_asset]
            cur = p.total_amount * p.price
        if abs(cur - tgt) > total_value * g.min_trade_pct:
            order_target_value(g.defensive_asset, tgt)
            log.info('  哑铃防守腿再平衡：%s → %.0f%%（%.0f万）' % (
                g.defensive_asset[:6], g.defensive_weight * 100, tgt / 1e4))

    # 7. 日志与记录
    ind_txt = ' '.join('%s' % t[1] for t in top_inds) if top_inds else ('全A' if not g.use_industry_filter else '空')
    if g.simulate_skill:
        slow_board = sorted(pool, key=lambda x: (-(x.get('mom120') if np.isfinite(x.get('mom120', np.nan)) else -9e9), x['code']))
        qual_top = [x for x in slow_board[:8] if np.isfinite(x.get('mom120', np.nan))]
        top5 = ' '.join('%s:%.1f%%' % (x['code'][:6], x['mom120'] * 100) for x in qual_top[:5])
        tail_label = '慢榜top5'
    else:
        qual_top = [x for x in pool if buy_ok(x)][:5]
        top5 = ' '.join('%s:%.3f' % (x['code'][:6], x['score']) for x in qual_top)
        tail_label = '闸后top5'
    status = ('冻结全退' if g.guard_mode == 'exit' else '冻结保留') if frozen else '正常'
    if g.use_vol_target:
        status += ' 敞口%.0f%%' % (g.vol_scale * 100)
    log.info('%s 行业[%s] 池%d(全A%d) 目标%d/%d %s | %s %s' % (
        today, ind_txt, len(pool), len(scored), len(target), n_target, status,
        tail_label, top5))
    if sold:
        log.info('  卖出 %d: %s' % (len(sold), ' '.join(sold)))
    if bought:
        log.info('  买入 %d: %s' % (len(bought), ' '.join(bought)))
    if sell_failed:
        log.info('  卖不出顺延: %s' % ' '.join(sell_failed))
    if buy_skipped:
        log.info('  买不进: %s' % ' '.join(buy_skipped))
    n_held = sum(1 for p in context.portfolio.positions.values() if p.total_amount > 0)
    record(持仓数=n_held, 冻结=int(frozen), 池内=float(len(pool)),
           敞口=float(g.vol_scale))
