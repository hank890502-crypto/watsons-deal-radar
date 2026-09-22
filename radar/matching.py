"""商品名稱正規化、多入組偵測、屈臣氏 ↔ 蝦皮列表相似度計算、參考價選擇。"""
from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Any

# 明顯不是「同一個正常商品」的關鍵字（即期、NG 品、旅行組、贈品、試用…）
VARIANT_PENALTY_WORDS = [
    "即期", "NG", "出清", "福利品", "瑕疵", "凹", "試用", "旅行", "體驗", "贈品", "小樣", "分裝",
    "空瓶", "空盒", "補充包", "替換", "替芯", "組合", "禮盒", "套組", "任選", "隨機", "二手", "代購",
]
UNIT_WORDS = "入|條|支|瓶|盒|罐|包|片|件|袋|串|組|捲|顆|粒|張|抽|雙|對|份|個|杯|桶|盤|塊|枚|球"
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|mL|ML|l|L|g|G|kg|KG|公克|毫升|cc|CC|公升|吋|片裝)")
_QTY_X_RE = re.compile(r"(?:[x×X\*＊]\s*(\d{1,2}))(?!\d)|(?:(\d{1,2})\s*(?:入組|入裝|組入|件組|件裝|入$))")
_NUM_UNIT_RE = re.compile(rf"(\d{{1,4}})\s*({UNIT_WORDS})")
_NOISE_RE = re.compile(r"[【】\[\]（）()〈〉《》「」『』｜|\-–—_/\\,，.。、:：;；!！?？~～+＋'\"“”‘’#＃&＆%％$＄@＠^*＊·•]+")
_SPACE_RE = re.compile(r"\s+")


def to_halfwidth(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def brand_parts(brand: str | None) -> list[str]:
    """把「KOTEX靠得住」「L`OREAL PARIS 巴黎萊雅」拆成 ["KOTEX","靠得住"] / ["L`OREAL","PARIS","巴黎萊雅"]。"""
    if not brand:
        return []
    b = to_halfwidth(brand)
    out: list[str] = []
    for tok in re.split(r"\s+", b):
        if not tok:
            continue
        out.extend(x for x in re.findall(r"[\u4e00-\u9fff]+|[^\u4e00-\u9fff]+", tok) if x.strip())
    return [x for x in out if len(x) >= 2]


def normalize_title(s: str | None) -> str:
    if not s:
        return ""
    s = to_halfwidth(s).lower()
    s = _NOISE_RE.sub(" ", s)
    s = _SPACE_RE.sub("", s)
    return s


def clean_keyword(name: str | None, brand: str | None = None, max_len: int = 32) -> str:
    """把屈臣氏商品名壓成適合搜尋的關鍵字（去括號備註、去重複品牌、限制長度）。"""
    if not name:
        return ""
    s = to_halfwidth(name)
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)          # (包裝隨機出貨) 之類
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)
    s = re.sub(r"\s*-\s*", " ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    if brand:
        # 品牌欄常是「KOTEX靠得住」這種雙語，若名稱已含其中一部分就不再前綴
        parts = brand_parts(brand)
        if parts and not any(p.lower() in s.lower() for p in parts):
            cjk = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]
            s = f"{(cjk or parts)[-1]} {s}"
    if len(s) > max_len:
        cut = s[:max_len]
        # 避免截在數字/單位中間
        cut = re.sub(r"[\d.]+$", "", cut)
        s = cut.strip()
    return s


def sizes(s: str) -> set[str]:
    out = set()
    for num, unit in _SIZE_RE.findall(to_halfwidth(s or "")):
        unit = unit.lower()
        unit = {"公克": "g", "毫升": "ml", "cc": "ml", "公升": "l", "kg": "kg"}.get(unit, unit)
        try:
            n = float(num)
        except ValueError:
            continue
        out.add(f"{n:g}{unit}")
    return out


def num_units(s: str) -> set[str]:
    return {f"{n}{u}" for n, u in _NUM_UNIT_RE.findall(to_halfwidth(s or ""))}


def detect_pack_qty(listing_title: str, base_title: str) -> int:
    """偵測蝦皮列表是否為多入組。回傳每筆列表包含幾個「屈臣氏單品」。

    規則：
      1. 「x3」「*2」「3入組」→ 該數字（2..24）
      2. 列表出現「N入/N組/N條…」且 N 不在屈臣氏名稱裡（屈臣氏名稱本身可能含 30包）→ N
    """
    lt = to_halfwidth(listing_title or "")
    bt = to_halfwidth(base_title or "")
    for m in _QTY_X_RE.finditer(lt):
        n = m.group(1) or m.group(2)
        if n and 2 <= int(n) <= 24:
            return int(n)
    base_units = num_units(bt)
    base_nums = set(re.findall(r"\d+", bt))
    for n, u in _NUM_UNIT_RE.findall(lt):
        token = f"{n}{u}"
        if token in base_units or n in base_nums:
            continue
        if u in ("入", "組", "條", "支", "瓶", "盒", "罐", "件", "袋", "串") and 2 <= int(n) <= 24:
            return int(n)
    return 1


def bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    """字元 bigram Dice 相似度（中文商品名效果比 token 好）。"""
    ba, bb = bigrams(normalize_title(a)), bigrams(normalize_title(b))
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def match_score(product: dict[str, Any], listing_title: str) -> dict[str, Any]:
    """回傳 {score, sim, size_ok, brand_ok, penalty, pack_qty}。score ∈ [0, 1]。"""
    name = product.get("name") or ""
    brand = product.get("brand") or ""
    sim = similarity(name, listing_title)
    ps, ls = sizes(name), sizes(listing_title)
    size_ok = True if not ps else bool(ps & ls) if ls else None  # None = 列表沒寫容量，不加不減
    bparts = brand_parts(brand)
    lt = to_halfwidth(listing_title).lower()
    brand_ok = any(p.lower() in lt for p in bparts) if bparts else None
    penalty_hits = [w for w in VARIANT_PENALTY_WORDS if w.lower() in lt and w.lower() not in to_halfwidth(name).lower()]
    score = sim
    if size_ok is False:
        score -= 0.25
    elif size_ok:
        score += 0.08
    if brand_ok:
        score += 0.08
    elif brand_ok is False:
        score -= 0.05
    score -= 0.12 * len(penalty_hits)
    score = max(0.0, min(1.0, score))
    return {
        "score": round(score, 3),
        "sim": round(sim, 3),
        "size_ok": size_ok,
        "brand_ok": brand_ok,
        "penalty": penalty_hits,
        "pack_qty": detect_pack_qty(listing_title, name),
    }


def pick_reference(
    product: dict[str, Any],
    listings: list[dict[str, Any]],
    min_score: float = 0.45,
    method: str = "low3_median",
    min_listings: int = 2,
    exclude_ids: set[str] | None = None,
) -> dict[str, Any]:
    """從蝦皮列表挑出參考售價。

    回傳 {ref_price, method, n_matched, n_total, min, median, official, candidates:[...]}
    candidates 依單位價排序，含 score / pack_qty / unit_price，供前端人工確認。
    """
    exclude_ids = exclude_ids or set()
    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for l in listings:
        lid = l.get("id") or l.get("url") or l.get("title")
        if not lid or lid in seen:
            continue
        seen.add(lid)
        price = l.get("price")
        if not price or price <= 0:
            continue
        ms = match_score(product, l.get("title") or "")
        unit_price = round(price / ms["pack_qty"], 2)
        c = dict(l)
        c.update(ms)
        c["unit_price"] = unit_price
        c["excluded"] = lid in exclude_ids
        c["matched"] = (ms["score"] >= min_score) and not l.get("offline") and not c["excluded"]
        cands.append(c)
    cands.sort(key=lambda c: (not c["matched"], c["unit_price"]))
    matched = [c for c in cands if c["matched"]]
    official = None
    for c in matched:
        if "屈臣氏" in (c.get("shop") or "") or "watsons" in (c.get("shop") or "").lower():
            official = c["unit_price"]
            break
    prices = [c["unit_price"] for c in matched]
    ref = None
    if len(prices) >= max(1, min_listings):
        low = sorted(prices)[:3]
        if method == "min":
            ref = low[0]
        elif method == "median":
            ref = statistics.median(prices)
        else:  # low3_median
            ref = statistics.median(low)
    return {
        "ref_price": round(ref, 2) if ref is not None else None,
        "method": method,
        "n_matched": len(matched),
        "n_total": len(cands),
        "min": min(prices) if prices else None,
        "median": statistics.median(prices) if prices else None,
        "official": official,
        "candidates": cands[:40],
    }
