---
name: workspace-init
description: 工作区初始化技能。当用户要在「一个空目录 / 新目录」从零搭建一套可运行的投资顾问工作区目录树时触发——一次性生成三大工具（抄作业 / 个股分析 / W底搜索）以及持仓、观察仓、个股知识库、知识更新状态等运行时目录骨架，并写入各清单的模板文件。核心：一键运行 `scripts/init_workspace.py` 全量生成（不逐模块交互），幂等可重复执行。
---

# 工作区初始化（Workspace Init）

在**任意（空）目录**一次性搭出投顾工作区的完整目录骨架，让后续各技能脚本开箱即用、产物各归其位。

> 定位：这是一个**脚手架/初始化器**，只生成目录与模板，不分析、不取数、不产研报。它把散落在各技能里的运行时目录约定，收敛成一份可一键落地的骨架。

## 何时触发

- 用户说「给我初始化一个工作目录 / 空目录搭骨架 / 初始化投资顾问工作区 / 生成目录结构」。
- 要在新环境（新电脑 / 别人的仓库 / 独立项目）复现完整工作区时。

## 生成什么（一次全量，不逐模块交互）

脚本 `scripts/init_workspace.py` 在目标目录下生成：

```
<目标目录>/
├── README.md                          # 工作区总览 + 各目录用途表
├── .gitignore                          # 忽略 output/ 与 knowledge/stocks/ 等运行时/个人数据
├── output/                             # 运行时产物根（均不入库）
│   ├── copy-trade/                     # 抄作业：作业回测产物 + alias-map.override.yaml
│   ├── reports/                        # 个股分析研报（research_*.md）
│   ├── portfolio/                      # 持仓清单 holdings.yaml
│   ├── sectors/                        # 景气行业快照
│   ├── skill-state/                    # 知识更新状态 update-manifest.yaml
│   ├── videos/                         # B站视频转录产物
│   ├── watchlist/                      # 观察仓清单 watchlist.yaml
│   └── w-bottom/                       # W底筛选取数缓存与报告
└── knowledge/                          # 个股知识库（清单索引 + 每票一份文件）
    ├── index.md                        # 清单索引（登记表）
    └── stocks/
        ├── _template.md                # 个股文件模板（复制改名即用）
        └── ...                         # 每档个股 <ts_code>.md（初始为空）
```

模板文件清单（脚本只在文件**缺失**时写入，不覆盖已编辑内容）：

| 文件 | 归属技能 | 用途 |
|---|---|---|
| `output/portfolio/holdings.yaml` | portfolio-tracker | 持仓清单（空 holdings，含字段注释） |
| `output/watchlist/watchlist.yaml` | w-bottom-screener | 观察仓标的池（含 600519.SH 示例） |
| `output/skill-state/update-manifest.yaml` | daily-update | 知识资产更新状态（last_update 为空） |
| `output/copy-trade/alias-map.override.yaml` | copy-trade | 别名→标的映射占位 |
| `knowledge/index.md` | （个股知识库） | 清单索引 |
| `knowledge/stocks/_template.md` | （个股知识库） | 个股文件模板 |

## 使用

```bash
# 在当前目录初始化
python .agents/skills/workspace-init/scripts/init_workspace.py

# 在指定空目录初始化（推荐：全新项目骨架）
python .agents/skills/workspace-init/scripts/init_workspace.py --target D:/my-advisor

# 先看会做什么，不落地
python .agents/skills/workspace-init/scripts/init_workspace.py --target D:/my-advisor --dry-run
```

## 幂等与安全

- **目录无脑补建**：`mkdir -p`，已存在不报错。
- **模板只补不覆盖**：目标文件已存在则跳过并提示，绝不覆盖用户已回填的持仓/观察仓/知识库内容。
- **纯标准库**：只依赖 `pathlib` / `argparse`，不依赖本仓库 quantify 包，可拷到任意机器单独运行；目标目录也不要求是 git 仓库(仍会生成 `.gitignore` 备用)。

## 与其它技能的分工

- 本技能**只搭骨架、不跑业务**。真正读写这些目录的是：
  - 抄作业回测产物 → `copy-trade`（写 `output/copy-trade/`）
  - 个股估值研报 → `stock-valuation`（写 `output/reports/`）
  - W底形态筛选 → `w-bottom-screener`（读 `output/watchlist/`、写 `output/w-bottom/`）
  - 持仓复核 → `portfolio-tracker`（读/写 `output/portfolio/holdings.yaml`）
  - 景气行业快照 → `prosperity-analysis`（写 `output/sectors/`）
  - 知识更新周期 → `daily-update`（读/写 `output/skill-state/update-manifest.yaml`）
  - 个股知识沉淀 → 本技能生成的 `knowledge/`（后续由 agent 按 `stock-valuation` 输出格式回填单票文件）

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能本身（`SKILL.md` + `scripts/init_workspace.py`），以及脚本里内置的**模板内容**（都是方法/结构约定，不含个人数据）。
- **不提交**：脚本在目标目录**生成出来的产物**（`output/`、`knowledge/stocks/` 下回填后的文件、README 里用户手写内容）。这些是运行时/个人数据，生成的 `.gitignore` 已将其排除——**尤其当目标目录本身就是本仓库时**（此时 `output/` 会被仓库已有的 `.gitignore` 忽略）。

> 注意：本仓库 `.agents/skills/workspace-init/` 是「脚本本身」，随仓库提交；而用它 `--target` 指向**本仓库根目录/任意目录**产出的 `output/` 结构是「运行时产物」，不入库。

## 免责

本技能仅做目录与模板脚手架，不取数、不估值、不产投资信号；生成的骨架供研究参考，不构成投资建议。