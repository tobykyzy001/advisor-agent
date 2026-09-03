"""watchlist-manager 技能配套：观察仓清单的增删改查 + 其他工具 param 的透传写入。

用法（用系统 python，纯标准库，无需联网）：
  python manage_watchlist.py                          # list：列出全部观察标的
  python ... add 600519 --name 贵州茅台 --note 等回调 --source stock-valuation
  python ... set 600519 --BS B --BS_DATE 2025-06-03   # 任意 param 透传（其他工具委托写入）
  python ... rm 600519                                # 移出观察仓
  python ... check 600519                             # 幂等判断：退出码 0=在仓 1=不在
  python ... --watchlist output/watchlist/watchlist.yaml ...   # 覆盖默认清单路径
  python ... --now 2025-06-03 ...                     # 指定"当前日期"（测试/演示）

数据契约（硬约束，与 w-bottom-screener / momentum-rotation 的极简解析、以及量化核心
quantify.analysis.watchlist 的 PyYAML safe_dump 三方兼容）：
  - 每条以 `- ts_code: "xxx"` 作为第一个键；
  - 子字段 2 个空格缩进（与 PyYAML safe_dump 同风格，量化核心 save_watchlist 写出的
    清单直接合规）；读取宽容任意缩进，写入恒规范为 2 空格——跑一次 add/set 即可把
    旧 4 空格手编文件规范化；
  - param 键约定只用英文字母/数字/下划线（各工具写死自己的英文缩写，如 BS / MR；
    非 ASCII 键在 set 时直接拒绝防呆）；
  - 值自由，含中文/空格/特殊字符时序列化为双引号包裹。

字段分两层：
  - 固定字段：ts_code / name / market / note / added_at / source（add 写入）；
  - 工具 param：任意英文键（如 w-bottom 的 BS、momentum 的 MR），由各工具写死自己的
    键名、通过 set 子命令委托写入——本脚本不预设 param 名单、不解释值语义，纯透传。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# 默认路径：运行时观察仓在 output 下（已被 .gitignore 忽略，不入库）
DEFAULT_WATCHLIST = Path("output") / "watchlist" / "watchlist.yaml"

# 固定字段（顺序即序列化顺序）；其余键视为"工具 param"，按插入序排在固定字段之后
FIXED_KEYS = ["name", "market", "note", "added_at", "source"]
_ALL_FIXED = {"ts_code", *FIXED_KEYS}

# 清单缺失时新建所用的默认头（含格式约定说明）
DEFAULT_HEADER = [
    "# 观察仓清单：watchlist-manager 维护的标的池（本文件已 gitignore，不入库）。",
    "# w-bottom-screener / momentum-rotation 只读此清单；增删改用 watchlist-manager：",
    "#   python manage_watchlist.py add 600519 --name 贵州茅台 --note 等回调",
    "#   python manage_watchlist.py set 600519 --BS B        # 其他工具委托写 param",
    "# 格式硬约束（消费方极简解析）：每条以 - ts_code: 作首键、子字段 2 空格缩进、键只用英文。",
    "watchlist:",
]

# 条目首行：与消费方（w_bottom_screen.py / momentum_strategy.py）的正则一致
_ITEM_RE = re.compile(r"^\s*-\s+ts_code:\s*(.*?)\s*$")
# 子字段行：读取宽容（1 个以上空格即可），写入恒规范为 2 空格（PyYAML safe_dump 同风格）
_FIELD_RE = re.compile(r"^\s+(\w+):\s*(.*?)\s*$")
# 序列化时无需引号的简单值（纯 ASCII 字母数字与 . _ / @ + -）
_PLAIN_RE = re.compile(r"[A-Za-z0-9._/@+-]*")
# param 键名合法字符：约定只用 ASCII 字母/数字/下划线（各工具写死自己的英文缩写，
# 如 w-bottom 的 BS、momentum 的 MR；非 ASCII 键直接拒绝防呆，不悄悄写进去）
_KEY_RE = re.compile(r"[A-Za-z0-9_]+")

# set 子命令的保留键（全局参数名，防止被 REMAINDER 吞掉后误当 param）
_RESERVED_KEYS = {"watchlist", "now"}


def _unquote(v: str) -> str:
    """去掉值两端成对的单/双引号（与消费方的 strip 引号语义兼容）。"""
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


def normalize_code(raw: str) -> str:
    """把用户输入的代码规范化为 tushare 格式。

    规则：
      - 已带 .SH/.SZ/.BJ/.HK 后缀的校验后原样保留（港股补零到 5 位）；
      - 6 位数字按首位判交易所：6→.SH，0/3→.SZ，4/8/920→.BJ；
      - 1~5 位数字视为港股，补零到 5 位加 .HK。
    """
    s = raw.strip().upper()
    if s.endswith(".HK"):
        body = s[: -len(".HK")]
        if body.isdigit():
            return f"{body.zfill(5)}.HK"
        raise SystemExit(f"无法识别股票代码 {raw!r}：.HK 后缀前应为数字")
    if s.endswith((".SH", ".SZ", ".BJ")):
        body, _, _ = s.partition(".")
        if body.isdigit() and len(body) == 6:
            return s
        raise SystemExit(f"无法识别股票代码 {raw!r}：.SH/.SZ/.BJ 后缀前应为 6 位数字")
    if s.isdigit():
        if len(s) == 6:
            if s[0] == "6":
                return s + ".SH"
            if s[0] in ("0", "3"):
                return s + ".SZ"
            if s[:3] == "920" or s[0] in ("4", "8"):
                return s + ".BJ"
            raise SystemExit(
                f"无法识别 6 位代码 {raw!r} 的交易所（9 开头疑似 B 股，本仓库只覆盖 A股/港股，"
                f"请显式给 .SH/.SZ/.BJ 后缀）"
            )
        if 1 <= len(s) <= 5:
            return s.zfill(5) + ".HK"
    raise SystemExit(
        f"无法识别股票代码 {raw!r}：A股给 6 位数字或带 .SH/.SZ/.BJ 后缀，"
        f"港股给 5 位数字（如 00700）或 .HK 后缀"
    )


def infer_market(ts_code: str) -> str:
    """按规范化后的代码推断市场：.HK → HK，其余 → A。"""
    return "HK" if ts_code.endswith(".HK") else "A"


def parse_text(text: str) -> tuple[list[str], list[dict]]:
    """解析清单文本：返回 (头注释行列表[含 watchlist: 行], 条目列表)。

    读取宽容（子字段任意缩进都能读出），写入恒规范为 2 空格（PyYAML safe_dump 同
    风格，量化核心 save_watchlist 直接合规）——跑一次 add/set 即可把旧 4 空格手编文件规范化。
    """
    lines = text.splitlines()
    header: list[str] = []
    items: list[dict] = []
    cur: dict | None = None
    seen_root = False
    for ln in lines:
        if not seen_root:
            header.append(ln)
            if ln.strip().startswith("watchlist:"):
                seen_root = True
            continue
        m = _ITEM_RE.match(ln)
        if m:
            if cur is not None:
                items.append(cur)
            cur = {"ts_code": _unquote(m.group(1))}
            continue
        fm = _FIELD_RE.match(ln)
        if fm and cur is not None:
            cur[fm.group(1)] = _unquote(fm.group(2))
            continue
        # 其余行（条目内注释、空行等）忽略
    if cur is not None:
        items.append(cur)
    if not seen_root:
        raise SystemExit(
            "清单缺少顶层 watchlist: 键，格式不符：请检查手改内容，"
            "或运行 workspace-init 技能重新生成模板"
        )
    return header, items


def _serialize_items(items: list[dict]) -> list[str]:
    """把条目序列化为消费方兼容的行：首键 ts_code、子字段恒 2 空格缩进（PyYAML 风格）。"""
    lines: list[str] = []
    for it in items:
        lines.append(f'- ts_code: "{it.get("ts_code", "")}"')
        for k in FIXED_KEYS:
            v = it.get(k)
            if v is None or v == "":
                continue
            lines.append(f"  {k}: {_fmt_value(v)}")
        for k, v in it.items():
            if k in _ALL_FIXED or v is None or v == "":
                continue
            lines.append(f"  {k}: {_fmt_value(v)}")
    return lines


def load(path: Path, create: bool) -> tuple[list[str], list[dict]]:
    """读取清单；不存在时 create=True 返回默认骨架（供 add 自动建文件），否则报错引导。"""
    if path.exists():
        return parse_text(path.read_text(encoding="utf-8"))
    if not create:
        raise SystemExit(
            f"观察仓清单不存在：{path}。请先运行 workspace-init 技能初始化工作区"
            f"（`python src/workspace-init/init_workspace.py`），或直接用 add 子命令加入标的（自动创建）。"
        )
    return DEFAULT_HEADER[:], []


def save(path: Path, header: list[str], items: list[dict]) -> None:
    """回写清单：保留头注释，条目按契约格式规范化输出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(header + _serialize_items(items)).rstrip("\n") + "\n"
    path.write_text(body, encoding="utf-8")


def _find(items: list[dict], code: str) -> dict | None:
    for it in items:
        if it.get("ts_code") == code:
            return it
    return None


def parse_kv_pairs(tokens: list[str]) -> dict[str, str]:
    """把 set 命令 code 之后的 `--key value` / `--key=value` 成对解析为 dict（纯透传）。"""
    pairs: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not t.startswith("--") or len(t) <= 2:
            raise SystemExit(f"set 的参数需成对 --key value（或 --key=value）：无法解析 {t!r}")
        body = t[2:]
        if "=" in body:
            key, _, val = body.partition("=")
            i += 1
        else:
            if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                raise SystemExit(f"参数 --{body} 缺少对应的值")
            key, val = body, tokens[i + 1]
            i += 2
        if not _KEY_RE.fullmatch(key):
            raise SystemExit(
                f"param 键 {key!r} 只能用英文字母/数字/下划线"
                f"（消费方极简解析认不出中文键，会被静默忽略）"
            )
        if key in _RESERVED_KEYS:
            raise SystemExit(f"--{key} 是全局参数，请放在子命令之前")
        pairs[key] = val
    if not pairs:
        raise SystemExit("set 至少需要一对 --key value 参数")
    return pairs


def cmd_list(args: argparse.Namespace) -> int:
    _, items = load(args.watchlist, create=False)
    if not items:
        print(f"观察仓为空（清单：{args.watchlist}）。用 add 子命令加入标的，或先运行 workspace-init 初始化。")
        return 0
    print(f"观察仓共 {len(items)} 只（清单：{args.watchlist}）：")
    for i, it in enumerate(items, 1):
        parts = [f"{i}. {it.get('ts_code', '')}", it.get("name") or "-", f"[{it.get('market') or 'A'}]"]
        if it.get("added_at"):
            parts.append(f"加入:{it['added_at']}")
        if it.get("source"):
            parts.append(f"来源:{it['source']}")
        if it.get("note"):
            parts.append(f"note={it['note']}")
        extras = [
            f"{k}={v}" for k, v in it.items() if k not in _ALL_FIXED and v not in (None, "")
        ]
        if extras:
            parts.append(" ".join(extras))
        print("  " + "  ".join(parts))
    return 0


def _local_today() -> str:
    """当前本地日期（ISO 格式）。显式带时区再转本地，等价 date.today() 且不触发 naive-datetime 检查。"""
    return dt.datetime.now(dt.UTC).astimezone().date().isoformat()


def cmd_add(args: argparse.Namespace) -> int:
    code = normalize_code(args.code)
    header, items = load(args.watchlist, create=True)
    today = args.now or _local_today()
    existing = _find(items, code)
    if existing is not None:
        changed = []
        if args.name and existing.get("name") != args.name:
            existing["name"] = args.name
            changed.append("name")
        if args.note is not None:
            existing["note"] = args.note
            changed.append("note")
        if args.source:
            existing["source"] = args.source
            changed.append("source")
        save(args.watchlist, header, items)
        what = "、".join(changed) if changed else "无字段变化"
        print(f"{code} 已在观察仓（{existing.get('name') or '未命名'}），已更新：{what}")
        return 0
    item: dict = {"ts_code": code}
    if args.name:
        item["name"] = args.name
    item["market"] = args.market or infer_market(code)
    if args.note is not None:
        item["note"] = args.note
    item["added_at"] = today
    if args.source:
        item["source"] = args.source
    items.append(item)
    save(args.watchlist, header, items)
    print(f"已加入观察仓：{code} {args.name or ''}".rstrip())
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    code = normalize_code(args.code)
    pairs = parse_kv_pairs(args.rest)
    header, items = load(args.watchlist, create=False)
    existing = _find(items, code)
    if existing is None:
        raise SystemExit(f"{code} 不在观察仓，set 只更新已有条目：请先 add {code}")
    existing.update(pairs)
    save(args.watchlist, header, items)
    print(f"{code} 已更新 param：" + "、".join(f"{k}={v}" for k, v in pairs.items()))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    code = normalize_code(args.code)
    header, items = load(args.watchlist, create=False)
    existing = _find(items, code)
    if existing is None:
        print(f"{code} 不在观察仓，无需移出。")
        return 1
    items = [it for it in items if it is not existing]
    save(args.watchlist, header, items)
    print(f"已移出观察仓：{code} {existing.get('name') or ''}".rstrip())
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    code = normalize_code(args.code)
    try:
        _, items = load(args.watchlist, create=False)
    except SystemExit:
        # 清单缺失视为"不在仓"（幂等判断语义：不在 → 退出码 1）
        print(f"不在观察仓（清单缺失：{args.watchlist}）")
        return 1
    if _find(items, code) is not None:
        print(f"在观察仓：{code}")
        return 0
    print(f"不在观察仓：{code}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    # 全局参数只定义在主 parser（须放在子命令之前，与 manage_holdings.py 同约定）：
    # 不用 parents 复制到子命令——Python 3.13+ 的子 parser 会用自身 default 覆盖
    # 主 namespace 已解析的同名参数，导致"子命令前给的 --watchlist 被静默丢弃"。
    p = argparse.ArgumentParser(
        prog="manage_watchlist.py",
        description="观察仓清单管理：list / add / set / rm / check（watchlist-manager 技能）",
    )
    p.add_argument(
        "--watchlist",
        type=Path,
        default=DEFAULT_WATCHLIST,
        help="观察仓清单路径（默认 output/watchlist/watchlist.yaml；须放在子命令之前）",
    )
    p.add_argument(
        "--now", default=None, help="指定当前日期 YYYY-MM-DD（测试/演示，用于 added_at；须放在子命令之前）"
    )
    sub = p.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="加入观察仓（已存在则更新 name/note/source）")
    p_add.add_argument("code", help="股票代码：600519 / 600519.SH / 00700 / 00700.HK")
    p_add.add_argument("--name", default=None, help="名称（便于报告展示）")
    p_add.add_argument("--note", default=None, help="跟踪理由/备注（如：等回调到 1500）")
    p_add.add_argument("--source", default=None, help="来源技能（如 stock-valuation / copy-trade / 手动）")
    p_add.add_argument("--market", default=None, help="市场 A|HK（默认按代码推断）")

    p_set = sub.add_parser(
        "set", help="透传写入任意 param（其他工具委托调用，如 --BS B）"
    )
    p_set.add_argument("code", help="股票代码")
    p_set.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        help="--key value 成对参数（可多对，也支持 --key=value）；只写命中的键，不动其他字段",
    )

    p_rm = sub.add_parser("rm", help="移出观察仓")
    p_rm.add_argument("code", help="股票代码")

    p_chk = sub.add_parser(
        "check", help="是否在观察仓（退出码 0=在 1=不在，供其他技能幂等判断）"
    )
    p_chk.add_argument("code", help="股票代码")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "set":
        return cmd_set(args)
    if args.cmd == "rm":
        return cmd_rm(args)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
