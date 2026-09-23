/* 計價引擎（JS 版）— 與 radar/pricing.py、effective.py、cards.py、profit.py 邏輯一致。
   前端一律用這裡即時重算，所以改設定不用重新掃描。 */

export const r2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
const r4 = (x) => Math.round((x + Number.EPSILON) * 10000) / 10000;

function multipliersFor(product, promoCfg) {
  const table = promoCfg.checkout_multipliers || {};
  const hits = (product.promotions || []).filter((n) => n in table).map((n) => [n, Number(table[n])]);
  if (!hits.length) return [1, []];
  if (promoCfg.stack_multipliers) {
    let m = 1;
    for (const [, v] of hits) m *= v;
    return [m, hits.map((h) => h[0])];
  }
  const best = hits.reduce((a, b) => (b[1] < a[1] ? b : a));
  return [best[1], [best[0]]];
}

export function unitCost(product, qty, promoCfg) {
  const price = product.price;
  if (price == null || qty <= 0) return null;
  const [mult, applied0] = multipliersFor(product, promoCfg);
  let applied = [...applied0];
  const single = r2(price * mult);
  const tiers = (product.multi_buy || []).filter((t) => Number(t.qty) >= 2 && t.total != null);
  let tier = null;
  const usable = tiers.filter((t) => Number(t.qty) <= qty);
  if (usable.length) tier = usable.reduce((a, b) => (Number(b.qty) > Number(a.qty) ? b : a));
  if (tier && Number(tier.total) > Number(tier.qty) * single + 0.01) tier = null;
  let total;
  if (tier) {
    const n = Number(tier.qty);
    const groups = Math.floor(qty / n);
    const rem = qty % n;
    total = groups * Number(tier.total) + rem * single;
    applied.push(`多件優惠：${n}件$${Number(tier.total)}`);
  } else {
    total = qty * single;
  }
  total = r2(total);
  return { qty, total, unit: r2(total / qty), single_unit: single, multiplier: mult, tier: tier ? { qty: Number(tier.qty), total: Number(tier.total) } : null, applied };
}

export function bestUnitCost(product, promoCfg) {
  const qtys = new Set([1, ...(product.multi_buy || []).filter((t) => Number(t.qty) >= 2).map((t) => Number(t.qty))]);
  let best = null;
  for (const q of [...qtys].sort((a, b) => a - b)) {
    const r = unitCost(product, q, promoCfg);
    if (r && (!best || r.unit < best.unit - 1e-9)) best = r;
  }
  return best;
}

export function pickCoupon(subtotal, coupons) {
  const ok = (coupons || []).filter((c) => c.value && c.threshold != null && subtotal >= Number(c.threshold));
  if (!ok.length) return null;
  return ok.reduce((a, b) => (Number(b.value) > Number(a.value) ? b : a));
}

export function shippingFee(amount, feesCfg) {
  const ws = feesCfg.watsons_shipping || {};
  const mode = ws.mode || 'home_delivery';
  const rule = ws[mode] || { free_threshold: 0, fee: 0 };
  const thr = Number(rule.free_threshold || 0);
  const fee = Number(rule.fee || 0);
  if (thr <= 0 || amount >= thr) return [0, { mode, free_threshold: thr, fee: 0, gap_to_free: 0 }];
  return [fee, { mode, free_threshold: thr, fee, gap_to_free: r2(thr - amount) }];
}

function channelMatches(rule, channel) {
  const chans = rule.channels || [];
  if (!chans.length) return true;
  return chans.some((c) => c && (channel.includes(c) || c.includes(channel)));
}

export function cardReward(amount, card, channel = '屈臣氏', cardPromos = {}, pointsPerDollarValue = 300) {
  const baseRate = Number(card.base_rate || 0);
  const details = [];
  let bestRate = baseRate;
  let bestRule = null;
  for (const rule of card.rules || []) {
    if (!channelMatches(rule, channel)) continue;
    if (amount < Number(rule.min_spend || 0)) continue;
    const rate = Number(rule.rate || 0);
    if (rate > bestRate) { bestRate = rate; bestRule = rule; }
  }
  const baseReward = amount * baseRate;
  let bonus = 0;
  if (bestRule) {
    bonus = amount * (bestRate - baseRate);
    const cap = Number(bestRule.cap_reward || 0);
    if (cap > 0) bonus = Math.min(bonus, cap);
    details.push({ name: bestRule.name, rate: bestRate, reward: r2(bonus), capped: !!bestRule.cap_reward });
  }
  let promoReward = 0;
  const issuer = `${card.issuer || ''} ${card.name || ''}`;
  for (const [pname, p] of Object.entries(cardPromos || {})) {
    const iss = p.issuer || '';
    if (iss && issuer.includes(iss) && amount >= Number(p.threshold || 0)) {
      const value = p.points ? Number(p.points) / Number(pointsPerDollarValue || 300) : Number(p.value || 0);
      promoReward += value;
      details.push({ name: pname, reward: r2(value), threshold: p.threshold });
    }
  }
  const total = baseReward + bonus + promoReward;
  return { card_id: card.id, card: card.name, amount: r2(amount), base_rate: baseRate, base_reward: r2(baseReward), bonus: r2(bonus), promo_reward: r2(promoReward), reward: r2(total), effective_rate: amount > 0 ? r4(total / amount) : 0, details };
}

export function rankCards(amount, cardsCfg, cardPromos, ppdv = 300) {
  const channel = cardsCfg.channel || '屈臣氏';
  const out = (cardsCfg.cards || []).filter((c) => c.enabled !== false).map((c) => cardReward(amount, c, channel, cardPromos, ppdv));
  out.sort((a, b) => b.reward - a.reward);
  return out;
}

export function buildContext(coupons, feesCfg, promoCfg, cardsCfg) {
  const a = feesCfg.assumptions || {};
  const order = Number(a.order_amount || 1600);
  const coupon = a.use_coupon !== false ? pickCoupon(order, coupons) : null;
  const couponValue = coupon ? Number(coupon.value) : 0;
  const couponRatio = order > 0 ? couponValue / order : 0;
  const after = order - couponValue;
  const [fee, ship] = shippingFee(after, feesCfg);
  const shippingRatio = after > 0 ? fee / after : 0;
  let card = null;
  let cardRate = 0;
  if (a.use_card !== false) {
    const ranked = rankCards(after, cardsCfg, promoCfg.card_promos, Number((feesCfg.points || {}).points_per_dollar_value || 300));
    card = ranked[0] || null;
    cardRate = card ? Number(card.effective_rate) : 0;
  }
  const pc = feesCfg.points || {};
  let pointsBase = 0;
  if (a.use_points !== false && pc.enabled !== false) pointsBase = Number(pc.earn_per_dollar || 1) / Number(pc.points_per_dollar_value || 300);
  return {
    order_amount: order,
    coupon: coupon ? { name: coupon.name, value: couponValue } : null,
    coupon_ratio: r4(couponRatio),
    shipping: ship,
    shipping_ratio: r4(shippingRatio),
    card: card ? { card_id: card.card_id, card: card.card, effective_rate: card.effective_rate, reward: card.reward } : null,
    card_rate: r4(cardRate),
    points_base_rate: pointsBase,
    points_multipliers: promoCfg.points_multipliers || {},
  };
}

export function effectiveUnit(product, unit, ctx) {
  const after = unit * (1 - ctx.coupon_ratio);
  const card = after * ctx.card_rate;
  let mult = 1;
  for (const n of product.promotions || []) if (n in ctx.points_multipliers) mult = Math.max(mult, Number(ctx.points_multipliers[n]));
  const points = after * ctx.points_base_rate * mult;
  const ship = after * ctx.shipping_ratio;
  return { unit: r2(after - card - points + ship), after_coupon: r2(after), card: r2(card), points: r2(points), points_multiplier: mult, shipping: r2(ship) };
}

export function shopeeNet(price, feesCfg) {
  const s = feesCfg.shopee || {};
  let commission = price * Number(s.commission_rate || 0);
  const cap = Number(s.commission_cap_per_item || 0);
  if (cap > 0) commission = Math.min(commission, cap);
  const payment = price * Number(s.payment_rate || 0);
  const fsp = price * Number(s.free_shipping_program_rate || 0);
  const packaging = Number(s.packaging_cost || 0);
  const subsidy = Number(s.shipping_subsidy || 0);
  const feeTotal = commission + payment + fsp + packaging + subsidy;
  return { price: r2(price), commission: r2(commission), payment: r2(payment), free_shipping_program: r2(fsp), packaging, shipping_subsidy: subsidy, fee_total: r2(feeTotal), net: r2(price - feeTotal) };
}

function priceForTarget(unit, roiTarget, feesCfg, marginTarget = null) {
  const s = feesCfg.shopee || {};
  const r = Number(s.commission_rate || 0) + Number(s.payment_rate || 0) + Number(s.free_shipping_program_rate || 0);
  const fixed = Number(s.packaging_cost || 0) + Number(s.shipping_subsidy || 0);
  if (marginTarget != null) { const d = 1 - r - marginTarget; return d > 0 ? (fixed + unit) / d : Infinity; }
  return r < 1 ? (unit + unit * (roiTarget || 0) + fixed) / (1 - r) : Infinity;
}

export function evaluate(unit, ref, feesCfg) {
  if (unit == null || ref == null || unit <= 0 || ref <= 0) return null;
  const net = shopeeNet(ref, feesCfg);
  const profit = r2(net.net - unit);
  const roi = r4(profit / unit);
  const margin = r4(profit / ref);
  const pc = feesCfg.profit || {};
  const metric = pc.metric || 'roi';
  const threshold = Number(pc.alert_threshold ?? 0.3);
  const value = metric === 'roi' ? roi : margin;
  return { unit_cost: r2(unit), ref_price: r2(ref), net: net.net, fees: net, profit, roi, margin, metric, threshold, hot: value >= threshold, breakeven_price: r2(priceForTarget(unit, 0, feesCfg)), target_price: r2(metric === 'roi' ? priceForTarget(unit, threshold, feesCfg) : priceForTarget(unit, null, feesCfg, threshold)) };
}

/* 從候選列表重算參考價（前端排除某筆後即時更新） */
export function refFromCandidates(cands, excludeIds, method = 'low3_median', minListings = 2, minScore = 0.45) {
  const ex = new Set(excludeIds || []);
  const matched = (cands || []).filter((c) => c.score >= minScore && !c.offline && !ex.has(c.id));
  const prices = matched.map((c) => c.unit_price).sort((a, b) => a - b);
  if (prices.length < Math.max(1, minListings)) return { ref_price: null, n_matched: matched.length };
  const median = (arr) => (arr.length % 2 ? arr[(arr.length - 1) / 2] : (arr[arr.length / 2 - 1] + arr[arr.length / 2]) / 2);
  let ref;
  if (method === 'min') ref = prices[0];
  else if (method === 'median') ref = median(prices);
  else ref = prices.length >= 3 ? median(prices.slice(0, 3)) : prices[0]; // 只有 2 筆時取最低（與 Python 一致）
  // 屈臣氏自家蝦皮商城也在賣 → 參考價不高於它
  const official = matched.find((c) => /屈臣氏|watsons/i.test(c.shop || ''));
  let capped = false;
  if (official && official.unit_price < ref) { ref = official.unit_price; capped = true; }
  return { ref_price: r2(ref), n_matched: matched.length, min: prices[0], median: median(prices), official: official ? official.unit_price : null, capped_by_official: capped };
}

export function cartTotal(items, coupons, feesCfg, promoCfg, cardsCfg) {
  const lines = [];
  let subtotal = 0;
  for (const it of items) {
    const uc = unitCost(it.product, Number(it.qty || 0), promoCfg);
    if (!uc) continue;
    lines.push({ code: it.product.code, name: it.product.name, qty: Number(it.qty), unit: uc.unit, total: uc.total, applied: uc.applied, product: it.product });
    subtotal += uc.total;
  }
  subtotal = r2(subtotal);
  const coupon = pickCoupon(subtotal, coupons);
  const couponValue = coupon ? Number(coupon.value) : 0;
  const afterCoupon = r2(subtotal - couponValue);
  const [fee, ship] = shippingFee(afterCoupon, feesCfg);
  const paid = r2(afterCoupon + fee);
  const ranked = rankCards(paid, cardsCfg || { cards: [] }, promoCfg.card_promos, Number((feesCfg.points || {}).points_per_dollar_value || 300));
  const card = ranked[0] || null;
  const pc = feesCfg.points || {};
  let pts = 0;
  if (pc.enabled !== false) {
    const earn = Number(pc.earn_per_dollar || 1);
    const mults = promoCfg.points_multipliers || {};
    for (const l of lines) {
      let m = 1;
      for (const n of l.product.promotions || []) if (n in mults) m = Math.max(m, Number(mults[n]));
      pts += l.total * earn * m;
    }
  }
  const points = { points: Math.round(pts), value: r2(pts / Number(pc.points_per_dollar_value || 300)) };
  const effective = r2(paid - (card ? card.reward : 0) - points.value);
  const ratio = subtotal > 0 ? effective / subtotal : 1;
  for (const l of lines) l.effective_unit = r2(l.unit * ratio);
  let nextCoupon = null;
  for (const c of (coupons || []).filter((c) => c.threshold != null && c.value).sort((a, b) => Number(a.threshold) - Number(b.threshold))) {
    if (Number(c.threshold) > subtotal) { nextCoupon = { name: c.name, threshold: Number(c.threshold), value: Number(c.value), gap: r2(Number(c.threshold) - subtotal) }; break; }
  }
  return { lines, subtotal, coupon: coupon ? { name: coupon.name, value: couponValue } : null, next_coupon: nextCoupon, shipping: ship, paid, card, cards: ranked, points, effective_total: effective, effective_ratio: r4(ratio) };
}
