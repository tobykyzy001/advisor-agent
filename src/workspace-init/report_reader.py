"""研报阅读器（report_reader）：自包含单文件脚本（纯标准库，可下载即跑）。

这是「读研报」工具的**唯一可执行真源**，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/report_reader.py` 提供给目标工作区里的 agent
下载执行（与 w_bottom_screen.py / fetch_forecast.py 同一分发模式）。

它解决什么
----------
拿到一份卖方研报（本地文本 / 网页链接），把它拆成「可核查的主张」，做一轮**批判性核查**，
然后在「采信账本」上明确每条主张到底采信了谁，最后才用采信后的预测去推估值结论
（前瞻PE / PEG / 合理价区间 / 安全边际 / 现价隐含要求 / 与机构一致预期的差异）。

核心原则（本工具的灵魂）
------------------------
批判性阅读 **≠** 否决研报。核查只做一件事：**找证据**。

- **有证据证伪**（报告内部算术自相矛盾 / 与已披露实际值冲突 / 评级与目标价方向矛盾）
  → 该主张不采信：能用证据值替代的就替代（overridden），替代不了的主张作废（rejected）；
  **作废的主张绝不带进估值**。
- **拿不出证据**（无外部数据、与一致预期有分歧、报告偏旧、增速异常、覆盖机构少）
  → **默认采信研报估算**，只把疑点登记为 caveat / 缺口 / 风险，**不擅自给研报数字打折**。

估值桥一律建立在「采信后的预测」之上：先采信，再判断贵不贵。

证据等级（高 → 低）
-------------------
1) 已披露实际值（表格里 20XXA 列、报告披露的历史实际值、外部实测注入）
2) 报告表格绝对数（净利 / 营收 / EPS，可复算、可交叉核对）
3) 报告行文措辞（同比增速、形容词、评级文字）

低等级与高等级冲突 → 采信高等级（例如用表格绝对值复算出的增速覆盖行文里的增速）；
同等级冲突且无法裁决 → 该字段作废并如实标注，不做猜测。

单位约定（全脚本统一）
----------------------
- 净利润 / 营业收入：**百万元**（卖方研报表格惯例，照抄表格即可）
- EPS / 目标价 / 现价：**元**
- 总市值：**亿元**（fetch_quotes 快照给的是万元，÷1e4）
- 股本：**亿股**（= 总市值亿元 ÷ 现价）

输入（三种，可组合）
--------------------
1) `--input`：研报入口，**一个参数同时支持本地文件与网址**
   - 本地：.txt / .md / .html（PDF 不解析，明确报缺口并以退出码 2 停下）
   - 网址：http(s)，抓取 HTML → 转纯文本
2) `--claims`：按报告正文抽好的结构化字段（JSON，格式见 CLAIMS_SCHEMA_DOC）；
   它覆盖脚本的自动抽取（抽得更准时用），不填就只用自动抽取。
3) `--snapshot-file` / `--consensus-file`：证据文件，脚本自己解析（数字不进 LLM 上下文）：
   - 快照：`python scripts/fetch_quotes.py --snapshot <代码> > 文件` 的原样输出
   - 一致预期：`python scripts/fetch_forecast.py <代码> --mcap <总市值亿元> > 文件` 的原样输出
   缺了就在报告里如实标缺口，不编造。

用法
----
  python report_reader.py --input output/research/report.md
  python report_reader.py --input 研报.txt --snapshot-file snap.txt --consensus-file cons.txt
  python report_reader.py --input https://example.com/rpt.html --claims claims.json
  python report_reader.py --input 研报.txt --text-out output/research/xx.txt   # 只抽正文

输出：
  - 标准输出：摘要（研报要素 / 核查发现 / 采信账本 / 估值桥 / 缺口）
  - 落盘：output/research/report_<标的或slug>_<时间戳>.md 与同名 .json（`--no-report` 可关）

退出码：0 正常；2 输入不可用（读不到文件 / URL 失败 / PDF 未转文本）；1 其他错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

# ── 阈值（集中在此，便于按经验调整；每处用途见注释） ──────────────────────────
#: 目标价与「自述估值锚（EPS × 给予PE）」的允许偏差，超过即判定报告内部算术不自洽
TARGET_PRICE_TOL = 0.05
#: 行文增速与「表格绝对值复算增速」的允许偏差（百分点），超过即判定两者矛盾
GROWTH_TOL_PCT = 3.0
#: EPS 与「净利 ÷ 股本」的偏差：超过此值登记为口径疑点（不证伪，股本口径可能不同）
EPS_SUSPECT_TOL = 0.10
#: EPS 与「净利 ÷ 股本」的偏差：超过此值判定互相矛盾，报告 EPS 作废、改用净利口径
EPS_FALSIFY_TOL = 0.20
#: 预测值 vs 已披露实际值的偏差上限，超过即判定预测被事实推翻（实测值优先）
ACTUAL_TOL = 0.10
#: 报告新鲜度：报告日期距 as_of 超过该自然日数 → 预测滞后疑点（不改数）
STALE_DAYS = 180
#: 报告预测与机构一致预期的偏离上限，超过即登记为分歧（不改数，只做差异分析）
CONSENSUS_GAP_TOL = 0.30
#: 增速异常阈值（%）：超过即提示核对一次性因素（不改数）
GROWTH_OUTLIER_PCT = 100.0

#: A 股看涨 / 看空评级词表（前者用于「评级 vs 目标价方向」自洽性检查）
BULLISH_RATINGS = ("强烈推荐", "买入", "增持", "推荐", "优于大市", "跑赢行业", "强于大市")
BEARISH_RATINGS = ("减持", "卖出", "回避", "弱于大市", "跑输行业")
ALL_RATINGS = BULLISH_RATINGS + ("中性", "持有") + BEARISH_RATINGS

CLAIMS_SCHEMA_DOC = """\
--claims JSON 字段（缺项一律留空，脚本会如实登记缺口；单位务必按注释）：
{
  "symbol": "600519.SH",              // 标的代码（A股带 .SH/.SZ/.BJ，港股如 00700.HK）
  "name": "贵州茅台",
  "broker": "XX证券",                  // 研报机构
  "analyst": "张三",                   // 分析师（可选）
  "report_date": "2026-03-15",         // 报告日期 YYYY-MM-DD
  "rating": "买入",                    // 投资评级
  "target_price": 2100.0,              // 目标价（元）
  "valuation_basis": {                 // 报告自述估值锚，如「给予 2027 年 25 倍 PE」
    "year": 2027,
    "pe": 25.0
  },
  "forecasts": [                       // 分年度预测（净利/营收单位：百万元；EPS：元）
    {"year": 2026, "revenue": 190000, "revenue_yoy": 15.0,
     "net_profit": 95000, "net_profit_yoy": 18.0, "eps": 75.6}
  ],
  "actuals": [                         // 报告里已披露的**实际值**（表格 20XXA 列 / 正文实际值）
    {"year": 2025, "revenue": 170000, "net_profit": 80000, "eps": 63.7}
  ],
  "assumptions": ["..."],              // 核心假设（原文摘录）
  "catalysts": ["..."],                // 催化剂
  "risks": ["..."]                     // 风险提示
}
"""

# ── 正则（模块级编译一次） ────────────────────────────────────────────────────
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PCT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*%")
_YEAR_TOKEN_RE = re.compile(r"(20\d\d)\s*([AE])?")
_DATE_RE = re.compile(r"(20\d\d)\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_TARGET_RE = re.compile(r"目标价[^\d\n]{0,16}(\d[\d,]*(?:\.\d+)?)")
_RATING_LINE_RE = re.compile(r"(?:投资评级|评级|维持|首次覆盖)[^\n]{0,12}?"
                             r"(" + "|".join(ALL_RATINGS) + r")")
_BROKER_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:证券|研究所|研究院|研究部))")
_BASIS_PATTERNS = (
    re.compile(r"(20\d\d)\s*年\s*(?:的)?\s*PE\s*(\d+(?:\.\d+)?)\s*倍"),
    re.compile(r"(20\d\d)\s*年\s*(?:的)?\s*(\d+(?:\.\d+)?)\s*倍"),
    re.compile(r"(\d+(?:\.\d+)?)\s*倍\s*(?:的)?\s*(20\d\d)\s*年"),
    re.compile(r"(20\d\d)\s*年\s*PE\s*(\d+(?:\.\d+)?)"),
)
_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    "revenue": (r"营业总收入", r"营业收入", r"营收"),
    "net_profit": (r"归母净利润", r"归属于母公司(?:股东)?的?净利润", r"净利润"),
    "eps": (r"基本每股收益", r"每股收益", r"EPS"),
}
_SNAPSHOT_PATTERNS = {
    "price": re.compile(r"收盘[：:]\s*([\d,]+(?:\.\d+)?)"),
    "mcap_wan": re.compile(r"总市值[：:]\s*([\d,]+(?:\.\d+)?)"),
    "pe_ttm": re.compile(r"PE\(TTM\)[：:]\s*([\d,]+(?:\.\d+)?)"),
    "pe_static": re.compile(r"PE\(静\)[：:]\s*([\d,]+(?:\.\d+)?)"),
    "pb": re.compile(r"PB[：:]\s*([\d,]+(?:\.\d+)?)"),
    "dv_ttm": re.compile(r"股息率\(TTM\)[：:]\s*([\d,]+(?:\.\d+)?)"),
    "as_of": re.compile(r"日期[：:]\s*(20\d\d-\d{2}-\d{2})"),
}
#: 表格单元格：纯数值（可带百分号）
_CELL_NUM_RE = re.compile(r"^(-?\d[\d,]*(?:\.\d+)?)(%?)$")
#: 表格单元格：空值占位（保留占位以保证列对齐）
_CELL_EMPTY_RE = re.compile(r"^(?:[-—–/]+|N/?A|不适用|--)$", re.IGNORECASE)


class InputUnavailable(RuntimeError):
    """输入不可用（文件读不到 / URL 失败 / PDF 未转文本）——fail-closed，绝不编造。"""


# ═══════════════════════════════════════════════════════════════════════════
# 一、取正文：本地文件 / 网址 → 纯文本
# ═══════════════════════════════════════════════════════════════════════════


class _HtmlToText(HTMLParser):
    """极简 HTML → 文本：丢弃 script/style，块级与表格单元格补分隔符。

    表格单元格用 " | " 连接，让「指标 | 2025E | 2026E |」这类行在转文本后仍是一行，
    后续按标签抽数的正则才认得出来。
    """

    _SKIP = ("script", "style", "noscript", "head")
    _BLOCK = ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "table",
              "section", "article", "br", "ul", "ol")
    _CELL = ("td", "th")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._buf.append("\n")
        elif tag in self._CELL:
            self._buf.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buf.append(data)

    def text(self) -> str:
        raw = "".join(self._buf)
        lines = []
        for ln in raw.splitlines():
            ln = re.sub(r"[ \t\u00a0]+", " ", ln).strip()
            if ln.strip(" |"):
                lines.append(ln)
        return "\n".join(lines)


def html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _decode_bytes(raw: bytes, content_type: str = "") -> str:
    """按 charset 提示解码，缺失时依次试 utf-8 / gbk（研报网页常见 gbk）。"""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    charsets = ([m.group(1)] if m else []) + ["utf-8", "gbk", "gb18030"]
    for cs in charsets:
        try:
            return raw.decode(cs)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass
class Source:
    """研报正文来源。"""

    kind: str            # file | url
    ref: str             # 文件路径或 URL
    text: str
    title: str = ""


def _looks_like_html(text: str) -> bool:
    head = text[:2000].lower()
    return "<html" in head or "<table" in head or "<div" in head or "<body" in head


def load_source(entry: str, timeout: float = 20.0) -> Source:
    """读一份研报正文：本地文件路径或 http(s) 网址。"""
    entry = (entry or "").strip().strip('"').strip("'")
    if not entry:
        raise InputUnavailable("--input 不能为空：请给研报的本地文件路径或 http(s) 链接")
    if re.match(r"^https?://", entry, re.IGNORECASE):
        req = urllib.request.Request(entry, headers={
            "User-Agent": "Mozilla/5.0 (compatible; advisor-agent report_reader)",
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            raise InputUnavailable(f"抓取研报链接失败：{entry}（{exc}）") from exc
        if raw[:4] == b"%PDF":
            raise InputUnavailable(
                "该链接返回的是 PDF：本脚本为纯标准库、不内置 PDF 解析，"
                "请先把 PDF 转成 .txt/.md 文本，再用 --input 指向文本文件"
            )
        text = _decode_bytes(raw, ctype)
        if _looks_like_html(text):
            text = html_to_text(text)
        title = _guess_title(text, entry)
        return Source(kind="url", ref=entry, text=text, title=title)

    path = Path(entry)
    if not path.exists():
        raise InputUnavailable(f"研报文件不存在：{path.resolve()}")
    if path.suffix.lower() == ".pdf":
        raise InputUnavailable(
            f"不解析 PDF（{path.name}）：请先把 PDF 转成 .txt/.md 文本再喂给本脚本"
            "（纯标准库不内置 PDF 解析，避免解析出错却当成事实用）"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputUnavailable(f"读取研报文件失败：{path}（{exc}）") from exc
    if raw[:4] == b"%PDF":
        raise InputUnavailable(f"文件内容是 PDF（{path.name}）：请先转成文本再重跑")
    text = _decode_bytes(raw)
    if path.suffix.lower() in (".html", ".htm") or _looks_like_html(text):
        text = html_to_text(text)
    return Source(kind="file", ref=str(path), text=text, title=_guess_title(text, path.name))


def _guess_title(text: str, fallback: str) -> str:
    """标题：优先第一行（研报首行通常是「XX证券研究报告 | 行业 | 日期」这类报头）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return fallback
    first = lines[0]
    if 4 <= len(first) <= 120:
        return first[:120]
    return max(lines[:6], key=len)[:120]


# ═══════════════════════════════════════════════════════════════════════════
# 二、领域结构
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Forecast:
    """单一年度的预测（或已披露实际）值。单位为研报口径：净利/营收百万元、EPS 元。"""

    year: int
    revenue: Optional[float] = None
    revenue_yoy: Optional[float] = None
    net_profit: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    eps: Optional[float] = None

    def copy(self) -> Forecast:
        return Forecast(self.year, self.revenue, self.revenue_yoy,
                        self.net_profit, self.net_profit_yoy, self.eps)

    def to_dict(self) -> dict:
        return {"year": self.year, "revenue": self.revenue, "revenue_yoy": self.revenue_yoy,
                "net_profit": self.net_profit, "net_profit_yoy": self.net_profit_yoy,
                "eps": self.eps}


@dataclass
class Claims:
    """研报主张集：报告说了什么（不含任何我们自己的判断）。"""

    symbol: str = ""
    name: str = ""
    broker: str = ""
    analyst: str = ""
    report_date: Optional[date] = None
    rating: Optional[str] = None
    target_price: Optional[float] = None
    basis_year: Optional[int] = None
    basis_pe: Optional[float] = None
    forecasts: dict[int, Forecast] = field(default_factory=dict)
    actuals: dict[int, Forecast] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    raw_quotes: list[str] = field(default_factory=list)   # 关键原文摘录（可回溯）
    gaps: list[str] = field(default_factory=list)         # 抽不到/报告没写的字段
    parser_notes: list[str] = field(default_factory=list)  # 抽取口径约定与来源说明

    def forecast_for(self, year: Optional[int]) -> Optional[Forecast]:
        return self.forecasts.get(year) if year else None

    def all_years(self) -> list[int]:
        return sorted(set(self.forecasts) | set(self.actuals))

    def base_for(self, year: int) -> Optional[Forecast]:
        """某年的基期值：优先已披露实际值，其次上一年预测。"""
        return self.actuals.get(year) or self.forecasts.get(year)


@dataclass
class Snapshot:
    """行情快照（由 fetch_quotes.py --snapshot 的输出解析，金额单位已统一为亿元）。"""

    price: Optional[float] = None
    mcap_yi: Optional[float] = None
    pe_ttm: Optional[float] = None
    pe_static: Optional[float] = None
    pb: Optional[float] = None
    dv_ttm: Optional[float] = None
    as_of: str = ""

    @property
    def shares_yi(self) -> Optional[float]:
        """股本（亿股）= 总市值(亿元) ÷ 现价(元)。"""
        if self.mcap_yi and self.price:
            return self.mcap_yi / self.price
        return None


@dataclass
class Consensus:
    """机构一致预期（由 fetch_forecast.py 的输出解析；净利单位亿元）。"""

    np_mean_yi: dict[int, float] = field(default_factory=dict)
    np_inst_count: dict[int, str] = field(default_factory=dict)
    eps_mean: dict[int, float] = field(default_factory=dict)
    as_of: str = ""


@dataclass
class Finding:
    """一条批判性核查发现。level：falsified（有证据证伪）/ suspicious（存疑，不否决）/ note。"""

    code: str
    level: str
    subject: str
    detail: str
    evidence: str
    action: str


@dataclass
class Override:
    """证伪后对主张的替代（证据值）。field 见 adopt() 支持的键。"""

    year: Optional[int]
    field: str
    value: object
    why: str


@dataclass
class Adoption:
    """采信账本：核查之后，实际带着哪些数字进入估值。"""

    forecasts: dict[int, Forecast] = field(default_factory=dict)
    target_price: Optional[float] = None
    target_price_status: str = "missing"   # assumed | overridden | rejected | missing
    rating: Optional[str] = None
    rating_status: str = "missing"
    overrides: list[Override] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)   # 存疑但采信（不改数）
    rejections: list[str] = field(default_factory=list)  # 作废的主张（不带进估值）

    @property
    def ledger(self) -> list[dict]:
        """采信账本表格行：主张 / 值 / 状态 / 依据。"""
        rows: list[dict] = []
        for yr in sorted(self.forecasts):
            fc = self.forecasts[yr]
            rows.append({"claim": f"{yr}E 归母净利润(百万元)", "value": fc.net_profit,
                         "status": "assumed" if fc.net_profit is not None else "missing",
                         "basis": "研报估算（无证据证伪，默认采信）"})
        rows.append({"claim": "目标价(元)", "value": self.target_price,
                     "status": self.target_price_status,
                     "basis": "研报目标价" if self.target_price_status == "assumed"
                              else "见核查发现与替代说明"})
        return rows


@dataclass
class BridgeResult:
    """估值桥：建立在采信后的预测之上。"""

    forward_year: Optional[int] = None
    forward_eps: Optional[float] = None
    forward_pe: Optional[float] = None            # 市值口径：总市值 ÷ 采信净利
    pe_by_year: dict[int, float] = field(default_factory=dict)   # 各预测年前瞻PE
    consensus_forward_pe: Optional[float] = None
    growth_pct: Optional[float] = None
    peg: Optional[float] = None
    target_upside_pct: Optional[float] = None
    anchor_price: Optional[float] = None
    fair_low: Optional[float] = None
    fair_high: Optional[float] = None
    margin_of_safety_pct: Optional[float] = None
    required_growth_pct: Optional[float] = None   # 现价隐含：PEG=1 时需要的净利增速
    consensus_gap: dict[int, float] = field(default_factory=dict)
    verdict: str = ""
    facts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "forward_year": self.forward_year, "forward_eps": self.forward_eps,
            "forward_pe": self.forward_pe, "pe_by_year": self.pe_by_year,
            "consensus_forward_pe": self.consensus_forward_pe,
            "growth_pct": self.growth_pct, "peg": self.peg,
            "target_upside_pct": self.target_upside_pct, "anchor_price": self.anchor_price,
            "fair_low": self.fair_low, "fair_high": self.fair_high,
            "margin_of_safety_pct": self.margin_of_safety_pct,
            "required_growth_pct": self.required_growth_pct,
            "consensus_gap": self.consensus_gap, "verdict": self.verdict,
            "facts": self.facts, "notes": self.notes, "risks": self.risks,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 三、从报告正文抽取主张（best-effort；抽不到的进 gaps，不猜）
# ═══════════════════════════════════════════════════════════════════════════


def _to_float(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text):
        v = _to_float(m.group(0))
        if v is not None:
            out.append(v)
    return out


def _pcts(text: str) -> list[float]:
    return [v for v in (_to_float(m.group(1)) for m in _PCT_RE.finditer(text)) if v is not None]


def _find_report_date(text: str) -> Optional[date]:
    preferred = [ln for ln in text.splitlines() if re.search(r"报告日期|发布日期|日期", ln)]
    for chunk in preferred + [text]:
        m = _DATE_RE.search(chunk)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


def _find_rating(text: str) -> Optional[str]:
    m = _RATING_LINE_RE.search(text)
    if m:
        return m.group(1)
    best: tuple[int, str] | None = None
    for word in ALL_RATINGS:
        idx = text.find(word)
        if idx < 0:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(word) > len(best[1])):
            best = (idx, word)
    return best[1] if best else None


def _find_target_price(text: str) -> Optional[float]:
    m = _TARGET_RE.search(text)
    return _to_float(m.group(1)) if m else None


def _find_broker(text: str) -> str:
    m = _BROKER_RE.search(text)
    return m.group(1) if m else ""


def _basis_in(sentence: str) -> tuple[Optional[int], Optional[float]]:
    """在一句话里找自述估值锚（年度 + 给予倍数）。"""
    for pat in _BASIS_PATTERNS:
        m = pat.search(sentence)
        if not m:
            continue
        groups = m.groups()
        year = pe = None
        for g in groups:
            if g is None:
                continue
            if re.fullmatch(r"20\d\d", g):
                year = int(g)
            else:
                pe = _to_float(g)
        if year and pe:
            return year, pe
    return None, None


def _find_valuation_basis(text: str) -> tuple[Optional[int], Optional[float], str]:
    """优先取与「目标价」同句的锚（那才是定目标价用的倍数），其次全文首个锚。"""
    sentences = [s for s in re.split(r"[。；;\n]", text) if s.strip()]
    for s in sentences:
        if "目标价" in s:
            year, pe = _basis_in(s)
            if year and pe:
                return year, pe, s.strip()[:120]
    for s in sentences:
        year, pe = _basis_in(s)
        if year and pe:
            return year, pe, s.strip()[:120]
    return None, None, ""


def _find_year_header(lines: list[str]) -> Optional[tuple[int, list[str], list[bool]]]:
    """定位预测表表头行：返回（行号, 年份标签, 是否为实际值列）。"""
    best: Optional[tuple[int, list[str], list[bool]]] = None
    for i, line in enumerate(lines):
        toks = _YEAR_TOKEN_RE.findall(line)
        if len(toks) < 2:
            continue
        has_sep = "|" in line or "\t" in line
        if not (len(toks) >= 3 or any(s for _, s in toks) or has_sep):
            continue
        tags = [f"{y}{s}" for y, s in toks]
        flags = [s == "A" for _, s in toks]
        if best is None or len(tags) > len(best[1]):
            best = (i, tags, flags)
    return best


def _value_cells(rest: str) -> list[Optional[tuple[float, bool]]]:
    """把标签之后的表格单元格转成数值序列（返回项与表格列一一对应）。

    - 数值单元格 → (值, 是否带百分号)；
    - '-'/'—'/'NA' 占位 → None（**保留占位**，避免后面列整体错位）；
    - 单位注释（如「（百万元）」）等非数值单元格 → 直接丢弃，不占位。

    这样「列对齐」由表格自身的单元格边界保证，比「把一行里所有数字按顺序取」安全得多。
    """
    raw = rest.split("|") if "|" in rest else re.split(r"\s{2,}|\t", rest)
    out: list[Optional[tuple[float, bool]]] = []
    for cell in raw:
        c = cell.strip()
        if not c:
            continue
        if _CELL_EMPTY_RE.match(c):
            out.append(None)
            continue
        m = _CELL_NUM_RE.match(c)
        if m:
            out.append((float(m.group(1).replace(",", "")), m.group(2) == "%"))
    return out


def _row_values(line: str,
                label_patterns: tuple[str, ...]) -> Optional[list[Optional[tuple[float, bool]]]]:
    """在数据行里取标签之后的单元格数值（标签前的行号/序号列因此被排除）。"""
    for pat in label_patterns:
        m = re.search(pat, line)
        if not m:
            continue
        cells = _value_cells(line[m.end():])
        if cells:
            return cells
    return None


def _extract_table_forecasts(text: str) -> tuple[dict[int, Forecast], dict[int, Forecast],
                                                 list[str], list[str]]:
    """解析预测表：返回（预测, 实际值, gaps, notes）。"""
    lines = text.splitlines()
    header = _find_year_header(lines)
    forecasts: dict[int, Forecast] = {}
    actuals: dict[int, Forecast] = {}
    gaps: list[str] = []
    notes: list[str] = []
    if header is None:
        return forecasts, actuals, gaps, notes

    head_idx, tags, is_actual = header
    years = [int(t[:4]) for t in tags]
    notes.append("预测表表头：" + " ".join(tags) + f"（第 {head_idx + 1} 行）")
    suffix_known = any(t.endswith(("A", "E")) for t in tags)
    if not suffix_known:
        ref_year = _find_report_date(text)
        ref = ref_year.year if ref_year else max(years)
        is_actual = [y < ref for y in years]
        notes.append(f"表头年份无 A/E 后缀：按「年份 < 报告年({ref}) = 已披露实际值」判定，"
                     "如有出入请用 --claims 显式指定 actuals")
    else:
        notes.append("表头 A/E 后缀：A 列记为已披露实际值，E 列为预测")

    body = lines[head_idx + 1: head_idx + 60]
    positions: dict[str, int] = {}
    for key, patterns in _LABEL_PATTERNS.items():
        found: Optional[tuple[int, list[Optional[tuple[float, bool]]]]] = None
        for offset, line in enumerate(body):
            if len(_YEAR_TOKEN_RE.findall(line)) >= 2:
                continue  # 跳过表头/年份行
            cells = _row_values(line, patterns)
            if cells is not None:
                found = (offset, cells)
                break
        if found is None:
            gaps.append(f"未能从预测表抽到「{key}」行")
            continue
        offset, cells = found
        positions[key] = offset
        plain = [c for c in cells if c is not None and not c[1]]
        if len(plain) < len(years):
            notes.append(f"「{key}」行只抽到 {len(plain)} 个数值（表头 {len(years)} 列），"
                         "按单元格顺序对齐，请核对原文")
        for idx, year in enumerate(years):
            if idx >= len(cells) or cells[idx] is None or cells[idx][1]:  # type: ignore[index]
                continue
            value = cells[idx][0]                                        # type: ignore[index]
            fc = (actuals if is_actual[idx] else forecasts).setdefault(
                year, Forecast(year=year))
            setattr(fc, key, value)

    # 增速行（同比/增速/增长率）：单元格数与年份列一致才解析，避免列错位；
    # 归属按研报表格惯例——优先记为**归母净利润增速**（PEG 的分母），否则营收增速。
    yoy_re = re.compile(r"同比|增速|增长率|YoY|yoy")
    for offset, line in enumerate(body):
        if not yoy_re.search(line):
            continue
        cells = _row_values(line, (yoy_re.pattern,))
        if not cells:
            continue
        window = cells[:len(years)]
        # 增速行的百分号常写在标签里（如「同比增长率（%）」），因此只校验「单元格够数且无缺值」，
        # 不强制单元格自带 %，否则会把这类整行增速误判成错位而整行丢弃。
        if len(cells) < len(years) or any(c is None for c in window):
            notes.append("增速行单元格数与年份列不一致（或缺值），跳过增速解析以免列错位")
            continue
        above = [(off, k) for k, off in positions.items() if off < offset]
        if not above:
            continue
        if any(k == "net_profit" for _, k in above):
            owner = "net_profit"
        else:
            owner = max(above)[1]
        field_name = {"net_profit": "net_profit_yoy", "revenue": "revenue_yoy"}.get(owner)
        if not field_name:
            continue
        notes.append(f"增速行归属：按表格惯例记为「{owner}」的同比增速")
        for idx, year in enumerate(years):
            fc = (actuals if is_actual[idx] else forecasts).setdefault(year, Forecast(year=year))
            setattr(fc, field_name, cells[idx][0])                      # type: ignore[index]
    return forecasts, actuals, gaps, notes


def _extract_inline_forecast(text: str) -> tuple[list[Forecast], list[str]]:
    """行文抽取兜底：「预计 2026-2028 年归母净利润分别为 X、Y、Z 亿元」这类句子。"""
    out: list[Forecast] = []
    notes: list[str] = []
    for m in re.finditer(r"(20\d\d)\s*[-~—至]\s*(20\d\d)\s*年", text):
        y0, y1 = int(m.group(1)), int(m.group(2))
        if y1 < y0 or y1 - y0 > 6:
            continue
        window = text[m.end(): m.end() + 140]
        seg = window.split("。")[0]
        if "净利润" not in seg or "亿" not in seg:
            continue
        vals = _numbers(re.sub(r"20\d\d", " ", seg))
        if not vals:
            continue
        for year, val in zip(range(y0, y1 + 1), vals):
            out.append(Forecast(year=year, net_profit=val * 100.0))
        notes.append(f"行文抽取：{y0}-{y1} 年归母净利润（亿元→百万元），原文「{seg.strip()[:60]}」")
    uniq: dict[int, Forecast] = {}
    for fc in out:
        uniq.setdefault(fc.year, fc)
    return list(uniq.values()), notes


def extract_claims(text: str) -> Claims:
    """从研报正文自动抽取主张（best-effort；抽不到的如实进 gaps）。"""
    claims = Claims()
    claims.report_date = _find_report_date(text)
    claims.rating = _find_rating(text)
    claims.target_price = _find_target_price(text)
    claims.broker = _find_broker(text)
    claims.basis_year, claims.basis_pe, basis_sentence = _find_valuation_basis(text)
    if basis_sentence:
        claims.raw_quotes.append(f"估值锚：{basis_sentence}")

    tf, af, gaps, notes = _extract_table_forecasts(text)
    claims.forecasts, claims.actuals = tf, af
    claims.gaps.extend(gaps)
    claims.parser_notes.extend(notes)
    if not claims.forecasts:
        inline, inotes = _extract_inline_forecast(text)
        if inline:
            claims.forecasts = {fc.year: fc for fc in inline}
            claims.parser_notes.extend(inotes)

    price_line = [ln for ln in text.splitlines() if "目标价" in ln]
    if price_line:
        claims.raw_quotes.append(price_line[0].strip()[:120])
    if claims.rating:
        rating_line = [ln for ln in text.splitlines() if claims.rating in ln]
        if rating_line:
            claims.raw_quotes.append(rating_line[0].strip()[:120])

    if not claims.symbol:
        m = re.search(r"\b(\d{6}\.(?:SH|SZ|BJ))\b", text) or re.search(r"\b(\d{5}\.HK)\b", text)
        if m:
            claims.symbol = m.group(1)
    if not claims.name:
        # 「贵州茅台（600519.SH）」这类标题写法：代码前的名字就是标的名称
        m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12})\s*[（(]\s*"
                      r"(?:\d{6}\.(?:SH|SZ|BJ)|\d{5}\.HK)\s*[)）]", text)
        if m:
            claims.name = m.group(1)
    for label, value in (("标的代码", claims.symbol), ("报告日期", claims.report_date),
                         ("机构", claims.broker), ("投资评级", claims.rating),
                         ("目标价", claims.target_price), ("分年度预测", claims.forecasts)):
        if not value:
            claims.gaps.append(f"自动抽取未拿到「{label}」，需人工/LLM 补齐（--claims）")
    return claims


def load_claims_json(path: Path) -> Claims:
    """读取 --claims 的 JSON（格式见 CLAIMS_SCHEMA_DOC）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputUnavailable(f"读取 --claims 失败：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise InputUnavailable("--claims 必须是 JSON 对象（字段见脚本头部 CLAIMS_SCHEMA_DOC）")

    def _fc(item: dict, key: str = "forecasts") -> dict[int, Forecast]:
        out: dict[int, Forecast] = {}
        for raw in item.get(key) or []:
            year = raw.get("year")
            if not year:
                continue
            out[int(year)] = Forecast(
                year=int(year), revenue=raw.get("revenue"), revenue_yoy=raw.get("revenue_yoy"),
                net_profit=raw.get("net_profit"), net_profit_yoy=raw.get("net_profit_yoy"),
                eps=raw.get("eps"))
        return out

    rd = data.get("report_date")
    report_date = None
    if rd:
        m = _DATE_RE.search(str(rd))
        if m:
            try:
                report_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                report_date = None
    basis = data.get("valuation_basis") or {}
    return Claims(
        symbol=str(data.get("symbol") or ""), name=str(data.get("name") or ""),
        broker=str(data.get("broker") or ""), analyst=str(data.get("analyst") or ""),
        report_date=report_date, rating=data.get("rating"),
        target_price=data.get("target_price"),
        basis_year=basis.get("year"), basis_pe=basis.get("pe"),
        forecasts=_fc(data), actuals=_fc(data, "actuals"),
        assumptions=[str(x) for x in (data.get("assumptions") or [])],
        catalysts=[str(x) for x in (data.get("catalysts") or [])],
        risks=[str(x) for x in (data.get("risks") or [])],
        parser_notes=["关键字段来源：--claims 显式给定（人工/LLM 从报告正文抽取）"],
    )


def merge_claims(given: Claims, auto: Claims) -> Claims:
    """合并：--claims 给定的字段优先，自动抽取只补空缺。"""
    out = Claims(
        symbol=given.symbol or auto.symbol,
        name=given.name or auto.name,
        broker=given.broker or auto.broker,
        analyst=given.analyst or auto.analyst,
        report_date=given.report_date or auto.report_date,
        rating=given.rating or auto.rating,
        target_price=(given.target_price if given.target_price is not None
                      else auto.target_price),
        basis_year=given.basis_year or auto.basis_year,
        basis_pe=given.basis_pe or auto.basis_pe,
        forecasts={**auto.forecasts, **given.forecasts},
        actuals={**auto.actuals, **given.actuals},
        assumptions=given.assumptions or auto.assumptions,
        catalysts=given.catalysts or auto.catalysts,
        risks=given.risks or auto.risks,
        raw_quotes=given.raw_quotes + auto.raw_quotes,
        gaps=list(auto.gaps),
        parser_notes=given.parser_notes + auto.parser_notes,
    )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 四、解析证据文件（fetch_quotes 快照 / fetch_forecast 一致预期）
# ═══════════════════════════════════════════════════════════════════════════


def parse_snapshot(text: str) -> Snapshot:
    """解析 fetch_quotes.py --snapshot 的原样输出（金额统一换算：万元 → 亿元）。"""
    snap = Snapshot()
    for key, pat in _SNAPSHOT_PATTERNS.items():
        m = pat.search(text)
        if not m:
            continue
        if key == "as_of":
            snap.as_of = m.group(1)
        elif key == "mcap_wan":
            v = _to_float(m.group(1))
            snap.mcap_yi = round(v / 1e4, 4) if v else None
        else:
            setattr(snap, key, _to_float(m.group(1)))
    return snap


def _parse_md_table(block: list[str]) -> list[list[str]]:
    rows = []
    for line in block:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r"-+", c or "-") for c in cells):
            continue
        rows.append(cells)
    return rows


def parse_consensus(text: str) -> Consensus:
    """解析 fetch_forecast.py 输出的「净利润/每股收益一致预期」表（净利单位亿元）。"""
    cons = Consensus()
    blocks: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        m = re.match(r"【(.+?)】", line.strip())
        if m:
            current = m.group(1)
            blocks.setdefault(current, [])
            continue
        if current:
            blocks[current].append(line)
    m = re.search(r"抓取时点[：:]\s*([0-9:\- ]+)", text)
    if m:
        cons.as_of = m.group(1).strip()

    for name, key in (("净利润一致预期", "np"), ("每股收益一致预期", "eps")):
        block = next((v for k, v in blocks.items() if name in k), None)
        if not block:
            continue
        for cells in _parse_md_table(block):
            if len(cells) < 4 or not re.match(r"^20\d\d", cells[0]):
                continue
            year = int(cells[0][:4])
            mean = _to_float(cells[3])
            if mean is None:
                continue
            if key == "np":
                cons.np_mean_yi[year] = mean
                cons.np_inst_count[year] = cells[1] if len(cells) > 1 else ""
            else:
                cons.eps_mean[year] = mean
    return cons


# ═══════════════════════════════════════════════════════════════════════════
# 五、批判性核查：只找证据；拿不出证据的一律不动数字
# ═══════════════════════════════════════════════════════════════════════════


def _effective_eps(claims: Claims, snap: Optional[Snapshot],
                   year: Optional[int]) -> tuple[Optional[float], str]:
    """取某年「可用 EPS」：报告 EPS 与「净利 ÷ 股本」矛盾超阈值时，按证据等级改用推算值。

    与 A3 同一套规则——EPS 与净利/股本严重冲突时，净利（表格核心口径）+ 股本是更硬的证据，
    否则会拿一个自相矛盾的数字去复算目标价（用错的数字验证错的数字）。
    """
    fc = claims.forecast_for(year)
    if fc is None or fc.eps is None:
        return None, ""
    shares = snap.shares_yi if snap else None
    if not (shares and fc.net_profit):
        return fc.eps, "报告 EPS"
    derived = (fc.net_profit / 100.0) / shares
    if derived and abs(fc.eps - derived) / abs(derived) > EPS_FALSIFY_TOL:
        return (round(derived, 4),
                f"净利÷股本推算（报告 EPS {fc.eps:g} 与净利矛盾，已按证据等级改用推算值）")
    return fc.eps, "报告 EPS"


def critique(claims: Claims, snap: Optional[Snapshot], cons: Optional[Consensus],
             as_of: date) -> tuple[list[Finding], list[Override], set[str]]:
    """核查报告主张。返回（发现, 替代, 作废字段集）。

    level 语义：
      falsified —— 拿到了证伪证据（内部矛盾 / 与实测冲突），按证据等级规则处置；
      suspicious —— 存疑但拿不出反证，**登记不改数**（默认继续采信研报）；
      note      —— 信息性说明（含缺口）。
    """
    findings: list[Finding] = []
    overrides: list[Override] = []
    rejected: set[str] = set()

    def add(code: str, level: str, subject: str, detail: str,
            evidence: str, action: str) -> None:
        findings.append(Finding(code, level, subject, detail, evidence, action))

    # ── A1 目标价 与 自述估值锚（EPS × 给予PE）是否自洽 ──────────────────────
    if claims.target_price is None:
        add("A1", "note", "目标价", "报告未给出可抽取的目标价", "字段缺失",
            "缺口登记，不参与目标价空间计算")
    elif claims.basis_pe and claims.basis_year:
        eps, eps_src = _effective_eps(claims, snap, claims.basis_year)
        if eps:
            implied = round(eps * claims.basis_pe, 2)
            dev = abs(claims.target_price - implied) / implied if implied else 0.0
            if dev > TARGET_PRICE_TOL:
                add("A1", "falsified", "目标价",
                    f"报告目标价 {claims.target_price:g} 元，按其自述锚（{claims.basis_year}E "
                    f"EPS {eps:g} 元 × {claims.basis_pe:g} 倍 PE）重算只有 {implied:g} 元，"
                    f"偏差 {dev * 100:.1f}%",
                    f"同一份报告内数字互相矛盾（算术不自洽）；复算所用 EPS 来源：{eps_src}",
                    f"目标价改用报告自身锚重算值 {implied:g} 元；原目标价作废")
                overrides.append(Override(None, "target_price", implied,
                                          f"{claims.basis_year}E EPS × {claims.basis_pe:g}x 重算"
                                          f"（{eps_src}）"))
            else:
                add("A1", "note", "目标价",
                    f"目标价 {claims.target_price:g} 元 ≈ 自述锚重算值 {implied:g} 元"
                    f"（偏差 {dev * 100:.1f}%，在容差内；EPS 来源：{eps_src}）",
                    "报告内部自洽", "维持采信")
        else:
            add("A1", "note", "目标价",
                f"报告自述锚为 {claims.basis_year} 年 {claims.basis_pe:g} 倍 PE，"
                "但该年 EPS 未能抽取/推算，无法复算", "缺该年 EPS",
                "不证伪，目标价维持采信")
    else:
        add("A1", "note", "目标价", "报告未自述估值锚（如「给予 20XX 年 N 倍 PE」），无法复算",
            "缺锚", "不证伪，目标价维持采信")

    # ── A1b 目标价 与 评级方向是否矛盾 ──────────────────────────────────────
    if claims.rating and claims.target_price is not None and snap and snap.price:
        up = (claims.target_price / snap.price - 1) * 100
        if claims.rating in BULLISH_RATINGS and claims.target_price <= snap.price:
            add("A1b", "falsified", "评级/目标价",
                f"评级「{claims.rating}」但目标价 {claims.target_price:g} 元不高于现价 "
                f"{snap.price:g} 元（空间 {up:+.1f}%）",
                "评级方向与目标价自相矛盾（同一份报告内冲突）",
                "目标价作废，不用于估值方向；评级保留但标注矛盾")
            rejected.add("target_price")
        elif claims.rating in BEARISH_RATINGS and claims.target_price > snap.price:
            add("A1b", "falsified", "评级/目标价",
                f"评级「{claims.rating}」但目标价 {claims.target_price:g} 元高于现价 "
                f"{snap.price:g} 元（空间 {up:+.1f}%）",
                "评级方向与目标价自相矛盾", "目标价作废，不用于估值方向")
            rejected.add("target_price")
        else:
            add("A1b", "note", "评级/目标价",
                f"评级「{claims.rating}」与目标价空间 {up:+.1f}% 方向一致", "自洽", "维持采信")
    elif claims.rating and claims.target_price is not None and not (snap and snap.price):
        add("A1b", "note", "评级/目标价", "无行情快照（现价缺失），无法校验评级与目标价方向",
            "缺现价", "补 --snapshot-file 后可校验")

    # ── A2 分年度预测：行文增速 vs 表格绝对值复算 ────────────────────────────
    for year in sorted(claims.forecasts):
        fc = claims.forecasts[year]
        base = claims.base_for(year - 1)
        for field_name, yoy_name, label in (("net_profit", "net_profit_yoy", "归母净利润"),
                                            ("revenue", "revenue_yoy", "营业收入")):
            cur_v = getattr(fc, field_name)
            stated = getattr(fc, yoy_name)
            base_v = getattr(base, field_name) if base else None
            if cur_v is None or stated is None or not base_v:
                continue
            computed = round((cur_v / base_v - 1) * 100, 1)
            if abs(computed - stated) > GROWTH_TOL_PCT:
                add("A2", "falsified", f"{year}E {label}增速",
                    f"报告行文写 {stated:g}%，按表格绝对值复算（{base_v:g} → {cur_v:g}）为 "
                    f"{computed:g}%，偏差 {abs(computed - stated):.1f} 个百分点",
                    "报告内部矛盾：表格绝对数 vs 行文增速",
                    f"增速改用表格绝对值复算值 {computed:g}%（证据等级：表格数 > 措辞）")
                overrides.append(Override(year, yoy_name, computed,
                                          "由表格绝对值复算（表格数优先于行文措辞）"))
            else:
                add("A2", "note", f"{year}E {label}增速",
                    f"行文 {stated:g}% ≈ 复算 {computed:g}%（偏差在 {GROWTH_TOL_PCT:g} 个百分点内）",
                    "自洽", "维持采信")
            if abs(stated) > GROWTH_OUTLIER_PCT:
                add("A2b", "suspicious", f"{year}E {label}增速",
                    f"报告增速 {stated:g}% 属异常区间（|增速| > {GROWTH_OUTLIER_PCT:g}%）",
                    "无外部证据可证伪", "登记疑点（核对是否含一次性因素/并表），数字维持采信")

    # ── A3 EPS 与 净利÷股本 是否互相印证（需行情快照推股本） ────────────────
    shares = snap.shares_yi if snap else None
    if shares and shares > 0:
        for year in sorted(claims.forecasts):
            fc = claims.forecasts[year]
            if fc.eps is None or fc.net_profit is None:
                continue
            derived = round((fc.net_profit / 100.0) / shares, 4)
            if not derived:
                continue
            dev = abs(fc.eps - derived) / abs(derived)
            if dev > EPS_FALSIFY_TOL:
                add("A3", "falsified", f"{year}E EPS",
                    f"报告 EPS {fc.eps:g} 元，按「净利 {fc.net_profit:g} 百万元 ÷ 股本 "
                    f"{shares:.4g} 亿股」推算为 {derived:g} 元，偏差 {dev * 100:.1f}%",
                    "同一份报告内 EPS 与净利/股本互相矛盾（股本或摊薄口径冲突）",
                    "报告 EPS 作废；估值改用净利+市值口径（不受股本口径影响）")
                rejected.add("eps")
            elif dev > EPS_SUSPECT_TOL:
                add("A3", "suspicious", f"{year}E EPS",
                    f"报告 EPS {fc.eps:g} 元 vs 净利/股本推算 {derived:g} 元，偏差 "
                    f"{dev * 100:.1f}%",
                    "可能为摊薄/加权股本口径差异，无证据证伪",
                    "登记口径疑点，EPS 维持采信（估值主口径走市值+净利，不受影响）")
            else:
                add("A3", "note", f"{year}E EPS",
                    f"EPS {fc.eps:g} 元 ≈ 净利/股本推算 {derived:g} 元（偏差 {dev * 100:.1f}%）",
                    "互相印证", "维持采信")
    else:
        add("A3", "note", "EPS 交叉核对",
            "缺行情快照（现价/总市值），无法用股本交叉核对 EPS",
            "缺证据", "不证伪；补 --snapshot-file 可核对")

    # ── A4 预测 vs 已披露实际值（同类年度冲突 → 实测优先） ──────────────────
    for year in sorted(set(claims.forecasts) & set(claims.actuals)):
        f_fc, a_fc = claims.forecasts[year], claims.actuals[year]
        for field_name, label in (("net_profit", "归母净利润"), ("revenue", "营业收入"),
                                  ("eps", "EPS")):
            pv, av = getattr(f_fc, field_name), getattr(a_fc, field_name)
            if pv is None or av is None or not av:
                continue
            dev = abs(pv - av) / abs(av)
            if dev > ACTUAL_TOL:
                add("A4", "falsified", f"{year} {label}",
                    f"报告同时给出预测 {pv:g} 与实际 {av:g}，偏差 {dev * 100:.1f}%",
                    "实测值优先：已披露实际值与预测口径冲突",
                    f"该年采用实际值 {av:g}（证据等级最高）")
                overrides.append(Override(year, field_name, av, "已披露实际值优先（实测 > 预测）"))

    # ── A5 报告新鲜度：偏旧只登记疑点，不改数字 ─────────────────────────────
    if claims.report_date:
        age = (as_of - claims.report_date).days
        if age > STALE_DAYS:
            add("A5", "suspicious", "报告新鲜度",
                f"报告日期 {claims.report_date.isoformat()}，距 {as_of.isoformat()} 有 {age} 天",
                "无证据证伪预测本身（可能已过预测时点或被后续上/下修覆盖）",
                "登记预测滞后疑点；数字仍采信，结论附「需核对是否有更新报告/上修下修」")
        else:
            add("A5", "note", "报告新鲜度",
                f"报告日期 {claims.report_date.isoformat()}（{age} 天内）", "时效正常", "维持采信")
    else:
        add("A5", "note", "报告新鲜度", "未抽到报告日期，时效无法判断", "字段缺失", "缺口登记")

    # ── A6 与机构一致预期的偏离：只做差异分析，绝不据此改研报数字 ───────────
    if cons and cons.np_mean_yi:
        for year in sorted(set(claims.forecasts) & set(cons.np_mean_yi)):
            np_report_yi = (claims.forecasts[year].net_profit or 0) / 100.0
            mean = cons.np_mean_yi[year]
            if not np_report_yi or not mean:
                continue
            gap = (np_report_yi - mean) / mean
            inst = cons.np_inst_count.get(year, "")
            if abs(gap) > CONSENSUS_GAP_TOL:
                add("A6", "suspicious", f"{year}E 净利 vs 一致预期",
                    f"报告 {np_report_yi:.2f} 亿元 vs 一致预期均值 {mean:.2f} 亿元"
                    f"（覆盖机构 {inst or '未知'}），偏离 {gap * 100:+.1f}%",
                    "一致预期不是证伪证据（机构同样可能错）",
                    "登记分歧并做差异分析（预期透支/预测滞后/覆盖不足/年内进度不符/"
                    "口径差异/独立判断），研报数字维持采信")
            else:
                add("A6", "note", f"{year}E 净利 vs 一致预期",
                    f"报告 {np_report_yi:.2f} 亿元 vs 一致预期 {mean:.2f} 亿元"
                    f"（偏离 {gap * 100:+.1f}%）", "与共识基本一致", "维持采信")
    else:
        add("A6", "note", "一致预期对照", "没有机构一致预期数据（未提供 --consensus-file，"
            "或该标的无覆盖）", "缺证据", "登记数据缺口，不做分歧判断")

    # ── A7 缺口登记（缺字段不阻断流程，但必须如实列出） ─────────────────────
    missing = []
    if not claims.forecasts:
        missing.append("分年度盈利预测")
    if claims.target_price is None:
        missing.append("目标价")
    if not claims.rating:
        missing.append("投资评级")
    if not (snap and snap.price):
        missing.append("现价（--snapshot-file）")
    if missing:
        add("A7", "note", "数据缺口", "缺：" + "、".join(missing),
            "抽取/取数未覆盖", "结论将限定在可用字段内，缺口如实标注")
    return findings, overrides, rejected


# ═══════════════════════════════════════════════════════════════════════════
# 六、采信账本：默认采信，仅证据证伪处替换或作废
# ═══════════════════════════════════════════════════════════════════════════


def adopt(claims: Claims, findings: list[Finding], overrides: list[Override],
          rejected: set[str]) -> Adoption:
    """把核查结论落到「工作假设」上。

    - 无证伪 → 原样采信（assumed）；
    - 有证伪且有替代值 → 用替代值（overridden）；
    - 有证伪但无法替代 → 该主张作废（rejected），**不进估值**；
    - suspicious → 仍采信，只加 caveat（这是本工具区别于「一律怀疑」的关键）。
    """
    adoption = Adoption()
    adoption.forecasts = {y: fc.copy() for y, fc in claims.forecasts.items()}
    for ov in overrides:
        if ov.year is None:
            continue
        fc = adoption.forecasts.get(ov.year)
        if fc is not None:
            setattr(fc, ov.field, ov.value)
    adoption.overrides = overrides
    if "eps" in rejected:
        for fc in adoption.forecasts.values():
            fc.eps = None
        adoption.rejections.append("报告 EPS（与净利/股本矛盾，无替代值时不参与估值）")

    tp_override = next((o for o in overrides if o.field == "target_price"), None)
    if "target_price" in rejected:
        # 评级与目标价方向矛盾：目标价文字不可信；若同时算出了自述锚重算值就改用它
        adoption.rejections.append(
            "研报目标价（与评级方向自相矛盾，无法裁决；"
            + ("改用自述锚重算值" if tp_override else "作废，不进估值") + "）")
    if tp_override is not None:
        adoption.target_price = float(tp_override.value)          # type: ignore[arg-type]
        adoption.target_price_status = "overridden"
    elif "target_price" in rejected:
        adoption.target_price = None
        adoption.target_price_status = "rejected"
    elif claims.target_price is not None:
        adoption.target_price = claims.target_price
        adoption.target_price_status = "assumed"
    else:
        adoption.target_price_status = "missing"

    adoption.rating = claims.rating
    adoption.rating_status = "assumed" if claims.rating else "missing"
    adoption.caveats = [f"[{f.code}] {f.subject}：{f.detail}"
                        for f in findings if f.level == "suspicious"]
    return adoption


# ═══════════════════════════════════════════════════════════════════════════
# 七、估值桥：只在采信后的预测上做判断
# ═══════════════════════════════════════════════════════════════════════════


def _pick_forward_year(adoption: Adoption, claims: Claims, as_of: date) -> Optional[int]:
    """前瞻年：优先与**研报自述估值锚年**一致（用研报自己的口径判断，别偷换年份）；
    否则取不早于基准日的最早预测年（≈FY1）；再否则取最后一个有净利的预测年。"""
    years = sorted(y for y, fc in adoption.forecasts.items() if fc.net_profit)
    if not years:
        return None
    if claims.basis_year in years:
        return claims.basis_year
    future = [y for y in years if y >= as_of.year]
    return future[0] if future else years[-1]


def build_bridge(adoption: Adoption, claims: Claims, snap: Optional[Snapshot],
                 cons: Optional[Consensus], pe_band: tuple[float, float],
                 as_of: date) -> BridgeResult:
    """估值桥：用采信后的预测算前瞻PE / PEG / 合理价区间 / 安全边际 / 现价隐含要求。"""
    r = BridgeResult()
    price = snap.price if snap else None
    year = _pick_forward_year(adoption, claims, as_of)
    r.forward_year = year
    pe_low, pe_high = pe_band
    if year:
        r.facts.append(f"前瞻年取 {year}E："
                       + ("与研报自述估值锚年一致（沿用研报口径）" if year == claims.basis_year
                          else "不早于基准日的最早预测年（FY1）"))
    else:
        r.facts.append(f"合理价口径：采信后净利预测缺失，无法建立估值桥"
                       f"（合理PE 区间 {pe_low:g}–{pe_high:g} 倍）")

    if snap and snap.mcap_yi:
        # 市值口径最稳：总市值 ÷ 净利，不受股本/摊薄口径差异影响；逐年给出便于看 FY1/FY2
        for yr in sorted(adoption.forecasts):
            np_yi = (adoption.forecasts[yr].net_profit or 0) / 100.0
            if np_yi > 0:
                r.pe_by_year[yr] = round(snap.mcap_yi / np_yi, 2)
        if year and year in r.pe_by_year:
            r.forward_pe = r.pe_by_year[year]
            np_year = (adoption.forecasts[year].net_profit or 0) / 100.0
            r.facts.append(f"前瞻PE（市值口径）= 总市值 {snap.mcap_yi:g} 亿元 ÷ {year}E 净利 "
                           f"{np_year:.2f} 亿元 = {r.forward_pe:g} 倍")

    fc = adoption.forecasts.get(year) if year else None
    if fc:
        r.forward_eps = fc.eps
        r.growth_pct = fc.net_profit_yoy

    if r.forward_pe is None and price and fc and fc.eps:
        r.forward_pe = round(price / fc.eps, 2)
        r.facts.append(f"前瞻PE（EPS 口径，缺总市值时的退路）= 现价 {price:g} 元 ÷ "
                       f"{year}E EPS {fc.eps:g} 元 = {r.forward_pe:g} 倍")

    if price and r.forward_pe:
        # 合理价 = 现价 × 合理PE ÷ 前瞻PE（等价于 合理PE × 采信EPS，但不受股本口径影响）
        r.fair_low = round(price * pe_low / r.forward_pe, 2)
        r.fair_high = round(price * pe_high / r.forward_pe, 2)
        r.margin_of_safety_pct = round((1 - r.forward_pe / pe_low) * 100, 1)
        r.required_growth_pct = r.forward_pe

    if r.forward_pe and r.growth_pct and r.growth_pct > 0:
        r.peg = round(r.forward_pe / r.growth_pct, 2)
    elif r.forward_pe:
        r.notes.append("PEG 不适用：采信后该年净利增速缺失或非正")

    if adoption.target_price and price:
        r.target_upside_pct = round((adoption.target_price / price - 1) * 100, 1)
    if claims.basis_pe and claims.basis_year:
        base_fc = adoption.forecasts.get(claims.basis_year) or claims.forecast_for(
            claims.basis_year)
        src_eps = base_fc.eps if base_fc else None
        if src_eps is None:
            shares = snap.shares_yi if snap else None
            np_m = base_fc.net_profit if base_fc else None
            if shares and np_m:
                src_eps = round((np_m / 100.0) / shares, 4)
        if src_eps:
            r.anchor_price = round(src_eps * claims.basis_pe, 2)
            r.facts.append(f"报告自述锚复核：{claims.basis_year}E EPS {src_eps:g} × "
                           f"{claims.basis_pe:g} 倍 = {r.anchor_price:g} 元")

    if cons and cons.np_mean_yi:
        for year_i in sorted(set(adoption.forecasts) & set(cons.np_mean_yi)):
            np_report = (adoption.forecasts[year_i].net_profit or 0) / 100.0
            mean = cons.np_mean_yi[year_i]
            if mean:
                r.consensus_gap[year_i] = round((np_report - mean) / mean * 100, 1)
        if snap and snap.mcap_yi and year and cons.np_mean_yi.get(year):
            r.consensus_forward_pe = round(snap.mcap_yi / cons.np_mean_yi[year], 2)
            r.facts.append(f"按一致预期算的前瞻PE（{year}E）= 总市值 {snap.mcap_yi:g} 亿元 ÷ "
                           f"一致预期 {cons.np_mean_yi[year]:.2f} 亿元 = "
                           f"{r.consensus_forward_pe:g} 倍")

    # ── 方向性结论 ─────────────────────────────────────────────────────────
    # 先声明倍数是个显式假设：默认区间是通用观察区间，不是针对该标的的「正确倍数」。
    r.notes.append(
        f"合理PE 区间 {pe_low:g}–{pe_high:g} 倍是**通用观察区间**（默认对齐 "
        "config/settings.yaml 的 valuation.pe_band，未做质量溢价 / 行业β 校准）。"
        "高质量（高 ROE、现金流转化好）标的通常享有质量溢价，周期/高β标的应压低倍数；"
        "要改口径就用 --pe-band 传入自己的区间——把倍数当显式假设，别默认接受。")
    if price is None:
        r.verdict = "缺现价（未提供 --snapshot-file），只能给出估值口径与合理价区间，无法判断贵/便宜"
    elif r.forward_pe is None:
        r.verdict = "采信后的预测不足以算出前瞻PE，无法给出方向性结论（见缺口）"
    elif r.forward_pe < pe_low:
        r.verdict = (f"便宜：前瞻PE {r.forward_pe:g} 倍已低于保守倍数 {pe_low:g} 倍，"
                     f"现价 {price:g} 元相对保守合理价 {r.fair_low:g} 元有 "
                     f"{abs(r.margin_of_safety_pct):g}% 折价")
    elif r.forward_pe <= pe_high:
        r.verdict = (f"合理：前瞻PE {r.forward_pe:g} 倍落在通用区间 "
                     f"{pe_low:g}–{pe_high:g} 倍内，合理价 {r.fair_low:g}–{r.fair_high:g} 元"
                     f"（现价 {price:g} 元）")
    else:
        r.verdict = (f"贵：前瞻PE {r.forward_pe:g} 倍已超中性倍数 {pe_high:g} 倍，"
                     f"现价 {price:g} 元高于中性合理价 {r.fair_high:g} 元 "
                     f"{abs(r.margin_of_safety_pct if r.margin_of_safety_pct is not None else 0):g}%")
    if r.forward_pe and r.consensus_forward_pe:
        r.notes.append(f"研报口径 vs 市场口径：按研报采信预测算前瞻PE {r.forward_pe:g} 倍，"
                       f"按机构一致预期算 {r.consensus_forward_pe:g} 倍——"
                       "两者差异即「研报相对共识的乐观程度」，不构成对任一方的证伪。")

    if r.required_growth_pct:
        r.notes.append(f"现价隐含要求：按 PEG=1 口径，现价对应的前瞻PE {r.required_growth_pct:g} 倍"
                       f"意味着市场已要求净利增速维持在约 {r.required_growth_pct:g}% 以上"
                       "（低于此则 PEG>1、性价比转差）")
    r.risks.extend(claims.risks)
    r.risks.extend(adoption.rejections)
    r.risks.extend(adoption.caveats)
    return r


# ═══════════════════════════════════════════════════════════════════════════
# 八、渲染与落盘
# ═══════════════════════════════════════════════════════════════════════════


def _fmt(v, digits: int = 2, suffix: str = "") -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}{suffix}"
    return f"{v}{suffix}"


def _slug(claims: Claims, source: Source) -> str:
    raw = claims.symbol or claims.name or Path(source.ref).stem or "report"
    return re.sub(r"[^\w\-.]", "_", raw)[:40] or "report"


def render_markdown(claims: Claims, source: Source, findings: list[Finding],
                    adoption: Adoption, bridge: BridgeResult, snap: Optional[Snapshot],
                    cons: Optional[Consensus], as_of: date) -> str:
    lines: list[str] = []
    title = claims.name or claims.symbol or source.title or "未识别标的"
    lines.append(f"# 研报阅读报告：{title}")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                 f"（分析基准日 {as_of.isoformat()}）")
    lines.append(f"- 研报入口：{source.kind} · {source.ref}")
    lines.append(f"- 研报要素：机构 {claims.broker or '-'} / 分析师 {claims.analyst or '-'} / "
                 f"日期 {claims.report_date.isoformat() if claims.report_date else '-'} / "
                 f"评级 {claims.rating or '-'} / 目标价 "
                 f"{_fmt(claims.target_price)} 元")
    lines.append(f"- 数据时点：行情快照 {(snap.as_of if snap else '') or '-'}；"
                 f"一致预期 {(cons.as_of if cons else '') or '-'}")
    lines.append("")
    lines.append("> 阅读原则：**批判性阅读不等于否决研报**。核查只找证据——"
                 "有证据证伪的（内部算术矛盾 / 与实测冲突 / 评级与目标价矛盾）才替换或作废；"
                 "拿不出证据的一律**默认采信研报估算**，疑点只登记不改数。"
                 "下文估值结论全部建立在「采信后的预测」之上。")
    lines.append("")

    lines.append("## 一、采信账本（带进估值的数字）")
    lines.append("")
    lines.append("| 主张 | 采信值 | 状态 | 依据 |")
    lines.append("|---|---|---|---|")
    for row in adoption.ledger:
        lines.append(f"| {row['claim']} | {_fmt(row['value'])} | {row['status']} | {row['basis']} |")
    status_map = {"assumed": "采信研报原值", "overridden": "按证据替换",
                  "rejected": "作废（不进估值）", "missing": "缺口"}
    lines.append(f"| 投资评级 | {adoption.rating or '-'} | {adoption.rating_status}"
                 f"（{status_map.get(adoption.rating_status, '')}） | 评级文字（证据等级最低） |")
    lines.append("")
    if adoption.overrides:
        lines.append("**证据替换记录**")
        lines.append("")
        for ov in adoption.overrides:
            where = f"{ov.year} " if ov.year else ""
            lines.append(f"- {where}{ov.field} → {_fmt(ov.value)}：{ov.why}")
        lines.append("")
    if adoption.rejections:
        lines.append("**作废主张（不带进估值）**")
        lines.append("")
        for item in adoption.rejections:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## 二、批判性核查发现")
    lines.append("")
    lines.append("| 编号 | 级别 | 对象 | 证据/细节 | 处置 |")
    lines.append("|---|---|---|---|---|")
    level_cn = {"falsified": "证伪", "suspicious": "存疑（不改数）", "note": "说明"}
    for f in findings:
        lines.append(f"| {f.code} | {level_cn.get(f.level, f.level)} | {f.subject} | "
                     f"{f.detail} | {f.action} |")
    lines.append("")

    lines.append("## 三、采信后的盈利预测")
    lines.append("")
    lines.append("| 年度 | 营收(百万元) | 营收增速 | 归母净利(百万元) | 净利增速 | EPS(元) | 来源 |")
    lines.append("|---|---|---|---|---|---|---|")
    for yr in sorted(adoption.forecasts):
        fc = adoption.forecasts[yr]
        srcs = []
        for ov in adoption.overrides:
            if ov.year == yr:
                srcs.append(f"{ov.field} 已按证据替换")
        lines.append(f"| {yr}E | {_fmt(fc.revenue)} | {_fmt(fc.revenue_yoy, 1, '%')} | "
                     f"{_fmt(fc.net_profit)} | {_fmt(fc.net_profit_yoy, 1, '%')} | "
                     f"{_fmt(fc.eps)} | {'；'.join(srcs) if srcs else '研报估算（默认采信）'} |")
    for yr in sorted(claims.actuals):
        fc = claims.actuals[yr]
        lines.append(f"| {yr}A（实际） | {_fmt(fc.revenue)} | - | {_fmt(fc.net_profit)} | - | "
                     f"{_fmt(fc.eps)} | 报告已披露实际值 |")
    lines.append("")

    lines.append("## 四、估值桥（建立在采信后的预测上）")
    lines.append("")
    lines.append(f"- 前瞻年：{bridge.forward_year or '-'}"
                 f"；前瞻EPS：{_fmt(bridge.forward_eps)} 元"
                 f"；前瞻PE：{_fmt(bridge.forward_pe)} 倍"
                 f"（按一致预期：{_fmt(bridge.consensus_forward_pe)} 倍）")
    lines.append(f"- PEG：{_fmt(bridge.peg)}（净利增速 {_fmt(bridge.growth_pct, 1, '%')}）")
    lines.append(f"- 研报目标价空间：{_fmt(bridge.target_upside_pct, 1, '%')}"
                 f"（采信后目标价 {_fmt(adoption.target_price)} 元，状态 {adoption.target_price_status}）")
    lines.append(f"- 报告自述锚复核价：{_fmt(bridge.anchor_price)} 元")
    lines.append(f"- 我的合理价区间（采信预测 × 合理PE 区间）："
                 f"{_fmt(bridge.fair_low)} – {_fmt(bridge.fair_high)} 元"
                 f"；相对保守合理价的溢/折价 {_fmt(bridge.margin_of_safety_pct, 1, '%')}"
                 "（负值＝现价高于保守合理价）")
    lines.append(f"- 现价隐含要求（PEG=1 口径）：净利增速需约 {_fmt(bridge.required_growth_pct, 1, '%')}")
    lines.append(f"- **方向结论：{bridge.verdict}**")
    if bridge.pe_by_year:
        lines.append("")
        lines.append("| 预测年度 | 采信净利(亿元) | 前瞻PE(市值口径) |")
        lines.append("|---|---|---|")
        for yr in sorted(bridge.pe_by_year):
            mark = "（前瞻年）" if yr == bridge.forward_year else ""
            np_yi = (adoption.forecasts[yr].net_profit or 0) / 100.0
            lines.append(f"| {yr}E{mark} | {np_yi:.2f} | {bridge.pe_by_year[yr]:g} 倍 |")
    lines.append("")
    for fact in bridge.facts:
        lines.append(f"- 口径说明：{fact}")
    for note in bridge.notes:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## 五、与机构一致预期的差异")
    lines.append("")
    if bridge.consensus_gap and cons:
        lines.append("| 年度 | 研报净利(亿元) | 一致预期均值(亿元) | 偏离 |")
        lines.append("|---|---|---|---|")
        for yr in sorted(bridge.consensus_gap):
            np_report = ((adoption.forecasts[yr].net_profit or 0) / 100.0
                         if yr in adoption.forecasts else 0.0)
            lines.append(f"| {yr}E | {np_report:.2f} | {cons.np_mean_yi.get(yr, 0):.2f} | "
                         f"{bridge.consensus_gap[yr]:+.1f}% |")
        lines.append("")
        lines.append("偏离原因需人工定性：预期透支 / 预测滞后 / 覆盖不足 / 年内进度不符 / "
                     "口径差异 / 独立判断。**一致预期不是证伪证据**，分歧只登记不否决研报数字。")
    else:
        lines.append("无一致预期数据（未提供 --consensus-file 或该标的无机构覆盖）："
                     "如实登记缺口，不做分歧判断。")
    lines.append("")

    lines.append("## 六、缺口与风险")
    lines.append("")
    for gap in claims.gaps:
        lines.append(f"- 缺口：{gap}")
    for note in claims.parser_notes:
        lines.append(f"- 抽取口径：{note}")
    for quote in claims.raw_quotes:
        lines.append(f"- 原文：{quote}")
    for risk in bridge.risks:
        lines.append(f"- 风险/疑点：{risk}")
    if claims.assumptions:
        lines.append("")
        lines.append("**报告核心假设（原文摘录）**")
        lines.append("")
        for item in claims.assumptions:
            lines.append(f"- {item}")
    if claims.catalysts:
        lines.append("")
        lines.append("**催化剂**")
        lines.append("")
        for item in claims.catalysts:
            lines.append(f"- {item}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 本报告为研究用途，不构成投资建议；数据与研报内容有滞后与口径差异，"
                 "采信不等于认同，请以关键节点（财报/销售/价格/现金流）验证后再决策。")
    return "\n".join(lines)


def to_json(claims: Claims, source: Source, findings: list[Finding], adoption: Adoption,
            bridge: BridgeResult, snap: Optional[Snapshot], cons: Optional[Consensus],
            as_of: date) -> dict:
    # JSON 的键只能是字符串：年份键统一转成 "2027" 形式，避免下游一半是 int 一半是 str
    bridge_dict = bridge.to_dict()
    bridge_dict["pe_by_year"] = {str(k): v for k, v in bridge.pe_by_year.items()}
    bridge_dict["consensus_gap"] = {str(k): v for k, v in bridge.consensus_gap.items()}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": as_of.isoformat(),
        "source": {"kind": source.kind, "ref": source.ref, "title": source.title},
        "report": {
            "symbol": claims.symbol, "name": claims.name, "broker": claims.broker,
            "analyst": claims.analyst,
            "report_date": claims.report_date.isoformat() if claims.report_date else None,
            "rating": claims.rating, "target_price": claims.target_price,
            "valuation_basis": {"year": claims.basis_year, "pe": claims.basis_pe},
            "forecasts": [claims.forecasts[y].to_dict() for y in sorted(claims.forecasts)],
            "actuals": [claims.actuals[y].to_dict() for y in sorted(claims.actuals)],
            "assumptions": claims.assumptions, "catalysts": claims.catalysts,
            "risks": claims.risks, "raw_quotes": claims.raw_quotes,
        },
        "findings": [{"code": f.code, "level": f.level, "subject": f.subject,
                      "detail": f.detail, "evidence": f.evidence, "action": f.action}
                     for f in findings],
        "adoption": {
            "forecasts": [adoption.forecasts[y].to_dict() for y in sorted(adoption.forecasts)],
            "target_price": adoption.target_price,
            "target_price_status": adoption.target_price_status,
            "rating": adoption.rating, "rating_status": adoption.rating_status,
            "overrides": [{"year": o.year, "field": o.field, "value": o.value, "why": o.why}
                          for o in adoption.overrides],
            "rejections": adoption.rejections, "caveats": adoption.caveats,
        },
        "bridge": bridge_dict,
        "snapshot": ({"price": snap.price, "mcap_yi": snap.mcap_yi, "pe_ttm": snap.pe_ttm,
                      "pe_static": snap.pe_static, "pb": snap.pb, "dv_ttm": snap.dv_ttm,
                      "as_of": snap.as_of} if snap else None),
        "consensus": ({"np_mean_yi": {str(k): v for k, v in cons.np_mean_yi.items()},
                       "np_inst_count": {str(k): v for k, v in cons.np_inst_count.items()},
                       "eps_mean": {str(k): v for k, v in cons.eps_mean.items()},
                       "as_of": cons.as_of} if cons else None),
        "gaps": claims.gaps, "parser_notes": claims.parser_notes,
    }


def print_summary(claims: Claims, adoption: Adoption, bridge: BridgeResult,
                  findings: list[Finding], out_md: Optional[Path]) -> None:
    print("===== 研报阅读结果 =====")
    print(f"  标的：{claims.symbol or '-'} {claims.name or ''}"
          f"  机构：{claims.broker or '-'}  报告日期："
          f"{claims.report_date.isoformat() if claims.report_date else '-'}")
    print(f"  评级：{claims.rating or '-'}（{adoption.rating_status}）"
          f"  目标价：{_fmt(claims.target_price)} 元"
          f" → 采信值 {_fmt(adoption.target_price)} 元（{adoption.target_price_status}）")
    counts = {"falsified": 0, "suspicious": 0, "note": 0}
    for f in findings:
        counts[f.level] = counts.get(f.level, 0) + 1
    print(f"  核查：证伪 {counts.get('falsified', 0)} 条 / 存疑（不改数）"
          f" {counts.get('suspicious', 0)} 条 / 说明 {counts.get('note', 0)} 条")
    for f in findings:
        if f.level == "falsified":
            print(f"    [证伪][{f.code}] {f.subject}：{f.action}")
    for f in findings:
        if f.level == "suspicious":
            print(f"    [存疑][{f.code}] {f.subject}：{f.detail}")
    print("  ── 采信账本（带进估值） ──")
    for yr in sorted(adoption.forecasts):
        fc = adoption.forecasts[yr]
        print(f"    {yr}E 净利 {_fmt(fc.net_profit)} 百万元（增速 "
              f"{_fmt(fc.net_profit_yoy, 1, '%')}）  EPS {_fmt(fc.eps)} 元")
    print("  ── 估值桥（建立在采信后的预测上） ──")
    print(f"    前瞻PE {_fmt(bridge.forward_pe)} 倍（{bridge.forward_year or '-'}E，"
          f"按一致预期 {_fmt(bridge.consensus_forward_pe)} 倍）  PEG {_fmt(bridge.peg)}")
    print(f"    目标价空间 {_fmt(bridge.target_upside_pct, 1, '%')}"
          f"  合理价区间 {_fmt(bridge.fair_low)} – {_fmt(bridge.fair_high)} 元"
          f"  相对保守合理价 {_fmt(bridge.margin_of_safety_pct, 1, '%')}")
    print(f"    方向结论：{bridge.verdict}")
    if claims.gaps:
        print(f"  数据缺口 {len(claims.gaps)} 项：" + "；".join(claims.gaps[:4])
              + ("…" if len(claims.gaps) > 4 else ""))
    if out_md:
        print(f"\n报告文件：{out_md}")


# ═══════════════════════════════════════════════════════════════════════════
# 九、CLI
# ═══════════════════════════════════════════════════════════════════════════


def _parse_pe_band(raw: str) -> tuple[float, float]:
    parts = [p for p in re.split(r"[,，\s]+", raw or "") if p]
    if len(parts) != 2:
        raise InputUnavailable("--pe-band 需形如 8,25（与 config/settings.yaml 的 "
                               "valuation.pe_band 对齐）")
    low, high = sorted(float(p) for p in parts)
    return low, high


def _parse_as_of(raw: str) -> date:
    if not raw:
        return date.today()
    m = _DATE_RE.search(raw)
    if not m:
        raise InputUnavailable(f"--as-of 需形如 YYYY-MM-DD：{raw}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def cmd_read(args: argparse.Namespace) -> int:
    source = load_source(args.input, timeout=args.timeout)
    if args.text_out:
        out_text = Path(args.text_out)
        out_text.parent.mkdir(parents=True, exist_ok=True)
        out_text.write_text(source.text, encoding="utf-8")
        print(f"正文已落盘：{out_text}（{len(source.text)} 字符）")

    auto = extract_claims(source.text)
    claims = auto
    if args.claims:
        claims = merge_claims(load_claims_json(Path(args.claims)), auto)

    snap = None
    if args.snapshot_file:
        p = Path(args.snapshot_file)
        if not p.exists():
            raise InputUnavailable(f"行情快照文件不存在：{p}")
        snap = parse_snapshot(p.read_text(encoding="utf-8", errors="replace"))
    cons = None
    if args.consensus_file:
        p = Path(args.consensus_file)
        if not p.exists():
            raise InputUnavailable(f"一致预期文件不存在：{p}")
        cons = parse_consensus(p.read_text(encoding="utf-8", errors="replace"))

    as_of = _parse_as_of(args.as_of)
    pe_band = _parse_pe_band(args.pe_band)
    findings, overrides, rejected = critique(claims, snap, cons, as_of)
    adoption = adopt(claims, findings, overrides, rejected)
    bridge = build_bridge(adoption, claims, snap, cons, pe_band, as_of)

    md = render_markdown(claims, source, findings, adoption, bridge, snap, cons, as_of)
    payload = to_json(claims, source, findings, adoption, bridge, snap, cons, as_of)

    out_md: Optional[Path] = None
    out_json: Optional[Path] = None
    if not args.no_report:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_md = out_dir / f"report_{_slug(claims, source)}_{stamp}.md"
        out_json = out_md.with_suffix(".json")
        out_md.write_text(md, encoding="utf-8")
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(claims, adoption, bridge, findings, out_md)
        if out_json:
            print(f"结构化数据：{out_json}")
        print("\n（提示：缺口项可用 --claims 补齐后重跑；行情/一致预期证据用 --snapshot-file / "
              "--consensus-file 传入，数字不经过会话手工转抄。）")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="report_reader",
        description="研报阅读器：抽取研报主张 → 批判性核查（有证据才证伪）→ 采信账本 → "
                    "估值桥（前瞻PE/PEG/合理价/安全边际）。")
    ap.add_argument("--input", required=True,
                    help="研报入口：本地文件（.txt/.md/.html）或 http(s) 链接（PDF 需先转文本）")
    ap.add_argument("--claims", default=None,
                    help="结构化字段 JSON（覆盖自动抽取；格式见脚本头部 CLAIMS_SCHEMA_DOC）")
    ap.add_argument("--snapshot-file", default=None,
                    help="fetch_quotes.py --snapshot 的原样输出文件（取现价/总市值/PE/PB）")
    ap.add_argument("--consensus-file", default=None,
                    help="fetch_forecast.py 的原样输出文件（机构一致预期，做差异分析）")
    ap.add_argument("--as-of", default=None, help="分析基准日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--pe-band", default="8,25",
                    help="合理PE区间 low,high（默认 8,25，与 config/settings.yaml 对齐）")
    ap.add_argument("--out-dir", default="output/research", help="报告输出目录")
    ap.add_argument("--text-out", default=None, help="把抽出的正文另存到该路径（便于人工核对）")
    ap.add_argument("--no-report", action="store_true", help="只打印摘要，不落盘")
    ap.add_argument("--json", action="store_true", help="标准输出结构化 JSON（不打印摘要）")
    ap.add_argument("--timeout", type=float, default=20.0, help="URL 抓取超时（秒）")
    args = ap.parse_args(argv)
    try:
        return cmd_read(args)
    except InputUnavailable as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
