"""daily-update 技能配套：按周期判断知识资产是否到期更新，并回写上次更新时间。

用法（示例）：
  python .agents/skills/daily-update/scripts/check_updates.py
      # 用系统当前时间查看哪些资产到期
  python .agents/skills/daily-update/scripts/check_updates.py --now 2026-09-01
      # 指定"当前时间"查看到期结果（测试/演示）
  python .agents/skills/daily-update/scripts/check_updates.py --mark prosperity-sectors
      # 标记该资产本次已更新（按 --now 或系统当前时间归入当前周期并回写）
  python ... --manifest <seed> --state <state>
      # 覆盖默认的模板/状态文件路径
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

# 默认路径：模板随技能提交，状态在 output 下（已被 .gitignore 忽略）
SCRIPT_DIR = Path(__file__).resolve().parent
SEED = SCRIPT_DIR.parent / "references" / "manifest.yaml"
STATE = Path("output") / "skill-state" / "update-manifest.yaml"

CADENCES = {"daily", "weekly", "monthly", "quarterly", "yearly"}


def current_period(cadence: str, now: dt.date) -> str:
    """把日期归入其所在周期，例如 quarterly -> '2026-Q3'。"""
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


def load(seed: Path, state: Path) -> dict:
    if state.exists():
        return yaml.safe_load(state.read_text(encoding="utf-8"))
    data = yaml.safe_load(seed.read_text(encoding="utf-8")) or {"assets": []}
    for a in data.setdefault("assets", []):
        if a.get("cadence") not in CADENCES:
            raise ValueError(f"资产 {a.get('id')} cadence 非法: {a.get('cadence')}")
        a.setdefault("last_update", None)
    save(data, state)
    return data


def save(data: dict, state: Path) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="判断知识资产是否到期更新并回写更新时间")
    ap.add_argument("--now", help="指定当前时间(ISO YYYY-MM-DD)，默认系统今天；测试用")
    ap.add_argument("--mark", help="标记该资产本次已更新(按 now/今天归入当前周期回写)")
    ap.add_argument("--seed", default=str(SEED))
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args()

    now = dt.date.fromisoformat(args.now) if args.now else dt.date.today()
    data = load(Path(args.seed), Path(args.state))

    if args.mark:
        by_id = {a["id"]: a for a in data["assets"]}
        if args.mark not in by_id:
            print(f"未知资产: {args.mark}", file=sys.stderr)
            return 2
        p = current_period(by_id[args.mark]["cadence"], now)
        by_id[args.mark]["last_update"] = p
        save(data, Path(args.state))
        print(f"已标记 {args.mark} 上次更新为 {p} ({args.state})")
        return 0

    print(f"now = {now.isoformat()} "
          f"({current_period('quarterly', now)})  "
          f"种子: {args.seed}  状态: {args.state}")
    for a in data["assets"]:
        cur = current_period(a["cadence"], now)
        last = a.get("last_update")
        due = last is None or last != cur
        flag = "DUE(需更新)" if due else "ok(周期内无需更新)"
        print(f"[{flag}] {a['id']:<20} cadence={a['cadence']:<8} "
              f"last_update={last!s:<10} now={cur}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
