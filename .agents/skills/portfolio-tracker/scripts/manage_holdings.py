"""portfolio-tracker 技能配套：持仓清单的增删改查 + 按周期判断是否到期复核。

用法（用系统 python，仅依赖标准库 + PyYAML，无需联网）：
  python .agents/skills/portfolio-tracker/scripts/manage_holdings.py
      # 列出全部持仓与到期状态
  python ... add 600519 --name 贵州茅台 --type 投资 --cadence quarterly
  python ... add 600519 --name 贵州茅台 --init 估值方式=DCF+股息率 --init 估值价格=1500
  python ... set 600519 现价=1450.5 结论=低估
  python ... mark 600519                    # 本轮复核完成，回写 last_update
  python ... rm 600519                        # 删除持仓
  python ... --now 2026-09-01                 # 指定"当前时间"（测试/演示）
  python ... --seed <模板> --state <状态>      # 覆盖默认路径
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

# 默认路径：模板随技能提交，运行时持仓在 output 下（已被 .gitignore 忽略）
SCRIPT_DIR = Path(__file__).resolve().parent
SEED = SCRIPT_DIR.parent / "references" / "holdings-template.yaml"
STATE = Path("output") / "portfolio" / "holdings.yaml"

CADENCES = {"daily", "weekly", "monthly", "quarterly", "yearly"}
TYPES = {"投资", "投机"}

# 每条持仓的默认字段
FIELD_DEFAULTS = {
    "name": None,
    "投资类型": None,
    "估值方式": None,
    "估值价格": None,
    "现价": None,
    "结论": None,
    "更新时点": None,
    "cadence": "monthly",
    "last_update": None,
    "建仓价": None,
    "数量": None,
    "备注": None,
}


def current_period(cadence: str, now: dt.date) -> str:
    """把日期归入所在周期，如 quarterly -> '2026-Q3'。与 daily-update 同规则。"""
    if cadence == "quarterly":
        return f"{now.year}-Q{(now.month - 1) // 3 + 1}"
    if cadence == "daily":
        return now.strftime("%Y-%m-%d")
    if cadence == "weekly":
        return f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    if cadence == "monthly":
        return now.strftime("%Y-%m")
    if cadence == "yearly":
        return str(now.year)
    raise ValueError(f"未知 cadence: {cadence}")


def _coerce(v: str):
    """把 key=值 的字符串值按 int/float/str 尽力去格式化。"""
    v = v.strip()
    if v == "":
        return None
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            continue
    return v


def load(seed: Path, state: Path) -> dict:
    """读取运行时持仓，不存在则从模板生成。"""
    if state.exists():
        data = yaml.safe_load(state.read_text(encoding="utf-8")) or {}
    else:
        data = yaml.safe_load(seed.read_text(encoding="utf-8")) or {}
    data.setdefault("holdings", [])
    for h in data["holdings"]:
        h.setdefault("code", None)
        for k, v in FIELD_DEFAULTS.items():
            h.setdefault(k, v)
        if h.get("cadence") not in CADENCES:
            raise ValueError(f"持仓 {h.get('code')} cadence 非法: {h.get('cadence')}")
    return data


def save(data: dict, state: Path) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _find(data: dict, code: str):
    for i, h in enumerate(data["holdings"]):
        if str(h.get("code")) == code:
            return h, i
    return None, None


def _parse_kv(pairs: list[str]) -> dict:
    out: dict = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"字段须为 key=value，收到: {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = _coerce(v)
    return out


def cmd_list(data: dict, now: dt.date) -> None:
    print(f"now = {now.isoformat()} ({current_period('quarterly', now)})  "
          f"持仓 {len(data['holdings'])} 条")
    if not data["holdings"]:
        print("  (空) 用 add 子命令新增持仓")
        return
    for h in data["holdings"]:
        cur = current_period(h["cadence"], now)
        last = h.get("last_update")
        due = last is None or last != cur
        flag = "DUE(该复核)" if due else "ok(周期内)"
        price = h.get("现价")
        target = h.get("估值价格")
        gap = ""
        if price is not None and target is not None:
            try:
                gap = f"  现价{price}/估值{target}"
            except TypeError:
                gap = ""
        print(f"[{flag:<11}] {h['code']} {h['name'] or ''}  类型={h['投资类型'] or '?'}  "
              f"cadence={h['cadence']:<10} last_update={last}{gap}")
        if h.get("结论"):
            print(f"              结论: {h['结论']}")


def cmd_add(data: dict, args, state: Path) -> None:
    code = args.code
    h, _ = _find(data, code)
    if h is not None:
        print(f"持仓 {code} 已存在，用 set 更新", file=sys.stderr)
        raise SystemExit(1)
    fields = _parse_kv(args.init)
    if args.type is not None:
        if args.type not in TYPES:
            print(f"投资类型须为 {TYPES}，收到 {args.type!r}", file=sys.stderr)
            raise SystemExit(2)
        fields["投资类型"] = args.type
    if args.name is not None:
        fields["name"] = args.name
    if args.cadence is not None:
        fields["cadence"] = args.cadence

    entry = {"code": code}
    for k, v in FIELD_DEFAULTS.items():
        entry[k] = fields.get(k, v)
    if entry["cadence"] not in CADENCES:
        print(f"cadence 非法: {entry['cadence']}", file=sys.stderr)
        raise SystemExit(2)
    data["holdings"].append(entry)
    save(data, state)
    print(f"已新增持仓 {code} {entry['name'] or ''} (共 {len(data['holdings'])} 条)")


def cmd_set(data: dict, args, state: Path) -> None:
    h, _ = _find(data, args.code)
    if h is None:
        print(f"持仓 {args.code} 不存在，先 add", file=sys.stderr)
        raise SystemExit(1)
    fields = _parse_kv(args.fields)
    if "code" in fields:
        del fields["code"]  # code 不可改
    for k, v in fields.items():
        if k == "cadence" and v not in CADENCES:
            print(f"cadence 非法: {v}", file=sys.stderr)
            raise SystemExit(2)
        h[k] = v
    save(data, state)
    print(f"已更新 {args.code}: {', '.join(f'{k}={v}' for k, v in fields.items())}")


def cmd_rm(args, seed: Path, state: Path) -> None:
    data = load(seed, state)
    h, i = _find(data, args.code)
    if h is None:
        print(f"持仓 {args.code} 不存在", file=sys.stderr)
        raise SystemExit(1)
    data["holdings"].pop(i)
    save(data, state)
    print(f"已删除 {args.code} (剩余 {len(data['holdings'])} 条)")


def cmd_mark(args, state: Path) -> None:
    data = load(Path(args.seed), state)
    h, _ = _find(data, args.code)
    if h is None:
        print(f"持仓 {args.code} 不存在", file=sys.stderr)
        raise SystemExit(1)
    now = dt.date.fromisoformat(args.now) if args.now else dt.date.today()
    p = current_period(h["cadence"], now)
    h["last_update"] = p
    h["更新时点"] = now.isoformat()
    save(data, state)
    print(f"已标记 {args.code} 复核完成: 周期={p}，更新时点={now.isoformat()}")


def main() -> int:
    ap = argparse.ArgumentParser(description="持仓清单增删改查 + 周期复核")
    ap.add_argument("--now", help="指定当前时间(YYYY-MM-DD)，测试用")
    ap.add_argument("--seed", default=str(SEED))
    ap.add_argument("--state", default=str(STATE))
    sub = ap.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="新增持仓")
    p_add.add_argument("code")
    p_add.add_argument("--name")
    p_add.add_argument("--type", help="投资 | 投机")
    p_add.add_argument("--cadence")
    p_add.add_argument("--init", nargs="*", default=[], help="其它字段 key=值 可重复")

    p_set = sub.add_parser("set", help="更新持仓字段")
    p_set.add_argument("code")
    p_set.add_argument("fields", nargs="+", help="key=值 可多个")

    p_rm = sub.add_parser("rm", help="删除持仓")
    p_rm.add_argument("code")

    p_mark = sub.add_parser("mark", help="标记本轮复核完成")
    p_mark.add_argument("code")

    args = ap.parse_args()
    seed, state = Path(args.seed), Path(args.state)

    if args.command == "add":
        data = load(seed, state)
        cmd_add(data, args, state)
        return 0
    if args.command == "set":
        data = load(seed, state)
        cmd_set(data, args, state)
        return 0
    if args.command == "rm":
        cmd_rm(args, seed, state)
        return 0
    if args.command == "mark":
        cmd_mark(args, state)
        return 0

    # 默认：列出持仓与到期状态
    data = load(seed, state)
    now = dt.date.fromisoformat(args.now) if args.now else dt.date.today()
    cmd_list(data, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())