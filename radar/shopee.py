"""蝦皮售價來源。

蝦皮官網搜尋現在必須登入（未登入會被導到 /verify/traffic/error），因此預設走比價網 BigGo：
  https://biggo.com.tw/s/<關鍵字>?m=cp&c[]=tw_bid_shopee&c[]=tw_mall_shopeemall   （蝦皮購物 + 蝦皮商城）
BigGo 是 Next.js SSR，商品清單以 RSC payload 內嵌在 HTML（`self.__next_f.push([1,"...ssrData..."])`）。

Provider 介面：search(keyword) -> list[Listing]
  Listing = {id, source, title, price, shop, url, is_ad, offline, image}

其他 provider：
  ManualProvider  — 讀 config/matches.json 的人工價（覆蓋任何自動結果）
  PlaywrightShopeeProvider — 用使用者自己的蝦皮登入（persistent profile）直接抓 search_items（選用）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

SHOPEE_INDEXES = ("tw_bid_shopee", "tw_mall_shopeemall")
BIGGO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


class Provider(Protocol):
    name: str

    def search(self, keyword: str) -> list[dict[str, Any]]: ...


# ----------------------------------------------------------------------------- BigGo parsing
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _extract_json_object(text: str, key: str) -> dict[str, Any] | None:
    """在字串裡找 `"key":{...}` 並用括號配對取出完整 JSON 物件（略過字串內容）。"""
    i = text.find(f'"{key}":')
    if i < 0:
        return None
    start = text.find("{", i)
    if start < 0:
        return None
    depth = 0
    k = start
    n = len(text)
    while k < n:
        c = text[k]
        if c == '"':
            k += 1
            while k < n and text[k] != '"':
                if text[k] == "\\":
                    k += 1
                k += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : k + 1])
                except json.JSONDecodeError:
                    return None
        k += 1
    return None


def parse_biggo_html(html: str) -> dict[str, Any]:
    """從 BigGo 搜尋頁 HTML 取出 ssrData（含 list/total/filter）。找不到回傳 {}。"""
    for m in _PUSH_RE.finditer(html):
        chunk = m.group(1)
        if "ssrData" not in chunk:
            continue
        try:
            inner = json.loads('"' + chunk + '"')  # 先解一次 JS 字串跳脫
        except json.JSONDecodeError:
            continue
        obj = _extract_json_object(inner, "ssrData")
        if obj and isinstance(obj.get("list"), list):
            return obj
    return {}


def normalize_biggo_item(it: dict[str, Any]) -> dict[str, Any] | None:
    nindex = it.get("nindex") or ""
    if nindex not in SHOPEE_INDEXES:
        return None
    try:
        price = float(it.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return None
    shop = (it.get("shop") or {}).get("name") or ""
    return {
        "id": it.get("history_id") or it.get("item_id") or it.get("purl"),
        "source": "shopee_mall" if nindex == "tw_mall_shopeemall" else "shopee",
        "title": it.get("title") or "",
        "price": price,
        "shop": shop,
        "url": it.get("purl") or "",
        "is_ad": bool(it.get("is_ad")),
        "offline": bool(it.get("is_offline")) or bool(it.get("is_expired")),
        "image": it.get("image"),
    }


class BigGoShopeeProvider:
    name = "biggo"

    def __init__(
        self,
        client: httpx.Client | None = None,
        delay_sec: float = 1.5,
        timeout: float = 30.0,
        cache_dir: Path | None = None,
        cache_hours: float = 48,
        sort: str | None = None,
        max_retries: int = 2,
    ):
        self._own = client is None
        self.client = client or httpx.Client(headers=BIGGO_HEADERS, timeout=timeout, follow_redirects=True)
        self.delay_sec = delay_sec
        self.cache_dir = cache_dir
        self.cache_hours = cache_hours
        self.sort = sort
        self.max_retries = max_retries
        self._last = 0.0
        self.calls = 0
        self.cache_hits = 0
        self.consecutive_failures = 0

    def close(self) -> None:
        if self._own:
            self.client.close()

    def url_for(self, keyword: str) -> str:
        q = quote(keyword.strip(), safe="")
        u = f"https://biggo.com.tw/s/{q}?m=cp&c%5B%5D=tw_bid_shopee&c%5B%5D=tw_mall_shopeemall"
        if self.sort:
            u += f"&sort={self.sort}"
        return u

    def _cache_path(self, keyword: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(f"{keyword}|{self.sort}".encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{h}.json"

    def _read_cache(self, keyword: str) -> list[dict[str, Any]] | None:
        p = self._cache_path(keyword)
        if not p or not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(data.get("ts", 0)) > self.cache_hours * 3600:
            return None
        return data.get("listings")

    def _write_cache(self, keyword: str, listings: list[dict[str, Any]]) -> None:
        p = self._cache_path(keyword)
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": time.time(), "keyword": keyword, "listings": listings}, ensure_ascii=False), encoding="utf-8")

    def fetch_html(self, keyword: str) -> str:
        url = self.url_for(keyword)
        for attempt in range(1, self.max_retries + 1):
            wait = self.delay_sec - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.client.get(url)
                self._last = time.monotonic()
                self.calls += 1
                if r.status_code == 200:
                    return r.text
                log.warning("biggo %s -> HTTP %s (attempt %d)", keyword, r.status_code, attempt)
                if r.status_code in (403, 429):
                    time.sleep(5.0 * attempt)
            except httpx.TransportError as e:
                log.warning("biggo %s error %s (attempt %d)", keyword, e, attempt)
                time.sleep(2.0 * attempt)
        return ""

    def search(self, keyword: str) -> list[dict[str, Any]]:
        cached = self._read_cache(keyword)
        if cached is not None:
            self.cache_hits += 1
            return cached
        html = self.fetch_html(keyword)
        if not html:
            self.consecutive_failures += 1
            return []
        ssr = parse_biggo_html(html)
        if not ssr:
            # 200 但沒有 ssrData：可能是驗證頁／版面改版 → 不寫快取，並計入失敗
            self.consecutive_failures += 1
            log.warning("biggo %s: 頁面沒有 ssrData（可能被擋或版面變更）", keyword)
            return []
        self.consecutive_failures = 0
        listings = [x for x in (normalize_biggo_item(it) for it in ssr.get("list") or []) if x]
        self._write_cache(keyword, listings)
        return listings


def prune_cache(cache_dir: Path, max_age_hours: float) -> int:
    """刪除過期的查價快取檔（依檔內 ts）。回傳刪除數。"""
    n = 0
    if not cache_dir.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    for f in cache_dir.glob("*.json"):
        try:
            ts = float(json.loads(f.read_text(encoding="utf-8")).get("ts", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            ts = 0
        if ts < cutoff:
            f.unlink(missing_ok=True)
            n += 1
    return n


class ManualProvider:
    """人工輸入：config/matches.json → overrides[code] = {"ref_price": 189, "note": "..."}"""

    name = "manual"

    def __init__(self, overrides: dict[str, Any] | None = None):
        self.overrides = overrides or {}

    def search(self, keyword: str) -> list[dict[str, Any]]:  # 介面相容，實際不用
        return []

    def ref_for(self, code: str) -> dict[str, Any] | None:
        o = self.overrides.get(code)
        if o and o.get("ref_price"):
            return {"ref_price": float(o["ref_price"]), "note": o.get("note", ""), "method": "manual"}
        return None


class PlaywrightShopeeProvider:
    """選用：以使用者自己登入的 Chromium profile 直接讀蝦皮搜尋 API 回應（需 `pip install playwright`）。

    第一次先執行 `python -m radar shopee-login` 手動登入；之後 scan 時加 `--shopee playwright`。
    """

    name = "playwright"

    def __init__(self, profile_dir: Path, headless: bool = True, delay_sec: float = 3.0, limit: int = 60):
        self.profile_dir = profile_dir
        self.headless = headless
        self.delay_sec = delay_sec
        self.limit = limit
        self._pw = None
        self._ctx = None
        self.calls = 0

    def _ensure(self):
        if self._ctx is not None:
            return
        from playwright.sync_api import sync_playwright  # type: ignore

        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless, locale="zh-TW", viewport={"width": 1280, "height": 900}
        )

    def close(self) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self._ctx = self._pw = None

    def search(self, keyword: str) -> list[dict[str, Any]]:
        self._ensure()
        assert self._ctx is not None
        page = self._ctx.new_page()
        captured: list[dict[str, Any]] = []

        def on_response(resp):
            if "/api/v4/search/search_items" in resp.url:
                try:
                    captured.append(resp.json())
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_response)
        try:
            page.goto(f"https://shopee.tw/search?keyword={quote(keyword)}", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(int(self.delay_sec * 1000))
        except Exception as e:  # noqa: BLE001
            log.warning("playwright shopee %s: %s", keyword, e)
        finally:
            page.close()
        self.calls += 1
        out: list[dict[str, Any]] = []
        for data in captured:
            for it in (data.get("items") or []):
                b = it.get("item_basic") or it
                price = (b.get("price") or 0) / 100000.0
                if price <= 0:
                    continue
                out.append(
                    {
                        "id": f"{b.get('shopid')}.{b.get('itemid')}",
                        "source": "shopee_mall" if b.get("is_official_shop") or b.get("shopee_verified") else "shopee",
                        "title": b.get("name") or "",
                        "price": price,
                        "shop": str(b.get("shopid") or ""),
                        "url": f"https://shopee.tw/product/{b.get('shopid')}/{b.get('itemid')}",
                        "is_ad": bool(b.get("is_adult") is False and it.get("adsid")),
                        "offline": (b.get("stock") or 1) <= 0,
                        "image": f"https://down-tw.img.susercontent.com/file/{b.get('image')}" if b.get("image") else None,
                    }
                )
        return out[: self.limit]


def make_provider(kind: str, cfg: dict[str, Any], cache_dir: Path) -> Provider:
    look = cfg.get("shopee_lookup", {})
    if kind == "playwright":
        return PlaywrightShopeeProvider(profile_dir=cache_dir.parent / "shopee_profile", delay_sec=float(look.get("request_delay_sec", 3)))
    if kind == "none":
        return ManualProvider({})
    return BigGoShopeeProvider(
        delay_sec=float(look.get("request_delay_sec", 1.5)),
        cache_dir=cache_dir,
        cache_hours=float(look.get("cache_hours", 48)),
    )
