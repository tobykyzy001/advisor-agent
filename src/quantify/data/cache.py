"""本地磁盘缓存，避免重复请求第三方数据源。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class DiskCache:
    """简单的 JSON 磁盘缓存，按 key 存储，带 TTL。"""

    def __init__(self, cache_dir: str, ttl_seconds: int = 3600) -> None:
        self.dir = Path(cache_dir)
        self.ttl = ttl_seconds
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 仅保留安全字符，避免目录穿越
        safe = "".join(c for c in key if c.isalnum() or c in "._-")
        return self.dir / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("ts", 0) > self.ttl:
            return None
        return payload.get("data")

    def set(self, key: str, value: Any) -> None:
        payload = {"ts": time.time(), "data": value}
        self._path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def clear(self, prefix: str = "") -> None:
        for p in self.dir.glob(f"{prefix}*.json"):
            p.unlink(missing_ok=True)
