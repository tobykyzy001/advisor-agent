# quantify-agent

面向 **A股/港股** 的投资顾问式智能体（投顾知识库 + 估值研究 + 选股信号 + 组合风控）。
偏 **研究/估值** 类，不做回测；配合 ZCode 形成「数据 → 估值 → 选股 → 配仓 → 风控 → 研报」的完整研究闭环。

> ⚠️ 本项目输出仅供研究参考，不构成任何投资建议。

## 核心能力

| 能力 | 模块 | 说明 |
|------|------|------|
| 市场分析 / 研报生成 | `agent/` | 基于结构化结果生成中文研报（可接 LLM） |
| 选股 / 信号发现 | `analysis/screener.py` | 多因子估值评分与买入/观望/卖出信号 |
| 组合管理与风控 | `portfolio/` | 建议仓位、单标的上限、现金缓冲、风控告警 |
| 投顾知识库 | `knowledge/` | YAML 规则（估值/风控/策略），可检索引用 |
| 估值方法 | `valuation/` | PE/PB/ROE/PEG、DDM、目标PE、相对估值 |

## 环境

- Python ≥ 3.11（本仓库在 3.12 验证）
- 可选的行情数据源：`akshare`（免费）；研报生成：任意 OpenAI 兼容接口

## 快速开始

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

## 目录结构

```
quantify-agent/
├── config/settings.yaml        # 主配置（估值/风控/数据源参数）
├── src/quantify/
│   ├── config.py               # 配置加载（env > yaml > 默认值）
│   ├── data/                   # 数据层：schema / fetcher / cache
│   ├── valuation/              # 估值：metrics / DCF / 相对估值 / core汇总
│   ├── knowledge/              # 投顾知识库：YAML规则 + 检索
│   ├── analysis/               # 选股评分 / 信号 / 市场概览
│   ├── portfolio/              # 配仓 / 风控
│   ├── agent/                  # LLM抽象 / 编排 / 研报生成 / Markdown
│   └── cli.py                  # 命令行入口
├── tests/                      # 单元测试
├── notebooks/                  # 研究notebook
└── output/reports/             # 生成的研报（已gitignore）
```

## 数据源说明

- `provider: akshare`（默认）：联网拉取实时行情；网络不可用或未安装时自动回退 `LocalProvider`（内置示例数据），保证离线可演示。
- `provider: local`：强制使用示例数据。
- 港股/更多财务字段可在 `data/fetcher.py` 的 `AkshareProvider` 中扩展映射。

## 测试

```bash
pytest
```

## 后续扩展方向

- 接 real 财务数据（ROE 趋势、EPS 历史）完善 DCF 与 PEG 计算
- 增加历史 PE/PB 分位、行业对标
- 组合级回撤/波动率监控与实时再平衡信号
- Agent 多工具调用（检索、计算、复核）
