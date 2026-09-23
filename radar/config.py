"""設定檔載入：config/*.json（可被使用者修改）＋ .env / 環境變數（密鑰）。

所有設定都有內建預設值，缺檔也能跑；使用者改過的值會覆蓋預設。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
WEB_DATA_DIR = WEB_DIR / "data"

# --------------------------------------------------------------------------------------
# 預設值（與 config/*.json 的結構一致；README 有逐項說明）
# --------------------------------------------------------------------------------------

DEFAULT_FEES: dict[str, Any] = {
    # 蝦皮賣家成本（請至蝦皮賣家中心確認最新費率；此為 2025 年常見一般賣家設定）
    "shopee": {
        "commission_rate": 0.055,        # 成交手續費
        "payment_rate": 0.02,            # 金流與系統處理費
        "free_shipping_program_rate": 0.0,  # 若有加入免運活動，填該活動的成交費率（例如 0.03）
        "packaging_cost": 5,             # 每單包材（元）
        "shipping_subsidy": 0,           # 每單賣家自行吸收的運費（元）
        "commission_cap_per_item": 0,    # 成交手續費單件上限（0 = 不設）
    },
    # 屈臣氏線上購物的運費與門檻（官網公告：單筆滿 $688 宅配免運）
    "watsons_shipping": {
        "mode": "home_delivery",
        "home_delivery": {"free_threshold": 688, "fee": 60},
        "store_pickup": {"free_threshold": 0, "fee": 0},
    },
    # 寵i 會員點數：每消費 $1 累積 1 點；300 點折抵 $1（可調整）
    "points": {"enabled": True, "earn_per_dollar": 1, "points_per_dollar_value": 300},
    # 利潤判斷
    "profit": {
        "alert_threshold": 0.30,        # 利潤 ≥ 30% 通知
        "metric": "roi",                # roi = 利潤/成本；margin = 利潤/售價
        "reference": "low3_median",     # 蝦皮參考價：最低 3 筆的中位數
        "min_listings": 2,              # 至少幾筆有效蝦皮列表才視為可靠
        "min_match_score": 0.45,        # 名稱相似度門檻
    },
    # 計算「有效單位成本」時的訂單假設（折價券／免運／刷卡門檻都跟訂單金額有關）
    "assumptions": {
        "order_amount": 1600,           # 假設一單買到這個金額
        "use_coupon": True,
        "use_card": True,
        "use_points": True,
    },
}

DEFAULT_PROMOTIONS: dict[str, Any] = {
    # 掃描哪些促銷：include 為空 = 全部（扣掉 exclude）
    "scan": {
        "include": [],
        "exclude": [
            "滿$100送數位印花",           # 幾乎全站，沒有價格意義
            "刷國泰卡滿$888送3萬點",       # 信用卡促銷改由 cards 模組處理
            "刷玉山卡滿$888送3萬點",
            "健康商品滿$1200送$100",
            "開架彩妝滿$688送擦手巾",
            "髮類商品滿$688送摺疊杯袋",
            "醫美/護膚滿$388送好禮",
            "醫美/護膚滿$888送$150",
            "衛生棉/保健食品滿$429送好禮",
            "專櫃指定品牌滿2000送$200",
            "蘇菲滿$299折$30",             # 品牌滿額折，引擎未建模
            "會員點數2倍送",               # 純點數倍率（價值 <1%），不另外掃描
            "點金會員點數10倍",
            "醫美商品點數6倍送",
            "官網不限金額享88折",          # 7000+ 件，改用商品自帶的 promo 標籤判斷
        ],
        "page_size": 100,
        "max_pages_per_promo": 40,
        "request_delay_sec": 0.6,
        "only_in_stock": True,
        # 屈臣氏 API 的傳輸方式：auto（curl_cffi → playwright）| curl_cffi | playwright | httpx
        # Akamai 擋純 Python 的 TLS 指紋（httpx 會 403），auto 會自動換方式
        "watsons_transport": "auto",
        "playwright_headless": True,
    },
    # 結帳時才套用的整體折扣（售價欄位不含）。取最優者，不疊加（stack_multipliers=false）
    "checkout_multipliers": {
        "官網不限金額享88折": 0.88,
        "醫美商品85折": 0.85,
        "開架彩妝85折": 0.85,
        "寵i會員9折": 0.90,
    },
    "stack_multipliers": False,
    "member_only": ["寵i會員9折", "寵i會員獨享價", "點金會員現折$30", "點金會員點數10倍", "會員點數2倍送", "醫美商品點數6倍送"],
    "points_multipliers": {"會員點數2倍送": 2, "醫美商品點數6倍送": 6, "點金會員點數10倍": 10},
    # 屈臣氏站上的刷卡活動（門檻 → 送點數）
    "card_promos": {
        "刷國泰卡滿$888送3萬點": {"issuer": "國泰", "threshold": 888, "points": 30000},
        "刷玉山卡滿$888送3萬點": {"issuer": "玉山", "threshold": 888, "points": 30000},
    },
    # 蝦皮查價：只查通過門檻的候選商品，並做快取
    "shopee_lookup": {
        "provider": "biggo",
        "max_lookups_per_run": 150,
        "cache_hours": 48,
        "min_discount_depth": 0.25,      # (原價 - 最佳單位成本)/原價 ≥ 25% 才去查
        "min_price": 40,                 # 太便宜的不值得賣
        "prefer_promos": ["任選兩件享買一送一，數量請選2件", "第2件4折", "清貨1折", "清貨3折", "清貨5折", "今日超殺價", "售價再享折扣"],
        "request_delay_sec": 3.0,        # BigGo 約 80 次/2.5 分鐘就會限流；放慢並加隨機抖動，被擋會冷卻再試
    },
}

DEFAULT_CARDS: dict[str, Any] = {
    "cards": [
        {
            "id": "cube",
            "name": "國泰 CUBE 卡（範例）",
            "issuer": "國泰",
            "enabled": True,
            "base_rate": 0.005,
            "rules": [
                {
                    "name": "指定方案加碼（如：樂饗購／玩數位）",
                    "rate": 0.03,
                    "channels": ["屈臣氏", "網購"],
                    "cap_reward": 0,
                    "min_spend": 0,
                    "notes": "請依當期方案調整；此為範例值",
                }
            ],
        },
        {
            "id": "esun-unicard",
            "name": "玉山 Unicard（範例）",
            "issuer": "玉山",
            "enabled": True,
            "base_rate": 0.01,
            "rules": [{"name": "指定通路加碼（範例）", "rate": 0.02, "channels": ["屈臣氏", "網購"], "cap_reward": 300, "min_spend": 0, "notes": "範例值"}],
        },
        {
            "id": "taishin-gogo",
            "name": "台新 @GoGo 卡（範例）",
            "issuer": "台新",
            "enabled": False,
            "base_rate": 0.005,
            "rules": [{"name": "網購加碼（範例）", "rate": 0.03, "channels": ["網購"], "cap_reward": 0, "min_spend": 0, "notes": "範例值"}],
        },
    ],
    "channel": "屈臣氏",
}

DEFAULT_MATCHES: dict[str, Any] = {
    # code -> {"ref_price": 123, "note": "...", "exclude_ids": [...], "keyword": "自訂搜尋字"}
    "overrides": {}
}


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """讀取 config/<name>.json 並與預設值合併。name ∈ {fees, promotions, cards, matches}"""
    defaults = {
        "fees": DEFAULT_FEES,
        "promotions": DEFAULT_PROMOTIONS,
        "cards": DEFAULT_CARDS,
        "matches": DEFAULT_MATCHES,
    }[name]
    path = (config_dir or CONFIG_DIR) / f"{name}.json"
    user = _load_json(path)
    if not user:
        return copy.deepcopy(defaults)
    if name == "cards" and "cards" in user:
        # 卡片清單整份以使用者為準（不與範例合併）
        merged = _deep_merge(defaults, {k: v for k, v in user.items() if k != "cards"})
        merged["cards"] = copy.deepcopy(user["cards"])
        return merged
    return _deep_merge(defaults, user)


def save_config(name: str, data: dict[str, Any], config_dir: Path | None = None) -> Path:
    d = config_dir or CONFIG_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_all(config_dir: Path | None = None) -> dict[str, Any]:
    return {n: load_config(n, config_dir) for n in ("fees", "promotions", "cards", "matches")}


def load_env(path: Path | None = None) -> None:
    """極簡 .env 讀取（不依賴 python-dotenv）。已存在的環境變數不覆蓋。"""
    p = path or (ROOT / ".env")
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


def notify_settings() -> dict[str, str]:
    load_env()
    keys = [
        "NOTIFY_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_WEBHOOK_URL",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_TO_USER_ID",
    ]
    return {k: os.environ.get(k, "") for k in keys}
