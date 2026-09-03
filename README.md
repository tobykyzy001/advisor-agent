# advisor-agent

面向 **A股/港股** 的投资顾问式智能体（投顾知识库 + 估值研究 + 选股信号 + 组合风控）。偏 **研究/估值** 类，不做回测。

> ⚠️ 本项目输出仅供研究参考，不构成任何投资建议。

本仓库同时也是一个可开源的 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（DSH）第三方插件，在 DSH Web GUI **侧边栏**提供常驻「投研工具」入口，以**表单化**方式调用投顾 skill —— 点击即运行「拉数据 → 分析 → LLM 分析」流水线，把命令行式的 skill 变成可点击、可输参数的图形交互页。

---

## 一、作为 DSH 插件使用（投研工具）

### 它解决什么问题

`.agents/skills` 里的 skill（个股估值、行业景气、持仓复盘、视频总结等）本质都是：

```
输入参数 → 拉取数据 → 按方法论分析 → LLM 生成结论
```

但它们当前是「给 LLM 读的 Markdown 方法论 + 散落的 Python 脚本」，只能靠 agent 对话或 CLI 触发。本插件把它们统一成 **参数 schema + 图形表单**：

- 每个 skill 声明一份参数 schema（输入类型 / 默认值）。
- 侧边栏「投研工具」入口点开，**按 schema 自动渲染表单**（文本框 / 下拉框）。
- 用户点「运行」，面板把参数拼成一条指令，通过 `session.prompt` 提交给 **DSH agent** 执行。
- 可**新开会话**投递（默认），也可**发到当前会话**（表单内临时切换）。
- agent 收到后执行：普通 skill 匹配对应 skill 跑方法论；`workspace-init`/`w-bottom-screener` 这类自包含脚本型 skill 则按指令下载脚本、python 运行，结果回显在会话流里。

**关键点：没有实现新的 agent** —— 执行者就是 DSH 自己的 agent，面板只是「表单 + 一次 prompt 转发」。

### 安装

完全退出 DSH host 后执行：

```powershell
# 从 GitHub 安装（本仓库本身就是插件包）
pnpm exec dsh plugin --profile web add github:tobykyzy001/advisor-agent

# 或发布后从 npm 安装
pnpm exec dsh plugin --profile web add advisor-agent
```

> `--profile web` 只在 `dsh plugin` 子命令里必需；日常启动仍是 `pnpm exec dsh web`。

装完重启 DSH WebUI。入口：

```
左侧栏底部 → 投研工具
设置 → 插件 → 投研工具（技能开关 / 默认投递目标）
```

### 已接入 skill

| skill id | 面板名 | 参数 | 状态 |
|---|---|---|---|
| `stock-valuation` | 个股估值 | `symbol`（股票名或代码）、`market`（A/HK） | ✅ 首期 |
| `copy-trade` | 抄作业分析 | `url`（作业链接）、`html`（本地文件，可选） | ✅ |
| `workspace-init` | 初始化工作区 | `target`（目标目录） | ✅ |
| `w-bottom-screener` | 观察仓 W底筛选 | `lookback`、`trough_tol`（可选） | ✅ |
| `bili-video-summary` | B站视频总结 | `video`（链接/BV号） | ✅ |

### 配置项

宿主侧配置（`cordis.patch.yml` 提供默认值，设置页可改）：

| 字段 | 含义 | 默认 |
|---|---|---|
| `enabled` | 总开关：关闭后侧边栏不显示入口 | `true` |
| `enabledSkills` | 启用的 skill id 列表 | `["stock-valuation","copy-trade","workspace-init","w-bottom-screener","bili-video-summary"]` |
| `defaultTarget` | 点「运行」默认投递目标：`new`(新开会话) / `current`(当前会话) | `new` |

### 新增一个 skill

1. 在 `lib/client.js` 顶部的 **`ADVISOR_SKILLS`（技能注册表）** 加一条对象：`{ id, label, description, params }`。
2. （可选）在 `cordis.patch.yml` 的 `enabledSkills` 默认列表里补上该 id，让它默认启用。
3. 决定该 skill 的**执行方式**，二选一：
   - **普通 skill**：确保 agent 侧能识别该 skill 并执行（复用 `.agents/skills/<skill-id>/` 的方法论）。走 `buildInstruction` 的通用分支 `请调用 skill「…」`。
   - **自包含脚本型 skill**（如 `workspace-init`、`w-bottom-screener`、`bili-video-summary`）：脚本随插件 `src/` 分发，通过宿主静态端点 `makeAssetHandler` 伺服；在 `buildInstruction` 里为它写**确定性指令分支**（下载脚本 → python 跑 → 汇报），不依赖目标工作区里存在 `.agents/skills` 或 quantify 包。其中 `workspace-init`/`w-bottom-screener`/`momentum-rotation` 是**纯标准库**脚本；`bili-video-summary` 依赖 `yt-dlp + faster-whisper`（联网下音频 + 离线转录），脚本内置依赖自检（`--selfcheck`）、模型缓存收拢到 `output/videos/models/`（`--models`）与幂等（已有文字稿跳过转录），非纯标准库但同样自包含分发、无需 skill。

> 注意：DSH 的 `/plugins/<id>/` 只伺服 `client.js` 一个 bundle，插件的其它静态文件**不会自动被伺服**——除了 `src/index.js` 里显式用 `makeAssetHandler` 注册的资产端点。技能注册表必须内联在 `lib/client.js`，不能走运行时 fetch；自包含脚本则要**同时**（a）放进 `src/` 随包分发、（b）在 `src/index.js` 注册静态端点、（c）在 `buildInstruction` 写下载+运行的确定性指令。增加/调整技能只是改这几处，渲染 / 校验 / 指令拼装全部自动跟进。

### 技术要点

- 客户端 bundle 用 `window.__ModuleLoader__.load({ id, factory })` 注册，返回 `{ name, inject, apply(ctx) }`。
- 两个槽位：`sidebar.footer.action`（侧边栏入口）+ `settings.plugin.item`（设置卡片）。配置走宿主 `/plugins/advisor-agent/config` 端点（GET/PATCH，本地回环校验）。
- 投递：
  - 新开会话：`ctx.get('workspaces').connectWorkspace(workspaceId)` 拿回已在 list 里的新 session id，再 `sessions.binding(id).session.prompt(...)`。
  - 当前会话：`sessions.list.getSnapshot().current` → `sessions.binding(current).session.prompt(...)`。
- `ADVISOR_SKILLS` 是 schema 驱动的通用表单**单一数据源**；渲染、必填校验、`buildInstruction` 都由它驱动。

### 插件目录结构

```
（仓库根）
├── package.json           # 插件 manifest：dsh.client（客户端依赖）+ dsh.bundle.patch
├── cordis.patch.yml       # 宿主侧 patch：注册插件行及其配置
├── src/index.js           # 宿主侧入口（node）：配置 schema + 本地 /config 端点 + 静态脚本端点
├── src/workspace-init/    # 自包含 Python 脚本（纯标准库，随包分发，经静态端点供 agent 下载）
│   ├── init_workspace.py  #   工作区初始化脚手架
│   └── w_bottom_screen.py #   W底放量筛选（--plan 列清单 / --data 判形态出报告）
└── lib/client.js          # 客户端 bundle：技能注册表 + 通用表单引擎 + 投递
```

---

## 二、Python 研究与估值（量化核心）

### 核心能力

| 能力 | 模块 | 说明 |
|------|------|------|
| 市场分析 / 研报生成 | `agent/` | 基于结构化结果生成中文研报（可接 LLM） |
| 选股 / 信号发现 | `analysis/screener.py` | 多因子估值评分与买入/观望/卖出信号 |
| 组合管理与风控 | `portfolio/` | 建议仓位、单标的上限、现金缓冲、风控告警 |
| 投顾知识库 | `knowledge/` | YAML 规则（估值/风控/策略），可检索引用 |
| 估值方法 | `valuation/` | PE/PB/ROE/PEG、DDM、目标 PE、相对估值 |

### 环境

- Python ≥ 3.11（本仓库在 3.12 验证）
- 可选的行情数据源：`akshare`（免费）；研报生成：任意 OpenAI 兼容接口

### 快速开始

```bash
# 1. 安装（建议先建虚拟环境）
pip install -e ".[dev]"

# 2. 配置（可选）
#    cp .env.example .env      # 填入 LLM_API_KEY 后研报会由大模型生成
#    未配置 key 时自动使用离线规则模式，功能仍可端到端跑通

# 3. 跑通一次研究（离线也会用内置示例数据演示）
python -m quantify.cli research 600519 000333

# 4. 以编程方式调用
python - <<'PY'
from quantify.agent.orchestrator import InvestAdvisor
r = InvestAdvisor().research(["600519"])
print(r.screen[0].signal, r.report_text)
PY
```

### 目录结构

```
advisor-agent/
├── config/settings.yaml        # 主配置（估值/风控/数据源参数）
├── src/quantify/
│   ├── config.py               # 配置加载（env > yaml > 默认值）
│   ├── data/                   # 数据层：schema / fetcher / cache
│   ├── valuation/              # 估值：metrics / DCF / 相对估值 / core 汇总
│   ├── knowledge/              # 投顾知识库：YAML 规则 + 检索
│   ├── analysis/               # 选股评分 / 信号 / 市场概览
│   ├── portfolio/              # 配仓 / 风控
│   ├── agent/                  # LLM 抽象 / 编排 / 研报生成
│   └── cli.py                  # 命令行入口
├── src/index.js + lib/client.js  # DSH 插件源码（见「一、DSH 插件」）
├── .agents/skills/             # skill 方法论（随仓库提交，方法不提交动态数据）
├── tests/                      # 单元测试
└── output/reports/             # 生成的研报（已 gitignore）
```

### 数据源说明

- `provider: akshare`（默认）：联网拉取实时行情；网络不可用或未安装时自动回退 `LocalProvider`（内置示例数据），保证离线可演示。
- `provider: local`：强制使用示例数据。
- 港股/更多财务字段可在 `data/fetcher.py` 的 `AkshareProvider` 中扩展映射。

### 测试

```bash
pytest
```

### 后续扩展方向

- 接入真实财务数据（ROE 趋势、EPS 历史）完善 DCF 与 PEG 计算
- 增加历史 PE/PB 分位、行业对标
- 组合级回撤/波动率监控与实时再平衡信号
- Agent 多工具调用（检索、计算、复核）

---

## 许可

[MIT](./LICENSE)