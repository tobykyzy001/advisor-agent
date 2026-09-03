# AGENTS.md

> 本文档面向 **agent / AI 编码助手**：说明本仓库是做什么的、目录怎么组织、有哪些约定不能踩。给「人 / 用户」看的完整说明见 [README.md](./README.md)。

## 本仓库是做什么的

面向 **A股/港股** 的投资顾问式智能体项目：投顾知识库 + 估值研究 + 选股信号 + 组合风控。偏**研究/估值**，**不涉及回测**。

一个仓库、两重身份：

1. **Python 研究/估值量化核心**（`src/quantify`）：数据层 → 估值 → 选股信号 → 组合风控 → LLM 研报生成，可 CLI / 编程调用。
2. **DeepSeek Harness（DSH）第三方插件**（`src/index.js` + `lib/client.js` + `src/workspace-init/`）：把 `.agents/skills` 里的投顾 skill 表单化，以侧边栏「投研工具」图形入口运行「拉数据 → 分析 → 生成结论」流水线。

代码、配置、注释、提交信息均为**中文**。

> ⚠️ 本仓库所有输出仅供研究参考，不构成投资建议。

## 快速上手

```bash
.venv/Scripts/python.exe -m quantify.cli research 600519 000333   # CLI 跑一次研究
pytest                                  # 单元测试
```

- 装包：`pip install -e ".[dev]"`（模块在 `src/quantify`，已 editable 安装到 `.venv`）。
- Python ≥ 3.11（仓库在 3.12 验证），虚拟环境在 `.venv`（勿提交）。
- 编程入口：`from quantify.agent.orchestrator import InvestAdvisor; InvestAdvisor().research([...])`。

## 目录结构

```
config/settings.yaml        # 主配置
src/quantify/
  ├── config.py             # 配置加载
  ├── data/                 # 数据层：schema / fetcher(akshare) / cache
  ├── valuation/            # 估值：metrics / dcf / relative / core
  ├── knowledge/            # 投顾知识库：rules/*.yaml + 检索
  ├── analysis/             # 选股评分 / 信号 / 市场概览
  ├── portfolio/            # 配仓 / 风控
  ├── agent/                # LLM抽象 / 编排 / 研报生成
  └── cli.py                # 命令行入口
tests/                      # 单元测试
output/reports/             # 生成的研报（gitignore）
output/sectors/             # 当期景气行业快照（gitignore，见下）
output/skill-state/         # daily-update 运行时状态（gitignore）
output/portfolio/           # 持仓清单（gitignore，portfolio-tracker 运行时数据）
output/videos/              # B站视频转录产物（gitignore，bili-video-summary 运行时数据）
output/copy-trade/          # 抄作业运行时产物（gitignore，copy-trade：原始HTML/解析消息流/回测结果/别名覆盖）
output/watchlist/           # 观察仓清单（gitignore，w-bottom-screener / momentum-rotation 共用的标的池）
output/w-bottom/            # W底筛选取数缓存与报告（gitignore，w-bottom-screener 运行时数据）
output/momentum/            # 动量轮动取数缓存、组合信号报告与持仓状态（gitignore，momentum-rotation 运行时数据）
.agents/skills/             # ZCode 技能：prosperity-analysis / daily-update / stock-valuation / portfolio-tracker / bili-video-summary / copy-trade / w-bottom-screener / momentum-rotation / workspace-init
```

## 配置约定

优先级：环境变量 `QUANTIFY_*`（嵌套用 `__`）> `config/settings.yaml` > pydantic 默认值。
第三方密钥（无前缀）从 `.env` / 进程环境读取：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`TUSHARE_TOKEN`（见 `.env.example`）。
未配置 `LLM_API_KEY` 时研报自动退回**离线规则模式**，功能仍可端到端跑通。

## 数据源

- 默认 `provider: akshare`（联网实时）；网络不可用或未安装时自动回退 `LocalProvider`（内置示例数据），保证离线可演示。
- `provider: local` 强制用示例数据。港股/财务字段扩展在 `data/fetcher.py` 的 `AkshareProvider` 中。
- **agent 侧可选 tushare MCP**：用户在 **投研工具设置卡片** 里填「tushare MCP 地址」（完整 URL，含 token 查询参数），**保存即立即生效**——advisor-agent 宿主插件（`src/mcp-tushare.js`）自建 MCP Streamable HTTP 连接桥，把工具以 `mcp__tushareMcp__*` 注册到会话内，供估值/持仓/景气分析直接取真实行情与财务。URL/token 含密钥，只存于 DSH 的 settings 文档（本地，不入库），**不提交**。字段映射与口径局限见 `stock-valuation/references/data-source.md`。
- 联网拿到的宏观/行业数据标注数据时点。

## 技能与「方法 vs 动态数据」提交边界（重要）

- `.agents/skills/` 下是**随仓库提交**的方法/知识：`prosperity-analysis`（行业景气度方法论）、`daily-update`（知识资产更新周期管理）、`stock-valuation`（单一个股估值方法论，含直连行情脚本 `scripts/fetch_snapshot.py`）、`portfolio-tracker`（持仓清单管理，每条持仓按 `cadence` 定期复核投资类型/估值方式/估值价格）、`bili-video-summary`（B站视频音频下载+离线转录工具，转录产物写 `output/videos/`、模型缓存收拢到 `output/videos/models/`，分析环节复用 stock-valuation / prosperity-analysis 的方法与数据口径；**已改为自包含分发，无需 .agents/skills**：脚本真源 `src/workspace-init/transcribe_video.py` 随插件包分发、由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/transcribe_video.py` 提供下载执行，依赖 yt-dlp+faster-whisper 非纯标准库，内置 `--selfcheck` 依赖自检、`--models` 模型缓存收拢、幂等跳过转录；**运行环境由 workspace-init 的 `setup_runtime.py` 统一准备**（建工作区持久 `.venv` + 装依赖，本脚本不自行装环境）；`.agents/skills/bili-video-summary/` 仅保留方法说明与历史脚本，作为 agent 无面板直连时的备选）、`copy-trade`（抄作业分析：抓取群作业链接→结构化消息→还原持仓路线→轻量回测→结合 tushare 行情判断「值不值得抄」，别名→标的映射需人工确认后固化到 `output/copy-trade/alias-map.override.yaml`）、`w-bottom-screener`（观察仓「W底+放量」形态筛选：读 `output/watchlist/` 标的池→用 tushare MCP 取近N日线→识别双底(两相近低点，允许横盘变体)+第二底后放量阳线确认→出命中报告；**只用 tushare MCP 取数，无 MCP 直接拒绝，不回退 akshare**；脚本真源 `src/workspace-init/w_bottom_screen.py` 自包含纯标准库、随插件包分发并由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py` 提供下载执行，三段式：`--plan` 列观察仓/取数清单→agent 调 MCP 回填 `output/w-bottom/quotes.json`→`--data` 判形态出报告）、`momentum-rotation`（观察仓「中期动量轮动」选股：读 `output/watchlist/` 标的池→用 tushare MCP 取每只近250日线→算 mom20/mom120/mom60 三条区间动量→三道过滤(MA120趋势关/反追高关/大盘状态关)→快慢双榜排名+老仓buffer16粘性+快4慢4补满8仓→等权组合+流动性约束+空仓兜底，退出纯靠排名滚动无价格止损；**只用 tushare MCP 取数，无 MCP 直接拒绝，不回退 akshare**；脚本真源 `src/workspace-init/momentum_strategy.py` 自包含纯标准库、随插件包分发并由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/momentum_strategy.py` 提供下载执行，投研工具面板走**一步式**（agent 读池→一次取齐全250日线→一次调脚本出报告并回写 `output/momentum/state.json`），脚本仍保留 `--plan`/`--data` 作为无面板直连的三段式备选）、`workspace-init`（工作区初始化：在任意空目录一键生成三大工具 + 持仓/观察仓/个股知识库/知识更新状态等运行时目录骨架与清单模板，脚本真源 `src/workspace-init/init_workspace.py` 全量生成、幂等可重复、纯标准库，随插件包分发并由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/init_workspace.py` 提供给目标会话下载执行；**并承担运行环境准备**：脚本 `src/workspace-init/setup_runtime.py` 在目标工作区建持久 `.venv` 并装 yt-dlp/faster-whisper 等第三方依赖，幂等、带进度、失败即清晰报错，由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/setup_runtime.py` 分发——bili-video-summary 等依赖第三方库的技能统一走它，不再各自现场 pip；**它只装依赖、不装 Python**，检测到系统无可用的 Python 解释器时输出 `python_missing`（退出码 2）并停下，提示用户自行安装完整版 CPython 3.11+，这是「非插件问题」、需用户确认处置，不做自动安装/兜底）。
- `output/sectors/`、`output/skill-state/`、`output/portfolio/` 是 skill **运行时生成的易过期/含个人信息动态输出**，已被 `.gitignore` 忽略，**不提交**。景气行业快照每次运行 `prosperity-analysis` 时重写 `output/sectors/current-sectors.md`；持仓清单首次运行 `portfolio-tracker` 的 `manage_holdings.py` 时从模板生成。
- tushare MCP 的 URL/token 由用户在投研工具设置卡片里填写，存于 DSH 本地 settings 文档（不入库）；他人需用自己的 tushare token 复现。
- 更新知识资产：用 `daily-update` 技能，运行 `python .agents/skills/daily-update/scripts/check_updates.py` 查看到期（景气快照按季度），更新后 `--mark <id>` 回写。修改该技能用**系统 python**，勿用需联网的依赖。持仓复核用 `portfolio-tracker`，不挂到 `daily-update` 的资产清单（它是逐条自带的 cadence，非知识资产）。
- **新增/调整技能时**：梳理它与其他技能的衔接关系，判断是否需要同步更新——`AGENTS.md` 的技能清单/提交边界，以及相关技能 `SKILL.md` 里的分工说明（如「与其它技能分工」一节）。避免职责重叠或登记遗漏。

## 工程约定

- 代码风格：ruff（line-length=100, py311），`src` 布局 + setuptools 自动发现。
- **新增/修改代码、配置、注释、提交信息一律用中文**（例外：代码标识符、日志里的字段名、外部约定的英文术语）。
- **只改该任务要求的文件**：不改与此任务无关的代码；与任务无因果关系的格式 / 风格调整不要夹带。
- 改完 Python 代码**先跑测试**：`.venv/Scripts/python.exe -m pytest`，全绿再交付。
- **提交边界**：`output/`、`.venv/`、`.env`、`__pycache__/`、`.tmp/` 等运行时/本地产物均已忽略，**不得 `git add` 或撰写进提交**。含密钥的配置（tushare MCP URL/token、LLM key）绝不入库。
- 新增知识规则放 `src/quantify/knowledge/rules/*.yaml`（package-data 会自动打包）。
- 改动估值/风控/选股逻辑时，先读 `docs/prosperity_investing.md` 与 `knowledge/rules/` 下的估值、策略、风控规则。

## 提交与验证清单（改代码前后对照）

- [ ] 只动了任务范围内的文件，无关改动未夹带。
- [ ] 新增代码/注释为中文，风格通过 ruff。
- [ ] `.venv/Scripts/python.exe -m pytest` 全绿。
- [ ] 未 `git add` 任何 `output/`、`.venv/`、`.env`、`__pycache__/`、`.tmp/` 下的文件。
- [ ] 若新增/调整了 skill，已同步更新本文件技能清单与相关 `SKILL.md` 的分工说明。
