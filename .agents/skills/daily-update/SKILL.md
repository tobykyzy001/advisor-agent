---
name: daily-update
description: 维护并更新项目知识库的技能。当用户要求「更新知识库/刷新知识/更新景气行业/定期更新/管理知识资产更新」、或触发定时自动化(如每日/每周/每季度更新当期景气行业快照)时使用。核心逻辑：按清单中每条知识资产登记的更新周期(如景气度快照按季度)，先读取上次更新时间，将其归入所在季度(或周期)，再与当前时间所处周期比对——周期不一致则执行更新并回写本次更新时间；周期内则跳过。
---

# 知识库更新(daily-update)

管理知识库各条资产的**更新周期**，判断哪些到期、触发更新、并回写更新时间。当前默认节奏：**景气度/当期景气行业快照按季度更新**。

## 何时触发
- 用户说「更新知识库 / 刷新景气行业 / 到时间该更新了吧 / 检查哪些知识要更新」等。
- 定时自动化(通过 Cron 安排)触发本技能，例如每季度核对景气行业是否需要刷新。
- 任何时候想查看各知识资产的上次更新与下次到期。

## 核心逻辑（按周期比对）
对每条资产读其 `cadence`(更新周期) 与 `last_update`(上次更新时间)：
1. **归期**：把 `last_update` 归入它所处的周期——季度取 `YYYY-Qn`，月度取 `YYYY-MM`，周取 `YYYY-Www`，年度取 `YYYY`，日取 `YYYY-MM-DD`。
2. **取当前周期**：同样归入 `now` 所处的周期。
3. **比对**：`last_update` 为空(从未更新) 或 上次周期 ≠ 当前周期 → **到期需更新**；相等 → 周期内无需更新。
   - 例：景气行业上次更新在 `2026-Q3`，当前 `2026-Q3` → 一致，**不更新**；当前 `2026-Q4` → 不一致，**更新**。

到期判断与回写全部由脚本完成，见下。

## 文件布局
```
.agents/skills/daily-update/
├── SKILL.md                     # 本技能
├── references/
│   └── manifest.yaml            # 资产清单+周期(模板，已提交；last_update 为空)
└── scripts/
    └── check_updates.py         # 周期比对 + 回写上次更新时间
output/skill-state/
└── update-manifest.yaml         # 运行时状态(含 last_update)，已被 .gitignore 忽略，不入库
```

> `references/manifest.yaml` 是**可提交的配置模板**；`output/skill-state/update-manifest.yaml` 是**运行时状态**（含每次更新的 `last_update`），会随更新变化故不提交。首次运行会自动读取模板生成状态文件。

## 工作流
1. **查看哪些资产到期**（用系统 python，脚本已随仓库提交）：
   ```
   python .agents/skills/daily-update/scripts/check_updates.py
   ```
   输出每一行 `[DUE需更新 | ok周期内] <id>: cadence=<周期> last_update=<上次> now=<当前>`。
2. 对每条 `DUE` 资产，按其 `update_instruction` 执行更新：
   - **prosperity-sectors** → 调用 `prosperity-analysis` 技能重新联网研究，改写其 `output_path`(`output/sectors/current-sectors.md`)，并标注数据时点。
3. **回写本次更新**（务必在更新完成后执行，避免误标）：
   ```
   python .agents/skills/daily-update/scripts/check_updates.py --mark prosperity-sectors
   ```
4. **报告**：列出本次已更新 / 未到期跳过 / 各自下次到期周期。

## 说明
- 新增知识资产：在 `references/manifest.yaml` 的 `assets` 里加一条（id、cadence、output_path、update_instruction），脚本自动按周期管理。
- 测试/演示：`--now YYYY-MM-DD` 可指定"当前时间"查看到期结果；`--mark <id>` 按该时间回写上次更新。
- 可选的持续自动化：若需要系统到点自动跑本技能，用 CronCreate 按季度/每日调度（由运行环境的模型执行更新）。
