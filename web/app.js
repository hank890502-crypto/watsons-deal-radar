/* 屈臣氏優惠雷達 — 前端（純靜態，GitHub Pages 與本機 FastAPI 皆可用） */
import * as E from './engine.js';

// ------------------------------------------------------------------ utils
const $ = (sel, el = document) => el.querySelector(sel);
const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (v, d = 0) => (v == null || Number.isNaN(v) ? '—' : '$' + Number(v).toLocaleString('zh-TW', { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v, d = 0) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
const fmtDate = (iso) => (iso ? iso.slice(0, 16).replace('T', ' ') : '—');
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
let toastTimer;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}
function deepMerge(base, over) {
  if (!over || typeof over !== 'object' || Array.isArray(over)) return over === undefined ? base : over;
  const out = { ...(base || {}) };
  for (const [k, v] of Object.entries(over)) out[k] = v && typeof v === 'object' && !Array.isArray(v) ? deepMerge(base?.[k], v) : v;
  return out;
}
const download = (name, obj) => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

// ------------------------------------------------------------------ state
const S = {
  mode: 'static',          // static | local
  data: null,              // latest.json
  alerts: [],
  history: null,
  settings: null,          // {fees, promotions, cards}
  matches: { overrides: {} },
  cart: LS.get('radar.cart', {}),   // code -> qty
  filters: LS.get('radar.filters', { q: '', promo: '', cat: '', hot: false, withRef: false, inStock: true, sort: 'roi', page: 1 }),
  expanded: new Set(),
  products: [],            // computed
  ctx: null,
  status: null,
  scanStatus: null,
};

async function fetchJSON(url, opts) {
  const r = await fetch(url, { cache: 'no-store', ...opts });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

async function boot() {
  try {
    S.status = await fetchJSON('/api/status');
    if (S.status && S.status.mode === 'local') S.mode = 'local';
  } catch { S.mode = 'static'; }
  try {
    S.data = await fetchJSON('data/latest.json');
  } catch (e) {
    $('#app').innerHTML = `<div class="card"><h2>還沒有資料</h2><p>尚未執行過掃描。${S.mode === 'local' ? '按右上角「重新掃描」開始。' : '請等待 GitHub Actions 排程執行，或在本機執行 <code>python -m radar scan</code>。'}</p><p class="muted small">${h(e.message)}</p></div>`;
    renderTopbar();
    return;
  }
  try { S.alerts = await fetchJSON('data/alerts.json'); } catch { S.alerts = []; }
  try { S.scanStatus = await fetchJSON('data/status.json'); } catch { S.scanStatus = null; }
  await loadSettings();
  recompute();
  renderTopbar();
  route();
}

async function loadSettings() {
  const base = { fees: S.data.config.fees, promotions: S.data.config.promotions, cards: S.data.config.cards };
  if (S.mode === 'local') {
    try {
      const cfg = await fetchJSON('/api/config');
      S.settings = { fees: cfg.fees, promotions: cfg.promotions, cards: cfg.cards };
      S.matches = cfg.matches || { overrides: {} };
      return;
    } catch { /* fall through */ }
  }
  const over = LS.get('radar.settings', {});
  S.settings = {
    fees: deepMerge(base.fees, over.fees),
    promotions: deepMerge(base.promotions, over.promotions),
    cards: over.cards ? over.cards : base.cards,
  };
  S.matches = LS.get('radar.matches', { overrides: {} });
}

async function saveSettings(name) {
  if (S.mode === 'local') {
    try {
      await fetchJSON(`/api/config/${name}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(S.settings[name]) });
      toast(`已儲存 config/${name}.json（下次掃描與通知都會使用）`);
    } catch (e) { toast('儲存失敗：' + e.message); }
  } else {
    const over = LS.get('radar.settings', {});
    over[name] = S.settings[name];
    LS.set('radar.settings', over);
    toast('已儲存在這個瀏覽器。排程掃描要用新設定的話，請下載 JSON 放進 repo 的 config/ 資料夾');
  }
  recompute();
}

async function saveMatch(code, patch) {
  const ov = S.matches.overrides || (S.matches.overrides = {});
  const cur = { ...(ov[code] || {}) };
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) delete cur[k];
    else cur[k] = v;
  }
  if (Object.keys(cur).length) ov[code] = cur; else delete ov[code];
  if (S.mode === 'local') {
    try {
      await fetchJSON('/api/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, ref_price: cur.ref_price ?? null, note: cur.note ?? '', exclude_ids: cur.exclude_ids ?? [], keyword: cur.keyword ?? '' }) });
      toast('已更新 config/matches.json');
    } catch (e) { toast('儲存失敗：' + e.message); }
  } else {
    LS.set('radar.matches', S.matches);
    toast('已記在這個瀏覽器（可在設定頁下載 matches.json）');
  }
  recompute();
  render();
}

// ------------------------------------------------------------------ compute
function recompute() {
  const { fees, promotions: promo, cards } = S.settings;
  S.ctx = E.buildContext(S.data.coupons || [], fees, promo, cards);
  const pc = fees.profit || {};
  S.products = (S.data.products || []).map((p) => {
    const cost = E.bestUnitCost(p, promo);
    const cost1 = E.unitCost(p, 1, promo);
    const eff = cost ? E.effectiveUnit(p, cost.unit, S.ctx) : null;
    const ov = (S.matches.overrides || {})[p.code] || {};
    let ref = null;
    let refMethod = null;
    let refInfo = p.shopee || null;
    if (ov.ref_price) { ref = Number(ov.ref_price); refMethod = 'manual'; }
    else if (p.shopee) {
      if (ov.exclude_ids?.length && p.shopee.candidates?.length) {
        const r = E.refFromCandidates(p.shopee.candidates, ov.exclude_ids, pc.reference || 'low3_median', pc.min_listings ?? 2, pc.min_match_score ?? 0.45);
        ref = r.ref_price; refMethod = pc.reference || 'low3_median'; refInfo = { ...p.shopee, ...r };
      } else { ref = p.shopee.auto_ref_price ?? p.shopee.ref_price ?? null; refMethod = p.shopee.method === 'manual' ? (pc.reference || 'low3_median') : p.shopee.method; }
    }
    const ev = eff && ref ? E.evaluate(eff.unit, ref, fees) : null;
    const lp = p.list_price || p.price;
    const depth = cost && lp ? 1 - cost.unit / lp : 0;
    return { ...p, cost, cost1, effective: eff, ref, refMethod, refInfo, eval: ev, depth, override: ov };
  });
  $('#cart-count').textContent = Object.values(S.cart).reduce((a, b) => a + Number(b || 0), 0);
}

const byCode = (code) => S.products.find((p) => p.code === code);
const topCat = (p) => (p.category_path || '').split('/')[0] || '';

// ------------------------------------------------------------------ router
const routes = { deals: renderDeals, cart: renderCart, cards: renderCards, settings: renderSettings, alerts: renderAlerts, about: renderAbout };
function currentTab() { const m = location.hash.match(/^#\/(\w+)/); return m && routes[m[1]] ? m[1] : 'deals'; }
function route() { render(); }
function render() {
  if (!S.data) return;
  const tab = currentTab();
  document.querySelectorAll('#tabs a').forEach((a) => a.classList.toggle('active', a.dataset.tab === tab));
  $('#app').innerHTML = routes[tab]();
  afterRender(tab);
}
window.addEventListener('hashchange', route);

function renderTopbar() {
  const gen = S.data?.generated_at;
  const src = S.data?.source || {};
  $('#meta-line').innerHTML = `${S.mode === 'local' ? '<span class="chip good">本機模式</span>' : '<span class="chip">GitHub Pages</span>'} 更新：${fmtDate(gen)}${src.shopee_provider ? ` · 蝦皮來源 ${h(src.shopee_provider)}` : ''}`;
  const acts = $('#topbar-actions');
  acts.innerHTML = S.mode === 'local'
    ? `<button class="btn primary sm" data-action="scan">${S.status?.running ? '掃描中…' : '重新掃描'}</button>`
    : `<a class="btn sm" href="data/latest.json" download>下載 latest.json</a>`;
  acts.onclick = async (e) => {
    if (e.target.dataset.action !== 'scan') return;
    try {
      const r = await fetchJSON('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      toast(r.ok ? '掃描已開始，完成後會自動重新載入（通常 3～10 分鐘）' : r.message);
      pollScan();
    } catch (err) { toast('無法啟動掃描：' + err.message); }
  };
}
async function pollScan() {
  const t = setInterval(async () => {
    try {
      const st = await fetchJSON('/api/status');
      S.status = st;
      renderTopbar();
      if (!st.running) {
        clearInterval(t);
        if (st.error) toast('掃描失敗：' + st.error, 6000);
        else { toast('掃描完成，重新載入資料'); await boot(); }
      }
    } catch { clearInterval(t); }
  }, 5000);
}

// ------------------------------------------------------------------ deals
function filteredProducts() {
  const f = S.filters;
  const q = f.q.trim().toLowerCase();
  let list = S.products.filter((p) => {
    if (f.inStock && !p.in_stock) return false;
    if (f.hot && !p.eval?.hot) return false;
    if (f.withRef && !p.ref) return false;
    if (f.promo && !(p.promotions || []).includes(f.promo)) return false;
    if (f.cat && topCat(p) !== f.cat) return false;
    if (q && !`${p.name} ${p.brand} ${p.code} ${p.ean}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const sorters = {
    roi: (a, b) => (b.eval?.roi ?? -9) - (a.eval?.roi ?? -9) || b.depth - a.depth,
    profit: (a, b) => (b.eval?.profit ?? -9e9) - (a.eval?.profit ?? -9e9),
    depth: (a, b) => b.depth - a.depth,
    sold: (a, b) => (b.sold || 0) - (a.sold || 0),
    cost: (a, b) => (a.effective?.unit ?? 9e9) - (b.effective?.unit ?? 9e9),
    price: (a, b) => (b.price || 0) - (a.price || 0),
  };
  list.sort(sorters[f.sort] || sorters.roi);
  return list;
}

function roiBadge(ev) {
  if (!ev) return '<span class="badge na">無參考價</span>';
  const v = ev.metric === 'roi' ? ev.roi : ev.margin;
  const cls = ev.hot ? 'hot' : v >= 0.15 ? 'good' : v >= 0 ? 'warn' : 'bad';
  return `<span class="badge ${cls}" title="利潤 ${money(ev.profit)}／ROI ${pct(ev.roi)}／毛利率 ${pct(ev.margin)}">${pct(v)}</span>`;
}

function statusBanner() {
  const st = S.scanStatus;
  if (!st || st.ok !== false) return '';
  return `<div class="card" style="border-color:var(--bad);background:var(--bad-soft)"><b>上次掃描失敗</b>（${fmtDate(st.at)}）：<span class="mono">${h((st.error || '').slice(0, 300))}</span><div class="hint">目前顯示的是更早一次成功掃描的資料。若錯誤是 HTTP 403，代表這台機器被屈臣氏擋住，請改在本機／家用主機執行掃描（README「排程」）。</div></div>`;
}

function renderDeals() {
  const list = filteredProducts();
  const f = S.filters;
  const per = 50;
  const pages = Math.max(1, Math.ceil(list.length / per));
  if (f.page > pages) f.page = pages;
  const rows = list.slice((f.page - 1) * per, f.page * per);
  const d = S.data;
  const promos = (d.promotions || []).filter((p) => p.selected).sort((a, b) => (b.scanned || 0) - (a.scanned || 0));
  const cats = [...new Set(S.products.map(topCat).filter(Boolean))].sort();
  const hot = S.products.filter((p) => p.eval?.hot).length;
  const withRef = S.products.filter((p) => p.ref).length;
  const ctx = S.ctx;
  return `
  ${statusBanner()}
  <div class="grid-tiles">
    <div class="tile"><div class="label">掃描促銷商品</div><div class="value">${S.products.length.toLocaleString()}</div><div class="sub">${promos.length} 個促銷 · ${fmtDate(d.generated_at)}</div></div>
    <div class="tile"><div class="label">有蝦皮參考價</div><div class="value">${withRef}</div><div class="sub">查價上限 ${h(S.settings.promotions.shopee_lookup?.max_lookups_per_run ?? '—')}/次，快取 ${h(S.settings.promotions.shopee_lookup?.cache_hours ?? '—')}h</div></div>
    <div class="tile"><div class="label">利潤達標（≥ ${pct(S.settings.fees.profit.alert_threshold)}）</div><div class="value" style="color:var(--hot)">${hot}</div><div class="sub">依 ${S.settings.fees.profit.metric === 'roi' ? 'ROI = 利潤／成本' : '毛利率 = 利潤／售價'}</div></div>
    <div class="tile"><div class="label">訂單假設 ${money(ctx.order_amount)}</div><div class="value" style="font-size:16px">${ctx.coupon ? h(ctx.coupon.name) : '無折價券'}</div><div class="sub">${ctx.shipping.fee ? `運費 ${money(ctx.shipping.fee)}` : '免運'} · 折價券 ${pct(ctx.coupon_ratio, 1)}</div></div>
    <div class="tile"><div class="label">最佳卡片（此訂單）</div><div class="value" style="font-size:16px">${ctx.card ? h(ctx.card.card) : '未設定卡片'}</div><div class="sub">${ctx.card ? `回饋 ${money(ctx.card.reward)}（${pct(ctx.card.effective_rate, 1)}）` : '到「信用卡」頁新增'}</div></div>
  </div>
  <div class="filters">
    <label class="field">搜尋<input type="search" id="f-q" value="${h(f.q)}" placeholder="名稱 / 品牌 / 條碼" /></label>
    <label class="field">促銷<select id="f-promo"><option value="">全部</option>${promos.map((p) => `<option value="${h(p.name)}" ${f.promo === p.name ? 'selected' : ''}>${h(p.name)}（${p.scanned ?? p.count}）</option>`).join('')}</select></label>
    <label class="field">分類<select id="f-cat"><option value="">全部</option>${cats.map((c) => `<option ${f.cat === c ? 'selected' : ''}>${h(c)}</option>`).join('')}</select></label>
    <label class="field">排序<select id="f-sort">
      <option value="roi" ${f.sort === 'roi' ? 'selected' : ''}>ROI 高→低</option>
      <option value="profit" ${f.sort === 'profit' ? 'selected' : ''}>每件利潤 高→低</option>
      <option value="depth" ${f.sort === 'depth' ? 'selected' : ''}>折扣深度 高→低</option>
      <option value="cost" ${f.sort === 'cost' ? 'selected' : ''}>有效成本 低→高</option>
      <option value="sold" ${f.sort === 'sold' ? 'selected' : ''}>銷量 高→低</option>
      <option value="price" ${f.sort === 'price' ? 'selected' : ''}>售價 高→低</option>
    </select></label>
    <label class="check"><input type="checkbox" id="f-hot" ${f.hot ? 'checked' : ''}/> 只看達標</label>
    <label class="check"><input type="checkbox" id="f-ref" ${f.withRef ? 'checked' : ''}/> 有蝦皮價</label>
    <label class="check"><input type="checkbox" id="f-stock" ${f.inStock ? 'checked' : ''}/> 有庫存</label>
    <span class="muted small">${list.length.toLocaleString()} 筆</span>
  </div>
  <div class="table-wrap"><table class="deals">
    <thead><tr>
      <th>商品</th><th class="num">售價／原價</th><th class="num">最佳買法</th><th class="num">有效成本</th><th class="num">蝦皮參考</th><th class="num opt">淨收入</th><th class="num">利潤</th><th></th>
    </tr></thead>
    <tbody>${rows.map(renderRow).join('') || `<tr><td colspan="8" class="empty">沒有符合的商品</td></tr>`}</tbody>
  </table></div>
  <div class="pager">
    <button class="btn sm" data-action="page" data-page="${f.page - 1}" ${f.page <= 1 ? 'disabled' : ''}>上一頁</button>
    <span class="small muted">${f.page} / ${pages}</span>
    <button class="btn sm" data-action="page" data-page="${f.page + 1}" ${f.page >= pages ? 'disabled' : ''}>下一頁</button>
  </div>`;
}

function renderRow(p) {
  const c = p.cost, e = p.effective, ev = p.eval;
  const open = S.expanded.has(p.code);
  const inCart = S.cart[p.code];
  const promoChips = (p.promotions || []).slice(0, 3).map((x) => `<span class="chip promo">${h(x)}</span>`).join('');
  const refCell = p.ref
    ? `<div>${money(p.ref)}</div><div class="small muted">${p.refMethod === 'manual' ? '手動' : `${p.refInfo?.n_matched ?? 0} 筆相符`}${p.refInfo?.official ? ` · 屈臣氏蝦皮 ${money(p.refInfo.official)}` : ''}</div>`
    : `<span class="muted small">${p.shopee ? (p.shopee.n_total ? `${p.shopee.n_total} 筆但不相符` : '查無') : '未查'}</span>`;
  return `
  <tr class="${ev?.hot ? 'hot' : ''}" data-code="${h(p.code)}">
    <td class="prod-cell"><div class="prod">${p.image ? `<img src="${h(p.image)}" loading="lazy" alt="" />` : '<div style="width:48px"></div>'}<div><div class="name"><a href="${h(p.url)}" target="_blank" rel="noopener">${h(p.name)}</a></div><div class="sub">${h(p.brand || '')} · ${h(p.category_path || '')}${p.sold ? ` · 銷量 ${p.sold}` : ''}${!p.in_stock ? ' · <span class="chip bad">缺貨</span>' : ''}</div><div class="chips">${promoChips}</div></div></div></td>
    <td class="num" data-label="售價／原價"><div>${money(p.price)}</div>${p.list_price && p.list_price > p.price ? `<div class="strike">${money(p.list_price)}</div>` : ''}</td>
    <td class="num" data-label="最佳買法">${c ? `<div>${c.qty > 1 ? `買 ${c.qty} 件` : '單買'} <b>${money(c.unit, 1)}</b>/件</div><div class="small muted">${c.tier ? `${c.tier.qty}件 ${money(c.tier.total)}` : ''}${c.multiplier < 1 ? ` ×${c.multiplier}` : ''} · 折 ${pct(p.depth)}</div>` : '—'}</td>
    <td class="num" data-label="有效成本">${e ? `<b>${money(e.unit, 1)}</b><div class="small muted">券 −${pct(S.ctx.coupon_ratio, 1)} 卡 −${money(e.card, 1)}${e.points ? ` 點 −${money(e.points, 1)}` : ''}</div>` : '—'}</td>
    <td class="num" data-label="蝦皮參考">${refCell}</td>
    <td class="num opt" data-label="淨收入">${ev ? money(ev.net) : '—'}</td>
    <td class="num" data-label="利潤">${roiBadge(ev)}${ev ? `<div class="small muted">${money(ev.profit)}/件</div>` : ''}</td>
    <td class="nowrap actions"><button class="btn sm ${inCart ? 'primary' : ''}" data-action="cart-add" data-code="${h(p.code)}" title="加入購物車">${inCart ? `🛒 ${inCart}` : '＋購物車'}</button> <button class="btn sm" data-action="toggle" data-code="${h(p.code)}">${open ? '收合' : '詳情'}</button></td>
  </tr>
  ${open ? `<tr class="detail"><td colspan="8">${renderDetail(p)}</td></tr>` : ''}`;
}

function renderDetail(p) {
  const c = p.cost, e = p.effective, ev = p.eval, ctx = S.ctx;
  const tiers = (p.multi_buy || []).map((t) => `<li>買 ${t.qty} 件 ${money(t.total)}（平均 ${money(t.avg, 1)}）${t.end ? `<span class="muted small"> 至 ${fmtDate(t.end)}</span>` : ''}</li>`).join('');
  const cands = p.refInfo?.candidates || p.shopee?.candidates || [];
  const ex = new Set(p.override.exclude_ids || []);
  const hist = S.history?.[p.code]?.points || null;
  return `<div class="detail-grid">
    <div>
      <h3>成本拆解</h3>
      <dl class="kv">
        <dt>售價</dt><dd>${money(p.price)}${p.list_price > p.price ? ` <span class="strike">${money(p.list_price)}</span>` : ''}</dd>
        <dt>單買（含結帳折扣）</dt><dd>${money(p.cost1?.unit, 1)}${p.cost1?.multiplier < 1 ? ` <span class="muted small">×${p.cost1.multiplier}</span>` : ''}</dd>
        <dt>最佳買法</dt><dd>${c ? `${c.qty} 件 → ${money(c.unit, 1)}/件 <span class="muted small">${c.applied.join('；')}</span>` : '—'}</dd>
        <dt>折價券（假設訂單 ${money(ctx.order_amount)}）</dt><dd>−${pct(ctx.coupon_ratio, 1)} → ${money(e?.after_coupon, 1)}</dd>
        <dt>刷卡回饋 ${ctx.card ? `(${h(ctx.card.card)})` : ''}</dt><dd>−${money(e?.card, 1)}</dd>
        <dt>寵i點數${e?.points_multiplier > 1 ? ` ×${e.points_multiplier}` : ''}</dt><dd>−${money(e?.points, 1)}</dd>
        ${e?.shipping ? `<dt>運費分攤</dt><dd>+${money(e.shipping, 1)}</dd>` : ''}
        <dt><b>有效單位成本</b></dt><dd><b>${money(e?.unit, 1)}</b></dd>
      </dl>
      ${tiers ? `<h3 style="margin-top:10px">多件優惠</h3><ul class="plain">${tiers}</ul>` : ''}
      <div class="small muted" style="margin-top:8px">促銷：${(p.promotions || []).map(h).join('、')}${p.promo_end ? `<br>促銷期間 ${fmtDate(p.promo_start)} ～ ${fmtDate(p.promo_end)}` : ''}<br>條碼 ${h(p.ean || '—')} · ${h(p.code)}</div>
      ${ev ? `<h3 style="margin-top:10px">蝦皮轉售</h3><dl class="kv">
        <dt>參考售價</dt><dd>${money(ev.ref_price)}</dd>
        <dt>手續費＋金流＋包材</dt><dd>−${money(ev.fees.fee_total, 1)}</dd>
        <dt>淨收入</dt><dd>${money(ev.net, 1)}</dd>
        <dt>每件利潤</dt><dd><b>${money(ev.profit, 1)}</b>（ROI ${pct(ev.roi)}，毛利率 ${pct(ev.margin)}）</dd>
        <dt>損益兩平售價</dt><dd>${money(ev.breakeven_price)}</dd>
        <dt>達 ${pct(ev.threshold)} 所需售價</dt><dd>${money(ev.target_price)}</dd>
      </dl>` : ''}
    </div>
    <div>
      <div class="row between"><h3>蝦皮列表${p.shopee?.keyword ? ` <span class="muted small">關鍵字「${h(p.override.keyword || p.shopee.keyword)}」</span>` : ''}</h3>
        ${S.mode === 'local' ? `<button class="btn sm" data-action="relookup" data-code="${h(p.code)}">重新查價</button>` : ''}</div>
      <div class="row" style="margin:6px 0">
        <label class="field">手動參考價<input type="number" step="1" data-field="manual-ref" data-code="${h(p.code)}" value="${h(p.override.ref_price ?? '')}" placeholder="例如 189" /></label>
        <label class="field">自訂關鍵字<input type="text" data-field="manual-kw" data-code="${h(p.code)}" value="${h(p.override.keyword ?? '')}" placeholder="覆蓋自動關鍵字" /></label>
        <button class="btn sm primary" data-action="save-match" data-code="${h(p.code)}">儲存</button>
        ${p.override.ref_price || p.override.keyword || ex.size ? `<button class="btn sm" data-action="clear-match" data-code="${h(p.code)}">清除人工設定</button>` : ''}
      </div>
      ${cands.length ? `<div class="cands table-wrap"><table><thead><tr><th>列表</th><th>賣家</th><th class="num">價格</th><th class="num">入數</th><th class="num">單價</th><th class="num">相似</th><th></th></tr></thead><tbody>
        ${cands.map((x) => `<tr class="${ex.has(x.id) ? 'excluded' : ''} ${x.matched ? '' : 'unmatched'}"><td><a href="${h(x.url)}" target="_blank" rel="noopener">${h(x.title)}</a>${x.source === 'shopee_mall' ? ' <span class="chip">商城</span>' : ''}${x.offline ? ' <span class="chip bad">下架</span>' : ''}${x.is_ad ? ' <span class="chip">廣告</span>' : ''}</td><td class="small">${h(x.shop || '')}</td><td class="num">${money(x.price)}</td><td class="num">${x.pack_qty}</td><td class="num">${money(x.unit_price)}</td><td class="num">${(x.score * 100).toFixed(0)}</td><td class="nowrap"><button class="btn sm" data-action="use-price" data-code="${h(p.code)}" data-price="${x.unit_price}">採用</button> <button class="btn sm" data-action="exclude" data-code="${h(p.code)}" data-id="${h(x.id)}">${ex.has(x.id) ? '還原' : '排除'}</button></td></tr>`).join('')}
      </tbody></table></div>` : `<div class="muted small">${p.shopee ? '沒有列表資料' : '此商品未達查價門檻（可在設定調整），或本次查價額度已用完。'}</div>`}
      ${hist && hist.length > 1 ? `<h3 style="margin-top:10px">價格歷史</h3>${sparkline(hist)}` : ''}
    </div>
  </div>`;
}

function sparkline(points) {
  const vals = points.map((p) => p[2] ?? p[1]).filter((v) => v != null);
  if (vals.length < 2) return '';
  const w = 300, hgt = 40, min = Math.min(...vals), max = Math.max(...vals);
  const xs = vals.map((v, i) => [i * (w / (vals.length - 1)), hgt - ((v - min) / (max - min || 1)) * (hgt - 6) - 3]);
  return `<svg class="sparkline" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${xs.map((p) => p.join(',')).join(' ')}"/></svg><div class="small muted">單位成本 ${money(min)} ～ ${money(max)}（${points.length} 個時間點）</div>`;
}

// ------------------------------------------------------------------ cart
function cartItems() {
  return Object.entries(S.cart).map(([code, qty]) => ({ product: byCode(code), qty: Number(qty) })).filter((x) => x.product && x.qty > 0);
}
function renderCart() {
  const items = cartItems();
  if (!items.length) return `<div class="card"><h2>購物車最佳化</h2><div class="empty">從儀表板把商品加進來，這裡會算出：多件優惠 → 折價券 → 免運 → 最划算的信用卡，並建議加購什麼最快達到下一個門檻。</div></div>`;
  const r = E.cartTotal(items, S.data.coupons || [], S.settings.fees, S.settings.promotions, S.settings.cards);
  const evalLine = (l) => { const p = l.product; const ref = byCode(p.code)?.ref; return ref ? E.evaluate(l.effective_unit, ref, S.settings.fees) : null; };
  const totalProfit = r.lines.reduce((a, l) => { const ev = evalLine(l); return a + (ev ? ev.profit * l.qty : 0); }, 0);
  const gap = r.next_coupon?.gap;
  const shipGap = r.shipping.gap_to_free;
  const suggestions = S.products.filter((p) => p.eval?.hot && !S.cart[p.code] && p.in_stock).slice(0, 6);
  return `
  <div class="card">
    <div class="row between"><h2>購物車最佳化</h2><div class="row"><button class="btn sm" data-action="copy-cart">複製清單</button><button class="btn sm danger" data-action="cart-clear">清空</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>商品</th><th class="num">數量</th><th class="num">單價</th><th class="num">小計</th><th class="num">有效單價</th><th class="num">蝦皮利潤</th><th></th></tr></thead><tbody>
    ${r.lines.map((l) => { const ev = evalLine(l); return `<tr><td><a href="${h(l.product.url)}" target="_blank" rel="noopener">${h(l.name)}</a><div class="small muted">${l.applied.map(h).join('；')}</div></td>
      <td class="num nowrap"><button class="btn sm" data-action="cart-qty" data-code="${h(l.code)}" data-delta="-1">−</button> <b>${l.qty}</b> <button class="btn sm" data-action="cart-qty" data-code="${h(l.code)}" data-delta="1">＋</button></td>
      <td class="num">${money(l.unit, 1)}</td><td class="num">${money(l.total)}</td><td class="num">${money(l.effective_unit, 1)}</td>
      <td class="num">${ev ? `${roiBadge(ev)}<div class="small muted">${money(ev.profit, 1)} × ${l.qty} = ${money(ev.profit * l.qty)}</div>` : '<span class="muted small">無參考價</span>'}</td>
      <td><button class="btn sm" data-action="cart-remove" data-code="${h(l.code)}">移除</button></td></tr>`; }).join('')}
    </tbody></table></div>
  </div>
  <div class="detail-grid">
    <div class="card">
      <h3>訂單試算</h3>
      <dl class="kv">
        <dt>商品小計</dt><dd>${money(r.subtotal)}</dd>
        <dt>折價券</dt><dd>${r.coupon ? `−${money(r.coupon.value)} <span class="muted small">${h(r.coupon.name)}</span>` : '—'}</dd>
        <dt>運費</dt><dd>${r.shipping.fee ? money(r.shipping.fee) : '免運'}</dd>
        <dt><b>實付</b></dt><dd><b>${money(r.paid)}</b></dd>
        <dt>刷卡回饋</dt><dd>${r.card ? `−${money(r.card.reward)} <span class="muted small">${h(r.card.card)}</span>` : '—'}</dd>
        <dt>寵i點數</dt><dd>${r.points.points ? `−${money(r.points.value, 1)} <span class="muted small">${r.points.points} 點</span>` : '—'}</dd>
        <dt><b>有效總成本</b></dt><dd><b>${money(r.effective_total)}</b>（${pct(r.effective_ratio)} 折）</dd>
        <dt>預估蝦皮總利潤</dt><dd>${money(totalProfit)}</dd>
      </dl>
      ${gap || shipGap ? `<div class="hint">${gap ? `再買 <b>${money(gap)}</b> 可用「${h(r.next_coupon.name)}」（省 ${money(r.next_coupon.value)}）。` : ''}${shipGap ? ` 再買 <b>${money(shipGap)}</b> 免運。` : ''}</div>` : ''}
    </div>
    <div class="card">
      <h3>信用卡比較（實付 ${money(r.paid)}）</h3>
      <table><thead><tr><th>卡片</th><th class="num">回饋</th><th class="num">有效率</th><th>說明</th></tr></thead><tbody>
      ${r.cards.map((c, i) => `<tr><td>${i === 0 ? '🏆 ' : ''}${h(c.card)}</td><td class="num">${money(c.reward, 1)}</td><td class="num">${pct(c.effective_rate, 2)}</td><td class="small muted">${c.details.map((d) => `${h(d.name)} ${money(d.reward)}`).join('；') || `一般 ${pct(c.base_rate, 1)}`}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">尚未設定卡片</td></tr>'}
      </tbody></table>
      ${suggestions.length ? `<h3 style="margin-top:12px">加購建議（達標商品）</h3><ul class="plain">${suggestions.map((p) => `<li><a href="#" data-action="cart-add" data-code="${h(p.code)}">＋</a> ${h(p.name)} <span class="muted small">${money(p.effective?.unit, 1)} → 蝦皮 ${money(p.ref)}，ROI ${pct(p.eval.roi)}</span></li>`).join('')}</ul>` : ''}
    </div>
  </div>`;
}

// ------------------------------------------------------------------ cards
function renderCards() {
  const cfg = S.settings.cards;
  const promos = S.settings.promotions.card_promos || {};
  return `
  <div class="card">
    <div class="row between"><h2>信用卡回饋設定</h2><div class="row"><button class="btn sm" data-action="card-add">＋ 新增卡片</button><button class="btn sm primary" data-action="cards-save">儲存</button></div></div>
    <p class="hint">「指定通路規則」的回饋率是該通路的<b>總回饋率</b>（會取代一般回饋率），上限只限制加碼部分。通路名稱只要包含「屈臣氏」或「網購」就會套用到屈臣氏線上購物。屈臣氏站上的刷卡活動（${Object.keys(promos).map(h).join('、') || '無'}）會依發卡行自動加上。</p>
    <label class="field" style="max-width:240px">計算通路<input type="text" data-cfg="cards.channel" value="${h(cfg.channel || '屈臣氏')}" /></label>
  </div>
  ${(cfg.cards || []).map((c, i) => `
  <div class="card-editor ${c.enabled === false ? 'disabled' : ''}" data-idx="${i}">
    <div class="row between">
      <label class="check"><input type="checkbox" data-card="${i}" data-k="enabled" ${c.enabled !== false ? 'checked' : ''}/> <b>${h(c.name || '未命名')}</b></label>
      <div class="row"><button class="btn sm" data-action="rule-add" data-card="${i}">＋ 規則</button><button class="btn sm danger" data-action="card-del" data-card="${i}">刪除卡片</button></div>
    </div>
    <div class="form-grid" style="margin-top:8px">
      <label class="field">名稱<input type="text" data-card="${i}" data-k="name" value="${h(c.name || '')}" /></label>
      <label class="field">發卡行（比對站上刷卡活動）<input type="text" data-card="${i}" data-k="issuer" value="${h(c.issuer || '')}" placeholder="國泰 / 玉山 / 台新…" /></label>
      <label class="field">一般回饋率 %<input type="number" step="0.1" data-card="${i}" data-k="base_rate" value="${((c.base_rate || 0) * 100).toFixed(2)}" /></label>
    </div>
    ${(c.rules || []).map((r, j) => `<div class="rule"><div class="form-grid">
      <label class="field">規則名稱<input type="text" data-card="${i}" data-rule="${j}" data-k="name" value="${h(r.name || '')}" /></label>
      <label class="field">回饋率 %<input type="number" step="0.1" data-card="${i}" data-rule="${j}" data-k="rate" value="${((r.rate || 0) * 100).toFixed(2)}" /></label>
      <label class="field">通路（逗號分隔）<input type="text" data-card="${i}" data-rule="${j}" data-k="channels" value="${h((r.channels || []).join(', '))}" /></label>
      <label class="field">加碼上限 $（0=無）<input type="number" data-card="${i}" data-rule="${j}" data-k="cap_reward" value="${r.cap_reward || 0}" /></label>
      <label class="field">最低消費 $<input type="number" data-card="${i}" data-rule="${j}" data-k="min_spend" value="${r.min_spend || 0}" /></label>
      <label class="field">備註<input type="text" data-card="${i}" data-rule="${j}" data-k="notes" value="${h(r.notes || '')}" /></label>
    </div><div class="right"><button class="btn sm danger" data-action="rule-del" data-card="${i}" data-rule="${j}">刪除規則</button></div></div>`).join('')}
  </div>`).join('')}
  <div class="card"><h3>試算</h3><div class="row"><label class="field">消費金額<input type="number" id="card-try" value="1500" /></label></div><div id="card-try-out" style="margin-top:8px"></div></div>`;
}

// ------------------------------------------------------------------ settings
function renderSettings() {
  const f = S.settings.fees, p = S.settings.promotions;
  const mult = p.checkout_multipliers || {};
  const st = S.status;
  return `
  <div class="card">
    <div class="row between"><h2>設定</h2><div class="row"><button class="btn sm" data-action="export-settings">下載設定 JSON</button><label class="btn sm">匯入 JSON<input type="file" id="import-file" accept="application/json" class="hidden" /></label><button class="btn sm primary" data-action="settings-save">儲存</button></div></div>
    <p class="hint">${S.mode === 'local' ? '本機模式：儲存會直接寫入 repo 的 config/ 檔案，掃描與通知都會用新設定。' : 'GitHub Pages 模式：儲存只存在這個瀏覽器（儀表板即時重算）。要讓排程掃描／通知也用新設定，請「下載設定 JSON」後把 fees.json / promotions.json / cards.json 放進 repo 的 config/ 資料夾並 commit。'}</p>
  </div>
  <div class="detail-grid">
    <div class="card"><h3>蝦皮賣家費用</h3><div class="form-grid">
      <label class="field">成交手續費 %<input type="number" step="0.1" data-cfg="fees.shopee.commission_rate" data-pct value="${(f.shopee.commission_rate * 100).toFixed(2)}" /></label>
      <label class="field">金流與系統處理費 %<input type="number" step="0.1" data-cfg="fees.shopee.payment_rate" data-pct value="${(f.shopee.payment_rate * 100).toFixed(2)}" /></label>
      <label class="field">免運活動費 %<input type="number" step="0.1" data-cfg="fees.shopee.free_shipping_program_rate" data-pct value="${(f.shopee.free_shipping_program_rate * 100).toFixed(2)}" /></label>
      <label class="field">每單包材 $<input type="number" data-cfg="fees.shopee.packaging_cost" value="${f.shopee.packaging_cost}" /></label>
      <label class="field">賣家吸收運費 $<input type="number" data-cfg="fees.shopee.shipping_subsidy" value="${f.shopee.shipping_subsidy}" /></label>
      <label class="field">成交費單件上限 $（0=無）<input type="number" data-cfg="fees.shopee.commission_cap_per_item" value="${f.shopee.commission_cap_per_item}" /></label>
    </div><p class="hint">請以蝦皮賣家中心公告的最新費率為準。</p></div>
    <div class="card"><h3>利潤判斷</h3><div class="form-grid">
      <label class="field">通知門檻 %<input type="number" step="1" data-cfg="fees.profit.alert_threshold" data-pct value="${(f.profit.alert_threshold * 100).toFixed(0)}" /></label>
      <label class="field">指標<select data-cfg="fees.profit.metric"><option value="roi" ${f.profit.metric === 'roi' ? 'selected' : ''}>ROI（利潤／成本）</option><option value="margin" ${f.profit.metric === 'margin' ? 'selected' : ''}>毛利率（利潤／售價）</option></select></label>
      <label class="field">蝦皮參考價<select data-cfg="fees.profit.reference"><option value="low3_median" ${f.profit.reference === 'low3_median' ? 'selected' : ''}>最低 3 筆中位數（建議）</option><option value="min" ${f.profit.reference === 'min' ? 'selected' : ''}>最低價</option><option value="median" ${f.profit.reference === 'median' ? 'selected' : ''}>全部中位數</option></select></label>
      <label class="field">最少相符筆數<input type="number" data-cfg="fees.profit.min_listings" value="${f.profit.min_listings}" /></label>
      <label class="field">名稱相似度門檻 (0–1)<input type="number" step="0.05" min="0" max="1" data-cfg="fees.profit.min_match_score" value="${f.profit.min_match_score ?? 0.45}" /></label>
    </div></div>
    <div class="card"><h3>訂單假設（有效成本用）</h3><div class="form-grid">
      <label class="field">一單金額 $<input type="number" data-cfg="fees.assumptions.order_amount" value="${f.assumptions.order_amount}" /></label>
      <label class="check"><input type="checkbox" data-cfg="fees.assumptions.use_coupon" ${f.assumptions.use_coupon !== false ? 'checked' : ''}/> 計入折價券</label>
      <label class="check"><input type="checkbox" data-cfg="fees.assumptions.use_card" ${f.assumptions.use_card !== false ? 'checked' : ''}/> 計入刷卡回饋</label>
      <label class="check"><input type="checkbox" data-cfg="fees.assumptions.use_points" ${f.assumptions.use_points !== false ? 'checked' : ''}/> 計入寵i點數</label>
    </div>
    <h3 style="margin-top:12px">屈臣氏運費</h3><div class="form-grid">
      <label class="field">配送方式<select data-cfg="fees.watsons_shipping.mode"><option value="home_delivery" ${f.watsons_shipping.mode === 'home_delivery' ? 'selected' : ''}>宅配到府</option><option value="store_pickup" ${f.watsons_shipping.mode === 'store_pickup' ? 'selected' : ''}>門市／超商取貨</option></select></label>
      <label class="field">宅配免運門檻 $<input type="number" data-cfg="fees.watsons_shipping.home_delivery.free_threshold" value="${f.watsons_shipping.home_delivery.free_threshold}" /></label>
      <label class="field">宅配運費 $<input type="number" data-cfg="fees.watsons_shipping.home_delivery.fee" value="${f.watsons_shipping.home_delivery.fee}" /></label>
      <label class="field">取貨免運門檻 $<input type="number" data-cfg="fees.watsons_shipping.store_pickup.free_threshold" value="${f.watsons_shipping.store_pickup.free_threshold}" /></label>
      <label class="field">取貨運費 $<input type="number" data-cfg="fees.watsons_shipping.store_pickup.fee" value="${f.watsons_shipping.store_pickup.fee}" /></label>
    </div>
    <h3 style="margin-top:12px">寵i點數</h3><div class="form-grid">
      <label class="check"><input type="checkbox" data-cfg="fees.points.enabled" ${f.points.enabled !== false ? 'checked' : ''}/> 計入</label>
      <label class="field">每 $1 累積點數<input type="number" step="0.1" data-cfg="fees.points.earn_per_dollar" value="${f.points.earn_per_dollar}" /></label>
      <label class="field">幾點折抵 $1<input type="number" data-cfg="fees.points.points_per_dollar_value" value="${f.points.points_per_dollar_value}" /></label>
    </div></div>
    <div class="card"><h3>結帳整體折扣（售價未含，結帳才扣）</h3>
      <table><thead><tr><th>促銷名稱</th><th class="num">乘數</th><th></th></tr></thead><tbody>
      ${Object.entries(mult).map(([k, v]) => `<tr><td>${h(k)}</td><td class="num">${v}</td><td><button class="btn sm danger" data-action="mult-del" data-key="${h(k)}">刪除</button></td></tr>`).join('')}
      <tr><td><input type="text" id="mult-name" placeholder="例如 醫美商品85折" style="width:100%" /></td><td class="num"><input type="number" id="mult-val" step="0.01" value="0.85" style="width:80px" /></td><td><button class="btn sm" data-action="mult-add">新增</button></td></tr>
      </tbody></table>
      <label class="check" style="margin-top:8px"><input type="checkbox" data-cfg="promotions.stack_multipliers" ${p.stack_multipliers ? 'checked' : ''}/> 多個折扣疊加（預設取最優一個）</label>
      <h3 style="margin-top:12px">掃描與查價（需 commit 到 repo 才影響排程）</h3><div class="form-grid">
        <label class="field">每次蝦皮查價上限<input type="number" data-cfg="promotions.shopee_lookup.max_lookups_per_run" value="${p.shopee_lookup?.max_lookups_per_run ?? 150}" /></label>
        <label class="field">查價快取（小時）<input type="number" data-cfg="promotions.shopee_lookup.cache_hours" value="${p.shopee_lookup?.cache_hours ?? 48}" /></label>
        <label class="field">查價門檻：折扣深度 ≥<input type="number" step="0.05" data-cfg="promotions.shopee_lookup.min_discount_depth" value="${p.shopee_lookup?.min_discount_depth ?? 0.25}" /></label>
        <label class="field">查價門檻：售價 ≥ $<input type="number" data-cfg="promotions.shopee_lookup.min_price" value="${p.shopee_lookup?.min_price ?? 40}" /></label>
      </div>
      <h3 style="margin-top:12px">通知</h3>
      <p class="hint">通知管道用環境變數／GitHub Secrets 設定（NOTIFY_WEBHOOK_URL、TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID、DISCORD_WEBHOOK_URL、LINE_CHANNEL_ACCESS_TOKEN + LINE_TO_USER_ID）。${st?.notify_channels ? `目前啟用：${st.notify_channels.join('、') || '無'}` : ''}</p>
      ${S.mode === 'local' ? `<button class="btn sm" data-action="notify-test">發送測試通知</button>` : ''}
      <h3 style="margin-top:12px">人工比對資料</h3>
      <p class="hint">目前有 ${Object.keys(S.matches.overrides || {}).length} 個人工設定（手動價／排除／關鍵字）。</p>
      <button class="btn sm" data-action="export-matches">下載 matches.json</button>
    </div>
  </div>`;
}

// ------------------------------------------------------------------ alerts / about
function renderAlerts() {
  const list = S.alerts || [];
  return `<div class="card"><h2>通知紀錄</h2><p class="hint">每次掃描後，利潤達標且 24 小時內未通知過（或 ROI 又提高 10 個百分點）的商品會記錄在此並推播。</p>
  ${list.length ? `<div class="table-wrap"><table><thead><tr><th>時間</th><th>商品</th><th class="num">成本</th><th class="num">蝦皮</th><th class="num">利潤</th><th class="num">ROI</th></tr></thead><tbody>
  ${list.slice(0, 300).map((a) => `<tr><td class="nowrap small">${fmtDate(a.ts)}</td><td><a href="${h(a.url)}" target="_blank" rel="noopener">${h(a.name)}</a><div class="small muted">${h(a.promo || '')}${a.buy_qty > 1 ? ` · 買 ${a.buy_qty} 件` : ''}${a.shopee_url ? ` · <a href="${h(a.shopee_url)}" target="_blank" rel="noopener">蝦皮列表</a>` : ''}</div></td><td class="num">${money(a.unit_cost, 1)}</td><td class="num">${money(a.ref_price)}</td><td class="num">${money(a.profit, 1)}</td><td class="num"><span class="badge hot">${pct(a.roi)}</span></td></tr>`).join('')}
  </tbody></table></div>` : '<div class="empty">還沒有達標通知</div>'}</div>`;
}

function renderAbout() {
  const src = S.data.source || {};
  return `<div class="card"><h2>怎麼算的</h2>
  <ol class="steps">
    <li><b>屈臣氏成本</b>：售價 → 結帳整體折扣（如官網88折，售價欄位不含）→ 多件優惠（API 提供「買 n 件實付」，已含可疊加優惠）→ 取每件最低的買法。</li>
    <li><b>有效成本</b>：依「訂單假設金額」分攤折價券（滿額折）、運費、最佳信用卡回饋、寵i點數 → 每件有效成本。</li>
    <li><b>蝦皮參考價</b>：用商品名搜尋 BigGo（蝦皮購物＋蝦皮商城），名稱相似度 ≥ 門檻的列表，換算多入組單價後取「最低 3 筆的中位數」。可人工採用某筆、排除、或直接輸入。</li>
    <li><b>利潤</b>：蝦皮價 × (1 − 成交手續費 − 金流費 − 免運活動費) − 包材 − 有效成本。ROI = 利潤／成本，≥ 門檻即標紅並通知。</li>
  </ol>
  <h3>注意</h3><ul class="plain">
    <li>折扣是否可疊加、折價券除外商品、缺貨與限購，以屈臣氏結帳頁為準；此工具是「快速篩選」，下單前請把商品放進官網購物袋再確認一次。</li>
    <li>蝦皮實際成交價可能低於列表價（優惠券／免運），且售出速度取決於品類；參考價僅供估算。</li>
    <li>費率（蝦皮手續費、點數換算）請定期在設定頁更新。</li>
  </ul>
  <h3>本次資料</h3><dl class="kv"><dt>產生時間</dt><dd>${fmtDate(S.data.generated_at)}</dd><dt>屈臣氏 API 呼叫</dt><dd>${src.watsons_calls ?? '—'}</dd><dt>蝦皮查價</dt><dd>${src.shopee_calls ?? 0} 次（快取命中 ${src.shopee_cache_hits ?? 0}）</dd><dt>耗時</dt><dd>${src.elapsed_sec ?? '—'} 秒</dd><dt>版本</dt><dd>${h(S.data.version || '')}</dd></dl>
  </div>`;
}

// ------------------------------------------------------------------ events
function setPath(obj, path, value) {
  const keys = path.split('.');
  let cur = obj;
  for (const k of keys.slice(0, -1)) cur = cur[k] ?? (cur[k] = {});
  cur[keys[keys.length - 1]] = value;
}

function afterRender(tab) {
  const app = $('#app');
  if (tab === 'deals') {
    const f = S.filters;
    const bind = (id, key, ev = 'change', map = (v) => v) => { const el = $(id); if (!el) return; el.addEventListener(ev, () => { f[key] = map(el.type === 'checkbox' ? el.checked : el.value); f.page = 1; LS.set('radar.filters', f); render(); }); };
    bind('#f-q', 'q', 'input');
    bind('#f-promo', 'promo'); bind('#f-cat', 'cat'); bind('#f-sort', 'sort');
    bind('#f-hot', 'hot'); bind('#f-ref', 'withRef'); bind('#f-stock', 'inStock');
    const q = $('#f-q'); if (q && document.activeElement !== q && f.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }
  if (tab === 'cards') {
    app.addEventListener('input', onCardInput);
    const tryOut = () => { const amt = Number($('#card-try')?.value || 0); const ranked = E.rankCards(amt, S.settings.cards, S.settings.promotions.card_promos, S.settings.fees.points.points_per_dollar_value); $('#card-try-out').innerHTML = ranked.map((c, i) => `<div>${i === 0 ? '🏆 ' : ''}${h(c.card)}：<b>${money(c.reward, 1)}</b>（${pct(c.effective_rate, 2)}）<span class="muted small">${c.details.map((d) => `${h(d.name)} ${money(d.reward)}`).join('；')}</span></div>`).join('') || '<span class="muted">沒有啟用的卡片</span>'; };
    $('#card-try')?.addEventListener('input', tryOut); tryOut();
  }
  if (tab === 'settings') {
    app.addEventListener('change', onSettingsInput);
    $('#import-file')?.addEventListener('change', async (e) => {
      const file = e.target.files[0]; if (!file) return;
      try {
        const obj = JSON.parse(await file.text());
        if (obj.fees || obj.promotions || obj.cards) { S.settings = { fees: obj.fees || S.settings.fees, promotions: obj.promotions || S.settings.promotions, cards: obj.cards || S.settings.cards }; }
        else if (obj.shopee) S.settings.fees = obj; else if (obj.cards) S.settings.cards = obj; else if (obj.checkout_multipliers) S.settings.promotions = obj; else if (obj.overrides) S.matches = obj;
        for (const n of ['fees', 'promotions', 'cards']) await saveSettings(n);
        render();
      } catch (err) { toast('匯入失敗：' + err.message); }
    });
  }
}

function onCardInput(e) {
  const el = e.target;
  if (el.dataset.cfg === 'cards.channel') { S.settings.cards.channel = el.value; return; }
  if (el.dataset.card == null) return;
  const card = S.settings.cards.cards[Number(el.dataset.card)];
  const k = el.dataset.k;
  const target = el.dataset.rule != null ? card.rules[Number(el.dataset.rule)] : card;
  if (el.type === 'checkbox') target[k] = el.checked;
  else if (k === 'rate' || k === 'base_rate') target[k] = Number(el.value) / 100;
  else if (k === 'channels') target[k] = el.value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
  else if (el.type === 'number') target[k] = Number(el.value);
  else target[k] = el.value;
}

function onSettingsInput(e) {
  const el = e.target;
  const path = el.dataset.cfg;
  if (!path) return;
  let v;
  if (el.type === 'checkbox') v = el.checked;
  else if (el.type === 'number') v = Number(el.value);
  else v = el.value;
  if (el.dataset.pct != null) v = v / 100;
  setPath(S.settings, path, v);
}

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const a = btn.dataset.action;
  const code = btn.dataset.code;
  if (a === 'page') { S.filters.page = Number(btn.dataset.page); LS.set('radar.filters', S.filters); render(); window.scrollTo({ top: 0 }); }
  else if (a === 'toggle') { S.expanded.has(code) ? S.expanded.delete(code) : S.expanded.add(code); if (S.expanded.has(code) && !S.history) { try { S.history = await fetchJSON('data/history.json'); } catch { S.history = {}; } } render(); }
  else if (a === 'cart-add') { e.preventDefault(); const p = byCode(code); const step = p?.cost?.qty || 1; S.cart[code] = (S.cart[code] || 0) + step; LS.set('radar.cart', S.cart); recompute(); render(); toast(`已加入 ${step} 件`); }
  else if (a === 'cart-qty') { const p = byCode(code); const step = Number(btn.dataset.delta) * (p?.cost?.qty || 1); S.cart[code] = Math.max(0, (S.cart[code] || 0) + step); if (!S.cart[code]) delete S.cart[code]; LS.set('radar.cart', S.cart); recompute(); render(); }
  else if (a === 'cart-remove') { delete S.cart[code]; LS.set('radar.cart', S.cart); recompute(); render(); }
  else if (a === 'cart-clear') { S.cart = {}; LS.set('radar.cart', S.cart); recompute(); render(); }
  else if (a === 'copy-cart') { const items = cartItems(); const text = items.map((it) => `${it.qty} × ${it.product.name} ${it.product.url}`).join('\n'); try { await navigator.clipboard.writeText(text); toast('已複製'); } catch { toast('無法複製'); } }
  else if (a === 'save-match') { const ref = $(`[data-field="manual-ref"][data-code="${CSS.escape(code)}"]`).value; const kw = $(`[data-field="manual-kw"][data-code="${CSS.escape(code)}"]`).value; await saveMatch(code, { ref_price: ref ? Number(ref) : null, keyword: kw || null }); }
  else if (a === 'clear-match') { await saveMatch(code, { ref_price: null, keyword: null, exclude_ids: null, note: null }); }
  else if (a === 'use-price') { await saveMatch(code, { ref_price: Number(btn.dataset.price) }); }
  else if (a === 'exclude') { const cur = new Set((S.matches.overrides?.[code]?.exclude_ids) || []); cur.has(btn.dataset.id) ? cur.delete(btn.dataset.id) : cur.add(btn.dataset.id); await saveMatch(code, { exclude_ids: [...cur] }); }
  else if (a === 'relookup') { btn.disabled = true; btn.textContent = '查價中…'; try { const r = await fetchJSON('/api/lookup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, keyword: S.matches.overrides?.[code]?.keyword || undefined }) }); const p = S.data.products.find((x) => x.code === code); if (p) { p.shopee = { ...(p.shopee || {}), ...r, fetched_at: new Date().toISOString(), method: r.method }; delete p.shopee.auto_ref_price; } recompute(); render(); toast(`找到 ${r.n_total} 筆，${r.n_matched} 筆相符`); } catch (err) { toast('查價失敗：' + err.message); render(); } }
  else if (a === 'card-add') { S.settings.cards.cards.push({ id: 'card-' + Date.now().toString(36), name: '新卡片', issuer: '', enabled: true, base_rate: 0.01, rules: [] }); render(); }
  else if (a === 'card-del') { S.settings.cards.cards.splice(Number(btn.dataset.card), 1); render(); }
  else if (a === 'rule-add') { (S.settings.cards.cards[Number(btn.dataset.card)].rules ||= []).push({ name: '指定通路', rate: 0.03, channels: ['屈臣氏', '網購'], cap_reward: 0, min_spend: 0, notes: '' }); render(); }
  else if (a === 'rule-del') { S.settings.cards.cards[Number(btn.dataset.card)].rules.splice(Number(btn.dataset.rule), 1); render(); }
  else if (a === 'cards-save') { await saveSettings('cards'); render(); }
  else if (a === 'settings-save') { await saveSettings('fees'); await saveSettings('promotions'); render(); }
  else if (a === 'mult-add') { const k = $('#mult-name').value.trim(); const v = Number($('#mult-val').value); if (k && v > 0 && v <= 1) { (S.settings.promotions.checkout_multipliers ||= {})[k] = v; render(); } }
  else if (a === 'mult-del') { delete S.settings.promotions.checkout_multipliers[btn.dataset.key]; render(); }
  else if (a === 'export-settings') { download('fees.json', S.settings.fees); setTimeout(() => download('promotions.json', S.settings.promotions), 300); setTimeout(() => download('cards.json', S.settings.cards), 600); }
  else if (a === 'export-matches') { download('matches.json', S.matches); }
  else if (a === 'notify-test') { try { const r = await fetchJSON('/api/notify-test', { method: 'POST' }); toast(`管道：${r.channels.join('、') || '無'}；結果：${JSON.stringify(r.result)}`, 6000); } catch (err) { toast('失敗：' + err.message); } }
});

boot();
