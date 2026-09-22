"""產生 tests/fixtures/engine_cases.json：Python 引擎的輸入/輸出，供 web/engine.test.mjs 驗證 JS 版一致。

  python tests/gen_engine_cases.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.cards import rank_cards  # noqa: E402
from radar.config import DEFAULT_CARDS, DEFAULT_FEES, DEFAULT_PROMOTIONS  # noqa: E402
from radar.effective import build_context, effective_unit  # noqa: E402
from radar.pricing import best_unit_cost, cart_total, unit_cost  # noqa: E402
from radar.profit import evaluate  # noqa: E402
from radar.watsons import normalize_coupon, normalize_product  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def main() -> None:
    fees, promo, cards = copy.deepcopy(DEFAULT_FEES), copy.deepcopy(DEFAULT_PROMOTIONS), copy.deepcopy(DEFAULT_CARDS)
    coupons = [normalize_coupon(v) for v in json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))["vouchers"]]
    search = json.loads((FIX / "watsons_search_bogo.json").read_text(encoding="utf-8"))
    products = [normalize_product(p, "任選兩件享買一送一，數量請選2件") for p in search["products"]]
    toothpaste = {
        "code": "BP_598686", "name": "舒酸定 進階護理 專業修復抗敏牙膏100g -原味(深層修復)", "brand": "舒酸定",
        "price": 209.0, "list_price": 292.0, "multi_buy": [{"qty": 2, "total": 307.12, "avg": 153.56}],
        "promotions": ["官網不限金額享88折", "刷玉山卡滿$888送3萬點", "醫美商品點數6倍送"], "in_stock": True,
    }
    products.append(toothpaste)
    ctx = build_context(coupons, fees, promo, cards)
    cases = {"config": {"fees": fees, "promotions": promo, "cards": cards}, "coupons": coupons, "context": ctx, "unit_cost": [], "best": [], "effective": [], "evaluate": [], "cards": [], "cart": []}
    for p in products:
        for q in (1, 2, 3, 5):
            cases["unit_cost"].append({"product": p, "qty": q, "expected": unit_cost(p, q, promo)})
        b = best_unit_cost(p, promo)
        cases["best"].append({"product": p, "expected": b})
        e = effective_unit(p, b["unit"], ctx)
        cases["effective"].append({"product": p, "unit": b["unit"], "expected": e})
        for ref in (p["price"], p["price"] * 1.5, 60.0):
            cases["evaluate"].append({"unit": e["unit"], "ref": ref, "expected": evaluate(e["unit"], ref, fees)})
    for amt in (300, 888, 1000, 1480, 5000, 100000):
        cases["cards"].append({"amount": amt, "expected": rank_cards(amt, cards, promo["card_promos"], 300)})
    for items in ([{"product": toothpaste, "qty": 6}, {"product": products[0], "qty": 6}], [{"product": products[1], "qty": 2}], [{"product": products[3], "qty": 4}, {"product": products[5], "qty": 3}, {"product": toothpaste, "qty": 1}]):
        r = cart_total(items, coupons, fees, promo, cards)
        cases["cart"].append({"items": items, "expected": r})
    (FIX / "engine_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print("wrote", FIX / "engine_cases.json", "cases:", {k: len(v) for k, v in cases.items() if isinstance(v, list)})


if __name__ == "__main__":
    main()
