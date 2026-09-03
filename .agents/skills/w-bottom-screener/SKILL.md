---
name: w-bottom-screener
description: 观察仓「W底 + 放量」形态筛选技能。当用户要求「筛选观察仓里当日/近几日出现 W底(双重底)且放量的标的」「找双底放量突破的票」「观察仓今天谁形成了W底形态」「帮我扫一遍自选里的技术形态买点」时触发。核心：从观察仓清单读标的池 → 用 tushare MCP 取近 N 日线 → 识别双底(两相近低点，允许横盘变体) + 第二底后放量阳线确认 → 输出命中标的报告。与技术面互补：本技能是「给定池子的形态买点筛选」，股票估值交给 stock-valuation，行业景气交给 prosperity-analysis，自己持仓跟踪交给 portfolio-tracker。
---

# 观察仓 W底 + 放量筛选（W-Bottom Screener）

在**观察仓**（一个自定义标的池，非持仓）里，筛出「近几日形成 W底（双重底）形态 + 放量确认」的标的，作为技术面买点候选。**只给形态信号，不给估值/景气结论**——那部分交给 `stock-valuation` / `prosperity-analysis`。

> 核心定位：这是一个**形态触发器**，回答"我的观察池里，谁刚走出了双底并放量确认"。它不判断"贵不贵""该不该买"，只把符合量价形态的标的挑出来，供后续估值/研判。

## 何时触发
- 用户说「帮我筛一下观察仓里的W底/双底/双重底」「观察仓今天有哪些形成W底且放量」「自选里谁走出双底形态了」「按 W底放量 扫一遍我的观察池」。
- 定时盘后跑本技能，对观察仓做一轮形态扫描。

## 数据源约定（硬约束）

- **只用 tushare MCP** 取日线：`mcp__tushareMcp__daily`（历史日K，无频率限制）。
- **无 MCP 直接拒绝运行**：会话内没有 `mcp__tushareMcp__daily` 工具、或没在投研工具设置卡片填 tushare MCP 地址时，**本功能不可用**，直接报错提示用户先配置，**不得回退 akshare、不写 tushare SDK 直连**（akshare 接口不稳定，禁用）。
- 取数字段保留：`trade_date / open / high / low / close / vol`，时点标注 data 更新时间。

## W底口径（可参数化，默认如下）

| 参数 | 默认 | 含义 |
|---|---|---|
| lookback | 30 | 回看交易日数 |
| trough_tol | 0.03 | 两底低点偏差上限（\|B1-A|/A ≤ 3%） |
| confirm_window | 3 | B1 之后几个交易日内出现确认 K 线 |
| ma_window | 5 | 放量基准 = 5 日均量 |
| anchor_window | 5 | 确认 K 线需落在近 5 个交易日内 |

判定步骤：
1. 取近 30 根日线，**自动识别局部低点**（low[i] ≤ 左右邻），挑出**两个相近低点** A（左底）、B1（右底），要求 |B1−A|/A ≤ 3%。
2. **中间不要求反弹幅度**：允许横盘/窄幅震荡（视为变体 W底），两底之间只要有间隔即可。
3. **确认**：B1 之后 **3 个交易日**内，出现一根「**阳线（收盘>开盘）且 成交量 ≥ 前 5 日均量**」的 K 线 — 即 W底成型，**不要求突破颈线**。
4. 确认 K 线落在**近 5 个交易日**内 → 命中并输出。

## 总流程（三段式，大池子分片多 agent 取数 + 落盘旁路）

```
列出观察仓 → （分片）子代理并行取数直接落盘 → 脚本合并判形态 → 出报告
```

### 取数纪律（硬约束，防会话爆炸）

- **行情数据本体永不进入主会话**：不回显、不粘贴、不汇总明细；主会话只接触「一行回执」。
- **池子 > 10 只必须分片多 agent**：主 agent 派子代理（subagent）并行取数，子代理各自把分片 JSON
  **直接写盘**（write/pwsh），分析脚本从目录合并，数据全程「MCP → 磁盘 → 脚本」旁路。
- 池子 ≤ 10 只（单片）允许主会话直取，但同样只写文件、不把行情贴进回复。

### 第 1 步：读观察仓 + 待取数清单（自动分片）

```bash
python src/workspace-init/w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml --plan
```

> 脚本 `w_bottom_screen.py` 是**自包含单文件**（纯标准库、零 quantify 依赖），随插件包 `src/` 分发、
> 由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py` 提供下载；也可在
> advisor-agent 仓库内直接 `python src/workspace-init/w_bottom_screen.py` 运行（等价于
> `python -m quantify.cli w-bottom --plan`）。

- 观察仓清单在 `output/watchlist/watchlist.yaml`（已被 gitignore，不入库，属个人关注信息）。
- 清单模板由 `workspace-init` 技能生成（`init_workspace.py` 的 WATCHLIST_YAML 是唯一模板真源）；本技能对清单**只读不写**——往池子加/删标的用 `watchlist-manager`（`manage_watchlist.py add/rm`），命中形态后如需留痕也**委托**它写入（`manage_watchlist.py set <code> --BS B --BS_DATE <确认日>`），不自己改这份 yaml。
- 池子 > 10 只时 `--plan` 自动按每片 10 只打印分片清单（`--shards N` 可显式指定片数）。

### 第 2 步：agent 取数（分片旁路落盘）

对清单里每个 `ts_code`，调用 `mcp__tushareMcp__daily` 取近 30+ 个交易日日线（`start_date` 取 today−60，覆盖 lookback + 均量缓冲即可）。

- **单片**（小池子）：主会话直取，整理成 JSON 保存为 `output/w-bottom/quotes.json`：

```json
{ "600519.SH": [ {"trade_date":"20250102","open":..,"high":..,"low":..,"close":..,"vol":..}, ... ] }
```

- **分片**（大池子）：**先清空** `output/w-bottom/quotes/` 目录（防上轮残留混入），每个分片派一个 subagent 并行：

  > 子代理任务模板：「对 ts_code 清单 <片内清单> 每只调 mcp__tushareMcp__daily 取近 30+ 交易日日线
  > （start_date 取 today−60），字段保留 trade_date/open/high/low/close/vol，整理为 `{"<ts_code>": [...]}`
  > 的 JSON，用写文件工具原样写入 `output/w-bottom/quotes/shard_<k>.json`。
  > 行情数据不得出现在你的回复中；回复只需一行：`shard <k>：完成 x/y，失败 [...]`。
  > 若本会话（含子代理）无 mcp__tushareMcp__daily 工具，回执 `shard <k>：无 MCP`，不得编造行情。」

  主 agent 只收集各片回执；失败分片重派一次，仍失败则在报告中标注缺口。

### 第 3 步：形态判定 + 出报告

```bash
# --data 单片传文件、分片传目录（脚本自动合并目录下全部 *.json）
python src/workspace-init/w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml --data output/w-bottom/quotes     # 分片目录
python src/workspace-init/w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml --data output/w-bottom/quotes.json # 单片小池子
```

输出 `output/w-bottom/screen_<时间戳>.md`：命中标的表格（代码/名称/左底/右底/确认日/量比）+ 逐只形态说明。

### 批量维护观察仓（可选指引）

往 `watchlist.yaml` 一次加几十上百只时，同样禁止「逐个 ts_code 查 MCP/逐个 edit」的串行模式；
且清单**写入口唯一是 `watchlist-manager`**，本指引只解决「名单 → 规范代码」的批量化，写入仍走它：

- 用户贴的名单（名称/代码混杂）→ 派**一个**子代理：一次调 `mcp__tushareMcp__stock_basic` 拉全市场基础表
  → 落盘 `output/watchlist/stock_basic.json`（数据不进主会话）→ 用本地脚本（pwsh/python 一次跑）对名单做
  名称/代码模糊匹配 → 产出规范化的 ts_code 清单（很小，可回主会话）。
- 主 agent 核对清单后，逐条走 `watchlist-manager` 写入（`manage_watchlist.py add <code> --name ...`，
  幂等、纯本地、无网络，几十条循环也很快）；不自己直接改 `watchlist.yaml`。
- 匹配不上（停牌/退市/简称差异）的列成清单交用户人工确认，不臆造代码。

## 与其它技能的分工

- 「这个形态信号值不值得买、贵不贵」→ `stock-valuation`（本技能只给形态，估值另算）。
- 行业景气上/下行 → `prosperity-analysis`。
- 自己持仓的持续跟踪 → `portfolio-tracker`（观察仓 ≠ 持仓，本技能不碰持仓）。
- 往观察仓加/删标的、或命中后给条目留 param → `watchlist-manager`（本技能对清单只读，写入一律委托它：`set <code> --BS B`）。
- 知识资产/景气快照周期更新 → `daily-update`。

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能（SKILL.md，方法论）+ 脚本真源 `src/workspace-init/w_bottom_screen.py`（自包含算法，随插件包分发）。
- **不提交**：`output/watchlist/`（观察仓清单，含个人关注信息）、`output/w-bottom/`（取数缓存与筛选报告），均已 gitignore。

## 免责

本技能输出仅为技术形态信号，供研究参考，不构成投资建议；行情数据有时点滞后，量价形态存在误报与失效风险。