---
name: w-bottom-screener
description: 观察仓「W底 + 放量」形态筛选技能。当用户要求「筛选观察仓里当日/近几日出现 W底(双重底)且放量的标的」「找双底放量突破的票」「观察仓今天谁形成了W底形态」「帮我扫一遍自选里的技术形态买点」时触发。核心：从观察仓清单读标的池 → fetch_quotes.py 直连 tushare 增量刷本地行情库 → 识别双底(两相近低点，允许横盘变体) + 第二底后放量阳线确认 → 输出命中标的报告。与技术面互补：本技能是「给定池子的形态买点筛选」，股票估值交给 stock-valuation，行业景气交给 prosperity-analysis，自己持仓跟踪交给 portfolio-tracker。
---

# 观察仓 W底 + 放量筛选（W-Bottom Screener）

在**观察仓**（一个自定义标的池，非持仓）里，筛出「近几日形成 W底（双重底）形态 + 放量确认」的标的，作为技术面买点候选。**只给形态信号，不给估值/景气结论**——那部分交给 `stock-valuation` / `prosperity-analysis`。

> 核心定位：这是一个**形态触发器**，回答"我的观察池里，谁刚走出了双底并放量确认"。它不判断"贵不贵""该不该买"，只把符合量价形态的标的挑出来，供后续估值/研判。

## 何时触发
- 用户说「帮我筛一下观察仓里的W底/双底/双重底」「观察仓今天有哪些形成W底且放量」「自选里谁走出双底形态了」「按 W底放量 扫一遍我的观察池」。
- 定时盘后跑本技能，对观察仓做一轮形态扫描。

## 数据源约定（硬约束）

- **取数统一走 `fetch_quotes.py`**（自包含纯标准库脚本，直连 tushare pro REST API）：
  刷库模式对照本地行情库增量补到最新交易日，行情数据**全程不经过任何 LLM 上下文**。
  token 配置：`--token` 参数 / 环境变量 `TUSHARE_TOKEN` / 工作区 `.env` 写 `TUSHARE_TOKEN=…`。
- **无 token 直接拒绝运行**：`fetch_quotes.py` 退出码 2（缺 token）时，如实转告用户先配置
  TUSHARE_TOKEN，**不得回退 akshare、不得编造行情**（akshare 接口不稳定，禁用）。
- 刷库字段保留：`trade_date / open / high / low / close / vol`，报告标注数据截止日。
- **本地 CSV 行情库**（与 momentum-rotation 共享）：`output/quotes-store/<ts_code>.csv`，每只一份、
  越攒越厚，由 `fetch_quotes.py` 幂等合并写回（按 trade_date 去重、新行覆盖同日旧行）。
  本脚本对库**只读**；判定前做数据门禁：库内无数据或最后交易日距今超过 10 自然日的标的视为缺口，
  fail-closed 提示先刷库，不拿陈旧数据误出形态。

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

## 总流程（两步：fetch_quotes.py 刷库 → 直接读库判定）

```
fetch_quotes.py 对照行情库增量拉数并幂等写回（行情只走「API → 磁盘」，不过 LLM）
→ w_bottom_screen.py 直接读库判形态 → 出报告
```

> 两个脚本都是**自包含单文件**（纯标准库、零 quantify 依赖），随插件包 `src/` 分发、由宿主静态端点
> `/plugins/advisor-agent/assets/workspace-init/<脚本名>` 提供下载；也可在 advisor-agent 仓库内直接
> `python src/workspace-init/<脚本名>` 运行（等价于 `python -m quantify.cli w-bottom` 的转调路径）。

### 第 1 步：刷库（增量补到最新交易日）

```bash
python scripts/fetch_quotes.py --watchlist output/watchlist/watchlist.yaml --min-bars 30 --full-days 90
```

- 观察仓清单在 `output/watchlist/watchlist.yaml`（已被 gitignore，不入库，属个人关注信息）。
- 清单模板由 `workspace-init` 技能生成（`init_workspace.py` 的 WATCHLIST_YAML 是唯一模板真源）；本技能对清单**只读不写**——往池子加/删标的用 `watchlist-manager`（`manage_watchlist.py add/rm`），命中形态后如需留痕也**委托**它写入（`manage_watchlist.py set <code> --BS B --BS_DATE <确认日>`），不自己改这份 yaml。
- 刷库输出为汇总式（成功/失败、有新数据的只数与总根数、异常标的逐只列出），**K 线与逐票明细不进会话**；一两句话转达结果即可，不要逐只复述。
- 失败分流：退出码 2（缺 token）→ 转告用户配置 `TUSHARE_TOKEN`（环境变量 / 工作区 `.env` / `--token`）；退出码 1/3（网络或部分标的失败）→ 转告失败清单，**不要跳过刷库直接判定**。
- `--min-bars 30`：库内不足 30 根的自动全量重取近 `--full-days 90` 自然日（覆盖 lookback + 均量缓冲）。

### 第 2 步：读库判形态 + 出报告

```bash
python scripts/w_bottom_screen.py --watchlist output/watchlist/watchlist.yaml
```

- 直接读 `output/quotes-store/` 判定（无需 --data 回填）；数据门禁发现缺口或过期（>10 自然日）会
  fail-closed 并提示先刷库。
- 输出 `output/w-bottom/screen_<时间戳>.md`：命中标的表格（代码/名称/左底/右底/确认日/量比）+ 逐只形态说明，报告头部标注数据截止日。
- `--plan` 仅作诊断：打印每只的库内根数/最后交易日/待补区间，不取数。

### 批量维护观察仓（可选指引）

往 `watchlist.yaml` 一次加几十上百只时，避免「逐个 edit」的串行模式；清单**写入口唯一是
`watchlist-manager`**，本指引只解决「名单 → 规范代码」的批量化，写入仍走它：

- 名单里的**代码**部分：`manage_watchlist.py add` 自动规范化（600519→600519.SH、00700→00700.HK），
  可用本地脚本循环批量跑（幂等、纯本地）。
- 名单里的**中文名称**部分：无法唯一确定代码时列成候选清单交用户人工确认，不臆造代码。
- 主 agent 核对清单后，逐条走 `watchlist-manager` 写入；不自己直接改 `watchlist.yaml`。

## 与其它技能的分工

- 「这个形态信号值不值得买、贵不贵」→ `stock-valuation`（本技能只给形态，估值另算）。
- 行业景气上/下行 → `prosperity-analysis`。
- 自己持仓的持续跟踪 → `portfolio-tracker`（观察仓 ≠ 持仓，本技能不碰持仓）。
- 往观察仓加/删标的、或命中后给条目留 param → `watchlist-manager`（本技能对清单只读，写入一律委托它：`set <code> --BS B`）。
- 知识资产/景气快照周期更新 → `daily-update`。

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能（SKILL.md，方法论）+ 脚本真源 `src/workspace-init/w_bottom_screen.py`（自包含算法，随插件包分发）。
- **不提交**：`output/watchlist/`（观察仓清单，含个人关注信息）、`output/w-bottom/`（取数缓存与筛选报告）、
  `output/quotes-store/`（本地 CSV 行情库，运行时数据），均已 gitignore。

## 免责

本技能输出仅为技术形态信号，供研究参考，不构成投资建议；行情数据有时点滞后，量价形态存在误报与失效风险。