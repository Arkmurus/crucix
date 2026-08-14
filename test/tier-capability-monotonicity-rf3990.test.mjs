// test/tier-capability-monotonicity-rf3990.test.mjs
//
// R-F3990 (C-75) — a more expensive tier must never expose LESS than a cheaper one,
// and a difference you sell must be a difference you enforce.
//
// THE DEFECT. `tiers.mjs` shipped:
//
//     free      deepResearchEnabled: true      "showcase depth"
//     pro £79   deepResearchEnabled: false     ← a PAYING customer loses it
//     proIntel  deepResearchEnabled: true
//
// Upgrading from Free to Essentials REMOVED a capability. Nothing caught it
// because nothing compared the tiers to each other — the same absence that let
// two escapers diverge (R-F3866) and let four C-numbers collide (R-F3878).
//
// The second half is subtler and is why this file exists rather than a one-line
// value change. `/api/billing/me` reports these flags as `capabilities`, and
// `account.html` renders them to the customer — but the ONLY capability flag
// enforced anywhere is `publicApiEnabled`. `deepResearchEnabled` and
// `autonomousEnabled` are read solely to be displayed. So the customer is shown
// an entitlement matrix that reflects no behaviour: CLAUDE.md §1's "certified by
// an absence" class, applied to billing.
//
// WHAT THIS FILE DOES NOT DO. It does not newly ENFORCE anything. Enforcing
// deepResearchEnabled would remove a capability free and pro users have today,
// and enforcing autonomousEnabled would gate a per-account feature the platform
// does not have (the autonomous engine is one global loop — verified live
// 2026-08-14: enabled, running, L3, 98 tasks — not a per-user subscription).
// Both are commercial decisions for the operator, not silent code changes. What
// this file does is make the gap VISIBLE and prevent it growing.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const { TIERS } = await import('../lib/billing/tiers.mjs');
const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const keysSrc = fs.readFileSync(new URL('../lib/api_keys/routes.mjs', import.meta.url), 'utf8');

// Cheapest first. Monotonicity is asserted along this order, so a new tier must
// be placed here deliberately rather than inheriting a comparison by accident.
const BY_PRICE = ['free', 'pro', 'proIntel'];

const BOOLEAN_CAPABILITIES = [
  'deepResearchEnabled',
  'autonomousEnabled',
  'publicApiEnabled',
];

const NUMERIC_ALLOWANCES = [
  'messagesPerDay',
  'ddRunsPerMonth',
  'uploadBytesMax',
  'uploadsPerDay',
];

/**
 * Capability flags that DIFFER between tiers but are not enforced anywhere.
 *
 * SHRINK-ONLY, the same contract as KNOWN_DEAD_CALLS and the C-number
 * LEGACY_COLLISIONS baseline: an entry here is a debt being tracked, not a
 * pattern being blessed. Adding one means a new difference is being sold without
 * being enforced — resolve it instead.
 *
 * NOW EMPTY (R-F3995, C-76). It held `autonomousEnabled`, which proIntel was
 * sold as "full autonomous" while free/pro were shown a restriction that did not
 * exist — the flag was read only to be displayed. It has been levelled to true
 * on every tier per operator direction, so it no longer varies and no longer
 * needs cover here.
 *
 * The emptying was FORCED by the test below rather than remembered: setting the
 * flag uniform made "every entry is a real difference" fail with the exact
 * instruction to remove it. That is the property this set is supposed to have —
 * a debt list that cannot quietly outlive the debt.
 */
const KNOWN_UNENFORCED_DIFFERENCES = new Set([]);

function codeOf(src) {
  // Whole-line comments only. A block-comment regex is not a parser and removed
  // 122,623 characters of real server.mjs when tried (see R-F3989's test).
  return src.split(/\r?\n/).filter(l => !l.trim().startsWith('//')).join('\n');
}

const ALL_CODE = codeOf(serverSrc) + '\n' + codeOf(keysSrc);

describe('R-F3990 — tier capabilities are monotonic and honestly presented', () => {

  it('THE DEFECT: no tier exposes fewer capabilities than a cheaper tier', () => {
    for (let i = 1; i < BY_PRICE.length; i++) {
      const cheaper = TIERS[BY_PRICE[i - 1]];
      const dearer = TIERS[BY_PRICE[i]];
      for (const cap of BOOLEAN_CAPABILITIES) {
        assert.ok(!(cheaper[cap] === true && dearer[cap] === false),
          `${dearer.id} (£${dearer.priceAmount}) loses '${cap}' that ${cheaper.id} `
          + `(£${cheaper.priceAmount}) has — upgrading must never remove a capability`);
      }
    }
  });

  it('numeric allowances never shrink as the price rises', () => {
    for (let i = 1; i < BY_PRICE.length; i++) {
      const cheaper = TIERS[BY_PRICE[i - 1]];
      const dearer = TIERS[BY_PRICE[i]];
      for (const field of NUMERIC_ALLOWANCES) {
        assert.ok(dearer[field] >= cheaper[field],
          `${dearer.id}.${field} (${dearer[field]}) is smaller than `
          + `${cheaper.id}.${field} (${cheaper[field]})`);
      }
    }
  });

  it('a capability sold as a DIFFERENCE is enforced, or tracked as a known gap', () => {
    // The generalisable property. A flag identical across every tier gates
    // nothing by construction and needs no enforcement; a flag that VARIES is a
    // promise, and a promise that no code reads is decoration.
    for (const cap of BOOLEAN_CAPABILITIES) {
      const values = new Set(BY_PRICE.map(id => TIERS[id][cap]));
      if (values.size <= 1) continue;                       // uniform → nothing sold
      if (KNOWN_UNENFORCED_DIFFERENCES.has(cap)) continue;  // tracked debt
      const enforced = new RegExp(`tierAllows\\([^)]*['"]${cap}['"]`).test(ALL_CODE);
      assert.ok(enforced,
        `'${cap}' differs between tiers but has no tierAllows() call site — `
        + 'it is displayed to customers and gates nothing');
    }
  });

  it('the known-gap list is SHRINK-ONLY and every entry is a real difference', () => {
    // Stops the escape hatch becoming a dumping ground: an entry that no longer
    // varies between tiers must be REMOVED, not left as permanent cover.
    for (const cap of KNOWN_UNENFORCED_DIFFERENCES) {
      assert.ok(BOOLEAN_CAPABILITIES.includes(cap),
        `${cap} is tracked but is not a known capability flag`);
      const values = new Set(BY_PRICE.map(id => TIERS[id][cap]));
      assert.ok(values.size > 1,
        `${cap} no longer differs between tiers — remove it from the known-gap list`);
    }
    // R-F3995 — the debt is now ZERO, so the bound is zero. Deliberately not
    // left at "<= 1": a slot that stays open gets filled. Re-opening it must be
    // a visible edit to this line, with a reason, not a quiet addition to a set
    // that already tolerated one entry.
    assert.equal(KNOWN_UNENFORCED_DIFFERENCES.size, 0,
      'every capability difference is now enforced or levelled — do not re-open this list '
      + 'without recording why a difference is being sold that nothing enforces');
  });

  it('publicApiEnabled — the one enforced flag — stays enforced', () => {
    // Guards the only capability that currently does gate behaviour, so a
    // refactor cannot quietly join it to the unenforced set.
    assert.match(ALL_CODE, /tierAllows\([^)]*publicApiEnabled/,
      'publicApiEnabled must keep its enforcement call sites');
  });

  it('every capability reported by /api/billing/me exists in the tier table', () => {
    // No phantom entitlements: the customer must not be shown a field the tier
    // table does not define.
    const billingSrc = fs.readFileSync(new URL('../lib/billing/routes.mjs', import.meta.url), 'utf8');
    const block = billingSrc.slice(billingSrc.indexOf('capabilities:'), billingSrc.indexOf('capabilities:') + 400);
    for (const m of block.matchAll(/tier\.([A-Za-z]+)/g)) {
      assert.ok(Object.hasOwn(TIERS.free, m[1]),
        `/api/billing/me reports tier.${m[1]}, which the tier table does not define`);
    }
  });
});
