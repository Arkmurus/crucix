// R-F2765 — per-tier quota enforcement on the web path (message + ddRun).
//
// Before this, tiers.mjs caps were DEFINED but never CHECKED on the main web
// proxy — only the public-API surface called checkAndConsume. That is a
// runaway-spend hole once the primary LLM switches DeepSeek -> Claude. server.mjs
// now calls enforceQuota() on /api/aria/chat, /chat/stream, and /dd/orchestrate.
//
// These tests drive the REAL enforcement decision (enforceQuota + the real
// checkAndConsume in-memory counter — no Redis in test). The first test is the
// load-bearing one: a system/internal caller (WhatsApp listener on the internal
// token, localhost bypass) has no JWT userId and MUST be exempt, or every
// internal call would be wrongly 429'd and WhatsApp chat would break.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

// R-F2858 — ISOLATE the durable counter file.
//
// R-F2829 made quota counters durable (they used to live only in a Map and
// reset on every deploy, so the "5 DD/month" cap was really "5 per deploy").
// The side effect: this file had no PERSIST_DIR/QUOTA_FILE_OVERRIDE, so its
// counters landed in the SHARED runs/quotas.json and survived the run. The
// suite therefore POISONED ITSELF — each execution consumed more of the caps
// these tests assert are free, and the failure count grew run over run (2 -> 4
// observed in one session). The DD keys are month-scoped, so once tripped they
// stayed red until the calendar month rolled.
//
// Worse than a red gate: on a box where PERSIST_DIR=/data (production), running
// the suite would consume REAL customer quota.
//
// QUOTA_FILE_OVERRIDE is read at quotas.mjs module-init, so it must be set
// BEFORE that module is imported — hence the dynamic import below (ESM static
// imports are hoisted and would run first). node --test gives each FILE its own
// process, so this cannot leak into sibling test files.
process.env.QUOTA_FILE_OVERRIDE = path.join(
  mkdtempSync(path.join(tmpdir(), 'quota-rf2765-')), 'quotas.json',
);
const { enforceQuota } = await import('../lib/billing/enforce.mjs');

test('R-F2765: system/internal caller (no userId) is EXEMPT — never blocked', async () => {
  assert.equal(await enforceQuota(undefined, null, 'message'), null);
  assert.equal(await enforceQuota('', 'free', 'message'), null);
  assert.equal(await enforceQuota(null, 'free', 'ddRun'), null);
  assert.equal(await enforceQuota(0, 'proIntel', 'message'), null);
});

test('R-F2765: a real user is allowed under the message cap, blocked at it (free = 50/day)', async () => {
  const uid = 'test-user-rf2765-msg';   // unique id → isolated counter
  for (let i = 0; i < 50; i++) {
    assert.equal(await enforceQuota(uid, 'free', 'message'), null, `message ${i + 1} should be allowed`);
  }
  const blocked = await enforceQuota(uid, 'free', 'message');
  assert.ok(blocked && blocked.allowed === false, 'the 51st message must be blocked');
  assert.equal(blocked.cap, 50);
  assert.match(blocked.reason, /cap reached/);
});

test('R-F2765: DD runs enforce the monthly cap (free = 5/month)', async () => {
  const uid = 'test-user-rf2765-dd';
  for (let i = 0; i < 5; i++) {
    assert.equal(await enforceQuota(uid, 'free', 'ddRun'), null, `dd ${i + 1} should be allowed`);
  }
  const blocked = await enforceQuota(uid, 'free', 'ddRun');
  assert.ok(blocked && blocked.allowed === false, 'the 6th DD must be blocked');
  assert.equal(blocked.cap, 5);
});

test('R-F2765: a paid tier gets its higher cap (proIntel ddRun = 100, not free 5)', async () => {
  const uid = 'test-user-rf2765-pro';
  // 6 DD runs would block a free user; a proIntel user (cap 100) sails through.
  for (let i = 0; i < 6; i++) {
    assert.equal(await enforceQuota(uid, 'proIntel', 'ddRun'), null, `proIntel dd ${i + 1} allowed`);
  }
});

test('R-F2765: unknown tier defaults to free (does not crash / does not grant unlimited)', async () => {
  const uid = 'test-user-rf2765-unknowntier';
  // getTier falls back to DEFAULT_TIER (free) for an unknown id → 5 DD cap applies.
  for (let i = 0; i < 5; i++) {
    assert.equal(await enforceQuota(uid, 'nonexistent-tier', 'ddRun'), null);
  }
  const blocked = await enforceQuota(uid, 'nonexistent-tier', 'ddRun');
  assert.ok(blocked && blocked.allowed === false, 'unknown tier must fall back to the free cap, not unlimited');
});
