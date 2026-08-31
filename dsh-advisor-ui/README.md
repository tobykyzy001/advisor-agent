# advisor-agent

**投研工具**：一个可开源的 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（DSH）第三方插件，在 DSH Web GUI **侧边栏**提供一个常驻入口，以**表单化**方式调用投顾 skill —— 点击即运行「拉数据 → 分析 → LLM 分析」流水线，把现有命令行式的 skill 变成可点击、可输参数的图形交互页。

> ⚠️ 本插件仅生成研究参考，不构成投资建议。

## 它解决什么问题

`advisor-agent` 仓库里的 skill（个股估值、行业景气、持仓复盘、视频总结等）本质都是：

```
输入参数 → 拉取数据 → 按方法论分析 → LLM 生成结论
```

但它们当前是「给 LLM 读的 Markdown 方法论 + 散落的 Python 脚本」，只能靠 agent 对话或 CLI 触发。本插件把它们统一成**「参数 schema + 图形表单」**：

- 每个 skill 声明一份**参数 schema**（输入参数类型/默认值）。
- 侧边栏「投研工具」入口点开**按 schema 自动渲染表单**（文本框/下拉框）。
- 用户点「运行」，面板把参数拼成一条指令，通过 `session.prompt` 提交给 **DSH agent**（也就是你正在对话的 agent）执行。
- 可**新开会话**投递（默认），也可**发到当前会话**（表单内临时切换）。
- agent 收到后匹配对应 skill，跑脚本拉数据、按方法论分析、LLM 生成结论，结果回显在会话流里。

**关键点：没有实现新的 agent** —— 执行者就是 DSH 自己的 agent，面板只是「表单 + 一次 prompt 转发」。

## 已接 skill

| skill id | 面板名 | 参数 | 状态 |
|---|---|---|---|
| `stock-valuation` | 个股估值 | `symbol`（代码）、`market`（A/HK） | ✅ 首期 |

## 安装

### 方式一：从 npm / GitHub 安装（推荐）

完全退出 DSH host 后，在 DSH 安装目录执行：

```powershell
pnpm exec dsh plugin --profile web add advisor-agent
# 或从 GitHub 直接装（免发布 npm）：
pnpm exec dsh plugin --profile web add git+https://github.com/<your-org>/advisor-agent.git
```

### 方式二：本地路径安装（开发用）

把本仓库的 `dsh-advisor-ui/` 目录作为本地包安装：

```powershell
dsh plugin --profile web add D:\Coding\advisor-agent\dsh-advisor-ui
```

> `--profile web` 只在 `dsh plugin` 子命令里必需；日常启动仍是 `dsh web`（或 `pnpm exec dsh web`）。

装完重启 DSH WebUI。入口：

```
左侧栏底部 → 投研工具
设置 → 插件 → 插件配置 → 投研工具（技能开关 / 默认投递目标）
```

## 配置项

宿主侧配置（`cordis.patch.yml` 提供默认值，设置页可改）：

| 字段 | 含义 | 默认 |
|---|---|---|
| `enabled` | 总开关：关闭后侧边栏不显示入口 | `true` |
| `enabledSkills` | 启用的 skill id 列表 | `["stock-valuation"]` |
| `defaultTarget` | 点「运行」默认投递目标：`new`(新开会话) / `current`(当前会话) | `new` |

## 目录结构

```
dsh-advisor-ui/
├── package.json           # 插件 manifest：dsh.client（客户端依赖）+ dsh.bundle.patch
├── cordis.patch.yml       # 宿主侧 patch：注册插件行及其配置
├── src/index.js           # 宿主侧入口（node）：配置 schema + 本地 /config 端点
└── lib/client.js          # 客户端 bundle：技能注册表 + 通用表单引擎 + 投递
```

## 新增一个 skill

1. 在 `lib/client.js` 顶部的 **`ADVISOR_SKILLS`（技能注册表）** 加一条对象：
   `{ id, label, description, params }`。
2. （可选）在 `cordis.patch.yml` 的 `enabledSkills` 默认列表里补上该 id，让它默认启用。
3. 确保 agent 侧能识别该 skill 并执行（复用 `.agents/skills/<skill-id>/` 的方法论）。

> 注意：DSH 的 `/plugins/<id>/` 只伺服 `client.js` 一个 bundle，插件的其它静态文件不会被伺服，因此**技能注册表必须内联在 `lib/client.js`，不能走运行时 fetch**。增加技能只是改这一处数据，渲染/校验/指令拼装全部自动跟进。

## 技术要点

- 客户端 bundle 用 `window.__ModuleLoader__.load({ id, factory })` 注册，返回 `{ name, inject, apply(ctx) }`。
- 两个槽位：`sidebar.footer.action`（侧边栏入口）+ `settings.plugin.item`（设置卡片）。配置走宿主 `/plugins/advisor-agent/config` 端点（GET/PATCH，本地回环校验）。
- 投递：
  - 新开会话：`ctx.get('workspaces').connectWorkspace(workspaceId)` 拿回已在 list 里的新 session id，再 `sessions.binding(id).session.prompt(...)`。
  - 当前会话：`sessions.list.getSnapshot().current` → `sessions.binding(current).session.prompt(...)`。
- `ADVISOR_SKILLS` 是 schema 驱动的通用表单**单一数据源**；渲染、必填校验、`buildInstruction` 都由它驱动。

## 许可

[MIT](./LICENSE)。