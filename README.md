# 屈臣氏優惠雷達 Watsons Deal Radar

> 自動整理屈臣氏線上商店的所有促銷 → 用「多件優惠＋結帳折扣＋折價券＋信用卡回饋＋點數」算出每件商品的**真實成本** → 對比蝦皮售價 → 利潤 ≥ 30% 就標紅並推播。附購物車最佳化（怎麼買最划算、刷哪張卡）。

- **儀表板**：GitHub Pages 靜態網頁（手機可看），或本機一鍵啟動（互動功能更完整）
- **排程**：在你的 Mac／家用主機每天 09:00／15:00／21:00 掃描並推送資料，GitHub Actions 自動部署 Pages（屈臣氏擋 GitHub 的 IP，掃描不能在 Actions 上跑）
- **通知**：generic webhook（可接 n8n）／Telegram／Discord／LINE Messaging API

---

## 1. 規劃：這個工具怎麼做

### 1.1 目標拆解

| 你要的 | 對應做法 |
|---|---|
| 整理屈臣氏目前的優惠 | 屈臣氏官網背後是 SAP Commerce 的公開 API（不用登入、不用瀏覽器），促銷是一個 facet（`allPromotions`），把每個促銷底下的商品全部翻出來 |
| 搭配信用卡回饋，算出怎麼買最便宜 | 計價引擎：結帳整體折扣（如官網 88 折）→ 多件優惠 → 折價券 → 免運門檻 → 各卡回饋（含屈臣氏站上的「刷○○卡滿 $888 送 3 萬點」）→ 寵i 點數 |
| 對比蝦皮價格、判斷有無利潤 | 蝦皮搜尋必須登入，改用比價網 BigGo 的「蝦皮購物＋蝦皮商城」篩選結果；名稱相似度比對、多入組換算單價、取「最低 3 筆中位數」當參考價；扣掉蝦皮成交手續費、金流費、包材後算利潤 |
| 利潤 > 30% 時通知 | 每次掃描後比對門檻，24 小時內同一商品不重複通知（ROI 再提高 10 個百分點例外），推播到你設定的管道 |
| 網頁 App、上線到 GitHub | 純靜態前端（無框架、無 build）；掃描程式把 JSON 推回 repo，GitHub Actions 把 `web/` 部署成 GitHub Pages |

### 1.2 資料來源（已實測）

**屈臣氏 `https://api.watsons.com.tw/api/v2/wtctw/…`（OCC v2，匿名可用；台灣一般網路可連，GitHub Actions 的 IP 被 Akamai 擋 403）**

| 端點 | 用途 |
|---|---|
| `GET /products/search?fields=FULL&query=:relevance:allPromotions:<促銷名>&pageSize=100&currentPage=n` | 某促銷底下的商品（含價格、原價、折扣率、多件試算、促銷起訖、EAN、品牌、分類、庫存、銷量） |
| `GET /products/search?fields=FULL&query=:relevance&pageSize=1` → `facets[allPromotions].values` | 站上所有促銷名稱與商品數（掃描時約 31 種） |
| `GET /users/anonymous/availableGrabCoupons?fields=FULL&product=<variant>` | 全站折價券（滿 $1200 折 $120、滿 $1600 折 $150、滿 $3000 折 $300…） |
| `GET /products/<variant>/multiBuy?fields=FULL` | 多件優惠（搜尋結果的 `elabFirstMultiBuyDatas` 已含，備用） |
| `GET /users/anonymous/cms/pages?pageType=ContentPage&pageLabelOrId=/promo-derma-1` + `/cms/components` | 活動頁（例如醫美高峰會）的商品輪播代碼 |

價格欄位的意義（以「舒酸定 專業修復抗敏牙膏 BP_598686」對照官網頁面驗證）：

```
price.value = 209          目前售價（不含結帳整體折扣）
elabPrice.value = 292      原價
promotionFirstTag = 官網不限金額享88折   → 結帳才扣：209 × 0.88 = 184（官網顯示「活動價 $184」）
elabFirstMultiBuyDatas = [{quantity:2, totalDiscountedPrice:307.12}]   → 買 2 件實付 $307（官網顯示「購買 2 $307」）
```

**蝦皮（經 BigGo）** `https://biggo.com.tw/s/<關鍵字>?m=cp&c[]=tw_bid_shopee&c[]=tw_mall_shopeemall`
Next.js SSR，商品清單以 RSC payload 內嵌在 HTML（`ssrData.list`），每筆有售價、賣家、蝦皮連結、是否下架／廣告。`&sort=lp` 可改價格低→高。
備援：`--shopee playwright`（用你自己的蝦皮登入，攔截 `search_items` API 回應）與人工輸入。

### 1.3 架構

```
┌──────────────┐   OCC API   ┌──────────────────────────────────────────────┐
│ 屈臣氏線上商店 │ ──────────▶ │ radar/watsons.py  促銷列表 → 商品 → 折價券     │
└──────────────┘             │ radar/pricing.py  售價→整體折扣→多件→最佳買法   │
┌──────────────┐  SSR HTML   │ radar/effective.py 折價券/運費/刷卡/點數分攤    │
│ BigGo（蝦皮） │ ──────────▶ │ radar/shopee.py + matching.py 相似度/多入/參考價 │
└──────────────┘             │ radar/profit.py   蝦皮淨收入、ROI、達標          │
                             │ radar/alerts.py   去重、推播                     │
                             └───────────────┬──────────────────────────────┘
                                             │ web/data/latest.json / alerts.json / history.json
              ┌──────────────────────────────┴──────────────────────────────┐
              │ web/（純靜態）index.html + app.js + engine.js（JS 版計價引擎）  │
              │  ‑ GitHub Pages：讀 JSON，設定存在瀏覽器                      │
              │  ‑ 本機 FastAPI（radar/server.py）：多了重新掃描、寫回設定、即時查價 │
              └─────────────────────────────────────────────────────────────┘
```

前端一律用 `engine.js` 即時重算，所以改費率、改卡片、改訂單假設都**不用重新掃描**；Python 引擎與 JS 引擎共用同一組測試資料（`tests/fixtures/engine_cases.json`）確保一致。

### 1.4 計價公式

```
單買價           = 售價 × 結帳整體折扣（多個折扣取最優，可設為疊加）
多件價           = API 的「買 n 件實付 T」（已含可疊加優惠）；買 q 件 = ⌊q/n⌋×T + (q mod n)×單買價
最佳買法         = 單買 vs 各多件 tier，取每件平均最低者
有效單位成本     = 最佳單價 × (1 − 折價券率) × (1 − 最佳卡回饋率) − 點數價值 + 運費分攤
                   （折價券率 = 假設訂單金額能用到的最大折價券 ÷ 訂單金額；預設訂單 $1600）
蝦皮淨收入       = 參考價 × (1 − 成交手續費 − 金流費 − 免運活動費) − 包材 − 賣家吸收運費
利潤             = 蝦皮淨收入 − 有效單位成本
ROI              = 利潤 ÷ 有效單位成本   （預設門檻 30%，可改成毛利率）
```

信用卡：每張卡有「一般回饋率」與「指定通路規則」（該通路總回饋率、加碼上限、最低消費）；屈臣氏站上的刷卡活動（`刷國泰卡滿$888送3萬點` = 30,000 點 ÷ 300 點/元 = $100）依發卡行自動加上。

---

## 2. 快速開始

### 2.1 部署架構（重要：掃描要在台灣的機器上跑）

實測 **屈臣氏 API 對 GitHub Actions 的機器回 403（Akamai Access Denied）**，BigGo 則正常。因此：

| 誰做什麼 | 在哪裡 |
|---|---|
| 掃描屈臣氏 → 查蝦皮 → 算利潤 → 推播 → `python -m radar scan --publish` 把 `web/data/*.json` 推回 repo | **你的 Mac 或家用主機**（台灣 IP），用 launchd / cron / Docker 排程 |
| 收到 push 後把 `web/` 部署成 GitHub Pages（`.github/workflows/deploy.yml`） | GitHub Actions（自動） |
| `scan.yml`（手動觸發用）— 保留給日後測試 GitHub 是否還被擋 | GitHub Actions |

儀表板網址：`https://<帳號>.github.io/watsons-deal-radar/`（第一次 push 資料後約 1 分鐘生效）。

**推送用的 token**：到 GitHub → Settings → Developer settings → Fine-grained tokens → 只選這個 repo、權限 *Contents: Read and write* → 把 token 放進 repo 根目錄的 `.env`（`GITHUB_TOKEN=github_pat_…`，此檔已在 .gitignore）。沒有 token 時，`--publish` 會先 commit，再由你用 GitHub Desktop 按 Push 也行。

**排程**

- macOS：雙擊 `scripts/install_launchd.command`（每天 09:00／15:00／21:00；Mac 睡眠時錯過的會在喚醒後補跑）。log 在 `/tmp/watsons-deal-radar.log`。
- Linux／ARM 家用主機（例如跑 Home Assistant 的 mini PC，24 小時開機最理想）：`git clone` 後 `crontab -e` 加
  `0 9,15,21 * * * /path/to/watsons-deal-radar/scripts/run_scan.sh >> /tmp/watsons-deal-radar.log 2>&1`
- Docker（家用主機）：`docker build -t watsons-deal-radar . && docker run --rm --env-file .env -v "$PWD":/app watsons-deal-radar`，一樣放進 cron。

**通知**：本機／家用主機的 `.env` 填入 §4 的變數即可（GitHub Secrets 只有手動 `scan.yml` 會用到）。

### 2.2 本機（macOS 一鍵）

```
雙擊 scripts/start.command        → 建 .venv、裝套件、開 http://127.0.0.1:8765（本機互動版儀表板）
雙擊 scripts/scan.command         → 跑一次掃描（含通知）並推送到 GitHub
雙擊 scripts/install_launchd.command → 安裝每日排程
```

或命令列：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m radar scan --publish      # 完整掃描 → web/data/latest.json → 推送
python -m radar serve               # 本機網頁 App
python -m radar promos              # 列出站上所有促銷與商品數
python -m radar lookup BP_598686    # 單一商品：成本 + 蝦皮參考價 + 利潤
python -m radar probe               # 測試這台機器能不能連屈臣氏／BigGo
python -m radar notify-test         # 測試通知管道
```

本機模式多了：右上角「重新掃描」、設定頁直接寫回 `config/*.json`、商品詳情的「重新查價」、人工比對即時存檔。

### 2.3 蝦皮來源選擇

| 模式 | 指令 | 說明 |
|---|---|---|
| BigGo（預設） | `--shopee biggo` | 不用登入。若某天被擋（連續 5 次抓不到資料）會自動停止本次查價並沿用快取 |
| Playwright | `pip install -r requirements-playwright.txt && playwright install chromium`，`python -m radar shopee-login` 登入一次，之後 `--shopee playwright` | 用你自己的蝦皮帳號直接看搜尋結果，最準但只能在本機跑 |
| 人工 | 儀表板商品詳情 → 手動參考價／採用某筆／排除某筆 | 存在 `config/matches.json`，優先於自動結果 |

---

## 3. 設定檔（`config/`）

所有檔案都有內建預設值，缺檔也能跑；儀表板「設定」「信用卡」頁可直接編輯（本機模式寫回檔案；Pages 模式存在瀏覽器，可下載 JSON 放回 repo）。

| 檔案 | 內容 |
|---|---|
| `fees.json` | 蝦皮費率（成交手續費 5.5%、金流 2%、免運活動費、包材、賣家吸收運費）、屈臣氏運費門檻（宅配滿 $688 免運）、寵i 點數（1 點/$1，300 點折 $1）、利潤門檻與指標、蝦皮參考價取法、**訂單假設**（預設一單 $1600，用來分攤折價券／免運／刷卡門檻） |
| `promotions.json` | 掃描哪些促銷（`scan.include` / `scan.exclude`）、每促銷頁數、**結帳整體折扣**表（`官網不限金額享88折: 0.88`、`醫美商品85折: 0.85`…）、點數倍率、站上刷卡活動、蝦皮查價門檻與上限（預設每次最多 150 次、快取 48 小時） |
| `cards.json` | 你的信用卡：一般回饋率、指定通路規則（總回饋率、通路關鍵字、加碼上限、最低消費）、發卡行（比對站上刷卡活動）。內建三張**範例卡**，請改成你實際的方案 |
| `matches.json` | 人工比對：`{"overrides": {"BP_xxx": {"ref_price": 189, "exclude_ids": [...], "keyword": "自訂搜尋字", "note": ""}}}` |

> 費率會變：蝦皮手續費、屈臣氏免運門檻、點數換算、各卡方案請定期核對。這些都是設定值，不用改程式。

## 4. 通知

環境變數（本機放 `.env`，GitHub 放 Actions Secrets；填哪個就啟用哪個，可多個）：

| 變數 | 說明 |
|---|---|
| `NOTIFY_WEBHOOK_URL` | generic webhook（例如 n8n Webhook 節點），POST JSON `{source, generated_at, text, count, items[]}`，items 內含商品名、成本、蝦皮價、利潤、ROI、連結 |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram Bot |
| `DISCORD_WEBHOOK_URL` | Discord 頻道 webhook |
| `LINE_CHANNEL_ACCESS_TOKEN` + `LINE_TO_USER_ID` | LINE Messaging API push（LINE Notify 已停止服務） |

去重規則：同一商品 24 小時內不重複；ROI 比上次高 10 個百分點以上會再通知。所有通知也會記錄在儀表板「通知」頁（`web/data/alerts.json`）。

## 5. 儀表板功能

- **儀表板**：搜尋／促銷／分類篩選，依 ROI、每件利潤、折扣深度、有效成本、銷量排序；達標商品標紅；展開看成本拆解、多件 tier、促銷期間、蝦皮候選列表（採用／排除／手動價／自訂關鍵字）、價格歷史。
- **購物車**：加入商品後自動以多件優惠計價，套用最大折價券、判斷免運、比較每張卡回饋、算寵i點數，給出「再買 $X 可用下一張折價券／免運」與達標商品加購建議；可複製清單。
- **信用卡**：新增／編輯卡片與規則，任意金額試算各卡回饋。
- **設定**：所有費率與假設；下載／匯入 JSON。
- **通知**：歷史通知紀錄。

## 6. 限制與注意事項

1. **請以屈臣氏結帳頁為準**：折扣能否疊加、折價券除外商品（廠商直送／專櫃／隱形眼鏡／集點商品）、限購量、缺貨，這個工具是「快速篩選」，下單前把商品放進官網購物袋再確認一次。
2. **蝦皮參考價是估計**：列表價 ≠ 成交價（賣場優惠券、免運），且賣出速度看品類；「最低 3 筆中位數」較保守，但名稱比對仍可能誤配，達標商品請點開候選列表看一眼。
3. **資料來源會變**：屈臣氏／BigGo 改版時解析可能失效，`tests/fixtures/` 存有實際回應樣本，改版後更新解析器與測試即可。`python -m radar probe` 可隨時檢查連線狀態（結果也會寫進 `web/data/probe.json`）。
4. **使用條款**：本工具只讀取公開頁面、低頻率、單執行緒（屈臣氏約 0.6 秒/請求，BigGo 1.5 秒/請求），請勿調高到造成對方負擔；資料僅供個人比價。
5. 儀表板 `latest.json` 約數 MB（依掃描的促銷數而定），手機第一次載入會慢幾秒。

## 7. 開發

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # Python 引擎、解析器、pipeline（用 fixtures，不連網）
python tests/gen_engine_cases.py          # 重新產生 Python↔JS 對照案例
node --test web/engine.test.mjs           # JS 引擎與 Python 一致性
```

```
radar/        watsons.py 屈臣氏 API｜shopee.py BigGo/Playwright/人工｜matching.py 相似度與多入組
              pricing.py 成本｜effective.py 分攤｜cards.py 信用卡｜profit.py 利潤｜alerts.py 通知
              pipeline.py 流程｜storage.py JSON/歷史｜server.py FastAPI｜cli.py 命令列｜config.py 設定
web/          index.html / app.js / engine.js / styles.css；data/ 為掃描輸出
config/       fees.json / promotions.json / cards.json / matches.json
tests/        pytest + fixtures（真實 API 回應樣本）
.github/      deploy.yml（push 後部署 Pages）、scan.yml（手動測試用）、test.yml
scripts/      start.command / scan.command / install_launchd.command / run_scan.sh / launchd plist
Dockerfile    家用主機用
```

### Roadmap（可再加）

- 活動頁（`/promo-*`）商品輪播掃描（API 已接好：`WatsonsClient.promo_page_product_codes`）
- 每日／週末限時（flashSale）與門市取貨限定價
- 蝦皮已售出數量／評價數納入「好賣度」評分
- 以 EAN 條碼比對蝦皮（列表標題有條碼時）
- 多帳號／多卡的月上限累計（`used_caps`）
