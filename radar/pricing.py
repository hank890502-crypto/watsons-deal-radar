"""計價引擎：屈臣氏「真實單位成本」與購物車試算。

成本組成（由內而外）：
  1. 售價 price（API 的 price.value）
  2. 結帳整體折扣 multiplier（例如「官網不限金額享88折」— 售價未含，結帳才扣）
  3. 多件優惠 tier（API 的 elabFirstMultiBuyDatas：買 n 件實付 T，已含可疊加優惠）
  4. 訂單層：折價券（全站滿 X 折 Y，一單一張）、運費（滿額免運）
  5. 支付層：信用卡回饋、寵i點數（可選）

web/engine.js 有同樣邏輯的 JS 版本；tests/fixtures/engine_cases.json 兩邊共用。
"""
from __future__ import annotations

from typing import Any

from .cards import rank_cards


def _multipliers_for(product: dict[str, Any], promo_cfg: dict[str, Any]) -> tuple[float, list[str]]:
    table = promo_cfg.get("checkout_multipliers") or {}
    hits = [(name, float(table[name])) for name in product.get("promotions") or [] if name in table]
    if not hits:
        return 1.0, []
    if promo_cfg.get("stack_multipliers"):
        m = 1.0
        for _, v in hits:
            m *= v
        return m, [n for n, _ in hits]
    name, v = min(hits, key=lambda h: h[1])
    return v, [name]


def unit_cost(product: dict[str, Any], qty: int, promo_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """買 qty 件的實付總額與平均單價（未含折價券/運費/刷卡）。"""
    price = product.get("price")
    if price is None or qty <= 0:
        return None
    mult, applied = _multipliers_for(product, promo_cfg)
    single = round(price * mult, 2)
    tiers = [t for t in product.get("multi_buy") or [] if int(t.get("qty") or 0) >= 2 and t.get("total") is not None]
    tier = None
    if tiers:
        # 取最大可用 tier（qty ≤ 購買量），若都不可用則 None
        usable = [t for t in tiers if int(t["qty"]) <= qty]
        if usable:
            tier = max(usable, key=lambda t: int(t["qty"]))
    if tier:
        n = int(tier["qty"])
        groups, rem = divmod(qty, n)
        tier_total = float(tier["total"])
        # 保守：若多件優惠反而比單買×整體折扣還貴，就當作沒有這個 tier
        if tier_total > n * single + 0.01:
            tier = None
    if tier:
        n = int(tier["qty"])
        groups, rem = divmod(qty, n)
        total = groups * float(tier["total"]) + rem * single
        applied = applied + [f"多件優惠：{n}件${float(tier['total']):g}"]
    else:
        total = qty * single
    total = round(total, 2)
    return {
        "qty": qty,
        "total": total,
        "unit": round(total / qty, 2),
        "single_unit": single,
        "multiplier": mult,
        "tier": {"qty": int(tier["qty"]), "total": float(tier["total"])} if tier else None,
        "applied": applied,
    }


def best_unit_cost(product: dict[str, Any], promo_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """在 qty=1 與各多件 tier 之間，找平均單價最低的購買量。"""
    candidates = [1] + [int(t["qty"]) for t in product.get("multi_buy") or [] if int(t.get("qty") or 0) >= 2]
    best = None
    for q in sorted(set(candidates)):
        r = unit_cost(product, q, promo_cfg)
        if r and (best is None or r["unit"] < best["unit"] - 1e-9):
            best = r
    return best


def pick_coupon(subtotal: float, coupons: list[dict[str, Any]]) -> dict[str, Any] | None:
    ok = [c for c in coupons or [] if c.get("value") and c.get("threshold") is not None and subtotal >= float(c["threshold"])]
    if not ok:
        return None
    return max(ok, key=lambda c: float(c["value"]))


def shipping_fee(amount: float, fees_cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    ws = fees_cfg.get("watsons_shipping") or {}
    mode = ws.get("mode") or "home_delivery"
    rule = ws.get(mode) or {"free_threshold": 0, "fee": 0}
    thr = float(rule.get("free_threshold") or 0)
    fee = float(rule.get("fee") or 0)
    if thr <= 0 or amount >= thr:
        return 0.0, {"mode": mode, "free_threshold": thr, "fee": 0.0, "gap_to_free": 0.0}
    return fee, {"mode": mode, "free_threshold": thr, "fee": fee, "gap_to_free": round(thr - amount, 2)}


def points_value(items_subtotals: list[tuple[dict[str, Any], float]], fees_cfg: dict[str, Any], promo_cfg: dict[str, Any]) -> dict[str, Any]:
    pc = fees_cfg.get("points") or {}
    if not pc.get("enabled", True):
        return {"points": 0, "value": 0.0}
    earn = float(pc.get("earn_per_dollar") or 1)
    per_dollar = float(pc.get("points_per_dollar_value") or 300)
    mults = promo_cfg.get("points_multipliers") or {}
    pts = 0.0
    for product, subtotal in items_subtotals:
        m = 1.0
        for name in product.get("promotions") or []:
            if name in mults:
                m = max(m, float(mults[name]))
        pts += subtotal * earn * m
    return {"points": round(pts), "value": round(pts / per_dollar, 2)}


def cart_total(
    items: list[dict[str, Any]],
    coupons: list[dict[str, Any]],
    fees_cfg: dict[str, Any],
    promo_cfg: dict[str, Any],
    cards_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """items: [{"product": {...}, "qty": n}] → 訂單試算與最佳卡片。"""
    lines = []
    subtotal = 0.0
    for it in items:
        p, q = it["product"], int(it.get("qty") or 0)
        uc = unit_cost(p, q, promo_cfg)
        if not uc:
            continue
        lines.append({"code": p.get("code"), "name": p.get("name"), "qty": q, "unit": uc["unit"], "total": uc["total"], "applied": uc["applied"]})
        subtotal += uc["total"]
    subtotal = round(subtotal, 2)
    coupon = pick_coupon(subtotal, coupons)
    coupon_value = float(coupon["value"]) if coupon else 0.0
    after_coupon = round(subtotal - coupon_value, 2)
    fee, ship = shipping_fee(after_coupon, fees_cfg)
    paid = round(after_coupon + fee, 2)
    ranked = rank_cards(
        paid,
        cards_cfg or {"cards": []},
        promo_cfg.get("card_promos"),
        float((fees_cfg.get("points") or {}).get("points_per_dollar_value") or 300),
    )
    card = ranked[0] if ranked else None
    pts = points_value([(it["product"], l["total"]) for it, l in zip(items, lines)], fees_cfg, promo_cfg)
    effective = round(paid - (card["reward"] if card else 0.0) - pts["value"], 2)
    # 依比例把折價券/運費/回饋分攤回每個品項，得出「有效單位成本」
    ratio = effective / subtotal if subtotal > 0 else 1.0
    for l in lines:
        l["effective_unit"] = round(l["unit"] * ratio, 2)
    # 下一個折價券門檻
    next_coupon = None
    for c in sorted([c for c in coupons or [] if c.get("threshold") is not None and c.get("value")], key=lambda c: float(c["threshold"])):
        if float(c["threshold"]) > subtotal:
            next_coupon = {"name": c["name"], "threshold": float(c["threshold"]), "value": float(c["value"]), "gap": round(float(c["threshold"]) - subtotal, 2)}
            break
    return {
        "lines": lines,
        "subtotal": subtotal,
        "coupon": {"name": coupon["name"], "value": coupon_value} if coupon else None,
        "next_coupon": next_coupon,
        "shipping": ship,
        "paid": paid,
        "card": card,
        "cards": ranked,
        "points": pts,
        "effective_total": effective,
        "effective_ratio": round(ratio, 4),
    }
