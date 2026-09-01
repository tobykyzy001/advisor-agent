"""工作区初始化脚本：在指定目录一次性生成投顾工作区的完整目录树与模板。

用途：
  在一个空目录（或任意目录）下，搭出三大工具（抄作业 / 个股分析 / W底搜索）以及
  持仓 / 观察仓 / 个股知识库 / 知识更新状态 的运行时目录骨架，并写入各清单的模板文件
  （含注释，用户可编辑）。后续各技能脚本默认读写这些目录，开箱即用。

设计约束：
  - 纯标准库（os / shutil / pathlib / argparse），零第三方依赖，可在任何 Python 3.11+ 环境运行。
  - 幂等：重复运行不会覆盖用户已编辑的清单/数据文件，只在文件缺失时补建模板。
  - 不依赖本仓库的 quantify 包，脚本本身可拷贝到别处单独运行。

用法：
  python init_workspace.py                          # 在当前工作目录初始化
  python init_workspace.py --target D:/my-advisor    # 在指定目录初始化
  python init_workspace.py --target D:/my-advisor --dry-run   # 只打印动作，不落地
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 模板文件内容（全部为可编辑的"种子"文件；脚本只在文件缺失时写入）
# ---------------------------------------------------------------------------

README_MD = """\
# 投顾工作区（Advisor Workspace）

本目录是由 `workspace-init` 技能初始化的工作区骨架，承载三大工具的运行产物与个人研究数据：

| 工具/资产 | 运行时目录 | 说明 |
|---|---|---|
| 抄作业（copy-trade） | `output/copy-trade/` | 作业回测产物：原始HTML、解析消息流、回测报告、别名覆盖 |
| 个股分析（stock-valuation） | `output/reports/` | 个股估值研报（`research_*.md`） |
| W底搜索（w-bottom-screener） | `output/w-bottom/` | W底筛选取数缓存与命中报告 |
| 观察仓 | `output/watchlist/` | 观察仓标的池清单（W底筛选的输入） |
| 持仓（portfolio-tracker） | `output/portfolio/` | 持仓清单 `holdings.yaml` |
| 个股知识库 | `knowledge/` | 清单索引 + 每票一份分析文件（长期沉淀） |
| 知识更新状态（daily-update） | `output/skill-state/` | 各知识资产的上次更新时间 |

## 快速上手

1. 先回填个人清单：
   - 持仓 → 编辑 `output/portfolio/holdings.yaml`
   - 观察仓 → 编辑 `output/watchlist/watchlist.yaml`
2. 新增个股知识：在 `knowledge/stocks/` 下按 `_template.md` 复制一份，命名 `<ts_code>.md`
   （如 `600519.SH.md`），完成后在 `knowledge/index.md` 登记一行。
3. 跑各技能脚本，产物自动落到对应 `output/` 子目录。

> 本目录所有 `output/` 运行时产物与个人研究数据均不应提交到版本库（骨架自带 `.gitignore` 已涵盖）。
> 行情/财务数据有滞后，输出仅供研究参考，不构成投资建议。
"""

GITIGNORE = """\
# ---- 运行时产物（个人/易过期数据，不入库） ----
output/
knowledge/stocks/

# ---- 环境与密钥 ----
.env
*.pem
*.key

# ---- Python ----
__pycache__/
*.py[cod]
.venv/
venv/

# ---- OS / 编辑产物 ----
.DS_Store
Thumbs.db
"""

HOLDINGS_YAML = """\
# 持仓清单：portfolio-tracker 的运行时数据（本文件已 gitignore，不入库）。
# 字段说明：
#   code        股票代码（A股6位，如 600519；港股自行约定并在备注标注）
#   name        名称
#   投资类型     投资 | 投机（决定性标签，现价脱离估值支撑时从"投资"滑向"投机"）
#   估值方式     对应 stock-valuation 的生意属性→估值口径，写一句（如 "DCF+股息率"）
#   估值价格     合理估值/目标价，可单值或区间（如 1500 或 "1400~1600"）
#   现价        最近一轮行情快照的现价
#   结论        低估 / 合理 / 高估 + 一句话
#   更新时点     最近复核日期 YYYY-MM-DD
#   cadence     复核周期 daily/weekly/monthly/quarterly/yearly（默认 monthly）
#   last_update 运行时周期标识（脚本自动回写，勿手填）
#   建仓价      可选
#   数量        可选
#   备注        可选
holdings: []
"""

WATCHLIST_YAML = """\
# 观察仓清单：W底放量筛选的标的池（本文件已 gitignore，不入库）。
# 每条一行；ts_code 用 tushare 格式（A股带 .SH/.SZ/.BJ，港股如 00700.HK）。
# market: A | HK；note 可选，纯展示。
watchlist:
  - ts_code: "600519.SH"
    name: "贵州茅台"
    market: A
    note: "示例，可按需增删"
"""

SKILL_STATE_YAML = """\
# 知识更新状态：daily-update 的运行时状态（本文件已 gitignore，不入库）。
# 每条资产：id / cadence / output_path / update_instruction / last_update
# last_update 由 check_updates.py 回写，请勿手填。
assets:
  - id: prosperity-sectors
    cadence: quarterly
    output_path: output/sectors/current-sectors.md
    update_instruction: 按季度重写当期景气行业快照（prosperity-analysis 方法）
    last_update: null
"""

ALIAS_OVERRIDE_YAML = """\
# 抄作业别名覆盖（个人确认后的黑话→标的映射，已 gitignore，不入库）。
# 格式：别名: { 题材: "硅微粉", 标的: "联瑞新材", 候选tss: ["688300.SH"], 备注: "" }
# 说明：脚本自动推断的映射、以及你现场确认后固化的映射都写在这里。
# aliases: {}
"""

INDEX_MD = """\
# 个股知识库索引

> 每档个股的分析沉淀在 `stocks/` 下，命名为 `<ts_code>.md`（如 `600519.SH.md`）。
> 新增/更新后请在本表登记或更新对应行；本表是"清单索引"，个股详情看具体文件。

| ts_code | 名称 | 生意属性 | 最新估值口径 | 结论 | 数据时点 | 最近更新 |
|---|---|---|---|---|---|---|
| （示例）600519.SH | 贵州茅台 | 稳定价值 | DCF+股息率 | 合理 | 2025-01-02 | 2025-01-02 |
| | | | | | | |

## 使用说明

1. 新增一只票：复制 `_template.md` 为 `stocks/<ts_code>.md`，填好字段。
2. 回本表登记一行。
3. 更新某只票：改对应 `stocks/<ts_code>.md`，并把本表"最近更新"推进。
"""

TEMPLATE_MD = """\
# {{ts_code}} {{name}}

> 复制本文件为 `{{ts_code}}.md`，替换标题里的占位符，填写以下字段。完成后到 `index.md` 登记。

- 标的：{{name}}（{{ts_code}}）
- 生意属性：景气成长制造 | 地产金融 | 周期资源 | 稳定价值
- 估值口径：前瞻PE+PEG / PB+RNAV / 周期中枢 / DCF+股息率（写一句理由）
- 估值价格：单值或区间
- 现价 / 交易数据时点：
- 结论：低估 / 合理 / 高估 + 一句话
- 关键节点：等财报 / 等销售 / 等价格库存拐点 / 等现金流分红
- 风险提示：

## 分析记录

| 日期 | 现价 | 估值价格 | 结论 | 一句话 |
|---|---|---|---|---|
| | | | | |
"""

# ---------------------------------------------------------------------------
# 目录骨架定义：相对目标目录的路径
# ---------------------------------------------------------------------------

DIRS = [
    "output",
    "output/copy-trade",
    "output/reports",
    "output/portfolio",
    "output/sectors",
    "output/skill-state",
    "output/videos",
    "output/watchlist",
    "output/w-bottom",
    "knowledge",
    "knowledge/stocks",
]

# 需要"仅在缺失时创建"的模板文件：相对路径 -> 内容
FILES = {
    "README.md": README_MD,
    ".gitignore": GITIGNORE,
    "output/portfolio/holdings.yaml": HOLDINGS_YAML,
    "output/watchlist/watchlist.yaml": WATCHLIST_YAML,
    "output/skill-state/update-manifest.yaml": SKILL_STATE_YAML,
    "output/copy-trade/alias-map.override.yaml": ALIAS_OVERRIDE_YAML,
    "knowledge/index.md": INDEX_MD,
    "knowledge/stocks/_template.md": TEMPLATE_MD,
}


def _init(target: Path, dry_run: bool) -> None:
    """在目标目录生成骨架。幂等：目录无脑 mkdir，模板文件仅在缺失时写入。"""
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    for rel in DIRS:
        p = target / rel
        if dry_run:
            print(f"[dry-run] mkdir  {p}")
        else:
            p.mkdir(parents=True, exist_ok=True)

    for rel, content in FILES.items():
        p = target / rel
        if p.exists():
            print(f"跳过（已存在，不覆盖）：{p}")
            continue
        if dry_run:
            print(f"[dry-run] write  {p}")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            print(f"已写入：{p}")

    print(f"\n[完成] 工作区初始化完成：{target}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="init_workspace", description="初始化投顾工作区目录骨架")
    ap.add_argument("--target", default=".", help="目标目录（默认当前目录）")
    ap.add_argument("--dry-run", action="store_true", help="只打印动作，不实际落地")
    args = ap.parse_args(argv)

    _init(Path(args.target), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())