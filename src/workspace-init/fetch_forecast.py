"""机构业绩预测取数 CLI：自包含单文件脚本（纯标准库，直连同花顺 F10 盈利预测页）。

为什么需要它：tushare 快照（fetch_quotes.py --snapshot）只有静态/TTM 两档 PE，
**没有前瞻盈利与机构一致预期**——景气投资的前瞻PE/PEG 缺分母。本脚本抓取并解析
同花顺 F10「盈利预测」页（basic.10jqka.com.cn/<代码>/worth.html，GBK 编码、
服务端渲染、无需 JS），把四张核心表解析成小体量结构化文本（每只几十行，
可直接进会话上下文）：

  1) 净利润一致预期年度汇总（caption「汇总--预测年报净利润」，单位亿元）：
     年度 / 预测机构数 / 最小值 / 均值 / 最大值 / 行业平均数 —— 前瞻PE 的分母。
  2) 每股收益一致预期年度汇总（caption「汇总--预测年报每股收益」，单位元）。
  3) 业绩预测明细（机构 / 研究员 / 各预测年 EPS / 各预测年净利润 / 报告日期，
     按报告日期倒序）—— 看预测分歧度与报告新鲜度。
  4) 详细指标预测（营收/净利/增速/ROE/每股净资产/静态PE 等的历史实际值，
     全空列自动剔除）—— 质量溢价与周期中枢的参考。

表格识别一律按 caption/表头内容匹配、不按位置猜；解析不到就如实说，宁缺毋滥。

用法：
  python fetch_forecast.py 002851
  python fetch_forecast.py 600519.SH,000333            # 多只逗号分隔
  python fetch_forecast.py 002851 --mcap 772           # 追加各预测年前瞻PE（总市值亿元）

设计约束（与 fetch_quotes.py 同款）：
- 纯标准库（argparse / datetime / html.parser / re / sys / time / urllib.request），
  零第三方依赖；无需任何 token（同花顺 F10 公开页）。
- 显式 ProxyHandler({}) 关闭系统代理直连；先走 http（与 tushare 直连同因，避开
  部分环境 TLS 不可达），网络层失败再退 https；限流（403/429）按退避重试。
- 只读不写：本脚本不落任何文件、不改任何状态，输出仅打印。

覆盖与口径：
- 仅 A 股（沪深北，6 位代码；.SH/.SZ/.BJ 后缀自动剥掉）。港股（5 位代码）在
  同花顺无此页 → 明确报错；估值时如实标注数据缺口，不编造。
- 页面存在但无上述表格（常见于无机构覆盖的小盘股）→ 输出「未解析到业绩预测
  表格」，退出码 0：这是有效结果而非错误。
- 一致预期均值为覆盖机构报告的算术平均：机构数 <5 家代表性弱，结论须注明
  覆盖数；明细表只列近年报告子集，机构总数以汇总表「预测机构数」为准；
  报告日期半年以上的旧预测谨慎引用（景气行业盈利预期变化快）。
- 行业平均数是同业均值对照，不是该股预测。

退出码：0=成功（含「无机构覆盖」）；1=部分/全部标的失败（已打印失败清单）。
输出仅供研究参考，不构成投资建议；数据口径以同花顺页面为准。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

#: 同花顺 F10 盈利预测页基地址（http 优先，失败退 https）
_BASES = ("http://basic.10jqka.com.cn", "https://basic.10jqka.com.cn")

#: 浏览器 UA：同花顺对非浏览器请求可能拒绝，带上更稳
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

#: 重试退避（秒）：网络错误 / 限流时逐次等待后重试
_RETRY_DELAYS = [2.0, 5.0, 10.0]

#: 相邻两标的抓取的默认间隔（秒）：对公开页面保持礼貌频率，防限流
_INTERVAL_DEFAULT = 1.0

#: 一致预期汇总表表头（去全部空白后逐格相等才认）
_SUMMARY_HEADER = ["年度", "预测机构数", "最小值", "均值", "最大值", "行业平均数"]

#: 标题形如「麦格米特(002851) 盈利预测_F10_同花顺金融服务网」（兼容全角括号）
_TITLE_RE = re.compile(r"^(.*?)\s*[(（](\d{6})[)）]")

#: 年份与日期
_YEAR_RE = re.compile(r"(20\d{2})")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FetchForecastError(RuntimeError):
    """单只标的取数/解析失败（错误信息面向用户，可直接转达）。"""


# ═══════════════════════════════════════════════════════════════════════════
# 页面解析：提取 <title> 与顶层 <table>（按 caption/表头内容识别，不按位置猜）
# ═══════════════════════════════════════════════════════════════════════════


class _WorthPageParser(HTMLParser):
    """收集 <title> 文本与全部**顶层**表格（每表 = {caption, rows}）。

    三类内容必须挡在外层单元格之外（页面在「详细指标预测」矩阵的预测列单元格里
    塞了隐藏 tipbox 浮层，浮层内还有「预测机构一览」嵌套小表）：
    - 嵌套表（depth>=2）的行/单元格/文本一律忽略；
    - div.tipbox 浮层整体跳过（含其中的嵌套表与标题）；
    - script/style 内文本丢弃。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.tables: list[dict] = []   # [{"caption": str, "rows": [[str, ...], ...]}]
        self._skip = 0                 # script/style 深度
        self._tipbox = 0               # tipbox 浮层内的 div 嵌套深度
        self._tbuf: list[str] | None = None
        self._depth = 0                # table 嵌套深度（1 = 顶层表）
        self._cur: dict | None = None
        self._caption: list[str] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title" and self._tbuf is None:
            self._tbuf = []
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._cur = {"caption": "", "rows": []}
                self._tipbox = 0  # 安全网：浮层不可能跨表
        elif tag == "div":
            cls = (dict(attrs).get("class") or "").split()
            if self._tipbox > 0:
                self._tipbox += 1
            elif "tipbox" in cls and self._depth >= 1:
                self._tipbox = 1
        elif self._depth != 1 or self._cur is None:
            return
        elif tag == "caption":
            self._caption = []
        elif tag == "tr":
            self._row = []
            self._cur["rows"].append(self._row)
            self._tipbox = 0  # 安全网：浮层不可能跨行
        elif tag in ("td", "th"):
            if self._row is not None:
                self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title" and self._tbuf is not None:
            self.title = " ".join("".join(self._tbuf).split())
            self._tbuf = None
        elif tag == "div":
            if self._tipbox > 0:
                self._tipbox -= 1
        elif tag == "caption" and self._caption is not None and self._depth == 1:
            if self._cur is not None:
                self._cur["caption"] = " ".join("".join(self._caption).split())
            self._caption = None
        elif tag == "table":
            if self._depth == 1 and self._cur is not None:
                self.tables.append(self._cur)
                self._cur = None
            self._depth = max(0, self._depth - 1)
        elif self._depth != 1:
            return
        elif tag == "tr":
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            if self._row is not None:
                self._row.append(text)
            self._cell = None

    def handle_data(self, data):
        if self._skip or self._tipbox or self._depth >= 2:
            return
        if self._tbuf is not None:
            self._tbuf.append(data)
        if self._caption is not None:
            self._caption.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _squash(text: str) -> str:
    """去掉全部空白（表头/单元格比对用）。"""
    return re.sub(r"\s+", "", text)


def _is_date(cell: str) -> bool:
    return bool(_DATE_RE.match(cell.strip()))


def _find_summary(tables: list[dict], caption_keyword: str) -> list[list[str]] | None:
    """按 caption 关键字找一致预期汇总表，返回数据行（年度/机构数/最小/均值/最大/行业平均）。"""
    for t in tables:
        if caption_keyword not in _squash(t["caption"]):
            continue
        rows = t["rows"]
        if not rows or [_squash(c) for c in rows[0]] != _SUMMARY_HEADER:
            continue
        out: list[list[str]] = []
        for r in rows[1:]:
            cells = [c.strip() for c in r]
            if len(cells) >= 6 and re.fullmatch(r"20\d{2}", cells[0]):
                out.append(cells[:6])
        if out:
            return out
    return None


def _detail_labels(year_cells: list[str] | None, width: int) -> list[str]:
    """明细表列名：机构 / 研究员 + 各年 EPS + 各年净利 + 报告日期。

    年份子表头前半组是每股收益、后半组是净利润（与首行跨列表头分组一致）；
    子表头缺失/对不齐时中间列退化为通用列名，不影响数据本身。
    """
    labels = ["机构", "研究员"]
    if year_cells and len(year_cells) % 2 == 0:
        half = len(year_cells) // 2
        for i, yc in enumerate(year_cells):
            m = _YEAR_RE.search(yc)
            year = m.group(1) + "E" if m else yc
            labels.append(("EPS " if i < half else "净利 ") + year)
    else:
        labels += [f"预测值{i + 1}" for i in range(max(0, width - 3))]
    labels.append("报告日期")
    return labels


def _find_details(tables: list[dict]) -> tuple[list[str], list[list[str]]]:
    """业绩预测明细表：返回 (列名, 数据行)。

    页面结构：首行跨列表头（机构名称|研究员|预测年报每股收益（元）|预测年报
    净利润（元）|报告日期）→ 次行年份子表头（2026预测|…|2028预测，前半 EPS、
    后半净利润）→ 数据行（净利润带亿/万单位，原样保留）。
    """
    for t in tables:
        rows = t["rows"]
        if not rows:
            continue
        head = [_squash(c) for c in rows[0]]
        if not head or head[0] != "机构名称" or "报告日期" not in head:
            continue
        year_cells: list[str] | None = None
        data: list[list[str]] = []
        for r in rows[1:]:
            cells = [c.strip() for c in r]
            if not cells:
                continue
            if (year_cells is None and cells[0]
                    and re.fullmatch(r"20\d{2}预测", _squash(cells[0]))
                    and not any(_is_date(c) for c in cells)):
                year_cells = [_squash(c) for c in cells]
                continue
            if any(_is_date(c) for c in cells):
                data.append(cells)
        width = max((len(r) for r in data), default=0)
        return _detail_labels(year_cells, width), data
    return [], []


def _find_matrix(tables: list[dict]) -> tuple[list[str], list[list[str]]] | None:
    """详细指标预测矩阵（首列表头为「预测指标」；预测均值列常为空，渲染时剔除）。"""
    for t in tables:
        rows = t["rows"]
        if not rows:
            continue
        head = [_squash(c) for c in rows[0]]
        if not head or head[0] != "预测指标":
            continue
        header = [c.strip() for c in rows[0]]
        data = []
        for r in rows[1:]:
            cells = [c.strip() for c in r]
            if cells and cells[0]:
                data.append(cells)
        return header, data
    return None


def parse_worth_page(html: str) -> dict:
    """解析盈利预测页 → {name, eps, np, detail_labels, detail_rows, matrix}。

    标题里无「股票名(6位代码)」（无效代码 / 港股空壳页）→ 抛 FetchForecastError。
    """
    p = _WorthPageParser()
    p.feed(html)
    m = _TITLE_RE.match(p.title or "")
    if not m or not m.group(1).strip():
        raise FetchForecastError(
            "页面无股票信息：代码可能有误，或为港股（同花顺 F10 无港股盈利预测页；"
            "港股前瞻估值请退回腾讯源 PE(动) 或如实标注数据缺口）")
    name = m.group(1).strip()
    detail_labels, detail_rows = _find_details(p.tables)
    return {
        "name": name,
        "eps": _find_summary(p.tables, "预测年报每股收益"),
        "np": _find_summary(p.tables, "预测年报净利润"),
        "detail_labels": detail_labels,
        "detail_rows": detail_rows,
        "matrix": _find_matrix(p.tables),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 抓取：直连（关系统代理）、限流退避重试、http→https 回退、GBK 解码
# ═══════════════════════════════════════════════════════════════════════════

# 关闭系统代理的 opener：本机代理可能对数据域名不可达，直连更稳（同 fetch_quotes.py）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _decode_html(raw: bytes) -> str:
    """按页面声明解码：meta charset 优先，其次逐个尝试 utf-8 / gbk / gb18030。"""
    head = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.IGNORECASE)
    for enc in ([m.group(1).lower()] if m else []) + ["utf-8", "gbk", "gb18030"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with _OPENER.open(req, timeout=timeout) as resp:
        return _decode_html(resp.read())


def fetch_worth_html(code: str, timeout: float) -> str:
    """抓取某 A 股代码的盈利预测页 HTML（限流退避重试；http 失败退 https）。"""
    last: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        for base in _BASES:
            try:
                return _http_get(f"{base}/{code}/worth.html", timeout)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise FetchForecastError(
                        f"HTTP 404：{code} 无同花顺 F10 盈利预测页") from e
                if e.code in (403, 429):
                    # 限流：同 host 换协议无意义，等待后整体重试
                    last = FetchForecastError(
                        f"HTTP {e.code}：疑似被限流，降低频率或稍后重试")
                    break
                last = FetchForecastError(f"HTTP {e.code}：{e.reason}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
        if attempt < len(_RETRY_DELAYS):
            wait = _RETRY_DELAYS[attempt]
            print(f"  [retry] {code} 抓取失败（{last}），{wait:.0f}s 后第 "
                  f"{attempt + 1}/{len(_RETRY_DELAYS)} 次重试…", flush=True)
            time.sleep(wait)
    raise FetchForecastError(f"重试 {len(_RETRY_DELAYS)} 次后仍失败：{last}")


# ═══════════════════════════════════════════════════════════════════════════
# 渲染：四张表 → Markdown 管道表（+ 可选前瞻PE）
# ═══════════════════════════════════════════════════════════════════════════


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(c if c else "-" for c in cells) + " |"


def _year_label(cell: str) -> str:
    m = _YEAR_RE.search(cell)
    return m.group(1) + "E" if m else cell


def _summary_md(rows: list[list[str]]) -> str:
    lines = ["| 年度 | 机构数 | 最小值 | 均值 | 最大值 | 行业平均 |"]
    for r in rows:
        lines.append(_md_row([_year_label(r[0])] + r[1:]))
    return "\n".join(lines)


def _detail_md(labels: list[str], rows: list[list[str]]) -> str:
    # 按报告日期倒序（ISO 日期字符串可直接排序；解析不出日期的行排最后）
    def _key(r: list[str]) -> str:
        for c in reversed(r):
            if _is_date(c):
                return c
        return "0000-00-00"

    width = len(labels)
    lines = [_md_row(labels)]
    for r in sorted(rows, key=_key, reverse=True):
        lines.append(_md_row((r + [""] * width)[:width]))
    return "\n".join(lines)


def _matrix_md(header: list[str], rows: list[list[str]]) -> str:
    width = len(header)
    padded = [(r + [""] * width)[:width] for r in rows]
    # 剔除全空的数据列（页面预测均值列常为空），首列（指标名）始终保留
    keep = [0] + [i for i in range(1, width) if any(p[i] for p in padded)]
    lines = [_md_row([header[i] for i in keep])]
    for p in padded:
        lines.append(_md_row([p[i] for i in keep]))
    return "\n".join(lines)


def _forward_pe_md(np_rows: list[list[str]] | None, mcap: float) -> str:
    lines = [f"【前瞻 PE】（总市值 {mcap:g} 亿元 ÷ 净利润一致预期均值）",
             "| 年度 | 净利均值(亿元) | 前瞻PE |"]
    for r in np_rows or []:
        try:
            mean = float(r[3])
        except (TypeError, ValueError):
            continue
        pe = f"{mcap / mean:.1f}x" if mean > 0 else "-"
        lines.append(_md_row([_year_label(r[0]), r[3], pe]))
    if len(lines) == 2:
        lines.append(_md_row(["-", "-", "（无净利润一致预期，无法计算）"]))
    return "\n".join(lines)


def render_stock(code: str, data: dict, mcap: float | None, fetched_at: str) -> str:
    """单只标的的完整输出（Markdown 管道表，可直接进会话上下文）。"""
    blocks = [
        f"===== {code} {data['name']}（同花顺 F10 机构业绩预测） =====",
        f"抓取时点：{fetched_at}（来源：basic.10jqka.com.cn/{code}/worth.html，A 股）",
    ]
    np_rows = data["np"]
    if np_rows:
        blocks.append("【净利润一致预期（亿元）】\n" + _summary_md(np_rows))
    if data["eps"]:
        blocks.append("【每股收益一致预期（元）】\n" + _summary_md(data["eps"]))
    if data["detail_rows"]:
        blocks.append(
            f"【机构预测明细】{len(data['detail_rows'])} 条（按报告日期倒序；"
            "机构覆盖总数以一致预期表「机构数」为准）\n"
            + _detail_md(data["detail_labels"], data["detail_rows"]))
    if data["matrix"]:
        blocks.append("【详细指标预测（历史实际值与预测均值，全空列已剔除）】\n"
                      + _matrix_md(*data["matrix"]))
    if mcap is not None:
        blocks.append(_forward_pe_md(np_rows, mcap))
    if not (np_rows or data["eps"] or data["detail_rows"] or data["matrix"]):
        blocks.append(
            "未解析到业绩预测表格：可能该股无机构覆盖，或页面结构变化——估值时"
            "如实标注数据缺口（前瞻口径可退回历史增速外推并注明口径），不编造。")
    blocks.append("口径：一致预期=覆盖机构预测的算术平均（机构数少时代表性弱，"
                  "结论须注明覆盖家数）；仅供研究参考，不构成投资建议。")
    return "\n\n".join(blocks)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def normalize_code(raw: str) -> str:
    """规范化为同花顺 F10 的 6 位 A 股代码；港股 / 无法识别时报错。"""
    s = (raw or "").strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        s = s[:-3]
    if re.fullmatch(r"\d{6}", s):
        return s
    if re.fullmatch(r"\d{5}", s) or s.endswith(".HK"):
        raise FetchForecastError(
            "港股暂不支持：同花顺 F10 无港股盈利预测页（港股前瞻估值退回"
            "腾讯源 PE(动) 或如实标注数据缺口）")
    raise FetchForecastError(f"代码格式无法识别：{raw}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fetch_forecast",
        description="机构业绩预测取数 CLI：直连同花顺 F10 盈利预测页，解析净利润/EPS "
                    "一致预期、机构预测明细与详细指标历史值（纯标准库、无需 token）。",
    )
    ap.add_argument("codes", help="股票代码（逗号分隔，如 002851 或 600519.SH,000333）")
    ap.add_argument("--mcap", type=float, default=None, metavar="亿元",
                    help="总市值（亿元）：追加输出各预测年前瞻PE；仅单只代码时可用"
                         "（fetch_quotes 快照的总市值为万元，÷1e4 后传入）")
    ap.add_argument("--interval", type=float, default=_INTERVAL_DEFAULT,
                    help=f"相邻两标的抓取间隔秒数（默认 {_INTERVAL_DEFAULT}，防限流）")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="单次请求超时秒数（默认 20）")
    args = ap.parse_args(argv)

    raw_codes = [c for c in re.split(r"[,，\s]+", args.codes) if c.strip()]
    if not raw_codes:
        print("[error] 需要至少一个股票代码，如：python fetch_forecast.py 002851",
              file=sys.stderr)
        return 1
    if args.mcap is not None and len(raw_codes) > 1:
        print("[error] --mcap 仅支持单只代码（各股市值不同，多只时请逐只调用）。",
              file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    for i, raw in enumerate(raw_codes):
        if i:
            time.sleep(max(0.0, args.interval))
        try:
            code = normalize_code(raw)
            html = fetch_worth_html(code, args.timeout)
            data = parse_worth_page(html)
        except FetchForecastError as e:
            failures.append((raw, str(e)))
            print(f"===== {raw} =====\n  取数失败：{e}")
            continue
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        print(render_stock(code, data, args.mcap, fetched_at))
        print()

    if failures:
        print(f"完成：{len(raw_codes) - len(failures)} 只成功 / {len(failures)} 只失败：")
        for code, msg in failures:
            print(f"  - {code}：{msg}")
        return 1
    print(f"完成：{len(raw_codes)} 只全部成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
