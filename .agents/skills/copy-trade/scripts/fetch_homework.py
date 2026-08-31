#!/usr/bin/env python3
"""抄作业技能配套：抓取并解析「群作业」链接，产出结构化消息流。

用法（用系统 python，仅依赖标准库，无需联网依赖）：
  python .agents/skills/copy-trade/scripts/fetch_homework.py "<链接>"
  python .agents/skills/copy-trade/scripts/fetch_homework.py --html <本地html路径>
  python ... --out output/copy-trade/<作者>.json   # 覆盖输出路径（默认自动推导）

说明：
- 直连（urllib 显式 ProxyHandler({}) 关闭系统代理，绕过本机被墙/损坏的代理），
  原因同 stock-valuation 的 fetch_snapshot.py。
- 把 HTML 解析成 [时间戳, 发言人, 是否群主, 正文, 是否撤回] 列表；清洗图片标签、
  HTML 实体、"回复@xxx"、"撤回"等噪音。
- 输出 JSON 到 output/copy-trade/（已被 .gitignore 忽略）；同时在 stdout 打印
  精简预览供 agent 直接读取。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path("output") / "copy-trade"

# 消息开头的时间戳："2026-08-31 15:01:29 【..."
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")
# 发言人：时间后紧跟的 【 ... 】 里第一个非标签文字即昵称
SPEAKER_RE = re.compile(r"【\s*([^】<]+?)】")
# 群主标记
GROUP_OWNER_RE = re.compile(r"【群主】")
# 撤回标记
RECALL_RE = re.compile(r"撤回了一条消息")
# 回复噪音：" ---------- 回复@xxx："
REPLY_RE = re.compile(r"----------\s*回复@[^：:]*[：:]\s*")


def _open_http(url: str, timeout: int = 20) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return resp.read()


def _strip_tags(html: str) -> str:
    # 去 <...> 标签（保留正文文字）
    txt = re.sub(r"(?s)<[^>]+>", " ", html)
    # 常见 HTML 实体
    txt = (txt.replace("&nbsp;", " ").replace("&gt;", ">")
           .replace("&lt;", "<").replace("&amp;", "&").replace("&quot;", '"'))
    return txt


def _clean_body(body: str) -> str:
    body = REPLY_RE.sub("", body)
    body = re.sub(r"!\s*\[[^\]]*\]\s*\(\s*\)", " ", body)  # 空图片 "![](...)" 残渣
    body = re.sub(r"!\s*\[\s*\]\s*", " ", body)               # 残留 "![]( "
    body = re.sub(r"^\s*\(\s*$|^\s*\(\s+", " ", body)          # 残留孤立 "(" 开头
    body = re.sub(r"\s+", " ", body).strip()
    return body


def _clean_speaker(sp: str) -> str:
    sp = re.sub(r"只看\s*Ta", "", sp)
    return sp.strip()


def parse_messages(html: str) -> list[dict]:
    """把群作业 HTML 解析成结构化消息列表（按时间升序）。"""
    text = _strip_tags(html)
    # 按"时间戳"切分：每条消息以 [YYYY-MM-DD HH:MM:SS] 开头
    parts = re.split(r"(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text)
    msgs: list[dict] = []
    for p in parts:
        p = p.strip()
        ts_match = TS_RE.search(p)
        if not ts_match:
            continue
        ts_str = f"{ts_match.group(1)} {ts_match.group(2)}"
        # 去掉开头的时间戳本身，保留其后的内容
        rest = p[ts_match.end():]
        speaker = ""
        is_owner = bool(GROUP_OWNER_RE.search(rest))
        sp = SPEAKER_RE.search(rest)
        if sp:
            speaker = _clean_speaker(sp.group(1))
        recalled = bool(RECALL_RE.search(rest))
        if recalled:
            body = "[已撤回]"
        else:
            # 去掉两处 "详情" 尾、发言人残段后的正文
            body = rest
            body = body.replace("详情", " ")
            # 丢弃发言人括号块本身
            body = re.sub(r"【[^】]*】", " ", body)
            body = _clean_body(body)
            # 去掉正文尾部残留的发言人昵称（"只看Ta"链接锚文本尾巴，如" 十倍之路"）
            if speaker and body.endswith(speaker):
                body = body[: -len(speaker)].strip()
        if not body and not speaker:
            continue
        msgs.append({
            "时间戳": ts_str,
            "发言人": speaker,
            "是否群主": is_owner,
            "正文": body,
            "是否撤回": recalled,
        })
    # 去重（同一秒内完全相同的合并），按时间升序
    seen: set[str] = set()
    dedup: list[dict] = []
    for m in msgs:
        # 去重键用「时间戳 + 正文」，合并仅发言人不同的重复行（同一人…）
        key = (m["时间戳"], m["正文"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(m)
    dedup.sort(key=lambda x: x["时间戳"])
    return dedup


def derive_author(url_or_none: str | None) -> str:
    """从链接的 a=xxx 参数推测作者/群主名，作为输出文件名前缀。"""
    if not url_or_none:
        return "homework"
    m = re.search(r"[?&]a=([^&]+)", url_or_none)
    if not m:
        return "homework"
    from urllib.parse import unquote
    name = unquote(m.group(1)).strip()
    name = re.sub(r"[^\w\u4e00-\u9fff-]", "", name)
    return name or "homework"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fetch_homework")
    ap.add_argument("url", nargs="?", help="群作业链接")
    ap.add_argument("--html", help="本地 HTML 文件路径（离线解析）")
    ap.add_argument("--out", default=None, help="JSON 输出路径")
    args = ap.parse_args(argv)

    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
        author = "local"
    elif args.url:
        print(f"[抓取] {args.url}", file=sys.stderr)
        html = _open_http(args.url).decode("utf-8", "ignore")
        author = derive_author(args.url)
    else:
        print(__doc__)
        return 2

    msgs = parse_messages(html)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{author}_raw.html"
    raw_path.write_text(html, encoding="utf-8")
    json_path = out_dir / f"{author}_{stamp}.json"
    payload = {"作者": author, "抓取时点": datetime.now().isoformat(timespec="seconds"),
               "消息": msgs}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[保存] 原始HTML={raw_path}", file=sys.stderr)
    print(f"[保存] 解析JSON={json_path}  消息数={len(msgs)}", file=sys.stderr)
    # stdout 打印精简预览（agent 可直接读）
    for m in msgs:
        owner = "【群主】" if m["是否群主"] else f"[{m['发言人']}]"
        print(f"{m['时间戳']} {owner} {m['正文']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))