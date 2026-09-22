"""命令列：

  python -m radar scan [--shopee biggo|playwright|none] [--max-promos N] [--max-pages N] [--max-lookups N] [--no-notify]
  python -m radar serve [--host 127.0.0.1] [--port 8765]
  python -m radar promos                      # 列出站上所有促銷與商品數
  python -m radar lookup BP_598686 [--keyword 自訂]   # 單一商品：成本 + 蝦皮參考價 + 利潤
  python -m radar notify-test                 # 發一則測試通知
  python -m radar shopee-login                # (選用) 開啟瀏覽器登入蝦皮，供 --shopee playwright 使用
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DATA_DIR, WEB_DATA_DIR, notify_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description="屈臣氏優惠雷達")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="完整掃描")
    s.add_argument("--shopee", default="biggo", choices=["biggo", "playwright", "none"])
    s.add_argument("--max-promos", type=int)
    s.add_argument("--max-pages", type=int)
    s.add_argument("--max-lookups", type=int)
    s.add_argument("--no-notify", action="store_true")
    s.add_argument("--dashboard-url", default=None)
    s.add_argument("--out", default=str(WEB_DATA_DIR))
    s.add_argument("--data", default=str(DATA_DIR))

    v = sub.add_parser("serve", help="本機網頁 App")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8765)

    sub.add_parser("promos", help="列出促銷")

    l = sub.add_parser("lookup", help="單一商品查價")
    l.add_argument("code")
    l.add_argument("--keyword")
    l.add_argument("--shopee", default="biggo", choices=["biggo", "playwright", "none"])

    sub.add_parser("notify-test", help="測試通知")
    sub.add_parser("shopee-login", help="登入蝦皮（Playwright）")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "scan":
        from .pipeline import ScanOptions, run

        summary = run(
            ScanOptions(
                shopee=args.shopee,
                max_promos=args.max_promos,
                max_pages=args.max_pages,
                max_lookups=args.max_lookups,
                notify=not args.no_notify,
                dashboard_url=args.dashboard_url,
                out_dir=Path(args.out),
                data_dir=Path(args.data),
            )
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "serve":
        import uvicorn

        from .server import app

        print(f"→ 開啟 http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if args.cmd == "promos":
        from .watsons import WatsonsClient

        c = WatsonsClient()
        for p in c.promotions():
            print(f"{p['count']:>6}  {p['name']}")
        return 0

    if args.cmd == "lookup":
        from .pipeline import ScanOptions, run

        summary = run(ScanOptions(shopee=args.shopee, notify=False, only_codes=[args.code], out_dir=Path("/tmp/radar-lookup"), data_dir=DATA_DIR))
        from .storage import read_json

        snap = read_json(Path("/tmp/radar-lookup/latest.json"), {})
        for p in snap.get("products", []):
            if p["code"] == args.code:
                print(json.dumps({k: p.get(k) for k in ("code", "name", "price", "list_price", "promotions", "multi_buy", "cost", "effective", "shopee", "eval")}, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if args.cmd == "notify-test":
        from .alerts import Notifier, now_iso

        n = Notifier(notify_settings())
        print("channels:", n.channels() or "（未設定任何通知管道，請先填 .env 或環境變數）")
        rec = [{"code": "TEST", "name": "測試商品", "buy_qty": 2, "unit_cost": 54.5, "ref_price": 109, "net": 96.3, "profit": 41.8, "roi": 0.77, "margin": 0.38, "promo": "任選兩件享買一送一", "url": "https://www.watsons.com.tw/"}]
        from .alerts import format_text

        print(n.send(format_text(rec, now_iso()), rec, now_iso()))
        return 0

    if args.cmd == "shopee-login":
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            print("請先安裝：pip install playwright && playwright install chromium", file=sys.stderr)
            return 1
        profile = DATA_DIR / "shopee_profile"
        profile.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(profile), headless=False, locale="zh-TW")
            page = ctx.new_page()
            page.goto("https://shopee.tw/buyer/login")
            print("請在開啟的瀏覽器完成登入，登入後回到此視窗按 Enter…")
            input()
            ctx.close()
        print("已儲存登入狀態於", profile)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
