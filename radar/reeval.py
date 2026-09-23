"""不重新掃描，用「上次快照 + 蝦皮查價快取 + 目前設定」重算參考價與利潤。

用途：改了比對規則、費率、卡片、訂單假設之後，馬上看到新結果（不必再打屈臣氏 / BigGo）。
會重寫 web/data/latest.json、修正 history.json 最後一個時間點、重算 alerts.json 裡同一批（同 ts）的紀錄；
不會發通知、不會動 alert_state.json。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .alerts import alert_records, now_iso, select_hot
from .config import DATA_DIR, WEB_DATA_DIR, load_all
from .effective import build_context, effective_unit
from .matching import clean_keyword, pick_reference
from .pipeline import discount_depth, trim_candidates
from .pricing import best_unit_cost
from .profit import evaluate
from .shopee import BigGoShopeeProvider, ManualProvider
from .storage import read_json, write_json

log = logging.getLogger(__name__)


def reeval(out_dir: Path = WEB_DATA_DIR, data_dir: Path = DATA_DIR, config_dir: Path | None = None) -> dict[str, Any]:
    snap = read_json(out_dir / "latest.json", None)
    if not snap or not snap.get("products"):
        return {"ok": False, "reason": "沒有 latest.json，請先跑 scan"}
    cfg = load_all(config_dir)
    fees_cfg, promo_cfg, cards_cfg, matches_cfg = cfg["fees"], cfg["promotions"], cfg["cards"], cfg["matches"]
    look_cfg = promo_cfg["shopee_lookup"]
    pc = fees_cfg.get("profit") or {}
    overrides = matches_cfg.get("overrides") or {}
    manual = ManualProvider(overrides)
    # 只讀快取（快取檔存活期比 cache_hours 長一倍，這裡放寬到那個上限）
    cache = BigGoShopeeProvider(cache_dir=data_dir / "cache" / "shopee", cache_hours=float(look_cfg.get("cache_hours") or 48) * 2)
    ctx = build_context(snap.get("coupons") or [], fees_cfg, promo_cfg, cards_cfg)
    plist = snap["products"]
    stamp = snap.get("generated_at") or now_iso()
    n_ref = n_cache = n_fallback = 0
    for p in plist:
        p["cost"] = best_unit_cost(p, promo_cfg)
        p["effective"] = effective_unit(p, p["cost"]["unit"], ctx) if p["cost"] else None
        p["discount_depth"] = discount_depth(p)
        old = p.get("shopee")
        over = overrides.get(p["code"]) or {}
        manual_ref = manual.ref_for(p["code"])
        if not old and not manual_ref:
            p["eval"] = None
            continue
        keyword = over.get("keyword") or (old or {}).get("keyword") or clean_keyword(p.get("name"), p.get("brand"))
        listings = cache._read_cache(keyword)
        if listings is None:
            listings = (old or {}).get("candidates") or []  # 快取沒了就用快照裡留下的候選（最多 12 筆）
            n_fallback += 1
        else:
            n_cache += 1
        ref = pick_reference(
            p,
            listings,
            min_score=float(pc.get("min_match_score") or 0.45),
            method=pc.get("reference") or "low3_median",
            min_listings=int(pc.get("min_listings") or 2),
            exclude_ids=set(over.get("exclude_ids") or []),
        )
        ref["candidates"] = trim_candidates(ref["candidates"])
        ref["keyword"] = keyword
        ref["provider"] = (old or {}).get("provider") or "biggo"
        ref["fetched_at"] = (old or {}).get("fetched_at") or stamp
        if manual_ref:
            ref["auto_ref_price"] = ref["ref_price"]
            ref["ref_price"] = manual_ref["ref_price"]
            ref["method"] = "manual"
            ref["note"] = manual_ref.get("note")
        p["shopee"] = ref
        p["eval"] = evaluate((p.get("effective") or {}).get("unit"), ref["ref_price"], fees_cfg)
        if ref["ref_price"]:
            n_ref += 1
    cache.close()

    def sort_key(p: dict[str, Any]):
        e = p.get("eval") or {}
        return (-(e.get("roi") if e.get("roi") is not None else -9), -p.get("discount_depth", 0))

    plist.sort(key=sort_key)
    hot = select_hot(plist)
    snap["context"] = ctx
    snap["config"] = {"fees": fees_cfg, "promotions": {k: v for k, v in promo_cfg.items() if k != "scan"}, "cards": cards_cfg}
    snap["summary"] = {"products": len(plist), "with_shopee": n_ref, "hot": len(hot)}
    snap.setdefault("source", {})["reevaluated_at"] = now_iso()
    write_json(out_dir / "latest.json", snap)

    # history：同一個 stamp 的最後一點改成新數字（不新增時間點）
    history = read_json(out_dir / "history.json", {}) or {}
    fixed = 0
    for p in plist:
        entry = history.get(p.get("code"))
        if not entry or not entry.get("points"):
            continue
        last = entry["points"][-1]
        if last[0] == stamp:
            last[2] = (p.get("cost") or {}).get("unit")
            last[3] = (p.get("shopee") or {}).get("ref_price")
            last[4] = (p.get("eval") or {}).get("roi")
            fixed += 1
    write_json(out_dir / "history.json", history)

    # alerts：把同一批（同 ts）的紀錄換成重算後仍達標的
    alerts = read_json(out_dir / "alerts.json", []) or []
    others = [a for a in alerts if a.get("ts") != stamp]
    had = {a.get("code") for a in alerts if a.get("ts") == stamp}
    seen_before = {a.get("code") for a in others}
    # 這批 = 重算後仍達標、且（原本就在這批 或 從沒通知過）的商品
    records = alert_records([p for p in hot if p["code"] in had or p["code"] not in seen_before], stamp)
    write_json(out_dir / "alerts.json", (records + others)[:500])
    summary = {"ok": True, "products": len(plist), "with_shopee": n_ref, "hot": len(hot), "from_cache": n_cache, "from_snapshot": n_fallback, "history_points_fixed": fixed, "alerts_in_batch": len(records)}
    log.info("reeval done: %s", summary)
    return summary
