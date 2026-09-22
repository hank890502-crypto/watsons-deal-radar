// node --test web/engine.test.mjs   （驗證 JS 引擎與 Python 引擎輸出一致）
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { unitCost, bestUnitCost, buildContext, effectiveUnit, evaluate, rankCards, cartTotal } from './engine.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const cases = JSON.parse(readFileSync(path.join(here, '..', 'tests', 'fixtures', 'engine_cases.json'), 'utf8'));
const { fees, promotions: promo, cards } = cases.config;

function close(a, b, msg, tol = 0.02) {
  if (a == null || b == null) return assert.equal(a ?? null, b ?? null, msg);
  assert.ok(Math.abs(a - b) <= tol, `${msg}: js=${a} py=${b}`);
}

test('unitCost matches python', () => {
  for (const c of cases.unit_cost) {
    const r = unitCost(c.product, c.qty, promo);
    close(r.total, c.expected.total, `${c.product.code} qty=${c.qty} total`);
    close(r.unit, c.expected.unit, `${c.product.code} qty=${c.qty} unit`);
    assert.equal(JSON.stringify(r.tier), JSON.stringify(c.expected.tier), `${c.product.code} tier`);
    assert.deepEqual(r.applied, c.expected.applied);
  }
});

test('bestUnitCost matches python', () => {
  for (const c of cases.best) {
    const r = bestUnitCost(c.product, promo);
    assert.equal(r.qty, c.expected.qty, c.product.code);
    close(r.unit, c.expected.unit, c.product.code);
  }
});

test('context + effectiveUnit match python', () => {
  const ctx = buildContext(cases.coupons, fees, promo, cards);
  close(ctx.coupon_ratio, cases.context.coupon_ratio, 'coupon_ratio', 1e-4);
  close(ctx.card_rate, cases.context.card_rate, 'card_rate', 1e-4);
  assert.equal(ctx.card.card_id, cases.context.card.card_id);
  for (const c of cases.effective) {
    const e = effectiveUnit(c.product, c.unit, ctx);
    close(e.unit, c.expected.unit, `${c.product.code} effective`);
    close(e.points, c.expected.points, `${c.product.code} points`);
  }
});

test('evaluate matches python', () => {
  for (const c of cases.evaluate) {
    const e = evaluate(c.unit, c.ref, fees);
    close(e.profit, c.expected.profit, 'profit');
    close(e.roi, c.expected.roi, 'roi', 1e-3);
    assert.equal(e.hot, c.expected.hot);
    close(e.target_price, c.expected.target_price, 'target_price', 0.05);
  }
});

test('rankCards matches python', () => {
  for (const c of cases.cards) {
    const r = rankCards(c.amount, cards, promo.card_promos, 300);
    assert.deepEqual(r.map((x) => x.card_id), c.expected.map((x) => x.card_id), `order @${c.amount}`);
    r.forEach((x, i) => close(x.reward, c.expected[i].reward, `${x.card_id} @${c.amount}`));
  }
});

test('cartTotal matches python', () => {
  for (const c of cases.cart) {
    const r = cartTotal(c.items, cases.coupons, fees, promo, cards);
    close(r.subtotal, c.expected.subtotal, 'subtotal');
    close(r.paid, c.expected.paid, 'paid');
    close(r.effective_total, c.expected.effective_total, 'effective_total');
    assert.equal(r.coupon?.value ?? null, c.expected.coupon?.value ?? null);
    assert.equal(r.card?.card_id ?? null, c.expected.card?.card_id ?? null);
    assert.equal(r.next_coupon?.threshold ?? null, c.expected.next_coupon?.threshold ?? null);
  }
});
