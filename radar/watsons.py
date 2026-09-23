"""屈臣氏線上商店（SAP Commerce OCC v2）公開 API 客戶端。

觀察到的端點（不需登入）：
  GET /products/search?fields=FULL&query=:relevance:allPromotions:<促銷名>&pageSize=100&currentPage=0
  GET /products/search?fields=FULL&query=BP_1 BP_2 ...&productCodeOnly=true      （批次查詢）
  GET /users/anonymous/availableGrabCoupons?fields=FULL&product=<variantCode>     （全站折價券）
  GET /products/<variantCode>/multiBuy?fields=FULL                                 （多件優惠）
  GET /users/anonymous/cms/components?componentIds=...                            （活動頁商品輪播）

售價欄位說明（以 BP_598686 舒酸定為例，官網 PDP 已驗證）：
  price.value          = 209  目前售價（未含結帳整體折扣，例如「官網不限金額享88折」）
  elabPrice.value      = 292  原價
  elabMarkDownPrice    = 折扣資訊（discountRate 28）
  elabFirstMultiBuyDatas[0] = {quantity:2, totalDiscountedPrice:307.12, avgDiscountedPrice:153.56}
                         → 買 2 件實付 307（已含所有可疊加優惠），為多件購買的權威數字
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.watsons.com.tw/api/v2/wtctw"
SITE_URL = "https://www.watsons.com.tw"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Origin": SITE_URL,
    "Referer": SITE_URL + "/",
}
COMMON_PARAMS = {"lang": "zh_TW", "curr": "TWD"}


class WatsonsError(RuntimeError):
    pass


class WatsonsBlocked(WatsonsError):
    """Akamai 回 403 Access Denied（機器人偵測），換一種傳輸方式再試。"""


# ---------------------------------------------------------------------------- transports
# Akamai 會擋掉 Python 預設的 TLS 指紋（httpx/requests 直接打 API 會 403 Access Denied），
# 但瀏覽器頁面裡的 fetch() 可以。三種傳輸方式，auto 會依序嘗試：
#   curl_cffi  — 模仿 Chrome 的 TLS/HTTP2 指紋（pip install curl_cffi），最快
#   playwright — 真的開一個 Chromium 到 watsons.com.tw，在頁面裡 fetch API（最穩，需 playwright install chromium）
#   httpx      — 純 Python（在某些網路環境可用）


class HttpxTransport:
    name = "httpx"

    def __init__(self, timeout: float = 30.0):
        self.client = httpx.Client(headers=DEFAULT_HEADERS, timeout=timeout, http2=False)

    def get(self, url: str, params: dict[str, Any]) -> tuple[int, str]:
        r = self.client.get(url, params=params)
        return r.status_code, r.text

    def close(self) -> None:
        self.client.close()


class CurlCffiTransport:
    name = "curl_cffi"

    def __init__(self, timeout: float = 30.0, impersonate: str = "chrome"):
        from curl_cffi import requests as cffi_requests  # type: ignore

        self.session = cffi_requests.Session(impersonate=impersonate, timeout=timeout)
        self.session.headers.update({k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"})

    def get(self, url: str, params: dict[str, Any]) -> tuple[int, str]:
        r = self.session.get(url, params=params)
        return r.status_code, r.text

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass


class PlaywrightTransport:
    """開 Chromium 載入 watsons.com.tw，之後所有 API 呼叫都在該頁面裡用 fetch() 完成（等同真人瀏覽）。"""

    name = "playwright"

    def __init__(self, timeout: float = 30.0, headless: bool = True, profile_dir: Path | None = None):
        from playwright.sync_api import sync_playwright  # type: ignore

        self.timeout_ms = int(timeout * 1000)
        self._pw = sync_playwright().start()
        self.profile_dir = profile_dir
        self.ctx = None
        last_err: Exception | None = None
        # 先用系統的 Google Chrome（指紋最像真人、不用下載），沒有再用內建 Chromium
        for attempt, kwargs in enumerate(({"channel": "chrome"}, {}, {"_install": True})):
            try:
                if kwargs.pop("_install", False):
                    log.info("playwright: 下載 Chromium（只需一次）…")
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
                args = dict(headless=headless, locale="zh-TW", viewport={"width": 1280, "height": 900}, **kwargs)
                if profile_dir:
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    self.ctx = self._pw.chromium.launch_persistent_context(str(profile_dir), **args)
                else:
                    self.browser = self._pw.chromium.launch(**{k: v for k, v in args.items() if k in ("headless", "channel")})
                    self.ctx = self.browser.new_context(locale="zh-TW", viewport={"width": 1280, "height": 900})
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if self.ctx is None:
            self._pw.stop()
            raise WatsonsError(f"無法啟動 Playwright 瀏覽器：{last_err}（請執行 python -m playwright install chromium）")
        self.page = self.ctx.new_page()
        self.page.goto(SITE_URL + "/", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(2500)

    def get(self, url: str, params: dict[str, Any]) -> tuple[int, str]:
        full = url + "?" + urlencode(params)
        status, text = self.page.evaluate(
            "async (u) => { const r = await fetch(u); const t = await r.text(); return [r.status, t]; }", full
        )
        return int(status), text

    def close(self) -> None:
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


def available_transports() -> list[str]:
    out = []
    try:
        import curl_cffi  # noqa: F401

        out.append("curl_cffi")
    except ImportError:
        pass
    out.append("httpx")
    try:
        import playwright  # noqa: F401

        out.append("playwright")
    except ImportError:
        pass
    return out


def make_transport(name: str, timeout: float = 30.0, headless: bool = True, profile_dir: Path | None = None):
    if name == "curl_cffi":
        return CurlCffiTransport(timeout)
    if name == "playwright":
        return PlaywrightTransport(timeout, headless=headless, profile_dir=profile_dir)
    return HttpxTransport(timeout)


class WatsonsClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        delay_sec: float = 0.6,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: str = "auto",
        headless: bool = True,
        profile_dir: Path | None = None,
    ):
        self.delay_sec = delay_sec
        self.timeout = timeout
        self.max_retries = max_retries
        self.headless = headless
        self.profile_dir = profile_dir
        self._last_call = 0.0
        self.calls = 0
        self.transport_name = transport
        if client is not None:  # 測試注入
            self._transport = HttpxTransport(timeout)
            self._transport.client = client
            self._chain: list[str] = []
        elif transport == "auto":
            avail = available_transports()
            # curl_cffi → playwright；沒有 curl_cffi 時先試 httpx 再 playwright
            chain = [t for t in ("curl_cffi", "httpx", "playwright") if t in avail]
            if "curl_cffi" in chain:
                chain.remove("httpx")
            self._chain = chain
            self._transport = make_transport(self._chain.pop(0), timeout, headless, profile_dir)
        else:
            self._chain = []
            self._transport = make_transport(transport, timeout, headless, profile_dir)

    @property
    def client(self):  # 舊介面相容
        return getattr(self._transport, "client", None)

    @property
    def active_transport(self) -> str:
        return self._transport.name

    # ------------------------------------------------------------------ low level
    def close(self) -> None:
        self._transport.close()

    def _escalate(self, reason: str) -> bool:
        """403 時換下一種傳輸方式；沒有可換的回傳 False。"""
        if not self._chain:
            return False
        nxt = self._chain.pop(0)
        log.warning("watsons: %s 被擋（%s），改用 %s", self._transport.name, reason, nxt)
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            pass
        self._transport = make_transport(nxt, self.timeout, self.headless, self.profile_dir)
        return True

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = dict(COMMON_PARAMS)
        if params:
            p.update(params)
        url = BASE_URL + path
        attempt = 0
        while attempt < self.max_retries:
            attempt += 1
            wait = self.delay_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                status, text = self._transport.get(url, p)
                self._last_call = time.monotonic()
                self.calls += 1
                if status == 200:
                    import json as _json

                    return _json.loads(text)
                if status == 403 and "Access Denied" in text:
                    if self._escalate("403 Access Denied"):
                        attempt -= 1  # 換傳輸方式不算重試次數
                        continue
                    raise WatsonsBlocked(
                        f"GET {path} -> HTTP 403 Access Denied（Akamai 擋機器人）。已試過的傳輸方式都被擋；"
                        "請安裝 playwright（pip install -r requirements-playwright.txt && python -m playwright install chromium）"
                        "或把 promotions.json 的 scan.watsons_transport 設為 playwright 並關閉 headless。"
                    )
                if status in (429, 500, 502, 503, 504):
                    log.warning("watsons %s -> %s (attempt %d)", path, status, attempt)
                    time.sleep(2.0 * attempt)
                    continue
                raise WatsonsError(f"GET {path} -> HTTP {status}: {text[:200]}")
            except (httpx.TransportError, ValueError) as e:  # network / bad json
                log.warning("watsons %s error %s (attempt %d)", path, e, attempt)
                time.sleep(2.0 * attempt)
        raise WatsonsError(f"GET {path} failed after {self.max_retries} attempts")

    # ------------------------------------------------------------------ endpoints
    def search(
        self,
        query: str = ":relevance",
        page: int = 0,
        page_size: int = 100,
        fields: str = "FULL",
        **extra: Any,
    ) -> dict[str, Any]:
        params = {"fields": fields, "query": query, "pageSize": page_size, "currentPage": page}
        params.update(extra)
        return self._get("/products/search", params)

    def promotions(self) -> list[dict[str, Any]]:
        """回傳站上所有促銷 facet：[{name, count}]"""
        data = self.search(":relevance", page=0, page_size=1)
        for facet in data.get("facets", []):
            if facet.get("code") == "allPromotions":
                values = facet.get("values") or facet.get("topValues") or []
                return [{"name": v.get("name") or v.get("code"), "count": v.get("count", 0)} for v in values]
        return []

    def iter_promotion_products(
        self, promo_name: str, page_size: int = 100, max_pages: int = 40, sort: str = "bestSeller"
    ) -> Iterator[dict[str, Any]]:
        query = f":{sort}:allPromotions:{promo_name}"
        page = 0
        while page < max_pages:
            data = self.search(query, page=page, page_size=page_size)
            products = data.get("products") or []
            for p in products:
                yield p
            pag = data.get("pagination") or {}
            total_pages = int(pag.get("totalPages") or 0)
            page += 1
            if page >= total_pages or not products:
                break

    def products_by_codes(self, codes: Iterable[str]) -> list[dict[str, Any]]:
        codes = [c for c in codes if c]
        out: list[dict[str, Any]] = []
        for i in range(0, len(codes), 100):
            chunk = codes[i : i + 100]
            data = self.search(" ".join(chunk), page=0, page_size=len(chunk), productCodeOnly="true", ignoreSort="true", filterOOS="false")
            out.extend(data.get("products") or [])
        return out

    def coupons(self, variant_code: str) -> list[dict[str, Any]]:
        """全站折價券（需帶任一商品 variant code 才會回傳）。"""
        try:
            data = self._get("/users/anonymous/availableGrabCoupons", {"fields": "FULL", "product": variant_code})
        except WatsonsError as e:
            log.warning("coupons failed: %s", e)
            return []
        return [normalize_coupon(v) for v in data.get("vouchers") or []]

    def multibuy(self, variant_code: str) -> list[dict[str, Any]]:
        data = self._get(f"/products/{variant_code}/multiBuy", {"fields": "FULL"})
        return data.get("elabMultiBuyPromotionList") or []

    def cms_components(self, component_ids: list[str]) -> list[dict[str, Any]]:
        data = self._get("/users/anonymous/cms/components", {"componentIds": ",".join(component_ids)})
        return data.get("component") or []

    def cms_page(self, label: str) -> dict[str, Any]:
        return self._get("/users/anonymous/cms/pages", {"pageType": "ContentPage", "pageLabelOrId": label})

    def promo_page_product_codes(self, label: str) -> list[str]:
        """活動頁（例如 /promo-derma-1）上所有商品輪播的商品代碼。"""
        page = self.cms_page(label)
        comp_ids: list[str] = []
        for slot in (page.get("contentSlots") or {}).get("contentSlot") or []:
            for comp in (slot.get("components") or {}).get("component") or []:
                if comp.get("typeCode") == "E2ProductCarouselComponent":
                    comp_ids.append(comp["uid"])
        # Spartacus 的 SSR 版本結構不同：slots 直接在 page 裡
        for slot in (page.get("slots") or {}).values():
            for comp in slot.get("components") or []:
                if comp.get("typeCode") == "E2ProductCarouselComponent":
                    comp_ids.append(comp["uid"])
        codes: list[str] = []
        for i in range(0, len(comp_ids), 20):
            for comp in self.cms_components(comp_ids[i : i + 20]):
                codes.extend((comp.get("productCodes") or "").split())
        return list(dict.fromkeys(codes))


# ---------------------------------------------------------------------------- normalisation
_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return value


def _money(obj: Any) -> float | None:
    if isinstance(obj, dict) and obj.get("value") is not None:
        try:
            return float(obj["value"])
        except (TypeError, ValueError):
            return None
    return None


def normalize_coupon(v: dict[str, Any]) -> dict[str, Any]:
    name = v.get("name") or ""
    m = re.search(r"滿\s*\$?\s*([\d,]+).*?折\s*\$?\s*([\d,]+)", name)
    threshold = int(m.group(1).replace(",", "")) if m else None
    value = v.get("value")
    if value is None and m:
        value = int(m.group(2).replace(",", ""))
    return {
        "code": v.get("code") or v.get("voucherCode"),
        "name": name,
        "threshold": threshold,
        "value": float(value) if value is not None else None,
        "type": v.get("discountType"),
        "free_shipping": bool(v.get("freeShipping")),
        "start": _parse_dt(v.get("startDate")),
        "end": _parse_dt(v.get("endDate")),
        "member_only": bool(v.get("isMemberPromotion") or v.get("isEliteMemberPromotion")),
        "online_only": bool(v.get("onlineExclusive")),
    }


def normalize_product(raw: dict[str, Any], promo_name: str | None = None) -> dict[str, Any]:
    """把 OCC 商品 JSON 壓成本專案用的精簡結構。"""
    price = _money(raw.get("price"))
    list_price = _money(raw.get("elabPrice")) or _money(raw.get("elabOldPrice")) or price
    md = raw.get("elabMarkDownPrice") or {}
    multi = []
    for m in raw.get("elabFirstMultiBuyDatas") or []:
        qty = int(m.get("quantity") or 0)
        total = _money(m.get("totalDiscountedPrice"))
        avg = _money(m.get("avgDiscountedPrice"))
        if qty >= 1 and total is not None:
            multi.append(
                {
                    "qty": qty,
                    "total": round(total, 2),
                    "avg": round(avg if avg is not None else total / qty, 2),
                    "base_price": _money(m.get("basePrice")),
                    "start": _parse_dt(m.get("startDate")),
                    "end": _parse_dt(m.get("endDate")),
                }
            )
    top = raw.get("topPromotion") or {}
    tag = raw.get("promotionFirstTag") or (top.get("tag") or {}).get("label")
    cats = [c.get("name") for c in raw.get("categoryNameLevels") or [] if c.get("name")]
    images = raw.get("images") or []
    image = images[0].get("url") if images else None
    if image and image.startswith("/"):
        image = "https://medias.watsons.com.tw" + image
    brand = (raw.get("masterBrand") or {}).get("name")
    promos: list[str] = []
    if tag:
        promos.append(tag)
    for t in raw.get("promotionTags") or []:
        label = t.get("label") if isinstance(t, dict) else t
        if label and label not in promos:
            promos.append(label)
    if promo_name and promo_name not in promos:
        promos.append(promo_name)
    stock = (raw.get("stock") or {}).get("stockLevelStatus")
    return {
        "code": raw.get("code"),
        "variant": raw.get("defaultVariantCode") or (raw.get("code") or "").replace("BP_", ""),
        "name": raw.get("elabProductName") or raw.get("name"),
        "brand": brand,
        "ean": raw.get("ean"),
        "url": SITE_URL + raw["url"] if raw.get("url") else None,
        "image": image,
        "category": cats,
        "category_path": raw.get("gtmCategoryPath") or "/".join(cats),
        "price": price,
        "list_price": list_price,
        "markdown_rate": (md.get("discountRate") or 0) / 100.0 if md else 0.0,
        "multi_buy": multi,
        "promo_tag": tag,
        "promo_title": top.get("title"),
        "promo_start": _parse_dt(raw.get("promotionFirstTagStartDate")),
        "promo_end": _parse_dt(raw.get("promotionFirstTagEndDate")),
        "promotions": promos,
        "flags": {
            "flash": bool(top.get("isFlashSalePromotionFlag")),
            "member": bool(top.get("isMemberPromotionFlag")),
            "elite": bool(top.get("isEliteMemberPromotionFlag")),
            "outlet": bool(raw.get("elabIsOutlet")),
            "store_only": bool(raw.get("elabIsStoreOnly")),
            "adult": bool(raw.get("elabIsAdultOnly")),
        },
        "in_stock": stock == "inStock",
        "sold": raw.get("sellQuantity"),
        "max_qty": raw.get("elabMaxOrderQuantity") or raw.get("maxOrderQuantity") or 0,
        "rating": raw.get("averageRating"),
        "reviews": raw.get("productNumberOfReview") or raw.get("numberOfReviews"),
    }


def merge_product(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """同一商品在多個促銷下出現 → 合併促銷清單，其餘以最新為準。"""
    promos = list(dict.fromkeys((existing.get("promotions") or []) + (incoming.get("promotions") or [])))
    merged = dict(existing)
    merged.update({k: v for k, v in incoming.items() if v not in (None, [], "")})
    merged["promotions"] = promos
    return merged
