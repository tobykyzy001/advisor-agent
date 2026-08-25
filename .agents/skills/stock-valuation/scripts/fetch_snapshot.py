#!/usr/bin/env python3
"""拉取单只 A 股实时估值快照：现价 / PB / PE 三口径 / 市值 / 经营同比。

用法：python .agents/skills/stock-valuation/scripts/fetch_snapshot.py 002244 [002384 ...]

说明：
- 用标准库 urllib 且显式 ProxyHandler({}) 关闭系统代理——akshare/requests 会读 Windows
  系统代理，而该代理对东财/腾讯部分域名不可达，故本脚本绕过代理直连公开接口。
- 双源（腾讯 qt.gtimg.cn + 东财 push2.eastmoney.com）交叉核对，任一源失败不影响另一源。
- 输出仅供研究参考，不构成投资建议；数据标注接口返回的时点。
"""

from __future__ import annotations

import json
import sys
import urllib.request

# 腾讯字段按 ~ 分隔的索引（0 起）
TENCENT_FIELDS = {
    1: "名称", 3: "现价", 4: "昨收", 30: "时间", 31: "涨跌", 32: "涨跌%",
    38: "换手%", 39: "PE_TTM", 44: "流通市值亿", 45: "总市值亿", 46: "PB",
    52: "PE动", 53: "PE静",
}
# 东财字段含义
EM_FIELDS = {
    "f43": "现价", "f60": "昨收", "f116": "总市值", "f117": "流通市值",
    "f162": "PE动", "f163": "PE静", "f164": "PE_TTM", "f167": "PB",
    "f168": "换手%", "f169": "涨跌", "f170": "涨跌%",
    "f183": "营收", "f184": "营收同比%", "f185": "归母净利",
    "f186": "净利同比%", "f187": "净利率%", "f188": "毛利率%",
}


def _open_http(url: str, timeout: int = 12) -> bytes:
    """绕过系统代理直连。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return resp.read()


def fetch_tencent(code: str) -> dict:
    """腾讯实时行情快照。深市 sz / 沪市 sh。"""
    prefix = "sh" if code.startswith("6") else "sz"
    raw = _open_http(f"https://qt.gtimg.cn/q={prefix}{code}").decode("gbk", "ignore")
    parts = raw.split("~")
    out: dict[str, str] = {}
    for idx, name in TENCENT_FIELDS.items():
        if idx < len(parts):
            out[name] = parts[idx]
    if len(parts) > 30 and len(parts[30]) >= 8:
        ts = parts[30][:8]
        out["时间"] = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    return out


def fetch_eastmoney(code: str) -> dict:
    """东财个股快照，含经营同比字段。

    优先用延迟行情主机 push2delay（实测更稳定），失败退 push2。财务字段单位(元)随
    接口版本可能不一致，结果应结合逻辑自检，勿盲信。
    """
    secid = "1" if code.startswith("6") else "0"
    fields = ",".join(EM_FIELDS)
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        url = (f"https://{host}/api/qt/stock/get?secid={secid}.{code}"
               f"&invt=2&fltt=2&fields={fields}")
        try:
            data = json.loads(_open_http(url).decode("utf-8", "ignore"))["data"]
            break
        except Exception:  # noqa: BLE001 某主机不可达则换下一个
            data = None
    if data is None:
        raise RuntimeError("东财源全部不可达")
    out: dict[str, str] = {}
    for k, name in EM_FIELDS.items():
        v = data.get(k)
        if v in (None, "-", ""):
            continue
        if k in ("f116", "f117"):  # 市值(元) -> 亿
            out[name] = f"{v / 1e8:.2f}亿"
        else:
            out[name] = str(v)
    return out


def print_snapshot(code: str) -> None:
    print(f"\n===== {code} =====")
    try:
        t = fetch_tencent(code)
        print("[腾讯 qt.gtimg.cn]")
        for k in ["名称", "现价", "涨跌%", "PB", "PE_TTM", "PE动", "PE静", "总市值亿", "时间"]:
            if k in t:
                print(f"  {k:<8} {t[k]}")
    except Exception as e:  # noqa: BLE001
        print(f"  腾讯源失败: {e}")
    try:
        em = fetch_eastmoney(code)
        print("[东财 push2.eastmoney.com]")
        for k in ["现价", "涨跌%", "PB", "PE_TTM", "PE动", "PE静", "总市值", "营收", "营收同比%",
                  "归母净利", "净利同比%", "净利率%", "毛利率%"]:
            if k in em:
                print(f"  {k:<10} {em[k]}")
    except Exception as e:  # noqa: BLE001
        print(f"  东财源失败: {e}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for code in argv:
        print_snapshot(code)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
