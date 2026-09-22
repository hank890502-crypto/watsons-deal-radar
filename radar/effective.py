"""「有效單位成本」：把訂單層（折價券、運費）與支付層（信用卡、點數）依假設訂單金額分攤到每一件。

    after_coupon = unit × (1 − coupon_ratio)
    card         = after_coupon × card_rate
    points       = after_coupon × points_rate(商品點數倍率)
    effective    = after_coupon − card − points + shipping_share
"""
from __future__ import annotations

from typing import Any

from .cards import best_card
from .pricing import pick_coupon, shipping_fee


def build_context(coupons: list[dict[str, Any]], fees_cfg: dict[str, Any], promo_cfg: dict[str, Any], cards_cfg: dict[str, Any]) -> dict[str, Any]:
    a = fees_cfg.get("assumptions") or {}
    order = float(a.get("order_amount") or 1600)
    coupon = pick_coupon(order, coupons) if a.get("use_coupon", True) else None
    coupon_value = float(coupon["value"]) if coupon else 0.0
    coupon_ratio = coupon_value / order if order > 0 else 0.0
    after = order - coupon_value
    fee, ship = shipping_fee(after, fees_cfg)
    shipping_ratio = fee / after if after > 0 else 0.0
    card = None
    card_rate = 0.0
    if a.get("use_card", True):
        card = best_card(after, cards_cfg, promo_cfg.get("card_promos"), float((fees_cfg.get("points") or {}).get("points_per_dollar_value") or 300))
        card_rate = float(card["effective_rate"]) if card else 0.0
    pc = fees_cfg.get("points") or {}
    points_base_rate = 0.0
    if a.get("use_points", True) and pc.get("enabled", True):
        points_base_rate = float(pc.get("earn_per_dollar") or 1) / float(pc.get("points_per_dollar_value") or 300)
    return {
        "order_amount": order,
        "coupon": {"name": coupon["name"], "value": coupon_value} if coupon else None,
        "coupon_ratio": round(coupon_ratio, 4),
        "shipping": ship,
        "shipping_ratio": round(shipping_ratio, 4),
        "card": {"card_id": card["card_id"], "card": card["card"], "effective_rate": card["effective_rate"], "reward": card["reward"]} if card else None,
        "card_rate": round(card_rate, 4),
        "points_base_rate": round(points_base_rate, 6),
        "points_multipliers": promo_cfg.get("points_multipliers") or {},
    }


def effective_unit(product: dict[str, Any], unit: float, ctx: dict[str, Any]) -> dict[str, Any]:
    after = unit * (1 - ctx["coupon_ratio"])
    card = after * ctx["card_rate"]
    mult = 1.0
    for name in product.get("promotions") or []:
        if name in ctx["points_multipliers"]:
            mult = max(mult, float(ctx["points_multipliers"][name]))
    points = after * ctx["points_base_rate"] * mult
    ship = after * ctx["shipping_ratio"]
    eff = after - card - points + ship
    return {
        "unit": round(eff, 2),
        "after_coupon": round(after, 2),
        "card": round(card, 2),
        "points": round(points, 2),
        "points_multiplier": mult,
        "shipping": round(ship, 2),
    }
