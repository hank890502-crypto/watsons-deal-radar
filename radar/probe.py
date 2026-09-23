"""資料來源連線探測：從目前環境（本機或 GitHub Actions）試打屈臣氏與 BigGo，記錄狀態碼。"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .alerts import now_iso
from .config import DATA_DIR
from .shopee import BIGGO_HEADERS, parse_biggo_html
from .watsons import BASE_URL, COMMON_PARAMS, DEFAULT_HEADERS, available_transports, make_transport

TARGETS = {
    "watsons_search": (BASE_URL + "/products/search?fields=BASIC&query=%3Arelevance&pageSize=1&currentPage=0&lang=zh_TW&curr=TWD", DEFAULT_HEADERS),
    "biggo_shopee": ("https://biggo.com.tw/s/%E8%88%92%E9%85%B8%E5%AE%9A%E7%89%99%E8%86%8F?m=cp&c%5B%5D=tw_bid_shopee&c%5B%5D=tw_mall_shopeemall", BIGGO_HEADERS),
}


def probe(timeout: float = 25.0) -> dict[str, Any]:
    out: dict[str, Any] = {"at": now_iso(), "results": {}}
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        for name, (url, headers) in TARGETS.items():
            t0 = time.time()
            rec: dict[str, Any] = {"url": url}
            try:
                r = c.get(url, headers=headers)
                rec["status"] = r.status_code
                rec["elapsed_ms"] = int((time.time() - t0) * 1000)
                rec["content_type"] = r.headers.get("content-type", "")
                rec["server"] = r.headers.get("server", "")
                body = r.text
                rec["body_head"] = body[:200]
                if name == "watsons_search":
                    rec["ok"] = r.status_code == 200 and '"products"' in body
                else:
                    ssr = parse_biggo_html(body) if r.status_code == 200 else {}
                    rec["ok"] = bool(ssr)
                    rec["listings"] = len(ssr.get("list") or []) if ssr else 0
            except Exception as e:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = f"{type(e).__name__}: {e}"
                rec["elapsed_ms"] = int((time.time() - t0) * 1000)
            out["results"][name] = rec
    # 屈臣氏：逐一測試可用的傳輸方式（Akamai 擋純 Python TLS 指紋時，curl_cffi / playwright 可繞過）
    params = dict(COMMON_PARAMS, fields="BASIC", query=":relevance", pageSize=1, currentPage=0)
    out["watsons_transports"] = {}
    for name in available_transports():
        t0 = time.time()
        rec = {}
        try:
            tr = make_transport(name, timeout=timeout, headless=True, profile_dir=DATA_DIR / "watsons_profile")
            try:
                status, body = tr.get(BASE_URL + "/products/search", params)
            finally:
                tr.close()
            rec = {"status": status, "ok": status == 200 and '"products"' in body, "body_head": body[:120]}
        except Exception as e:  # noqa: BLE001
            rec = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        rec["elapsed_ms"] = int((time.time() - t0) * 1000)
        out["watsons_transports"][name] = rec
    out["watsons_working_transport"] = next((n for n, r in out["watsons_transports"].items() if r.get("ok")), None)
    out["ok"] = bool(out["watsons_working_transport"]) and out["results"]["biggo_shopee"].get("ok", False)
    return out
