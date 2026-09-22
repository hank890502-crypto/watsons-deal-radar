"""一次完整掃描：屈臣氏促銷 → 成本 → 蝦皮參考價 → 利潤 → 快照 / 歷史 / 通知。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .alerts import Notifier, alert_records, dedupe, format_text, now_iso, select_hot
from .config import DATA_DIR, WEB_DATA_DIR, load_all, notify_settings
from .effective import build_context, effective_unit
from .matching import clean_keyword, pick_reference
from .pricing import best_unit_cost, unit_cost
from .profit import evaluate
from .shopee import ManualProvider, make_provider, prune_cache
from .storage import read_json, update_history, write_json
from .watsons import WatsonsClient, merge_product, normalize_product

log = logging.getLogger(__name__)


@dataclass
class ScanOptions:
    shopee: str = "biggo"            # biggo | playwright | none
    max_promos: int | None = None    # 測試用：只掃前 N 個促銷
    max_pages: int | None = None     # 覆蓋 config 的每促銷頁數
    max_lookups: int | None = None   # 覆蓋 config 的蝦皮查價上限
    notify: bool = True
    data_dir: Path = DATA_DIR
    out_dir: Path = WEB_DATA_DIR
    config_dir: Path | None = None
    dashboard_url: str | None = None
    only_codes: list[str] = field(default_factory=list)  # 只處理這些商品（測試 / 單品查價）


def select_promotions(all_promos: list[dict[str, Any]], scan_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    include = set(scan_cfg.get("include") or [])
    exclude = set(scan_cfg.get("exclude") or [])
    out = []
    for p in all_promos:
        name = p["name"]
        if include and name not in include:
            continue
        if name in exclude:
            continue
        out.append(p)
    return out


def discount_depth(p: dict[str, Any]) -> float:
    lp = p.get("list_price") or p.get("price")
    cost = (p.get("cost") or {}).get("unit")
    if not lp or cost is None or lp <= 0:
        return 0.0
    return round(1 - cost / lp, 4)


def choose_lookup_candidates(products: list[dict[str, Any]], look_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    prefer = look_cfg.get("prefer_promos") or []
    min_depth = float(look_cfg.get("min_discount_depth") or 0)
    min_price = float(look_cfg.get("min_price") or 0)
    cands = []
    for p in products:
        if not p.get("cost") or not p.get("in_stock"):
            continue
        if (p.get("price") or 0) < min_price:
            continue
        preferred = any(x in prefer for x in p.get("promotions") or [])
        if p["discount_depth"] < min_depth and not preferred:
            continue
        cands.append(p)
    cands.sort(key=lambda p: (not any(x in prefer for x in p.get("promotions") or []), -p["discount_depth"], -(p.get("sold") or 0)))
    return cands


def trim_candidates(cands: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
    keep = ("id", "source", "title", "price", "unit_price", "pack_qty", "shop", "url", "score", "matched", "excluded", "offline", "is_ad", "image")
    return [{k: c.get(k) for k in keep} for c in cands[:n]]


def run(opts: ScanOptions | None = None) -> dict[str, Any]:
    opts = opts or ScanOptions()
    t0 = time.time()
    cfg = load_all(opts.config_dir)
    fees_cfg, promo_cfg, cards_cfg, matches_cfg = cfg["fees"], cfg["promotions"], cfg["cards"], cfg["matches"]
    scan_cfg = promo_cfg["scan"]
    look_cfg = promo_cfg["shopee_lookup"]
    stamp = now_iso()

    # ---------------------------------------------------------------- 1. 屈臣氏
    client = WatsonsClient(delay_sec=float(scan_cfg.get("request_delay_sec") or 0.6))
    all_promos = client.promotions()
    selected = select_promotions(all_promos, scan_cfg)
    if opts.max_promos:
        selected = selected[: opts.max_promos]
    log.info("promotions: %d total, %d selected", len(all_promos), len(selected))
    products: dict[str, dict[str, Any]] = {}
    page_size = int(scan_cfg.get("page_size") or 100)
    max_pages = opts.max_pages or int(scan_cfg.get("max_pages_per_promo") or 40)
    for promo in selected:
        n = 0
        for raw in client.iter_promotion_products(promo["name"], page_size=page_size, max_pages=max_pages):
            p = normalize_product(raw, promo["name"])
            if not p.get("code"):
                continue
            if opts.only_codes and p["code"] not in opts.only_codes:
                continue
            if scan_cfg.get("only_in_stock", True) and not p["in_stock"]:
                continue
            products[p["code"]] = merge_product(products[p["code"]], p) if p["code"] in products else p
            n += 1
        promo["scanned"] = n
        log.info("  %s: %d products", promo["name"], n)
    if opts.only_codes:
        missing = [c for c in opts.only_codes if c not in products]
        for raw in client.products_by_codes(missing) if missing else []:
            p = normalize_product(raw)
            products[p["code"]] = p
    coupons: list[dict[str, Any]] = []
    if products:
        first = next(iter(products.values()))
        coupons = client.coupons(first.get("variant") or "")
    client.close()

    # ---------------------------------------------------------------- 2. 成本
    ctx = build_context(coupons, fees_cfg, promo_cfg, cards_cfg)
    plist = list(products.values())
    for p in plist:
        p["cost"] = best_unit_cost(p, promo_cfg)
        p["cost1"] = unit_cost(p, 1, promo_cfg)
        p["effective"] = effective_unit(p, p["cost"]["unit"], ctx) if p["cost"] else None
        p["discount_depth"] = discount_depth(p)

    # ---------------------------------------------------------------- 3. 蝦皮
    provider = make_provider(opts.shopee, promo_cfg, opts.data_dir / "cache" / "shopee")
    manual = ManualProvider(matches_cfg.get("overrides") or {})
    max_lookups = opts.max_lookups if opts.max_lookups is not None else int(look_cfg.get("max_lookups_per_run") or 150)
    pc = fees_cfg.get("profit") or {}
    cands = choose_lookup_candidates(plist, look_cfg)
    if opts.only_codes:
        cands = [p for p in plist if p.get("cost")]
    # 有人工價的商品一定要評估
    for p in plist:
        if p["code"] in (matches_cfg.get("overrides") or {}) and p not in cands and p.get("cost"):
            cands.insert(0, p)
    fresh_calls = 0
    looked = 0
    for p in cands:
        over = (matches_cfg.get("overrides") or {}).get(p["code"]) or {}
        manual_ref = manual.ref_for(p["code"])
        keyword = over.get("keyword") or clean_keyword(p.get("name"), p.get("brand"))
        listings: list[dict[str, Any]] = []
        if provider.name != "manual" and keyword:
            before = getattr(provider, "calls", 0)
            if fresh_calls >= max_lookups:
                # 超過本次上限：只吃快取
                cached = provider._read_cache(keyword) if hasattr(provider, "_read_cache") else None  # type: ignore[attr-defined]
                if cached is None and not manual_ref:
                    continue
                listings = cached or []
            else:
                if getattr(provider, "consecutive_failures", 0) >= 5:
                    log.error("蝦皮查價連續失敗 5 次，本次停止查價（可能被擋），改用快取")
                    cached = provider._read_cache(keyword) if hasattr(provider, "_read_cache") else None  # type: ignore[attr-defined]
                    if cached is None and not manual_ref:
                        continue
                    listings = cached or []
                else:
                    listings = provider.search(keyword)
                    fresh_calls += getattr(provider, "calls", 0) - before
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
        ref["provider"] = provider.name
        ref["fetched_at"] = stamp
        if manual_ref:
            ref["auto_ref_price"] = ref["ref_price"]
            ref["ref_price"] = manual_ref["ref_price"]
            ref["method"] = "manual"
            ref["note"] = manual_ref.get("note")
        p["shopee"] = ref
        p["eval"] = evaluate((p.get("effective") or {}).get("unit"), ref["ref_price"], fees_cfg)
        looked += 1
    if hasattr(provider, "close"):
        provider.close()
    pruned = prune_cache(opts.data_dir / "cache" / "shopee", float(look_cfg.get("cache_hours") or 48) * 2)

    # ---------------------------------------------------------------- 4. 排序 / 快照
    def sort_key(p: dict[str, Any]):
        e = p.get("eval") or {}
        return (-(e.get("roi") if e.get("roi") is not None else -9), -p.get("discount_depth", 0))

    plist.sort(key=sort_key)
    hot = select_hot(plist)
    snapshot = {
        "generated_at": stamp,
        "version": __version__,
        "source": {
            "watsons_calls": client.calls,
            "shopee_provider": provider.name,
            "shopee_calls": getattr(provider, "calls", 0),
            "shopee_cache_hits": getattr(provider, "cache_hits", 0),
            "shopee_blocked": getattr(provider, "consecutive_failures", 0) >= 5,
            "lookups_evaluated": looked,
            "cache_pruned": pruned,
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "promotions": [{**p, "selected": p in selected} for p in all_promos],
        "coupons": coupons,
        "context": ctx,
        "config": {"fees": fees_cfg, "promotions": {k: v for k, v in promo_cfg.items() if k != "scan"}, "cards": cards_cfg},
        "summary": {"products": len(plist), "with_shopee": sum(1 for p in plist if (p.get("shopee") or {}).get("ref_price")), "hot": len(hot)},
        "products": plist,
    }
    write_json(opts.out_dir / "latest.json", snapshot)
    history = read_json(opts.out_dir / "history.json", {}) or {}
    write_json(opts.out_dir / "history.json", update_history(history, plist, stamp))

    # ---------------------------------------------------------------- 5. 通知
    state = read_json(opts.data_dir / "alert_state.json", {}) or {}
    fresh, state = dedupe(hot, state)
    records = alert_records(fresh, stamp)
    alerts = read_json(opts.out_dir / "alerts.json", []) or []
    alerts = (records + alerts)[:500]
    write_json(opts.out_dir / "alerts.json", alerts)
    write_json(opts.data_dir / "alert_state.json", state)
    notify_result: dict[str, str] = {}
    if opts.notify and records:
        text = format_text(records, stamp, dashboard_url=opts.dashboard_url)
        notify_result = Notifier(notify_settings()).send(text, records, stamp)
    summary = {**snapshot["summary"], "new_alerts": len(records), "notify": notify_result, **snapshot["source"]}
    log.info("done: %s", summary)
    return summary
