"""利潤達標通知：去重、訊息格式、推播（generic webhook / Telegram / Discord / LINE Messaging API）。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def select_hot(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hot = [p for p in products if (p.get("eval") or {}).get("hot")]
    hot.sort(key=lambda p: p["eval"].get("roi") or 0, reverse=True)
    return hot


def dedupe(hot: list[dict[str, Any]], state: dict[str, Any], cooldown_hours: float = 24, improve_pts: float = 0.10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """同一商品 cooldown 內不重複通知，除非 ROI 又提高 improve_pts 以上。回傳 (新通知, 新 state)。"""
    now = datetime.now(TZ)
    fresh: list[dict[str, Any]] = []
    for p in hot:
        code = p["code"]
        roi = float(p["eval"].get("roi") or 0)
        prev = state.get(code)
        should = True
        if prev:
            try:
                last = datetime.fromisoformat(prev["ts"])
            except (KeyError, ValueError):
                last = now - timedelta(days=365)
            within = now - last < timedelta(hours=cooldown_hours)
            improved = roi >= float(prev.get("roi") or 0) + improve_pts
            should = (not within) or improved
        if should:
            fresh.append(p)
            state[code] = {"ts": now.replace(microsecond=0).isoformat(), "roi": roi, "unit_cost": p["eval"].get("unit_cost"), "ref_price": p["eval"].get("ref_price")}
    return fresh, state


def alert_records(hot: list[dict[str, Any]], stamp: str) -> list[dict[str, Any]]:
    out = []
    for p in hot:
        e = p["eval"]
        sp = p.get("shopee") or {}
        top = (sp.get("candidates") or [{}])[0]
        out.append(
            {
                "ts": stamp,
                "code": p["code"],
                "name": p.get("name"),
                "brand": p.get("brand"),
                "promo": p.get("promo_tag"),
                "price": p.get("price"),
                "buy_qty": (p.get("cost") or {}).get("qty"),
                "unit_cost": e.get("unit_cost"),
                "ref_price": e.get("ref_price"),
                "net": e.get("net"),
                "profit": e.get("profit"),
                "roi": e.get("roi"),
                "margin": e.get("margin"),
                "n_matched": sp.get("n_matched"),
                "url": p.get("url"),
                "shopee_url": top.get("url"),
                "image": p.get("image"),
            }
        )
    return out


def format_text(records: list[dict[str, Any]], generated_at: str, limit: int = 15, dashboard_url: str | None = None) -> str:
    if not records:
        return ""
    lines = [f"🔥 屈臣氏優惠雷達：{len(records)} 件商品利潤達標（{generated_at[:16].replace('T', ' ')}）"]
    for r in records[:limit]:
        qty = f"買{r['buy_qty']}件" if r.get("buy_qty") and r["buy_qty"] > 1 else "單買"
        lines.append(
            f"• {r['name']}\n"
            f"  {qty} 成本 ${r['unit_cost']:.0f} → 蝦皮 ${r['ref_price']:.0f}（淨收 ${r['net']:.0f}）"
            f" 利潤 ${r['profit']:.0f} / ROI {r['roi']*100:.0f}%｜{r.get('promo') or ''}\n"
            f"  {r.get('url') or ''}"
        )
    if len(records) > limit:
        lines.append(f"…另有 {len(records) - limit} 件，詳見儀表板")
    if dashboard_url:
        lines.append(dashboard_url)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- notifiers
class Notifier:
    def __init__(self, settings: dict[str, str], timeout: float = 20.0):
        self.s = settings
        self.client = httpx.Client(timeout=timeout)

    def channels(self) -> list[str]:
        ch = []
        if self.s.get("NOTIFY_WEBHOOK_URL"):
            ch.append("webhook")
        if self.s.get("TELEGRAM_BOT_TOKEN") and self.s.get("TELEGRAM_CHAT_ID"):
            ch.append("telegram")
        if self.s.get("DISCORD_WEBHOOK_URL"):
            ch.append("discord")
        if self.s.get("LINE_CHANNEL_ACCESS_TOKEN") and self.s.get("LINE_TO_USER_ID"):
            ch.append("line")
        return ch

    def send(self, text: str, records: list[dict[str, Any]], generated_at: str) -> dict[str, str]:
        results: dict[str, str] = {}
        for ch in self.channels():
            try:
                getattr(self, f"_send_{ch}")(text, records, generated_at)
                results[ch] = "ok"
            except Exception as e:  # noqa: BLE001
                log.warning("notify %s failed: %s", ch, e)
                results[ch] = f"error: {e}"
        return results

    def _send_webhook(self, text: str, records: list[dict[str, Any]], generated_at: str) -> None:
        r = self.client.post(self.s["NOTIFY_WEBHOOK_URL"], json={"source": "watsons-deal-radar", "generated_at": generated_at, "text": text, "count": len(records), "items": records})
        r.raise_for_status()

    def _send_telegram(self, text: str, records: list[dict[str, Any]], generated_at: str) -> None:
        url = f"https://api.telegram.org/bot{self.s['TELEGRAM_BOT_TOKEN']}/sendMessage"
        for chunk in _chunks(text, 3800):
            r = self.client.post(url, json={"chat_id": self.s["TELEGRAM_CHAT_ID"], "text": chunk, "disable_web_page_preview": True})
            r.raise_for_status()

    def _send_discord(self, text: str, records: list[dict[str, Any]], generated_at: str) -> None:
        for chunk in _chunks(text, 1900):
            r = self.client.post(self.s["DISCORD_WEBHOOK_URL"], json={"content": chunk})
            r.raise_for_status()

    def _send_line(self, text: str, records: list[dict[str, Any]], generated_at: str) -> None:
        headers = {"Authorization": f"Bearer {self.s['LINE_CHANNEL_ACCESS_TOKEN']}"}
        msgs = [{"type": "text", "text": c} for c in _chunks(text, 4900)][:5]
        r = self.client.post("https://api.line.me/v2/bot/message/push", headers=headers, json={"to": self.s["LINE_TO_USER_ID"], "messages": msgs})
        r.raise_for_status()


def _chunks(text: str, size: int) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out or [text]
