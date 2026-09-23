"""端到端：用 fixture 取代網路，跑完整 pipeline 並檢查輸出檔。"""
import json
from pathlib import Path

import pytest

from radar import pipeline
from radar.shopee import normalize_biggo_item, parse_biggo_html
from radar.storage import read_json

FIX = Path(__file__).parent / "fixtures"


class FakeWatsons:
    def __init__(self, *a, **k):
        self.calls = 0
        self._search = json.loads((FIX / "watsons_search_bogo.json").read_text(encoding="utf-8"))
        self._coupons = json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))

    def promotions(self):
        self.calls += 1
        return [{"name": "任選兩件享買一送一，數量請選2件", "count": 546}, {"name": "滿$100送數位印花", "count": 7765}, {"name": "清貨3折", "count": 16}]

    def iter_promotion_products(self, name, page_size=100, max_pages=40, sort="bestSeller"):
        self.calls += 1
        if name == "清貨3折":
            return iter([])
        return iter(self._search["products"])

    def products_by_codes(self, codes):
        return []

    def coupons(self, variant):
        from radar.watsons import normalize_coupon

        self.calls += 1
        return [normalize_coupon(v) for v in self._coupons["vouchers"]]

    def close(self):
        pass


class FakeProvider:
    name = "biggo"

    def __init__(self, html):
        self.calls = 0
        self.cache_hits = 0
        ssr = parse_biggo_html(html)
        self._listings = [x for x in (normalize_biggo_item(i) for i in ssr["list"]) if x]

    def search(self, keyword):
        self.calls += 1
        # 假裝每個關鍵字都回舒酸定的結果（只有牙膏會 match）
        return self._listings

    def _read_cache(self, keyword):
        return None

    def close(self):
        pass


@pytest.fixture
def fake_env(monkeypatch, tmp_path, biggo_html_shopee):
    monkeypatch.setattr(pipeline, "WatsonsClient", FakeWatsons)
    prov = FakeProvider(biggo_html_shopee)
    monkeypatch.setattr(pipeline, "make_provider", lambda kind, cfg, cache_dir: prov)
    monkeypatch.setattr(pipeline, "notify_settings", lambda: {})
    return tmp_path, prov


def test_pipeline_end_to_end(fake_env):
    tmp, prov = fake_env
    out = tmp / "web"
    data = tmp / "data"
    summary = pipeline.run(pipeline.ScanOptions(out_dir=out, data_dir=data, notify=False, config_dir=tmp / "nocfg"))
    assert summary["products"] == 12
    latest = read_json(out / "latest.json")
    assert latest["summary"]["products"] == 12
    assert latest["coupons"][0]["threshold"] == 1200
    assert latest["context"]["coupon"]["value"] == 150
    # 排除清單有生效：滿$100送數位印花 不掃
    assert [p["name"] for p in latest["promotions"] if p["selected"]] == ["任選兩件享買一送一，數量請選2件", "清貨3折"]
    p = latest["products"][0]
    assert p["cost"]["qty"] == 2 and p["cost"]["unit"] == p["multi_buy"][0]["avg"]
    assert p["effective"]["unit"] < p["cost"]["unit"]
    # 通過門檻的商品都拿去查蝦皮（77脆可 $29 低於 min_price=40 被略過），但只有名稱相近的才會有參考價
    assert summary["lookups_evaluated"] == 11
    assert prov.calls == 11
    with_ref = [x for x in latest["products"] if (x.get("shopee") or {}).get("ref_price")]
    assert len(with_ref) == 0  # fixture 是舒酸定列表，跟面紙/護墊都不像 → 不會誤配
    assert sum(1 for x in latest["products"] if "candidates" in (x.get("shopee") or {})) == 11
    hist = read_json(out / "history.json")
    assert len(hist) == 12 and len(next(iter(hist.values()))["points"]) == 1
    assert read_json(out / "alerts.json") == []
    # 第二次跑：歷史不重複增加（值相同只更新時間）
    pipeline.run(pipeline.ScanOptions(out_dir=out, data_dir=data, notify=False, config_dir=tmp / "nocfg"))
    hist2 = read_json(out / "history.json")
    assert len(next(iter(hist2.values()))["points"]) == 1


def test_pipeline_manual_override_triggers_alert(fake_env):
    tmp, prov = fake_env
    cfgdir = tmp / "cfg"
    cfgdir.mkdir()
    (cfgdir / "matches.json").write_text(json.dumps({"overrides": {"BP_270473": {"ref_price": 109, "note": "手動"}}}), encoding="utf-8")
    out = tmp / "web"
    summary = pipeline.run(pipeline.ScanOptions(out_dir=out, data_dir=tmp / "data", notify=False, config_dir=cfgdir))
    latest = read_json(out / "latest.json")
    p = next(x for x in latest["products"] if x["code"] == "BP_270473")
    assert p["shopee"]["method"] == "manual" and p["shopee"]["ref_price"] == 109
    assert p["eval"]["hot"] is True
    assert summary["hot"] == 1 and summary["new_alerts"] == 1
    alerts = read_json(out / "alerts.json")
    assert alerts[0]["code"] == "BP_270473" and alerts[0]["roi"] > 0.3
    # 再跑一次：24h 內不重複通知
    summary2 = pipeline.run(pipeline.ScanOptions(out_dir=out, data_dir=tmp / "data", notify=False, config_dir=cfgdir))
    assert summary2["new_alerts"] == 0


def test_reeval_recomputes_from_snapshot(fake_env):
    from radar import reeval as rv

    tmp, prov = fake_env
    out, data, cfgdir = tmp / "web", tmp / "data", tmp / "cfg"
    pipeline.run(pipeline.ScanOptions(out_dir=out, data_dir=data, notify=False, config_dir=cfgdir))
    before = read_json(out / "latest.json")
    # 改設定：門檻放寬到 0%、訂單假設改小 → 不重掃也要反映
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / "fees.json").write_text(json.dumps({"profit": {"alert_threshold": 0.0}, "assumptions": {"order_amount": 800}}), encoding="utf-8")
    res = rv.reeval(out_dir=out, data_dir=data, config_dir=cfgdir)
    assert res["ok"] and res["products"] == before["summary"]["products"]
    after = read_json(out / "latest.json")
    assert after["config"]["fees"]["assumptions"]["order_amount"] == 800
    assert after["source"]["reevaluated_at"]
    assert after["generated_at"] == before["generated_at"]
    # 候選列表仍在（沒有快取時用快照裡的候選重算）
    assert sum(1 for x in after["products"] if "candidates" in (x.get("shopee") or {})) == sum(1 for x in before["products"] if "candidates" in (x.get("shopee") or {}))
    hist = read_json(out / "history.json")
    assert len(next(iter(hist.values()))["points"]) == 1  # 沒有新增時間點
