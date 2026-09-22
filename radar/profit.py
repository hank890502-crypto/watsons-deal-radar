"""蝦皮轉售利潤計算。

淨收入 = 蝦皮售價 × (1 − 成交手續費 − 金流費 − 免運活動費) − 包材 − 賣家吸收運費
利潤   = 淨收入 − 屈臣氏單位成本（含結帳折扣、多件優惠、最佳卡片回饋、點數）
ROI    = 利潤 / 成本         （預設門檻 30%）
Margin = 利潤 / 蝦皮售價
"""
from __future__ import annotations

from typing import Any


def shopee_net(price: float, fees_cfg: dict[str, Any]) -> dict[str, Any]:
    s = fees_cfg.get("shopee") or {}
    commission = price * float(s.get("commission_rate") or 0)
    cap = float(s.get("commission_cap_per_item") or 0)
    if cap > 0:
        commission = min(commission, cap)
    payment = price * float(s.get("payment_rate") or 0)
    fsp = price * float(s.get("free_shipping_program_rate") or 0)
    packaging = float(s.get("packaging_cost") or 0)
    subsidy = float(s.get("shipping_subsidy") or 0)
    fee_total = commission + payment + fsp + packaging + subsidy
    return {
        "price": round(price, 2),
        "commission": round(commission, 2),
        "payment": round(payment, 2),
        "free_shipping_program": round(fsp, 2),
        "packaging": packaging,
        "shipping_subsidy": subsidy,
        "fee_total": round(fee_total, 2),
        "net": round(price - fee_total, 2),
    }


def evaluate(unit_cost: float | None, ref_price: float | None, fees_cfg: dict[str, Any]) -> dict[str, Any] | None:
    if unit_cost is None or ref_price is None or unit_cost <= 0 or ref_price <= 0:
        return None
    net = shopee_net(ref_price, fees_cfg)
    profit = round(net["net"] - unit_cost, 2)
    roi = round(profit / unit_cost, 4)
    margin = round(profit / ref_price, 4)
    pc = fees_cfg.get("profit") or {}
    metric = pc.get("metric") or "roi"
    threshold = float(pc.get("alert_threshold") or 0.30)
    value = roi if metric == "roi" else margin
    return {
        "unit_cost": round(unit_cost, 2),
        "ref_price": round(ref_price, 2),
        "net": net["net"],
        "fees": net,
        "profit": profit,
        "roi": roi,
        "margin": margin,
        "metric": metric,
        "threshold": threshold,
        "hot": value >= threshold,
        # 若要達到門檻，蝦皮至少要賣多少（反推）
        "breakeven_price": round(_price_for_target(unit_cost, 0.0, fees_cfg), 2),
        "target_price": round(_price_for_target(unit_cost, threshold if metric == "roi" else None, fees_cfg, margin_target=threshold if metric == "margin" else None), 2),
    }


def _price_for_target(unit_cost: float, roi_target: float | None, fees_cfg: dict[str, Any], margin_target: float | None = None) -> float:
    """反推達成目標 ROI / margin 所需售價（忽略單件手續費上限）。"""
    s = fees_cfg.get("shopee") or {}
    r = float(s.get("commission_rate") or 0) + float(s.get("payment_rate") or 0) + float(s.get("free_shipping_program_rate") or 0)
    fixed = float(s.get("packaging_cost") or 0) + float(s.get("shipping_subsidy") or 0)
    if margin_target is not None:
        # price*(1-r) - fixed - cost = margin*price → price = (fixed + cost)/(1 - r - margin)
        denom = 1 - r - margin_target
        return (fixed + unit_cost) / denom if denom > 0 else float("inf")
    target_profit = unit_cost * (roi_target or 0.0)
    return (unit_cost + target_profit + fixed) / (1 - r) if r < 1 else float("inf")
