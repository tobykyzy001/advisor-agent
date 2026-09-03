---
name: workspace-init
description: 工作区初始化技能。当用户要在「一个空目录 / 新目录」从零搭建一套可运行的投资顾问工作区目录树时触发——一次性生成抄作业 copy-trade（output/copy-trade/）、个股分析 stock-valuation（output/reports/）、W底搜索 w-bottom-screener（output/w-bottom/ + output/watchlist/）这三大技能所需的运行目录，以及持仓（portfolio-tracker）、个股知识库（knowledge/）、知识更新状态（daily-update）等骨架，并写入各清单的模板文件。核心：一键运行 `scripts/init_workspace.py` 全量生成（不逐模块交互），幂等可重复执行。
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
│   ├── w-bottom/                       # W底筛选取数缓存与报告
│   └── momentum/                       # 动量轮动取数缓存、组合报告、持仓状态
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
| `output/watchlist/watchlist.yaml` | watchlist-manager（写）/ w-bottom-screener / momentum-rotation（读） | 观察仓标的池（含 600519.SH 示例） |
| `output/skill-state/update-manifest.yaml` | daily-update | 知识资产更新状态（last_update 为空） |
| `output/copy-trade/alias-map.override.yaml` | copy-trade | 别名→标的映射占位 |
| `knowledge/index.md` | （个股知识库） | 清单索引 |
| `knowledge/stocks/_template.md` | （个股知识库） | 个股文件模板 |

## 使用

脚本真源在 **`src/workspace-init/`**（随插件包分发），真实逻辑只改这些文件：

```bash
# 1) 在当前目录初始化（目录骨架 + 模板）
python src/workspace-init/init_workspace.py
# 2) 在指定空目录初始化（推荐：全新项目骨架）
python src/workspace-init/init_workspace.py --target D:/my-advisor
# 3) 先看会做什么，不落地
python src/workspace-init/init_workspace.py --target D:/my-advisor --dry-run

# 4) 准备运行环境（建工作区持久 .venv + 装 yt-dlp/faster-whisper，供 B站视频总结用）
python src/workspace-init/setup_runtime.py --target D:/my-advisor
#    可选 --with-opencc（繁转简） / --check（只查不装） / --force（强制重装）
```

**插件安装环境下**（无本仓库源码）：脚本由宿主静态端点分发，URL 为
`http://127.0.0.1:<port>/plugins/advisor-agent/assets/workspace-init/init_workspace.py` 与
`.../assets/workspace-init/setup_runtime.py`，agent 下载后执行即可；客户端投递的指令会带上该地址。

## 幂等与安全

- **目录无脑补建**：`mkdir -p`，已存在不报错。
- **模板只补不覆盖**：目标文件已存在则跳过并提示，绝不覆盖用户已回填的持仓/观察仓/知识库内容。
- **纯标准库**：`init_workspace.py` 只依赖 `pathlib` / `argparse`；`setup_runtime.py` 只依赖 `os/sys/subprocess/shutil/pathlib/argparse`，两者都不依赖本仓库 quantify 包，可拷到任意机器单独运行；目标目录也不要求是 git 仓库(仍会生成 `.gitignore` 备用)。
- **Python 边界（重要）**：`setup_runtime.py` 只负责建 `.venv` + 装第三方依赖，**不负责安装 Python 解释器本身**。检测到系统无可用 Python 时输出 `python_missing`、以退出码 2 停下，提示用户安装完整版 CPython 3.11+（勾选 Add to PATH）——这是「非插件问题」，需用户确认处置，脚本不做任何自动安装/兜底。装依赖失败（网络/编译/磁盘，退出码 1）同理：报错停下、等用户处置，不换路径硬来。**权限拒绝例外（退出码 3 needs_escalation）**：安装撞 Errno 13 / WinError 5（典型为会话文件沙箱的受限令牌与 Python 建临时目录冲突）时，脚本打印提权指引并以退出码 3 停下——agent 的唯一动作是用会话提权机制重跑**同一条**命令、交用户审批；`--target` / monkeypatch / 换路径等替代方案会同样失败，一律禁止。半成品 `.venv`（有解释器、缺 pip，常因上次安装被中断）下次运行会自动补装，无需手动清理。

## 与其它技能的分工

- 本技能**只搭骨架、装环境、不跑业务**。真正读写这些目录的是：
  - 抄作业回测产物 → `copy-trade`（写 `output/copy-trade/`）
  - 个股估值研报 → `stock-valuation`（写 `output/reports/`）
  - W底形态筛选 → `w-bottom-screener`（读 `output/watchlist/`、写 `output/w-bottom/`）
  - 中期动量轮动 → `momentum-rotation`（读 `output/watchlist/`、写 `output/momentum/`）
  - 观察仓清单维护 → `watchlist-manager`（写 `output/watchlist/`，是观察仓的唯一写入口；上面两个筛选技能只读）
  - 持仓复核 → `portfolio-tracker`（读/写 `output/portfolio/holdings.yaml`）
  - 景气行业快照 → `prosperity-analysis`（写 `output/sectors/`）
  - 知识更新周期 → `daily-update`（读/写 `output/skill-state/update-manifest.yaml`）
  - 个股知识沉淀 → 本技能生成的 `knowledge/`（后续由 agent 按 `stock-valuation` 输出格式回填单票文件）
  - B站视频总结 → `bili-video-summary`（读/写 `output/videos/`；**其运行环境（.venv + yt-dlp/faster-whisper）由本技能的 `setup_runtime.py` 准备**；Python 解释器本身是用户环境前提，缺失时由 setup_runtime 报 `python_missing` 交用户处置，非插件问题）

## 生成数据 vs 技能方法（提交边界）

- **提交**：本技能本身（`SKILL.md`），以及真正的脚本真源 `src/workspace-init/init_workspace.py` 与其中内置的**模板内容**（都是方法/结构约定，不含个人数据）。
- **不提交**：脚本在目标目录**生成出来的产物**（`output/`、`knowledge/stocks/` 下回填后的文件、README 里用户手写内容）。这些是运行时/个人数据，生成的 `.gitignore` 已将其排除——**尤其当目标目录本身就是本仓库时**（此时 `output/` 会被仓库已有的 `.gitignore` 忽略）。

> 注意：`src/workspace-init/init_workspace.py` 是「脚本本身」随插件包分发。用它 `--target` 指向**本仓库根目录/任意目录**产出的 `output/` 结构是「运行时产物」，不入库。

## 免责

本技能仅做目录与模板脚手架，不取数、不估值、不产投资信号；生成的骨架供研究参考，不构成投资建议。