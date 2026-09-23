"""JSON 快照與歷史紀錄（不用資料庫，方便 GitHub Actions 提交 / GitHub Pages 讀取）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

HISTORY_CAP = 60  # 每個商品最多保留幾個時間點


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: Any, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent, separators=(",", ":") if indent is None else None)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def update_history(history: dict[str, Any], products: list[dict[str, Any]], stamp: str, cap: int = HISTORY_CAP) -> dict[str, Any]:
    """history[code] = {"name": ..., "points": [[stamp, price, unit_cost, ref_price, roi], ...]}"""
    for p in products:
        code = p.get("code")
        if not code:
            continue
        cost = (p.get("cost") or {}).get("unit")
        ref = (p.get("shopee") or {}).get("ref_price")
        roi = (p.get("eval") or {}).get("roi")
        entry = history.setdefault(code, {"name": p.get("name"), "points": []})
        entry["name"] = p.get("name") or entry.get("name")
        pts = entry["points"]
        point = [stamp, p.get("price"), cost, ref, roi]
        if pts and pts[-1][1:] == point[1:]:
            pts[-1][0] = stamp  # 沒變就只更新時間
        else:
            pts.append(point)
        if len(pts) > cap:
            del pts[: len(pts) - cap]
    return history
