from radar.watsons import merge_product, normalize_coupon, normalize_product


def test_normalize_bogo_products(bogo_search):
    products = [normalize_product(p, "任選兩件享買一送一，數量請選2件") for p in bogo_search["products"]]
    assert len(products) == 12
    p = products[0]
    assert p["code"] == "BP_270473"
    assert p["variant"] == "270473"
    assert p["ean"] == "4710114862178"
    assert p["brand"] == "舒潔"
    assert p["price"] == 109.0 and p["list_price"] == 109.0
    assert p["multi_buy"][0]["qty"] == 2 and p["multi_buy"][0]["total"] == 109.0 and p["multi_buy"][0]["avg"] == 54.5
    assert p["promo_tag"] == "任選兩件享買一送一，數量請選2件"
    assert "任選兩件享買一送一，數量請選2件" in p["promotions"]
    assert p["in_stock"] is True
    assert p["url"].startswith("https://www.watsons.com.tw/")
    assert p["image"].startswith("https://")
    assert p["category_path"] == "日用品/衛生紙/袖珍包/紙手帕"
    assert p["promo_end"] is not None


def test_markdown_rate(bogo_search):
    # 蘇菲 護墊 158 / 169 → 有折扣率
    p = next(normalize_product(x) for x in bogo_search["products"] if x["code"] == "BP_146630")
    assert p["price"] == 158.0 and p["list_price"] == 169.0
    assert 0 < p["markdown_rate"] < 0.2


def test_merge_product_unions_promotions():
    a = {"code": "X", "promotions": ["A"], "price": 10}
    b = {"code": "X", "promotions": ["B"], "price": 9, "image": None}
    m = merge_product(a, b)
    assert m["promotions"] == ["A", "B"]
    assert m["price"] == 9


def test_normalize_coupon(coupons_raw):
    cs = [normalize_coupon(v) for v in coupons_raw["vouchers"]]
    assert [c["threshold"] for c in cs] == [1200, 1600, 2000, 3000]
    assert [c["value"] for c in cs] == [120.0, 150.0, 200.0, 300.0]
    assert cs[0]["end"].startswith("2026-09-23")
    assert cs[0]["online_only"] is True
