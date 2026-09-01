"""观察仓清单：W底放量筛选的标的池。

清单文件位于 output/watchlist/watchlist.yaml（已被 .gitignore 忽略，不入库，
含个人关注信息）。清单**模板文件的唯一真源在 workspace-init 技能**
（`src/workspace-init/init_workspace.py` 的 WATCHLIST_YAML），由 `workspace-init`
初始化工作区时生成；本模块**只负责读取**，不生成、不写模板。

清单格式：
    watchlist:
      - ts_code: "600519.SH"
        name: "贵州茅台"
        market: "A"
        note: "举例"

字段说明：
- ts_code：tushare 代码（A股带 .SH/.SZ/.BJ，港股如 00700.HK）。
- name：可选，便于报告展示；缺失时留空。
- market：A | HK，默认 A。
- note：可选备注，纯展示。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class WatchItem:
    """观察仓清单中的一条标的。"""

    ts_code: str
    name: str = ""
    market: str = "A"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "market": self.market,
            "note": self.note,
        }


def default_path() -> Path:
    return Path("output/watchlist/watchlist.yaml")


def load_watchlist(path: Path | None = None) -> list[WatchItem]:
    """读取观察仓清单。

    清单文件缺失时抛 FileNotFoundError，提示先用 workspace-init 初始化工作区
    （模板唯一真源在 init_workspace.py，本函数不代写模板、不自动生成）。
    """
    p = path or default_path()
    if not p.exists():
        raise FileNotFoundError(
            f"观察仓清单不存在：{p}。请先运行 workspace-init 技能初始化工作区"
            f"（`python src/workspace-init/init_workspace.py`），再编辑该文件。"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("watchlist") or []
    items: list[WatchItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ts_code = str(entry.get("ts_code", "")).strip()
        if not ts_code:
            continue
        items.append(
            WatchItem(
                ts_code=ts_code,
                name=str(entry.get("name", "") or ""),
                market=str(entry.get("market", "A") or "A"),
                note=str(entry.get("note", "") or ""),
            )
        )
    return items


def save_watchlist(items: list[WatchItem], path: Path | None = None) -> None:
    """把清单回写为 YAML（不含注释，仅数据）。"""
    p = path or default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"watchlist": [it.to_dict() for it in items]}
    p.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )