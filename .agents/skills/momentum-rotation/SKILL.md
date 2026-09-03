---
name: momentum-rotation
description: 中期动量轮动选股技能。当用户要求「给观察股票池按中期动量排名选最强的一组等权买入」「做一套长短双动量+老仓粘性的轮动策略」「收盘后扫一遍观察池选8只等权持仓」时触发。核心：读观察仓标的池 → 用 tushare MCP 取每只近250日线 → 算 mom20/mom120/mom60 三条区间动量 → 三道过滤（MA120趋势关 / 反追高关 / 大盘状态关）→ 快慢双榜排名 + 老仓buffer16粘性 + 快4慢4补满8仓 → 等权组合 + 流动性约束 + 空仓兜底；退出纯靠排名滚动（无价格止损）。与 stock-valuation(个股估值)、prosperity-analysis(行业景气)、w-bottom-screener(形态买点)互补：本技能是「给定池子的动量排名→组合轮动」，不看估值/景气/形态。
---

# 中期动量轮动（Momentum Rotation）

把**观察仓标的池**按「长短期双动量」排名，在严格过滤 + 大盘择时下，选出一组（最多 8 只）**等权持有**的组合，并用「老仓粘性」把换手压到最低。

> 核心定位：这是一个**组合层面的动量择时/轮动器**，回答"我的池子里，按中期动量该持有哪些票、各占多少"。它不判断个股贵不贵（交给 `stock-valuation`）、不判断行业景气（交给 `prosperity-analysis`）、不识别形态买点（交给 `w-bottom-screener`）。

## 何时触发

- 用户说「帮我按中期动量给观察持仓排名选股」「扫一遍观察池选最强一组等权买」「做一套双动量+老仓缓冲的轮动策略」「大盘走弱了就停开新仓那种策略」。
- 定时盘后跑本技能，对观察池做一轮动量排名 + 组合调仓。

## 核心策略（口径完整版）

### 1. 买什么 —— 观察池 + 双动量窗口

- 从 `output/watchlist/watchlist.yaml`（观察仓，与 w-bottom-screener 共用）读标的池。
- 每只股票算三条**区间涨幅** `mom(N) = close[-1] / close[-1-N] − 1`：

| 指标 | 窗口 | 用途 |
|---|---|---|
| `mom20` | 20 交易日 | **快榜**（短期强度，捕捉动量切换速度） |
| `mom120` | 120 交易日 | **慢榜**（中期强度，压住组合稳定性） |
| `mom60` | 60 交易日 | **只看大盘状态，不参与选股排名** |

- **不合成**：快慢两条线各自独立排名，各用各的；不加权、不平滑。

### 2. 三道过滤器（任何一道不过都不买）

| 关 | 口径 | 不通过 |
|---|---|---|
| 趋势关 | 收盘价 ≥ MA120（停在 120 日均线上方） | 长期趋势向下 |
| 反追高关 | 偏离 MA20 ≤ 28%，且近 5 日涨幅 ≤ 24% | 涨得太急、不追高 |
| 大盘状态关 | 池内 mom60 **中位数**：≥ +5% 上行 / ≤ −5% 下行 / 之间震荡 | 下行时**冻结新增**（老仓保留不清仓） |

> 大盘状态用「池内全部股票 mom60 的中位数」，不是市场指数——是这池股票自己的整体表现。
> 大盘状态关可用 `--market-guard false` **临时关闭**：下行状态下也照常选股出组合——大盘状态仍照常计算并写进报告/状态供人工判断风险（默认 true 开启；面板表单里「大盘状态关」选「关」即透传此开关）。

### 3. 选股顺序（严格五步）

1. **过滤**：先砍掉趋势走坏、追高两类，剩下的叫「合格」。
2. **双轨排名**：对合格股分别排**快榜**（mom20 降序）和**慢榜**（mom120 降序）；同分按 `ts_code` 字典序，保证结果确定。
3. **老仓优先（buffer16）**：现有持仓只要在快榜**或**慢榜任一榜上仍在前 **16** 名，直接保留——排名小幅下滑不触发换仓（缓冲 8 个名次的续命空间）。
4. **补满 8 仓**：老仓保留完未满 8 只，按「快榜前 4 → 慢榜前 4 → 快榜续扫 → 慢榜续扫」的顺序补位、去重，直到凑满 `max_positions=8` 只。这个「快 4 + 慢 4」结构保证组合一半短期强势 + 一半中长期强势，两种动量节奏平衡。
5. **兜底**：过滤完一只合格标的都没有时——有老仓则继续持有老仓（`hold_on_empty`，默认 true；可改 false 全部退现金），无老仓则空仓退出（100% 现金）。

### 4. 持有 / 风控 / 流动性

- 组合最多 8 只，**等权**，每只占总资产 `1/8 = 12.5%`。
- **退出机制＝纯滚动，无价格止损**：持仓期间没有任何价格型止损在跑。退出全靠动量排名机制——股票跌出快/慢榜前 16 名缓冲 → 下次调仓被移出目标组合 → 生成 `zero_out` 清仓单。跌破 MA120 只是「不再具备新买入资格」，**已在手的仓位仍靠前 16 名缓冲决定去留**，不是跌破均线就立刻卖。
- **流动性约束**：单票下单量 ≤ 信号日成交量 × `max_trade_vol_ratio`（默认 10%）。
- **节奏**：信号日（T）收盘后算信号，按 **T 日收盘价成交**（信号与成交同一天，无隔日滑点）。

### 5. 数据问题 fail-closed

- 池内股票数据**覆盖率 < 95%**（默认）→ 全策略不出信号，不凑合交易。
- 某只股票历史**不足 121 交易日** → 该只剔除并在报告标注。
- 过滤后一只合格标的都没有 → 按兜底规则退出为现金。

### 6. 一键回滚开关

- 环境变量 `STRATEGY_MOMENTUM_DECISION_MODE=decision_runs`：一键降级，脚本**只产决策快照、不回写持仓状态**（研究核对或执行链故障时用，不写入会被下一轮当成「老仓」依据的状态）。

## 数据源约定（硬约束）

- **只用 tushare MCP** 取日线：`mcp__tushareMcp__daily`（历史日K，无频率限制）。历史窗口取 121 交易日以上，建议 `start_date` 取 today−250 覆盖 mom120/MA120。
- **无 MCP 直接拒绝运行**：会话内无 `mcp__tushareMcp__daily` 工具、或未在投研工具设置卡片填 tushare MCP 地址时，**本功能不可用**，直接报错提示用户先配置，**不得回退 akshare、不写 tushare SDK 直连**。
- 取数字段保留：`trade_date / open / high / low / close / vol`，时点标注数据更新时间。
- **本地 CSV 行情库**（与 w-bottom-screener 共享）：`output/quotes-store/<ts_code>.csv`，每只一份、
  越攒越厚。`--plan` 依据库内最后交易日把标的分成**增量补数（只取「最后日期+1→今天」）/ 全量
  （新票或库内不足 history_min 根）/ 免取（已最新）**；`--data` 把增量按 trade_date 幂等合并写回后
  用库内全量历史计算——二次运行通常每天只差几根 K 线。`--no-store` 可整体关闭退回旧行为。

## 总流程（增量行情库 + 分片多 agent 取数 + 落盘旁路，面板与直连同构）

> 脚本 `momentum_strategy.py` 自包含单文件（纯标准库、零 quantify 依赖），随插件包 `src/` 分发、
> 由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/momentum_strategy.py` 提供下载。

### 取数纪律（硬约束，防会话爆炸）

每只近 250 日线 ≈ 数千 token；80+ 只的池子若让行情数据流经主会话（MCP 返回进会话 → 再复述写文件），
token 直接翻倍且会话必爆、串行 80+ 次调用也极慢。因此：

- **行情数据本体永不进入主会话**：不回显、不粘贴、不汇总明细；主会话只接触「一行回执」。
- **池子 > 10 只必须分片多 agent**：主 agent 派子代理（subagent）并行取数，子代理各自把分片 JSON
  **直接写盘**（write/pwsh），分析脚本从目录合并，数据全程「MCP → 磁盘 → 脚本」旁路。
- 池子 ≤ 10 只（单片）允许主会话直取，但同样只写文件、不把行情贴进回复。

### 执行步骤

```bash
# 第 1 步：读观察仓 + 打印分片取数清单（池子 >10 只自动分片；--shards N 可显式指定）
python scripts/momentum_strategy.py --watchlist output/watchlist/watchlist.yaml --plan
```

- `--plan` 会对照本地行情库给每只标注**取数区间**：增量（`start_date=<最后日期+1> end_date=<今天>`）
  或全量（新票/库内不足 121 根，`start_date` 不晚于 today−250）；库内已最新的直接「免取」。
  后续取数**严格按区间执行**。
- 输出为**单片**（小池子）时：主会话按区间逐只调 `mcp__tushareMcp__daily`，
  整理成 JSON 写到 `output/momentum/quotes.json`，格式 `{"<ts_code>": [{trade_date,open,high,low,close,vol}, ...]}`。
- 输出为**分片**（大池子）时：对每个分片派一个 subagent 并行执行（**无需清空 quotes 目录**：
  `--data` 按 trade_date 幂等合并，残留分片重复合并不产生副作用）：

  > 子代理任务模板：「对 ts_code 清单 <片内清单+各自 start/end> 按标注区间逐只调
  > mcp__tushareMcp__daily（增量票只取尾巴；全量票 start_date 不晚于 today−250），字段保留
  > trade_date/open/high/low/close/vol，整理为 `{"<ts_code>": [...]}` 的 JSON，用写文件工具原样写入
  > `output/momentum/quotes/shard_<k>.json`。
  > 行情数据不得出现在你的回复中；回复只需一行：`shard <k>：完成 x/y，失败 [...]`。
  > 若本会话（含子代理）无 mcp__tushareMcp__daily 工具，回执 `shard <k>：无 MCP`，不得编造行情。」

  主 agent 只收集各片回执；某片失败/无 MCP 时重派一次，仍失败则在报告中标注缺口（覆盖率关卡会 fail-closed）。

```bash
# 第 2 步：分析（--data 单片传文件、分片传目录：脚本自动合并全部 *.json、按日期幂等写回
# output/quotes-store/，再用库内全量历史计算；全部免取时放一个空 JSON 分片 {} 即可）
python scripts/momentum_strategy.py \
  --watchlist output/watchlist/watchlist.yaml \
  --state output/momentum/state.json \
  --data output/momentum/quotes        # 分片目录；单片小池子用 output/momentum/quotes.json
```

- 输出 `output/momentum/plan_<时间戳>.md`：大盘状态、目标持仓（快榜/慢榜排名 + mom + 现价）、逐只过滤明细。
- 回写持仓状态 `output/momentum/state.json`（含 as_of / cash_pct / signal / target / positions），供下一轮「老仓优先」使用。
- 无面板直连时脚本就在仓库内 `src/workspace-init/momentum_strategy.py`（面板场景则从端点下载到 `scripts/`，两条路径命令其余部分完全一致）。

### 批量维护观察仓（可选指引）

往观察仓一次加几十上百只时，同样禁止「逐个 ts_code 查 MCP/逐个 edit」的串行模式；
且清单**写入口唯一是 `watchlist-manager`**，本指引只解决「名单 → 规范代码」的批量化，写入仍走它：

- 用户贴的名单（名称/代码混杂）→ 派**一个**子代理：一次调 `mcp__tushareMcp__stock_basic` 拉全市场基础表
  → 落盘 `output/watchlist/stock_basic.json`（数据不进主会话）→ 用本地脚本（pwsh/python 一次跑）对名单做
  名称/代码模糊匹配 → 产出规范化的 ts_code 清单（很小，可回主会话）。
- 主 agent 核对清单后，逐条走 `watchlist-manager` 写入（`manage_watchlist.py add <code> --name ...`，
  幂等、纯本地、无网络，几十条循环也很快）；不自己直接改 `watchlist.yaml`。
- 匹配不上（停牌/退市/简称差异）的列成清单交用户人工确认，不臆造代码。

---

## 与其它技能的分工

- 这个动量信号「值不值得买、贵不贵」→ `stock-valuation`（本技能只管动量轮动，估值另算）。
- 行业景气上/下行 → `prosperity-analysis`。
- 自己持仓的持续跟踪 → `portfolio-tracker`（本技能产出的是策略组合，不是人工持仓清单）。
- 技术形态买点 → `w-bottom-screener`（本技能与 W底筛选共用观察仓，但一个是形态触发器、一个是动量轮动）。
- 往观察仓加/删标的、或轮动结果给条目留 param → `watchlist-manager`（本技能对清单只读，写入一律委托它：`set <code> --MR 3`）。

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能（SKILL.md，方法论）+ 脚本真源 `src/workspace-init/momentum_strategy.py`（自包含算法，随插件包分发）。
- **不提交**：`output/watchlist/`（观察仓清单，个人关注信息）、`output/momentum/`（取数缓存、组合报告、持仓状态）、
  `output/quotes-store/`（本地 CSV 行情库，运行时数据），均已 gitignore。

## 免责

本技能输出仅为动量轮动信号，供研究参考，不构成投资建议；行情数据有时点滞后，动量因子存在反转与失效风险。

---

## 参数速查（脚本默认值，可用命令行覆盖）

| 参数 | 默认 | 含义 |
|---|---|---|
| `win-short` | 20 | mom20 窗口 |
| `win-mid` | 120 | mom120 窗口 |
| `win-market` | 60 | mom60 窗口（大盘状态） |
| `ma-dev-max` | 0.28 | 反追高：偏离 MA20 上限 |
| `rush-max` | 0.24 | 反追高：近 5 日涨幅上限 |
| `market-up` / `market-down` | +0.05 / −0.05 | 大盘上下行阈值 |
| `buffer-rank` | 16 | 老仓粘性名次 |
| `max-positions` | 8 | 最大持仓数（等权） |
| `fast-top` / `slow-top` | 4 / 4 | 补位快 4 + 慢 4 |
| `max-trade-vol-ratio` | 0.10 | 流动性 10% |
| `coverage-min` | 0.95 | 覆盖率下限 |
| `history-min` | 121 | 最低历史交易日 |
| `hold-on-empty` | true | 兜底保留老仓（false 全退现金） |
| `market-guard` | true | 大盘状态关（false 关闭：下行也照常选股，大盘状态仍照常计算展示） |