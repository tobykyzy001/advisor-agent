# 本地测试指南

> 在把此插件发布到 npm / GitHub 之前，先用本地路径验证「安装 → 加载 → 侧边栏入口 → 技能表单 → 调用 skill」全链路。

## 前置

- 已安装并能运行 DeepSeek Harness WebUI（`dsh web`）。
- 已在 `D:\Coding\advisor-agent` 下准备好本仓库（仓库根即插件包根）。

## 步骤

1. **完全退出 DSH host**（不是关浏览器标签页，是停掉 `dsh` 进程）。

2. **安装插件**（本地路径）：

   ```powershell
   dsh plugin --profile web add "D:\Coding\advisor-agent"
   ```

   底层等价于在 `~/.dsh/profiles/web` 里执行 `pnpm add <路径>`，并把本包（因声明了 `dsh.bundle`）自动 reconcile 进 `dsh.profile.bundles`。

3. **重启 DSH WebUI**：

   ```powershell
   dsh web
   # 或 pnpm exec dsh web / dsh --profile web
   ```

4. **浏览器验证**：打开 `http://127.0.0.1:3080`，
   - **左侧栏底部**应出现「投研工具」按钮。
   - 点它弹窗：选择工具（个股估值）、股票名或代码输入、市场下拉、**投递目标**（新开会话/当前会话）、运行按钮。
   - **设置 → 插件**里应有「投研工具」卡片（技能开关 + 默认投递目标）。

5. **点「运行」**：输入 `600519` → 市场 A股 → 投递目标选「新开会话」→ 运行。
   - 应新开会话并提交首条指令，随后 agent 执行估值分析并回显结论。

6. **配置持久化**：在设置卡片里关掉 / 开启某个技能，或改默认投递目标，重启后应保持（写入 `~/.dsh/profiles/web` 的配置）。

## 常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 侧边栏无「投研工具」 | 插件未 reconcile、host 未重启、或 `enabled=false` | 确认第 2 步无报错；查 `~/.dsh/profiles/web/package.json` 的 `dsh.profile.bundles` 含 `advisor-agent` |
| 侧边栏按钮置灰/点不开 | `enabledSkills` 为空或总开关关闭 | 到设置 → 插件 → 投研工具，勾选技能与总开关 |
| 面板点运行报「未连接/未找到会话」 | `sessions`/`workspaces` 服务未就绪 | 刷新页面重试；先在主会话待一会再点 |
| 点「新开会话」报「没有可用工作区」 | 该 profile 无工作区 | 手动建一个工作区/会话后再试 |
| 点运行 agent 无反应 | 指令措辞未命中 skill，或 agent 未装载 skill 目录 | 见下 |

## 指令措辞与 agent 匹配

面板拼出的指令形如：

```
请调用 skill「stock-valuation」执行个股估值（股票名或代码: 600519；市场: A）。对一只 A股/港股 做估值判断：贵不贵 / 值多少 / 目标价 / 何时重估
```

若 agent 未据此触发 `stock-valuation` 技能，需在 `lib/client.js` 的 `buildInstruction` 里调整措辞，或确认 agent 侧 `.agents/skills/stock-valuation/SKILL.md` 的触发条件能命中该文本。

## 卸载

```powershell
dsh plugin --profile web remove advisor-agent
```

（先完全退出 DSH；remove 后重启 DSH 一次，侧边栏入口与设置卡片才会消失。）