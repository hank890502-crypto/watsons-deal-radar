"""信用卡回饋規則引擎。

卡片設定（config/cards.json）：
{
  "cards": [
    {"id": "cube", "name": "國泰 CUBE 卡", "issuer": "國泰", "enabled": true,
     "base_rate": 0.005,                       # 一般消費回饋率
     "rules": [                                 # 指定通路：rate 為「該通路的總回饋率」（取代 base_rate）
        {"name": "指定方案加碼", "rate": 0.03, "channels": ["屈臣氏", "網購"],
         "cap_reward": 0,                       # 加碼部分（rate - base_rate）每期上限，0 = 無上限
         "min_spend": 0, "notes": ""}
     ]}
  ],
  "channel": "屈臣氏"
}

屈臣氏站上的刷卡活動（config/promotions.json → card_promos）例如「刷國泰卡滿$888送3萬點」：
  依 issuer 關鍵字比對卡片，滿門檻即加上 points / points_per_dollar_value 的價值。
"""
from __future__ import annotations

from typing import Any


def _channel_matches(rule: dict[str, Any], channel: str) -> bool:
    chans = rule.get("channels") or []
    if not chans:
        return True
    return any(c and (c in channel or channel in c) for c in chans)


def card_reward(
    amount: float,
    card: dict[str, Any],
    channel: str = "屈臣氏",
    card_promos: dict[str, Any] | None = None,
    points_per_dollar_value: float = 300,
    used_caps: dict[str, float] | None = None,
) -> dict[str, Any]:
    """計算單張卡在此筆消費的回饋（元）。

    used_caps: {rule_key: 本期已用掉的加碼金額}，用來扣除上限（可選）。
    """
    used_caps = used_caps or {}
    base_rate = float(card.get("base_rate") or 0)
    details: list[dict[str, Any]] = []
    best_rate = base_rate
    best_rule: dict[str, Any] | None = None
    for rule in card.get("rules") or []:
        if not _channel_matches(rule, channel):
            continue
        if amount < float(rule.get("min_spend") or 0):
            continue
        rate = float(rule.get("rate") or 0)
        if rate > best_rate:
            best_rate, best_rule = rate, rule
    base_reward = amount * base_rate
    bonus = 0.0
    if best_rule is not None:
        bonus = amount * (best_rate - base_rate)
        cap = float(best_rule.get("cap_reward") or 0)
        if cap > 0:
            key = f"{card.get('id')}::{best_rule.get('name')}"
            remaining = max(0.0, cap - float(used_caps.get(key, 0)))
            bonus = min(bonus, remaining)
        details.append({"name": best_rule.get("name"), "rate": best_rate, "reward": round(bonus, 2), "capped": bool(best_rule.get("cap_reward"))})
    promo_reward = 0.0
    issuer = f"{card.get('issuer') or ''} {card.get('name') or ''}"
    for pname, p in (card_promos or {}).items():
        iss = p.get("issuer") or ""
        if iss and iss in issuer and amount >= float(p.get("threshold") or 0):
            value = float(p.get("points") or 0) / float(points_per_dollar_value or 300) if p.get("points") else float(p.get("value") or 0)
            promo_reward += value
            details.append({"name": pname, "reward": round(value, 2), "threshold": p.get("threshold")})
    total = base_reward + bonus + promo_reward
    return {
        "card_id": card.get("id"),
        "card": card.get("name"),
        "amount": round(amount, 2),
        "base_rate": base_rate,
        "base_reward": round(base_reward, 2),
        "bonus": round(bonus, 2),
        "promo_reward": round(promo_reward, 2),
        "reward": round(total, 2),
        "effective_rate": round(total / amount, 4) if amount > 0 else 0.0,
        "details": details,
    }


def rank_cards(
    amount: float,
    cards_cfg: dict[str, Any],
    card_promos: dict[str, Any] | None = None,
    points_per_dollar_value: float = 300,
) -> list[dict[str, Any]]:
    channel = cards_cfg.get("channel") or "屈臣氏"
    out = []
    for card in cards_cfg.get("cards") or []:
        if card.get("enabled") is False:
            continue
        out.append(card_reward(amount, card, channel, card_promos, points_per_dollar_value))
    out.sort(key=lambda r: r["reward"], reverse=True)
    return out


def best_card(amount: float, cards_cfg: dict[str, Any], card_promos: dict[str, Any] | None = None, points_per_dollar_value: float = 300) -> dict[str, Any] | None:
    ranked = rank_cards(amount, cards_cfg, card_promos, points_per_dollar_value)
    return ranked[0] if ranked else None
