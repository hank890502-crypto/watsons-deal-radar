"""本機模式：FastAPI 提供靜態網頁 + 互動 API（掃描、設定、單品查價、比對管理、通知測試）。

  python -m radar serve  →  http://127.0.0.1:8765
GitHub Pages 模式只有靜態檔，沒有這些 API；前端會自動偵測（/api/status）。
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import __version__
from .alerts import Notifier, format_text, now_iso
from .config import DATA_DIR, WEB_DATA_DIR, WEB_DIR, load_all, load_config, notify_settings, save_config
from .matching import clean_keyword, pick_reference
from .pipeline import ScanOptions, run
from .shopee import make_provider
from .storage import read_json, write_json

app = FastAPI(title="屈臣氏優惠雷達", version=__version__)
_state: dict[str, Any] = {"running": False, "last": None, "error": None, "log": []}
_lock = threading.Lock()


@app.get("/api/status")
def status() -> dict[str, Any]:
    latest = read_json(WEB_DATA_DIR / "latest.json", {}) or {}
    return {
        "mode": "local",
        "version": __version__,
        "running": _state["running"],
        "last": _state["last"],
        "error": _state["error"],
        "generated_at": latest.get("generated_at"),
        "notify_channels": Notifier(notify_settings()).channels(),
    }


@app.post("/api/scan")
def scan(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    with _lock:
        if _state["running"]:
            return {"ok": False, "message": "掃描進行中"}
        _state["running"] = True
        _state["error"] = None

    def worker() -> None:
        try:
            summary = run(
                ScanOptions(
                    shopee=body.get("shopee", "biggo"),
                    max_promos=body.get("max_promos"),
                    max_pages=body.get("max_pages"),
                    max_lookups=body.get("max_lookups"),
                    notify=bool(body.get("notify", True)),
                )
            )
            _state["last"] = {"at": now_iso(), **summary}
        except Exception as e:  # noqa: BLE001
            _state["error"] = str(e)
        finally:
            _state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return load_all()


@app.get("/api/config/{name}")
def get_config_one(name: str) -> dict[str, Any]:
    if name not in ("fees", "promotions", "cards", "matches"):
        raise HTTPException(404)
    return load_config(name)


@app.put("/api/config/{name}")
def put_config(name: str, body: dict[str, Any]) -> dict[str, Any]:
    if name not in ("fees", "promotions", "cards", "matches"):
        raise HTTPException(404)
    path = save_config(name, body)
    return {"ok": True, "path": str(path)}


@app.post("/api/match")
def set_match(body: dict[str, Any]) -> dict[str, Any]:
    """人工比對：{"code": "BP_x", "ref_price": 189, "note": "", "exclude_ids": [...], "keyword": "..."}；ref_price=null 表示清除。"""
    code = body.get("code")
    if not code:
        raise HTTPException(400, "code required")
    m = load_config("matches")
    ov = m.setdefault("overrides", {})
    cur = ov.get(code, {})
    for k in ("ref_price", "note", "exclude_ids", "keyword"):
        if k in body:
            if body[k] in (None, "", []):
                cur.pop(k, None)
            else:
                cur[k] = body[k]
    if cur:
        ov[code] = cur
    else:
        ov.pop(code, None)
    save_config("matches", m)
    # 同步更新 latest.json 裡的該商品，讓前端立即看到
    latest = read_json(WEB_DATA_DIR / "latest.json", None)
    if latest:
        from .profit import evaluate

        for p in latest.get("products", []):
            if p.get("code") == code:
                sp = p.setdefault("shopee", {})
                if cur.get("ref_price"):
                    sp.setdefault("auto_ref_price", sp.get("ref_price"))
                    sp["ref_price"] = float(cur["ref_price"])
                    sp["method"] = "manual"
                elif "auto_ref_price" in sp:
                    sp["ref_price"] = sp.pop("auto_ref_price")
                    sp["method"] = latest.get("config", {}).get("fees", {}).get("profit", {}).get("reference", "low3_median")
                p["eval"] = evaluate((p.get("effective") or {}).get("unit"), sp.get("ref_price"), latest["config"]["fees"])
                break
        write_json(WEB_DATA_DIR / "latest.json", latest)
    return {"ok": True, "override": cur}


@app.post("/api/lookup")
def lookup(body: dict[str, Any]) -> dict[str, Any]:
    """即時查一個商品的蝦皮列表：{"code": "BP_x"} 或 {"keyword": "..."}（用快取以外的最新資料）。"""
    latest = read_json(WEB_DATA_DIR / "latest.json", {}) or {}
    product = None
    for p in latest.get("products", []):
        if p.get("code") == body.get("code"):
            product = p
            break
    keyword = body.get("keyword") or (clean_keyword(product.get("name"), product.get("brand")) if product else None)
    if not keyword:
        raise HTTPException(400, "keyword or known code required")
    cfg = load_all()
    provider = make_provider(body.get("provider", "biggo"), cfg["promotions"], DATA_DIR / "cache" / "shopee")
    provider.cache_hours = 0  # 強制重新抓
    listings = provider.search(keyword)
    pc = cfg["fees"]["profit"]
    ref = pick_reference(product or {"name": keyword, "brand": ""}, listings, min_score=float(pc.get("min_match_score", 0.45)), method=pc.get("reference", "low3_median"), min_listings=int(pc.get("min_listings", 2)))
    ref["keyword"] = keyword
    ref["candidates"] = ref["candidates"][:25]
    return ref


@app.post("/api/notify-test")
def notify_test() -> dict[str, Any]:
    n = Notifier(notify_settings())
    rec = [{"code": "TEST", "name": "測試商品", "buy_qty": 2, "unit_cost": 54.5, "ref_price": 109, "net": 96.3, "profit": 41.8, "roi": 0.77, "margin": 0.38, "promo": "任選兩件享買一送一", "url": "https://www.watsons.com.tw/"}]
    return {"channels": n.channels(), "result": n.send(format_text(rec, now_iso()), rec, now_iso())}


# 靜態檔（web/）— 放在最後，避免蓋掉 /api
WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
