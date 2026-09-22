import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def bogo_search():
    return json.loads((FIX / "watsons_search_bogo.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def coupons_raw():
    return json.loads((FIX / "watsons_coupons.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def biggo_html_shopee():
    script = (FIX / "biggo_rsc_script_shopee.txt").read_text(encoding="utf-8")
    return f"<html><body><script>{script}</script></body></html>"


@pytest.fixture(scope="session")
def biggo_html_all():
    script = (FIX / "biggo_rsc_script.txt").read_text(encoding="utf-8")
    return f"<html><body><script>{script}</script></body></html>"


@pytest.fixture
def promo_cfg():
    from radar.config import DEFAULT_PROMOTIONS
    import copy

    return copy.deepcopy(DEFAULT_PROMOTIONS)


@pytest.fixture
def fees_cfg():
    from radar.config import DEFAULT_FEES
    import copy

    return copy.deepcopy(DEFAULT_FEES)


@pytest.fixture
def cards_cfg():
    from radar.config import DEFAULT_CARDS
    import copy

    return copy.deepcopy(DEFAULT_CARDS)


@pytest.fixture
def toothpaste():
    """BP_598686 舒酸定（官網 PDP 驗證：$209、原價 $292、88折後 $184、買 2 件 $307）"""
    return {
        "code": "BP_598686",
        "variant": "598686",
        "name": "舒酸定 進階護理 專業修復抗敏牙膏100g -原味(深層修復)",
        "brand": "舒酸定",
        "price": 209.0,
        "list_price": 292.0,
        "multi_buy": [{"qty": 2, "total": 307.12, "avg": 153.56}],
        "promotions": ["官網不限金額享88折", "刷玉山卡滿$888送3萬點", "滿$100送數位印花"],
        "in_stock": True,
    }
