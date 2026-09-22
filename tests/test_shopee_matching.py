from radar.matching import clean_keyword, detect_pack_qty, match_score, pick_reference, similarity, sizes
from radar.shopee import normalize_biggo_item, parse_biggo_html

TOOTHPASTE = {"code": "BP_598686", "name": "舒酸定 進階護理 專業修復抗敏牙膏100g -原味(深層修復)", "brand": "舒酸定"}


def test_parse_biggo_shopee_filtered(biggo_html_shopee):
    ssr = parse_biggo_html(biggo_html_shopee)
    assert ssr["total"] == 127
    assert len(ssr["list"]) == 25
    listings = [x for x in (normalize_biggo_item(i) for i in ssr["list"]) if x]
    assert len(listings) == 25
    assert all(l["source"] in ("shopee", "shopee_mall") for l in listings)
    assert all(l["url"].startswith("https://shopee.tw/") for l in listings)
    watsons = [l for l in listings if "屈臣氏" in l["shop"]]
    assert watsons and watsons[0]["price"] == 209


def test_parse_biggo_unfiltered_keeps_only_shopee(biggo_html_all):
    ssr = parse_biggo_html(biggo_html_all)
    assert ssr["total"] == 219
    listings = [x for x in (normalize_biggo_item(i) for i in ssr["list"]) if x]
    assert len(listings) == 1  # 只有一筆蝦皮商城
    assert listings[0]["shop"] == "愛買線上購物"


def test_parse_biggo_garbage():
    assert parse_biggo_html("<html>nothing</html>") == {}


def test_clean_keyword():
    assert clean_keyword("舒潔袖珍包面紙10抽30包(包裝隨機出貨)", "舒潔") == "舒潔袖珍包面紙10抽30包"
    assert clean_keyword("靠得住 超薄潔淨護墊(無香) 23片 6包入", "KOTEX靠得住") == "靠得住 超薄潔淨護墊 23片 6包入"
    assert clean_keyword("巴黎萊雅溫和眼唇卸粧液125ML", "L`OREAL PARIS 巴黎萊雅") == "巴黎萊雅溫和眼唇卸粧液125ML"
    kw = clean_keyword("舒酸定 進階護理 專業修復抗敏牙膏100g -原味(深層修復)", "舒酸定")
    assert kw.startswith("舒酸定 進階護理 專業修復抗敏牙膏100g")
    assert len(kw) <= 32


def test_sizes_and_pack_qty():
    assert sizes("舒酸定 專業修復抗敏牙膏100g") == {"100g"}
    assert sizes("巴黎萊雅溫和眼唇卸粧液125ML") == {"125ml"}
    base = "舒酸定 進階護理 專業修復抗敏牙膏100g -原味"
    assert detect_pack_qty("舒酸定專業修復抗敏牙膏100g x 2入【愛買】", base) == 2
    assert detect_pack_qty("舒酸定抗敏牙膏100g x 3入(專業修復/亮白)", base) == 3
    assert detect_pack_qty("舒酸定 專業修復抗敏牙膏-亮白配方 100g克x3x1組", base) == 3
    assert detect_pack_qty("舒酸定專業修復抗敏牙膏100g【愛買】", base) == 1
    # 屈臣氏名稱本身就有「30包」→ 不算多入
    assert detect_pack_qty("舒潔 袖珍包面紙 10抽30包", "舒潔袖珍包面紙10抽30包(包裝隨機出貨)") == 1
    assert detect_pack_qty("舒潔袖珍包面紙10抽30包 *2串", "舒潔袖珍包面紙10抽30包") == 2


def test_similarity_and_score():
    assert similarity("舒酸定專業修復抗敏牙膏100g【愛買】", TOOTHPASTE["name"]) > 0.5
    good = match_score(TOOTHPASTE, "【舒酸定】進階護理 專業修復抗敏牙膏100g")
    bad = match_score(TOOTHPASTE, "高露潔 全效牙膏 150g")
    ng = match_score(TOOTHPASTE, "【即期商品、外觀NG凹盒出清品】舒酸定 專業修復抗敏牙膏100g")
    assert good["score"] > 0.6
    assert bad["score"] < 0.3
    assert ng["penalty"] and ng["score"] < good["score"]
    assert good["size_ok"] is True and good["brand_ok"] is True


def test_pick_reference_from_fixture(biggo_html_shopee):
    ssr = parse_biggo_html(biggo_html_shopee)
    listings = [x for x in (normalize_biggo_item(i) for i in ssr["list"]) if x]
    ref = pick_reference(TOOTHPASTE, listings, min_score=0.45)
    assert ref["n_matched"] >= 8
    assert ref["ref_price"] is not None
    # 最低三筆中位數，應落在 119~209 之間（多入組要被換算成單價）
    assert 100 <= ref["ref_price"] <= 209
    assert ref["official"] == 209  # 屈臣氏蝦皮商城自己的價
    multi = [c for c in ref["candidates"] if c["pack_qty"] > 1]
    assert multi and all(c["unit_price"] < c["price"] for c in multi)
    assert ref["candidates"][0]["matched"] is True


def test_pick_reference_excludes_and_min_listings():
    listings = [
        {"id": "a", "title": "舒酸定 專業修復抗敏牙膏100g", "price": 150, "shop": "x", "url": "u1"},
        {"id": "b", "title": "舒酸定 專業修復抗敏牙膏100g", "price": 160, "shop": "y", "url": "u2"},
    ]
    ref = pick_reference(TOOTHPASTE, listings, exclude_ids={"a"}, min_listings=1)
    assert ref["n_matched"] == 1 and ref["ref_price"] == 160
    ref2 = pick_reference(TOOTHPASTE, listings, exclude_ids={"a"}, min_listings=2)
    assert ref2["ref_price"] is None
