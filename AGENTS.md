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
output/watchlist/           # 观察仓清单（gitignore，watchlist-manager 唯一写入口；w-bottom-screener / momentum-rotation 只读共用）
output/quotes-store/        # 本地 CSV 行情库（gitignore，每标的一份 <ts_code>.csv；fetch_quotes.py 刷库写回，w-bottom-screener / momentum-rotation 只读共用）
output/w-bottom/            # W底筛选取数缓存与报告（gitignore，w-bottom-screener 运行时数据）
output/momentum/            # 动量轮动取数缓存、组合信号报告与持仓状态（gitignore，momentum-rotation 运行时数据）
output/etf-rotation/        # ETF 池清单 pool.yaml、轮动信号报告与持仓状态（gitignore，etf-rotation 运行时数据）
output/prosperity/          # 景气板块跟踪状态与筛选/选股报告（gitignore，prosperity-picking 运行时数据）
output/research/            # 研报阅读产物：正文抽取、claims、行情/一致预期证据文件与阅读报告（gitignore，report-reading 运行时数据）
.agents/skills/             # ZCode 技能：prosperity-analysis / daily-update / stock-valuation / report-reading / portfolio-tracker / bili-video-summary / copy-trade / w-bottom-screener / momentum-rotation / prosperity-picking / watchlist-manager / workspace-init / feishen-trading-logic
```

## 配置约定

优先级：环境变量 `QUANTIFY_*`（嵌套用 `__`）> `config/settings.yaml` > pydantic 默认值。
第三方密钥（无前缀）从 `.env` / 进程环境读取：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`TUSHARE_TOKEN`（见 `.env.example`）。
未配置 `LLM_API_KEY` 时研报自动退回**离线规则模式**，功能仍可端到端跑通。

## 数据源

- 默认 `provider: akshare`（联网实时）；网络不可用或未安装时自动回退 `LocalProvider`（内置示例数据），保证离线可演示。
- `provider: local` 强制用示例数据。港股/财务字段扩展在 `data/fetcher.py` 的 `AkshareProvider` 中。
- **统一行情取数 CLI（tushare REST 直连）**：所有行情经 `src/workspace-init/fetch_quotes.py`（自包含纯标准库，随插件分发，面板场景由投研工具预置到工作区 scripts/）直连 tushare pro REST API：刷库模式对照 `output/quotes-store/` 增量补到最新交易日（幂等合并），快照模式（`--snapshot`）出最新收盘/PE/PB/市值等估值字段，供 W底/动量/估值/持仓复核/论据核查使用；行情数据全程不经过 LLM 上下文。token 优先级：投研工具面板配置（随指令以 `--token` 参数传递，存宿主设置）> 环境变量 `TUSHARE_TOKEN` > 工作区 `.env`（见 `.env.example`），均不入库。
- **机构一致预期取数 CLI（同花顺 F10 直连）**：前瞻估值的分母（机构预测净利润/EPS 均值，tushare 没有）经 `src/workspace-init/fetch_forecast.py`（自包含纯标准库、**无需 token**，随插件分发，面板场景由投研工具预置到工作区 scripts/）直连同花顺 F10「盈利预测」页解析：净利润/EPS 一致预期年度汇总（年度/机构数/最小/均值/最大/行业平均）、机构预测明细（机构/研究员/各年 EPS/净利润/报告日期）、详细指标历史实际值与预测均值（营收/净利/增速/ROE/每股净资产/静态PE）；`--mcap <总市值亿元>` 可追加各预测年前瞻PE。表格按 caption/表头内容识别、不按位置猜，解析不到如实报缺口。仅 A 股（沪深北，含北交所；港股无此页、明确报错）。供 stock-valuation 前瞻PE/PEG 与 prosperity-picking 选股估值复用；输出小体量、可进会话上下文。
- 联网拿到的宏观/行业数据标注数据时点。

## 技能与「方法 vs 动态数据」提交边界（重要）

- `.agents/skills/` 下是**随仓库提交**的方法/知识：`prosperity-analysis`（行业景气度方法论）、`daily-update`（知识资产更新周期管理）、`stock-valuation`（单一个股估值方法论，含直连行情脚本 `scripts/fetch_snapshot.py` 与机构一致预期取数脚本 `src/workspace-init/fetch_forecast.py`——同花顺 F10 盈利预测页直连解析、前瞻PE/PEG 的分母来源，纯标准库、无需 token，面板场景由投研工具预置到工作区 scripts/；SKILL.md、references/data-source.md 与回退取数脚本 scripts/fetch_snapshot.py 由面板经 /provision 预置到目标工作区（方法论文档在 scripts/stock-valuation/、脚本在 scripts/）：**投研面板的「个股估值」为工具类调用——不调用 skill**，预置后按方法论就地估值（无 .agents/skills 的任意工作区可跑），prosperity-picking 面板流程的选股估值亦复用同一预置）、`report-reading`（研报阅读：把一份卖方研报（本地文本或 http(s) 链接）拆成可核查的主张——标的/机构/日期/评级/目标价/自述估值锚（如「给予 2027 年 25 倍 PE」）/分年度预测表（A 列记为已披露实际值、E 列为预测）——做一轮**只找证据**的批判性核查：内部算术矛盾（目标价 vs 自述锚重算）、行文增速 vs 表格绝对值、EPS vs 净利÷股本、预测 vs 已披露实际值、评级方向 vs 目标价空间 —— 以上为**证伪**（有替代证据就替换 overridden、替代不了就作废 rejected，且作废主张绝不带进估值）；报告偏旧、与机构一致预期偏离、增速异常 —— 以上为**存疑**（只登记 caveat，**绝不改数字**）。核查后落成「采信账本」（主张/采信值/状态/依据），再**只基于采信后的预测**推估值桥：前瞻PE 走市值口径（总市值÷采信净利，不受股本摊薄口径影响）、逐年PE、PEG、目标价空间、自述锚复核价、合理价区间与相对保守价溢折价、现价隐含要求（PEG=1 时市场要求的净利增速）、方向结论（便宜/合理/贵，倍数为显式假设：默认 8–25 倍只是通用观察区间、未做质量溢价/行业β 校准，可用 `--pe-band` 覆盖）；与机构一致预期的偏离只做差异分析（预期透支/预测滞后/覆盖不足/年内进度不符/口径差异/独立判断），**一致预期不是证伪证据**。脚本真源 `src/workspace-init/report_reader.py` 自包含纯标准库、随插件包分发，面板场景由投研工具预置到工作区 scripts/ 直接执行；入口**一个参数同时支持本地文件与 http(s) 链接**（PDF 不解析、退出码 2 fail-closed 报缺口），行情与一致预期证据经 fetch_quotes.py `--snapshot` 与 fetch_forecast.py 的**原样输出文件**喂入（`--snapshot-file` / `--consensus-file`，数字不经过会话手工转抄），抽取缺口用 `--claims` JSON 补齐，产物（正文/claims/证据/报告）写 `output/research/`；投研工具面板有表单入口（只一个「研报入口」字段）；与 stock-valuation 互补——stock-valuation 决定「该给几倍」，本技能决定「假设哪个盈利数、这份研报哪里被证据推翻了」）、`portfolio-tracker`（持仓清单管理，每条持仓按 `cadence` 定期复核投资类型/估值方式/估值价格）、`bili-video-summary`（B站视频音频下载+离线转录工具，转录产物写 `output/videos/`、模型缓存收拢到 `output/videos/models/`，分析环节复用 stock-valuation / prosperity-analysis 的方法与数据口径；**已改为自包含分发，无需 .agents/skills**：脚本真源 `src/workspace-init/transcribe_video.py` 随插件包分发、面板场景由投研工具预置到工作区 scripts/ 直接执行，依赖 yt-dlp+faster-whisper 非纯标准库，内置 `--selfcheck` 依赖自检、`--models` 模型缓存收拢、幂等跳过转录；**运行环境由 workspace-init 的 `setup_runtime.py` 统一准备**（建工作区持久 `.venv` + 装依赖，本脚本不自行装环境）；`.agents/skills/bili-video-summary/` 仅保留方法说明与历史脚本，作为 agent 无面板直连时的备选）、`copy-trade`（抄作业分析：抓取群作业链接→结构化消息→还原持仓路线→轻量回测→结合 tushare 行情判断「值不值得抄」，别名→标的映射需人工确认后固化到 `output/copy-trade/alias-map.override.yaml`）、`w-bottom-screener`（观察仓「W底+放量」形态筛选：读 `output/watchlist/` 标的池→取数统一走 fetch_quotes.py 直连 tushare 增量刷共享行情库（W底场景 `--min-bars 30 --full-days 90`）→识别双底(两相近低点，允许横盘变体)+第二底后放量阳线确认→出命中报告；脚本真源 `src/workspace-init/w_bottom_screen.py` 自包含纯标准库、随插件包分发，面板场景由投研工具预置到工作区 scripts/ 直接执行，策略脚本默认直接读库计算（`--plan` 仅诊断；库内无数据或最后交易日距今超 10 自然日 fail-closed 提示先刷库））、`momentum-rotation`（观察仓「中期动量轮动」选股：读 `output/watchlist/` 标的池→取数统一走 fetch_quotes.py 直连 tushare 增量刷共享行情库（动量场景 `--min-bars 121 --full-days 420`）→算 mom20/mom120/mom60 三条区间动量（前复权口径：行情库每行带 pct_chg，策略端复利连乘还原除权跳空；旧六列库刷库自动回补、断链 fail-closed）→三道过滤(MA120趋势关/反追高关/大盘状态关；大盘状态关可用 `--market-guard false` 临时关闭)→快慢双榜排名+老仓buffer16粘性+快4慢4补满8仓→等权组合+流动性约束+空仓兜底，退出纯靠排名滚动无价格止损；脚本真源 `src/workspace-init/momentum_strategy.py` 自包含纯标准库、随插件包分发，面板场景由投研工具预置到工作区 scripts/ 直接执行，策略脚本默认直接读库计算并回写 `output/momentum/state.json`（`--plan` 仅诊断；库内无数据或最后交易日距今超 10 自然日 fail-closed 提示先刷库））、`etf-rotation`（ETF 动量轮动：**两段式**——①pool-build 选 ETF：ETF 目录 `references/etf-map.yaml`（随技能提交的方法资产：常驻层跨资产 11 只全收/行业主题层 28 只按 sector 映射/扩展层小众默认不收）按 `output/prosperity/state.yaml` 在跟景气板块映射生成当期池 `output/etf-rotation/pool.yaml`（gitignored，note 附入选原因+复核日期；无在跟板块则只按常驻层生成最小池并提示先跑 prosperity-picking screen；菜单未覆盖板块可联网补录并追加写回目录）；②run 轮动：对当期池取数统一走 fetch_quotes.py 直连 tushare 增量刷共享行情库（本场景 `--min-bars 45 --full-days 120`）→算 24 日加权(linspace 1→2)对数回归斜率年化×R²（前复权口径同 momentum-rotation；设计可追溯聚宽主帖"ETF动量策略"的用户审定改良版：去分钟级止损/去分钟量过滤/满仓单吊改前 K 等权）→三道过滤(R²≥0.4/近3日单日跌幅≤3.5%/得分∈(0,5))→合格榜降序取前 K 等权（默认 K=4，实测 K=10 为低波端点）+老仓 buffer=ceil(1.5K) 粘性+空槽切 511880 货币ETF防御，退出纯靠排名滚动无价格止损；脚本真源 `src/workspace-init/etf_rotation_strategy.py` 自包含纯标准库、随插件包分发并由宿主静态端点 `/plugins/advisor-agent/assets/workspace-init/etf_rotation_strategy.py` 提供下载执行，策略脚本默认直接读库计算并回写 `output/etf-rotation/state.json`（`--plan` 仅诊断；库内无数据或最后交易日距今超 10 自然日 fail-closed 提示先刷库；QDII 品种入选持仓时报告提示人工核溢价；`pool.yaml` 不存在时提示先 pool-build 不自建名单）；2026-10-11 聚宽实测 K=4 年化 ~22.8%/夏普 1.123/回撤 21.49%、可复现，详见 `docs/etf_rotation_joinquant.md` 第十三节与 `docs/etf_rotation_joinquant_v7.py`）、`prosperity-picking`（景气板块选股组合工具：两段式流水线——①筛选景气板块：读 `output/prosperity/state.yaml`（脚本 `src/workspace-init/prosperity_state.py` 是该状态唯一写入口，自包含纯标准库、随插件包分发，面板场景由投研工具预置到工作区 scripts/ 直接执行），无在跟板块则按 prosperity-analysis 方法论联网研究初始化（2–5 个板块+入选原因），有则复核景气点/风险点并决定剔除/新增（默认全量：逐板块复核+扫描新热点板块；范围可收窄为仅扫当前新热点、或仅指定板块——在跟的重筛、未在跟的用户指名板块研究后新增，避免每次全量重跑），剔除时**必须先询问用户**是否将该板块观察仓标的（`PS=<板块id>`）一并踢出；②选出景气标的：无在跟板块则拒绝请求，有则每个在跟板块选出 8–12 支（默认 10）与板块景气高相关的核心受益标的（仅 A 股沪深北、含北交所，不选港股；分板块各起一个后台 subagent 并行完成选标的/取行情/估值，并发上限 3、完成一个补一个，无 subagent 工具时串行退化），按景气投资口径估值（前瞻PE/PEG/周期中枢/逆向DCF/估值分位，**禁用孤立静态PE/PB下结论**，复用 stock-valuation 方法论——其 SKILL.md 与 references/data-source.md 由面板预置到 scripts/stock-valuation/ 供 pick 时通读，目标工作区无需装有该技能；前瞻PE/PEG 的分母用 fetch_forecast.py 取同花顺机构一致预期，随插件分发、面板预置、无需 token；估值结论与机构一致预期分歧显著时必须分析差距原因——预期透支/预测滞后/覆盖不足/年内进度不符/口径差异/独立判断——并给采信立场），经用户确认后委托 watchlist-manager 写入观察仓并 `set --PS/--PS_DATE` 打板块 key（供板块剔除时快速筛选删除）；每轮筛选/复核/选股报告写 `output/prosperity/report_*.md`；投研工具面板有表单入口（动作 screen 筛选/复核景气板块、pick 选出景气标的 + 范围 all 全量/new 仅扫当前新热点/only 仅指定板块 + 关注点/数量））、`watchlist-manager`（观察仓清单管理：`output/watchlist/` 标的池的**唯一写入口**——list/add/set/rm/check，list 支持关键字搜索与 `--where` param 筛选（如按 `PS=<板块id>` 精确筛出某景气板块全部标的），add 代码自动规范化（600519→600519.SH）且幂等、保留首加日期，set 支持任意 `--param` 透传供其他工具写死自己的英文键委托写入（如 w-bottom 的 `--BS B`、momentum 的 `--MR 3`）；w-bottom-screener / momentum-rotation 对清单只读，成果明细留各自 `output/` 目录、仅委托 set 留痕；脚本真源 `src/workspace-init/manage_watchlist.py` 自包含纯标准库、随插件包分发，面板场景由投研工具预置到工作区 scripts/ 直接执行；投研工具面板有表单入口（动作 add/list/set/rm/check + 代码/名称/备注/param 透传 + list 搜索），对话式或投研流程内亦可触发）、`workspace-init`（工作区初始化：在任意空目录一键生成三大工具 + 持仓/观察仓/个股知识库/知识更新状态等运行时目录骨架与清单模板，脚本真源 `src/workspace-init/init_workspace.py` 全量生成、幂等可重复、纯标准库，随插件包分发，面板场景由投研工具预置到目标工作区 scripts/ 直接执行；**并承担运行环境准备**：脚本 `src/workspace-init/setup_runtime.py` 在目标工作区建持久 `.venv` 并装 yt-dlp/faster-whisper 等第三方依赖，幂等、带进度、失败即清晰报错，面板场景同由投研工具预置到工作区 scripts/——bili-video-summary 等依赖第三方库的技能统一走它，不再各自现场 pip；**它只装依赖、不装 Python**，检测到系统无可用的 Python 解释器时输出 `python_missing`（退出码 2）并停下，提示用户自行安装完整版 CPython 3.11+，这是「非插件问题」、需用户确认处置，不做自动安装/兜底；装依赖撞权限拒绝（Errno 13 / WinError 5，典型为会话文件沙箱受限令牌与 Python 建临时目录冲突）时以退出码 3 停下并打印提权指引——agent 唯一动作是用会话提权机制重跑同一条命令、交用户审批，--target / monkeypatch / 换路径等替代方案会同样失败，一律禁止；半成品 .venv（有解释器、缺 pip）下次运行自动补装）。
- `feishen-trading-logic`（飞神/飞大交易逻辑：**非脚本型上层决策框架**）：按「市场环境→板块阶段→个股角色→量价触发→交易周期→成本仓位→条件动作」判断观察池/单股/持仓/复盘；规则卡与别名字典随技能提交并随插件包分发，面板场景由投研工具预置到工作区 scripts/feishen-trading-logic/（SKILL.md、feishen-rulebook.md、feishen-aliases.md）供目标会话直接执行，**不要求目标工作区存在 `.agents/skills`，面板指令不调用 skill**；个股价格/量能证据优先用 fetch_quotes.py --snapshot，证据不足只给候选观察/待验证；**默认不把结论写回观察仓**，确需留痕时只能委托 `watchlist-manager set` 写入。
- `output/sectors/`、`output/skill-state/`、`output/portfolio/` 是 skill **运行时生成的易过期/含个人信息动态输出**，已被 `.gitignore` 忽略，**不提交**。景气行业快照每次运行 `prosperity-analysis` 时重写 `output/sectors/current-sectors.md`；持仓清单首次运行 `portfolio-tracker` 的 `manage_holdings.py` 时从模板生成；观察仓清单由 `watchlist-manager` 维护（首次 add 自动创建，w-bottom-screener / momentum-rotation 只读）；景气板块跟踪状态由 `prosperity-picking` 的 `prosperity_state.py` 维护（`output/prosperity/state.yaml`，含剔除历史留痕，筛选/复核/选股报告写同目录）；当期 ETF 池 `output/etf-rotation/pool.yaml` 由 `etf-rotation` 维护（pool-build 时从目录资产 `references/etf-map.yaml` 按在跟景气板块映射生成并附入选原因+复核日期，景气研究定期复核成分，轮动报告 plan_*.md 与持仓状态 state.json 写同目录）；研报阅读的正文抽取、claims、行情/一致预期证据文件与阅读报告由 `report-reading` 写 `output/research/`（读库只读、不改任何清单）。
- tushare token 可在投研工具面板配置（随指令以 `--token` 传递，存宿主设置）或用环境变量 `TUSHARE_TOKEN` / 工作区 `.env`（均不入库）；他人需用自己的 tushare token 复现。
- 更新知识资产：用 `daily-update` 技能，运行 `python .agents/skills/daily-update/scripts/check_updates.py` 查看到期（景气快照按季度），更新后 `--mark <id>` 回写。修改该技能用**系统 python**，勿用需联网的依赖。持仓复核用 `portfolio-tracker`，不挂到 `daily-update` 的资产清单（它是逐条自带的 cadence，非知识资产）。
- **新增/调整技能时**：梳理它与其他技能的衔接关系，判断是否需要同步更新——`AGENTS.md` 的技能清单/提交边界，以及相关技能 `SKILL.md` 里的分工说明（如「与其它技能分工」一节）。避免职责重叠或登记遗漏。

## 工程约定

- 代码风格：ruff（line-length=100, py311），`src` 布局 + setuptools 自动发现。
- **新增/修改代码、配置、注释、提交信息一律用中文**（例外：代码标识符、日志里的字段名、外部约定的英文术语）。
- **只改该任务要求的文件**：不改与此任务无关的代码；与任务无因果关系的格式 / 风格调整不要夹带。
- 改完 Python 代码**先跑测试**：`.venv/Scripts/python.exe -m pytest`，全绿再交付。
- **提交边界**：`output/`、`.venv/`、`.env`、`__pycache__/`、`.tmp/` 等运行时/本地产物均已忽略，**不得 `git add` 或撰写进提交**。含密钥的配置（tushare token、LLM key）绝不入库。
- 新增知识规则放 `src/quantify/knowledge/rules/*.yaml`（package-data 会自动打包）。
- 改动估值/风控/选股逻辑时，先读 `docs/prosperity_investing.md` 与 `knowledge/rules/` 下的估值、策略、风控规则。

## 提交与验证清单（改代码前后对照）

- [ ] 只动了任务范围内的文件，无关改动未夹带。
- [ ] 新增代码/注释为中文，风格通过 ruff。
- [ ] `.venv/Scripts/python.exe -m pytest` 全绿。
- [ ] 未 `git add` 任何 `output/`、`.venv/`、`.env`、`__pycache__/`、`.tmp/` 下的文件。
- [ ] 若新增/调整了 skill，已同步更新本文件技能清单与相关 `SKILL.md` 的分工说明。
