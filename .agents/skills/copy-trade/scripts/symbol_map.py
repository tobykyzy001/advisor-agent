"""抄作业技能配套：别名 → 标的（ts_code）映射与自动推断。

加载顺序（优先级从高到低）：
  1. output/copy-trade/alias-map.override.yaml —— 现场确认的回写覆盖（不入库）
  2. 本模块内置 DEFAULT_CONFIRMED —— 随仓库提交的题材常识映射
  3. 消息里出现的 6 位代码 / 具体中文名 → 交给 agent 调 tushare stock_basic 反查

本脚本只做「确定性」部分：
  - `load_map()` 合并 override + 默认，返回 alias -> 候选 ts_code 列表。
  - `resolve(code_or_name)` 处理 6 位数字补后缀、以及命中 confirmed_map 的题材。
  - 中文名反查 ts_code 需联网(tushare MCP)，由 agent 在会话内完成，不在本脚本内请求。
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OVERRIDE = Path("output") / "copy-trade" / "alias-map.override.yaml"

# override 文件为极简 YAML（本脚本自控格式），格式：
#   硅微粉:
#     - 688300.SH
#   冷液: 301018.SZ
# 用 stdlib 解析以去掉对 PyYAML 的硬依赖（skill 脚本约定仅标准库）。


def _parse_override(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("-") and ":" in line:
            k, _, _v = line.partition(":")
            k = k.strip().strip("\"'")
            cur = k
            out.setdefault(k, [])
            v = _v.strip().strip("\"'")
            if v:
                out[k].append(v)
        elif line.startswith("-") and cur is not None:
            out[cur].append(line.lstrip("- ").strip().strip("\"'"))
    return out


def _load_override() -> dict[str, list[str]]:
    if not OVERRIDE.exists():
        return {}
    return _parse_override(OVERRIDE.read_text(encoding="utf-8"))

# 随仓库提交的通用「题材→标的」常识映射（候选列表，供人工确认）
DEFAULT_CONFIRMED: dict[str, list[str]] = {
    "硅微粉": ["688300.SH"],       # 联瑞新材
    "冷液": ["301018.SZ"],         # 申菱环境
    "冷夜": ["301018.SZ"],         # 群主同音黑话
    "液冷": ["301018.SZ"],
    "铜箔": ["301559.SZ", "301596.SZ"],   # 铜冠铜箔 / 德福科技(待确认)
    "铜冠铜箔": ["301559.SZ"],
    "德福": ["301596.SZ"],
    "有研新材": ["600206.SH"],
    "有研硅": ["688432.SH"],
    "扬杰科技": ["300373.SZ"],
    "新雷能": ["300593.SZ"],
    "联瑞新材": ["688300.SH"],
    "申菱环境": ["301018.SZ"],
    "锐捷网络": ["301165.SZ"],
    "天岳先进": ["688234.SH"],
    "士兰微": ["600460.SH"],
    "泰晶科技": ["603738.SH"],
    "江海": ["002484.SZ"],
    "泰嘉科技": ["002843.SZ"],
    "电源": ["300593.SZ"],          # 新雷能（群主"电源"拉黑，具指待确认）
    "燕子家族": ["600206.SH", "688432.SH"],  # 有研新材 + 有研硅
    "特高压": [],                    # 待确认
    "折叠屏": [],                    # 待确认
}

# 6 位代码补后缀的规则
def _suffix(code: str) -> str:
    if code.startswith(("60", "68")):
        return code + ".SH"
    if code.startswith(("00", "30")):
        return code + ".SZ"
    if code.startswith(("8", "4", "9")):
        return code + ".BJ"
    return code


def load_map() -> dict[str, list[str]]:
    """合并 override + 默认映射，返回 alias -> [ts_code]。"""
    merged = {k: list(v) for k, v in DEFAULT_CONFIRMED.items()}
    for k, v in _load_override().items():
        merged[k] = v
    return merged


def resolve_alias(alias: str, amap: dict[str, list[str]] | None = None) -> list[str]:
    """把别名/题材/代码解析为候选 ts_code 列表。空列表 = 未确认。

    - 纯 6 位数字 → 按交易所补后缀。
    - 命中映射表 → 返回候选。
    - 其余（纯黑话未登记 / 中文名未在表内）→ 空（由 agent 联网反查后回写 override）。
    """
    amap = amap or load_map()
    alias = alias.strip()
    if re.fullmatch(r"\d{6}", alias):
        return [_suffix(alias)]
    if alias in amap:
        return amap[alias]
    return []


def know_themes(amap: dict[str, list[str]] | None = None) -> set[str]:
    amap = amap or load_map()
    return set(amap.keys())