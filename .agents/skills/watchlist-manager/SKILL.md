---
name: watchlist-manager
description: 观察仓清单管理技能。当用户要「把某只票加入观察仓/移出观察仓」「看看我的观察仓里都有谁」「维护观察标的池」时触发；也是投研流程的复用接口——stock-valuation 估值后想持续跟踪、copy-trade 挖到值得盯的标的时，由 agent 调本技能的脚本写入。核心：在 output/watchlist/watchlist.yaml（已忽略不入库，w-bottom-screener / momentum-rotation 共用的标的池）上做增删改查——add 代码自动规范化（600519→600519.SH）且幂等，set 支持任意 --param 透传（其他工具写死自己的英文键委托写入，如 w-bottom 的 --BS B、momentum 的 --MR 3），写入口唯一。脚本真源 src/workspace-init/manage_watchlist.py 自包含纯标准库、随插件包分发并由宿主静态端点提供下载执行；投研工具面板有表单入口（动作 add/list/set/rm/check + 代码/名称/备注/param 透传），对话式或投研流程内亦可触发。
---

# 观察仓管理（Watchlist Manager）

维护**观察仓清单** `output/watchlist/watchlist.yaml`：一个自定义标的池（非持仓），供 `w-bottom-screener`（W底形态筛选）与 `momentum-rotation`（中期动量轮动）作为**只读输入**共用。

> 核心定位：这是观察仓的**唯一写入口**。其他工具（估值、抄作业、形态筛选、动量轮动）想往池子里放标的、或想在自己跑完后留个 param，都委托本技能的脚本写入——避免多方各自读写同一份 yaml 造成覆盖冲突。与 `portfolio-tracker` 对称：它管持仓（`output/portfolio/`），本技能管观察仓（`output/watchlist/`）。

## 何时触发

- 用户说「把 600519 加入观察仓」「移出观察仓」「我的观察仓里都有谁」「观察仓加个票盯着」。
- **投研工具面板**：侧边栏「观察仓管理」卡片（动作 add/list/set/rm/check + 代码/名称/备注/param 透传），点「运行」投递执行。
- 投研流程内委托调用：
  - `stock-valuation` 结论为「贵/高估/等买点」→ 建议加入观察仓持续跟踪；
  - `copy-trade` 还原出值得盯但其价位未到的标的 → 纳入观察仓；
  - `w-bottom-screener` 命中形态后 → 委托 `set --BS` 留痕；
  - `momentum-rotation` 出组合后 → 委托 `set --MR` 留痕。

## 数据契约（硬约束）

消费方（w-bottom / momentum 的脚本）用**自写正则的极简解析**读清单，写入必须满足：

- 每条以 `- ts_code: "xxx"` 作为**第一个键**；
- 子字段 **2 个空格**缩进（与 PyYAML safe_dump 同风格，量化核心 `save_watchlist` 写出的清单直接合规；读取宽容任意缩进，本脚本写入恒规范为 2 空格——跑一次 add/set 可把旧 4 空格手编文件规范化）；
- `ts_code` 用 tushare 格式：A股带 `.SH/.SZ/.BJ`，港股如 `00700.HK`。

每条标的分两层字段：

| 层 | 字段 | 谁写 |
|---|---|---|
| 固定字段 | `ts_code` / `name` / `market`（A\|HK） / `note`（跟踪理由） / `added_at`（加入日期） / `source`（来源技能） | 本技能 `add` |
| 工具 param | 任意英文键（如 `BS`、`MR`、`BS_DATE`），键由各工具**写死**、值语义自定义 | 各工具委托 `set` |

本脚本对 param **不预设名单、不解释值语义，纯透传**——其他工具随时可以加自己的 param，不用改本技能代码。

## 使用

脚本 `manage_watchlist.py` 是**自包含单文件**（纯标准库、零 quantify 依赖），随插件包 `src/` 分发、由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/manage_watchlist.py` 提供下载；也可在 advisor-agent 仓库内直接 `python src/workspace-init/manage_watchlist.py` 运行。**插件安装环境下**（无本仓库源码）：agent 先从该端点 URL 下载脚本到工作区，再 `python manage_watchlist.py <子命令>` 执行。

> 全局参数 `--watchlist`（清单路径，默认 `output/watchlist/watchlist.yaml`）与 `--now`（指定当前日期，测试用）**须放在子命令之前**。

```bash
# 列出观察仓（无子命令时的默认动作）
python manage_watchlist.py

# 加入观察仓：代码自动规范化，幂等（已存在则只更新 name/note/source，保留首加日期）
python manage_watchlist.py add 600519 --name 贵州茅台 --note 等回调到 1500 --source stock-valuation
python manage_watchlist.py add 700 --name 腾讯控股          # → 00700.HK，market 自动 HK

# 透传写入任意 param（其他工具委托调用；只写命中的键，不动其他字段）
python manage_watchlist.py set 600519 --BS B --BS_DATE 2025-06-03
python manage_watchlist.py set 000858 --MR 3

# 移出 / 幂等判断（check 退出码 0=在仓 1=不在，供其他技能调用）
python manage_watchlist.py rm 600519
python manage_watchlist.py check 600519
```

**代码规范化规则**：已带 `.SH/.SZ/.BJ/.HK` 后缀的校验后原样保留（港股补零到 5 位）；6 位数字按首位判交易所（`6→.SH`、`0/3→.SZ`、`4/8/920→.BJ`）；1~5 位数字视为港股补零加 `.HK`。识别不了（如 9 开头疑似 B 股、含字母）直接报错，不猜。

**param 键约定**：只用英文字母/数字/下划线（各工具写死自己的英文缩写；非 ASCII 键在 set 时直接拒绝防呆）。建议命名（各工具写进自己 SKILL.md，本技能不校验）：

| 工具 | param 键 | 值语义（该工具自定义） |
|---|---|---|
| w-bottom-screener | `BS` / `BS_DATE` | `B`=命中 W底 / 确认日 |
| momentum-rotation | `MR` | 排名数字或 `IN`/`OUT` |

**set 语义**：`set` 只更新**已在仓**的条目（code 不存在直接报错，防止手滑静默建条目）；先 `check` 或 `add` 再 `set`。

## 错误与引导

- 清单不存在：`add` 自动创建（含默认头注释）；`list/set/rm/check` 报错并提示先跑 workspace-init 初始化或直接 add。
- 清单缺顶层 `watchlist:` 键：报错提示检查手改内容。
- 委托写入方（w-bottom / momentum 等）**不得自己改写这份 yaml**——成果明细留在各自 `output/` 目录，只用 `set` 留痕。

## 与其它技能的分工

- 持仓（真金白银的仓位）清单与复核 → `portfolio-tracker`（观察仓 ≠ 持仓）。
- 观察仓标的的估值 → `stock-valuation`（本技能不管贵不贵）。
- W底形态筛选 / 动量轮动 → `w-bottom-screener` / `momentum-rotation`（它们**只读**本清单，是下游消费方）。
- 清单模板的目录骨架生成 → `workspace-init`（`WATCHLIST_YAML` 是模板唯一真源；本技能运行时在其上增删改）。

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能（SKILL.md，方法论）+ 脚本真源 `src/workspace-init/manage_watchlist.py`（自包含工具，随插件包分发）。
- **不提交**：`output/watchlist/`（观察仓清单，含个人关注信息），已被 gitignore。

## 免责

本技能仅维护个人关注清单，供研究参考，不构成投资建议；加入观察仓不等于买入建议。
