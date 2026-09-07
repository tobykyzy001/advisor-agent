"""prosperity-picking 技能配套：景气板块跟踪状态的增删改查。

维护「当前跟踪哪些景气板块」的运行时状态（output/prosperity/state.yaml），供
「景气板块选股」两段式流水线使用：筛选/复核景气板块（screen）与选出景气标的（pick）。
板块的景气判断（研究、联网取证、给出原因）由 agent 完成，本脚本只负责状态落盘，
保证格式一致、幂等可重复；选股明细与复核报告由 agent 写同目录 report_*.md。

用法（用系统 python，纯标准库，无需联网）：
  python prosperity_state.py                       # show：当前跟踪的景气板块 + 剔除历史
  python ... add <板块id> --name AI算力 --reason "盈利上修+订单饱满"
  python ... review <板块id> --note "景气点：…；风险点：…"
  python ... rm <板块id> --reason "预期密集下修，景气见顶"
  python ... --state output/prosperity/state.yaml ...   # 覆盖默认状态文件路径
  python ... --now 2025-06-03 ...                       # 指定"当前日期"（测试/演示）

数据契约：
  - 板块 id 只用小写字母/数字/连字符（如 ai-compute、robotics）——它是观察仓
    watchlist param `PS` 的值（manage_watchlist.py set <code> --PS <板块id>），
    需保持 ASCII 短代号，便于按板块快速筛选删除观察仓标的；
  - 条目按固定字段顺序序列化（sectors: id/name/added_at/reason/last_review/note，
    history 再加 removed_at/remove_reason），子字段 2 空格缩进，与 PyYAML
    safe_dump 同风格；读取宽容任意缩进；
  - sectors 是"当前跟踪"，history 是"已剔除留痕"——rm 把条目整体移入 history
    并记录 removed_at 与剔除原因；同一板块剔除后可再次 add（景气重新上行）。

退出码：0=成功；1=业务错误（板块 id 重复/不存在、id 非法、状态文件缺失等，见报错文案）。
输出仅供研究参考，不构成投资建议。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# 默认路径：景气投资跟踪状态在 output 下（已被 .gitignore 忽略，不入库）
DEFAULT_STATE = Path("output") / "prosperity" / "state.yaml"

# sectors 条目的固定字段（顺序即序列化顺序）
SECTOR_KEYS = ["id", "name", "added_at", "reason", "last_review", "note"]
# history 条目在 sectors 字段之上多出的字段（剔除留痕）
HISTORY_EXTRA_KEYS = ["removed_at", "remove_reason"]
HISTORY_KEYS = ["id", "name", "added_at", "removed_at", "reason", "remove_reason",
                "last_review", "note"]

# 板块 id：小写字母/数字/连字符，1-24 位（作为观察仓 PS param 的值，必须 ASCII 短代号）
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
# 序列化时无需引号的简单值（纯 ASCII 字母数字与 . _ / @ + -）
_PLAIN_RE = re.compile(r"[A-Za-z0-9._/@+-]*")
# 章节行（顶层键）：sectors: / history:
_SECTION_RE = re.compile(r"^(\w+):\s*$")
# 条目首行：与 manage_watchlist.py 的条目正则同风格（读取宽容任意缩进）
_ITEM_RE = re.compile(r"^\s*-\s+(\w+):\s*(.*?)\s*$")
# 子字段行：读取宽容（1 个以上空格即可）
_FIELD_RE = re.compile(r"^\s+(\w+):\s*(.*?)\s*$")

# 清单缺失时新建所用的默认头（含格式约定说明）
DEFAULT_HEADER = [
    "# 景气投资跟踪状态：prosperity-picking 的运行时数据（本文件已 gitignore，不入库）。",
    "# 唯一写入口：prosperity_state.py（show/add/review/rm），请勿手改；",
    "# 复核/选股报告见同目录 report_*.md。sectors=当前跟踪板块；history=已剔除留痕。",
]

#: show 输出中「无在跟板块」的判定短语（面板/指令据此走初始化或拒绝分支）
EMPTY_MARK = "当前没有跟踪中的景气板块"


def _unquote(v: str) -> str:
    """去掉值两端成对的单/双引号。"""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _fmt_value(v) -> str:
    """把值序列化为 YAML 标量：简单 ASCII 值不加引号，其余用双引号包裹。"""
    s = str(v).strip()
    if s == "":
        return '""'
    if _PLAIN_RE.fullmatch(s):
        return s
    return '"' + s.replace('"', "'") + '"'


def validate_id(raw: str) -> str:
    """校验板块 id：小写字母/数字/连字符（它是观察仓 PS param 的值，须 ASCII 短代号）。"""
    s = raw.strip()
    if _ID_RE.fullmatch(s):
        return s
    raise SystemExit(
        f"板块 id 非法：{raw!r}。id 只能用小写字母/数字/连字符（1-24 位，如 ai-compute、"
        f"robotics）——它是观察仓 PS param 的值，需保持 ASCII 短代号，便于按板块快速筛选删除"
    )


def parse_text(text: str) -> tuple[list[str], dict[str, list[dict]]]:
    """解析状态文本：返回 (头注释行列表, {'sectors': [...], 'history': [...]})。

    读取宽容（条目与子字段任意缩进都能读出），写入恒规范为「条目顶格 + 子字段 2 空格」
    （PyYAML safe_dump 同风格）。未出现过的章节视为空。
    """
    header: list[str] = []
    sections: dict[str, list[dict]] = {"sectors": [], "history": []}
    cur_section: str | None = None
    cur: dict | None = None
    for ln in text.splitlines():
        # 章节切换（sectors: / history: 顶层键，任意阶段都识别；切走前先把当前条目收尾）
        m = _SECTION_RE.match(ln)
        if m and m.group(1) in sections:
            if cur is not None and cur_section is not None:
                sections[cur_section].append(cur)
                cur = None
            cur_section = m.group(1)
            continue
        if cur_section is None:
            header.append(ln)  # 首个章节出现前的行全部视为头注释
            continue
        m = _ITEM_RE.match(ln)
        if m:
            if cur is not None:
                sections[cur_section].append(cur)
            cur = {m.group(1): _unquote(m.group(2))}
            continue
        fm = _FIELD_RE.match(ln)
        if fm and cur is not None:
            cur[fm.group(1)] = _unquote(fm.group(2))
            continue
        # 其余行（条目内注释、空行等）忽略
    if cur is not None and cur_section is not None:
        sections[cur_section].append(cur)
    return header, sections


def _serialize_items(items: list[dict], keys: list[str]) -> list[str]:
    """把条目序列化为规范行：首键固定、子字段恒 2 空格缩进（PyYAML 风格）。"""
    lines: list[str] = []
    for it in items:
        first, *rest = keys
        lines.append(f"- {first}: {_fmt_value(it.get(first, ''))}")
        for k in rest:
            v = it.get(k)
            if v is None or v == "":
                continue
            lines.append(f"  {k}: {_fmt_value(v)}")
    return lines


def load(path: Path, create: bool) -> tuple[list[str], dict[str, list[dict]]]:
    """读取状态；不存在时 create=True 返回默认骨架（供 add 自动建文件），否则报错引导。"""
    if path.exists():
        return parse_text(path.read_text(encoding="utf-8"))
    if not create:
        raise SystemExit(
            f"景气跟踪状态不存在：{path}。请先用 add 子命令登记景气板块"
            f"（「筛选景气板块」流程会自动完成初始化）"
        )
    return DEFAULT_HEADER[:], {"sectors": [], "history": []}


def save(path: Path, header: list[str], sections: dict[str, list[dict]]) -> None:
    """回写状态：保留头注释，sectors/history 两章节按契约格式规范化输出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = ["\n".join(header).rstrip("\n")]
    for name, keys in (("sectors", SECTOR_KEYS), ("history", HISTORY_KEYS)):
        lines = _serialize_items(sections[name], keys)
        chunks.append(f"{name}:\n" + "\n".join(lines) if lines else f"{name}:")
    body = "\n".join(c for c in chunks if c.strip()) + "\n"
    path.write_text(body, encoding="utf-8")


def _find(items: list[dict], sid: str) -> dict | None:
    for it in items:
        if it.get("id") == sid:
            return it
    return None


def _local_today() -> str:
    """当前本地日期（ISO 格式）。显式带时区再转本地，等价 date.today() 且不触发 naive-datetime 检查。"""
    return dt.datetime.now(dt.UTC).astimezone().date().isoformat()


def cmd_show(args: argparse.Namespace) -> int:
    try:
        _, sections = load(args.state, create=False)
    except SystemExit:
        print(f"{EMPTY_MARK}（状态文件不存在：{args.state}）。")
        print("可先运行「景气板块选股」的「筛选景气板块」动作完成初始化。")
        return 0
    sectors = [it for it in sections["sectors"] if it.get("id")]
    history = [it for it in sections["history"] if it.get("id")]
    print(f"景气投资跟踪状态（{args.state}）")
    if not sectors:
        print(f"{EMPTY_MARK}。可先运行「筛选景气板块」初始化。")
    else:
        print(f"当前跟踪景气板块 {len(sectors)} 个：")
        for i, it in enumerate(sectors, 1):
            parts = [f"{i}. {it['id']}", it.get("name") or "-"]
            if it.get("added_at"):
                parts.append(f"加入:{it['added_at']}")
            if it.get("last_review"):
                parts.append(f"最近复核:{it['last_review']}")
            print("  " + "  ".join(parts))
            if it.get("reason"):
                print(f"     入选原因: {it['reason']}")
            if it.get("note"):
                print(f"     复核结论: {it['note']}")
    if history:
        print(f"已剔除板块 {len(history)} 个（留痕）：")
        for it in history[-5:]:
            parts = [f"- {it['id']}", it.get("name") or "-"]
            if it.get("removed_at"):
                parts.append(f"剔除:{it['removed_at']}")
            if it.get("remove_reason"):
                parts.append(f"原因:{it['remove_reason']}")
            print("  " + "  ".join(parts))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    sid = validate_id(args.id)
    header, sections = load(args.state, create=True)
    if _find(sections["sectors"], sid) is not None:
        raise SystemExit(f"板块 {sid} 已在跟踪中：如需更新结论请用 review，如需剔除请用 rm")
    today = args.now or _local_today()
    sections["sectors"].append(
        {
            "id": sid,
            "name": args.name,
            "added_at": today,
            "reason": args.reason,
            "last_review": today,
            "note": "",
        }
    )
    save(args.state, header, sections)
    n = len(sections["sectors"])
    print(f"已登记景气板块：{sid} {args.name}（加入日期 {today}）；当前共 {n} 个跟踪中")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    sid = validate_id(args.id)
    header, sections = load(args.state, create=False)
    existing = _find(sections["sectors"], sid)
    if existing is None:
        raise SystemExit(f"板块 {sid} 不在跟踪中：只能复核在跟板块（现有：{_ids(sections)}）")
    today = args.now or _local_today()
    existing["last_review"] = today
    existing["note"] = args.note
    save(args.state, header, sections)
    print(f"已回写复核结论：{sid} {existing.get('name') or ''}（{today}）".rstrip())
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    sid = validate_id(args.id)
    header, sections = load(args.state, create=False)
    existing = _find(sections["sectors"], sid)
    if existing is None:
        raise SystemExit(f"板块 {sid} 不在跟踪中（现有：{_ids(sections)}），无需剔除")
    today = args.now or _local_today()
    sections["sectors"] = [it for it in sections["sectors"] if it is not existing]
    record = dict(existing)
    record["removed_at"] = today
    record["remove_reason"] = args.reason
    sections["history"].append(record)
    save(args.state, header, sections)
    n = len(sections["sectors"])
    print(f"已剔除景气板块：{sid} {existing.get('name') or ''}（{today}，原因：{args.reason}）；"
          f"当前共 {n} 个跟踪中".rstrip())
    return 0


def _ids(sections: dict[str, list[dict]]) -> str:
    ids = [it.get("id", "") for it in sections["sectors"]]
    return "、".join(ids) if ids else "无"


def build_parser() -> argparse.ArgumentParser:
    # 全局参数只定义在主 parser（须放在子命令之前，与 manage_watchlist.py 同约定）。
    p = argparse.ArgumentParser(
        prog="prosperity_state.py",
        description="景气板块跟踪状态管理：show / add / review / rm（prosperity-picking 技能）",
    )
    p.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help="状态文件路径（默认 output/prosperity/state.yaml；须放在子命令之前）",
    )
    p.add_argument(
        "--now", default=None, help="指定当前日期 YYYY-MM-DD（测试/演示；须放在子命令之前）"
    )
    sub = p.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="登记景气板块（在跟板块重复 id 会报错）")
    p_add.add_argument("id", help="板块 id：小写字母/数字/连字符（观察仓 PS param 的值，如 ai-compute）")
    p_add.add_argument("--name", required=True, help="板块名称（如 AI算力）")
    p_add.add_argument("--reason", required=True, help="入选原因（景气证据，含数据时点）")

    p_rev = sub.add_parser("review", help="回写板块复核结论（景气点/风险点）")
    p_rev.add_argument("id", help="板块 id")
    p_rev.add_argument("--note", required=True, help="复核结论一句话（景气点/风险点/判断）")

    p_rm = sub.add_parser("rm", help="剔除景气板块（移入 history 留痕）")
    p_rm.add_argument("id", help="板块 id")
    p_rm.add_argument("--reason", required=True, help="剔除原因（不再景气的证据）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "review":
        return cmd_review(args)
    if args.cmd == "rm":
        return cmd_rm(args)
    return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
