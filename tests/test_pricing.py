import json
from pathlib import Path

import pytest

from radar.cards import card_reward, rank_cards
from radar.effective import build_context, effective_unit
from radar.pricing import best_unit_cost, cart_total, pick_coupon, shipping_fee, unit_cost
from radar.profit import evaluate, shopee_net
from radar.watsons import normalize_coupon

FIX = Path(__file__).parent / "fixtures"


def test_single_unit_applies_checkout_multiplier(toothpaste, promo_cfg):
    r = unit_cost(toothpaste, 1, promo_cfg)
    assert r["unit"] == pytest.approx(183.92, abs=0.01)  # 官網 PDP 顯示 $184
    assert r["multiplier"] == 0.88
    assert r["tier"] is None
    assert "官網不限金額享88折" in r["applied"]


def test_multibuy_tier_is_authoritative(toothpaste, promo_cfg):
    r2 = unit_cost(toothpaste, 2, promo_cfg)
    assert r2["total"] == pytest.approx(307.12)
    assert r2["unit"] == pytest.approx(153.56)
    assert r2["tier"] == {"qty": 2, "total": 307.12}
    r3 = unit_cost(toothpaste, 3, promo_cfg)
    assert r3["total"] == pytest.approx(307.12 + 183.92, abs=0.01)
    r4 = unit_cost(toothpaste, 4, promo_cfg)
    assert r4["total"] == pytest.approx(2 * 307.12)
    best = best_unit_cost(toothpaste, promo_cfg)
    assert best["qty"] == 2 and best["unit"] == pytest.approx(153.56)


def test_tier_worse_than_single_is_ignored(promo_cfg):
    p = {"price": 100, "multi_buy": [{"qty": 2, "total": 250, "avg": 125}], "promotions": []}
    r = unit_cost(p, 2, promo_cfg)
    assert r["tier"] is None and r["total"] == 200


def test_bogo(promo_cfg):
    p = {"price": 109, "multi_buy": [{"qty": 2, "total": 109, "avg": 54.5}], "promotions": ["任選兩件享買一送一，數量請選2件"]}
    assert unit_cost(p, 1, promo_cfg)["unit"] == 109
    assert best_unit_cost(p, promo_cfg)["unit"] == 54.5


def test_stack_multipliers(promo_cfg):
    p = {"price": 100, "multi_buy": [], "promotions": ["官網不限金額享88折", "醫美商品85折"]}
    assert unit_cost(p, 1, promo_cfg)["unit"] == 85  # 取最優，不疊加
    promo_cfg["stack_multipliers"] = True
    assert unit_cost(p, 1, promo_cfg)["unit"] == pytest.approx(74.8)


def test_coupon_and_shipping(fees_cfg):
    coupons = [normalize_coupon(v) for v in json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))["vouchers"]]
    assert pick_coupon(1000, coupons) is None
    assert pick_coupon(1250, coupons)["value"] == 120
    assert pick_coupon(1700, coupons)["value"] == 150
    assert pick_coupon(5000, coupons)["value"] == 300
    fee, info = shipping_fee(500, fees_cfg)
    assert fee == 60 and info["gap_to_free"] == 188
    assert shipping_fee(688, fees_cfg)[0] == 0


def test_card_reward_rules(cards_cfg, promo_cfg):
    cube = cards_cfg["cards"][0]
    r = card_reward(1000, cube, "屈臣氏", promo_cfg["card_promos"], 300)
    # 3% 指定通路 + 刷國泰卡滿$888送3萬點（30000/300 = $100）
    assert r["reward"] == pytest.approx(30 + 100)
    assert r["effective_rate"] == pytest.approx(0.13)
    r2 = card_reward(500, cube, "屈臣氏", promo_cfg["card_promos"], 300)
    assert r2["reward"] == pytest.approx(15)  # 未達 888 門檻
    # 上限
    esun = cards_cfg["cards"][1]
    r3 = card_reward(100000, esun, "屈臣氏", {}, 300)
    assert r3["bonus"] == 300 and r3["base_reward"] == 1000
    # 通路不符 → 只有 base
    r4 = card_reward(1000, cube, "加油站", {}, 300)
    assert r4["reward"] == pytest.approx(5)
    ranked = rank_cards(1000, cards_cfg, promo_cfg["card_promos"], 300)
    assert ranked[0]["card_id"] == "cube"
    assert all(x["card_id"] != "taishin-gogo" for x in ranked)  # disabled


def test_cart_total(toothpaste, fees_cfg, promo_cfg, cards_cfg):
    coupons = [normalize_coupon(v) for v in json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))["vouchers"]]
    bogo = {"code": "BP_270473", "name": "舒潔袖珍包面紙", "price": 109, "multi_buy": [{"qty": 2, "total": 109, "avg": 54.5}], "promotions": ["任選兩件享買一送一，數量請選2件"]}
    r = cart_total([{"product": toothpaste, "qty": 6}, {"product": bogo, "qty": 6}], coupons, fees_cfg, promo_cfg, cards_cfg)
    assert r["subtotal"] == pytest.approx(3 * 307.12 + 3 * 109)   # 921.36 + 327 = 1248.36
    assert r["coupon"]["value"] == 120
    assert r["shipping"]["fee"] == 0
    assert r["paid"] == pytest.approx(1248.36 - 120)
    assert r["card"]["card_id"] == "cube"
    assert r["effective_total"] < r["paid"]
    assert r["next_coupon"]["threshold"] == 1600
    assert all(l["effective_unit"] < l["unit"] for l in r["lines"])


def test_effective_context(toothpaste, fees_cfg, promo_cfg, cards_cfg):
    coupons = [normalize_coupon(v) for v in json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))["vouchers"]]
    ctx = build_context(coupons, fees_cfg, promo_cfg, cards_cfg)
    assert ctx["coupon"]["value"] == 150
    assert ctx["coupon_ratio"] == pytest.approx(150 / 1600, abs=1e-4)
    assert ctx["card"]["card_id"] == "cube"
    e = effective_unit(toothpaste, 153.56, ctx)
    assert e["unit"] < 153.56 * (1 - 150 / 1600)
    assert e["points"] > 0


def test_profit(fees_cfg):
    net = shopee_net(109, fees_cfg)
    assert net["fee_total"] == pytest.approx(109 * 0.075 + 5, abs=0.01)
    ev = evaluate(54.5, 109, fees_cfg)
    assert ev["profit"] == pytest.approx(109 - 109 * 0.075 - 5 - 54.5, abs=0.01)
    assert ev["hot"] is True and ev["roi"] > 0.5
    assert evaluate(150, 160, fees_cfg)["hot"] is False
    assert evaluate(None, 100, fees_cfg) is None
    # 反推：達到 30% ROI 所需售價
    ev2 = evaluate(100, 120, fees_cfg)
    target = ev2["target_price"]
    assert evaluate(100, target, fees_cfg)["roi"] == pytest.approx(0.30, abs=0.001)
