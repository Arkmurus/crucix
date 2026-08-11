// test/brain-wire-readable-rf3884.test.mjs
//
// C-27 / R-F3884 — the Node tier's brain wire had FULL instrumentation and NO
// READER.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// R-F2821 built `ErrorTracker.brainWireStats()` for an explicit reason, stated
// in its own docstring:
//
//   "Observability of the wire ITSELF (§21a: a signal that silently fails is
//    still dark). Before R-F2821 this method had no res.ok check and a bare
//    `catch {}`, so a brain returning 401/404/500 was indistinguishable from a
//    successful delivery — the tier could report 'wired' while emitting nothing."
//
// It counts delivered / dropped / droppedNoTarget / throttled / lastError /
// lastOkAt. Then nothing in production ever called it. Every call site was in
// `test/`. So the counters incremented in memory where no operator, dashboard or
// probe could reach them, and in production the wire was *exactly* as
// unobservable as it had been before the fix — the property R-F2821 set out to
// guarantee held only inside a test process.
//
// That is this repo's most-repeated failure shape, and CLAUDE.md records it at
// least four times: three Phase A gates "certified by an absence" (§1), the
// route_audit guard that returned {} for a 770-route app and passed (§16), the
// cost meter that read $0.00 through a process with no store connection (§17),
// and the search health board that could not show a dead engine (§27d). An
// instrument nobody can read is indistinguishable from health.
//
// Consequence if left: the wire degrades silently. A rotated ARIA_API_TOKEN
// (§18 rotates these), a brain 500, or an unset ARIA_SERVICE_URL all leave the
// Node tier emitting nothing to the brain while every surface still reads green
// — and §19e's worst outcome is the operator discovering it himself.
//
// ── WHAT THIS PINS ───────────────────────────────────────────────────────────
// The load-bearing assertion is the FIRST one: brainWireStats() must have a
// production call site. A route can be renamed or moved; "somebody outside
// test/ can read this" is the property that actually failed and the one that
// must not silently regress.
//
// Run: node --test test/brain-wire-readable-rf3884.test.mjs

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import { ErrorTracker } from '../lib/observability/errorTracker.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = readFileSync(join(ROOT, 'server.mjs'), 'utf8');

describe('C-27 the brain wire is READABLE from production, not only from tests', () => {
  it('brainWireStats() has at least one non-test call site', () => {
    // THE defect. Before R-F3884 the only callers were in test/, so the wire's
    // health was observable exclusively from a process that is not production.
    const prodFiles = [];
    const walk = (dir) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        if (e.name === 'node_modules' || e.name === '.git' || e.name === 'test') continue;
        const p = join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (e.name.endsWith('.mjs')) prodFiles.push(p);
      }
    };
    walk(join(ROOT, 'lib'));
    prodFiles.push(join(ROOT, 'server.mjs'));

    const callers = prodFiles.filter((f) => {
      if (f.endsWith(join('observability', 'errorTracker.mjs'))) return false; // its own definition
      const src = readFileSync(f, 'utf8');
      // Ignore comment-only mentions: require an actual invocation.
      return /brainWireStats\s*\(/.test(src.split('\n').filter((l) => !/^\s*(\/\/|\*)/.test(l)).join('\n'));
    });

    assert.ok(
      callers.length > 0,
      'brainWireStats() is called ONLY from test/ — the Node tier\'s brain wire is '
      + 'instrumented but unreadable in production, which is the same as unwired. '
      + 'Expose it on an operator surface.',
    );
  });

  it('an operator-gated route exposes it', () => {
    assert.match(SERVER, /app\.get\(\s*'\/api\/health\/brain-wire'/,
      'expected GET /api/health/brain-wire');

    // It reveals internal wiring state (is the brain reachable? is a token set?),
    // which R-F2775 established is an operator surface, not a customer one.
    const route = SERVER.slice(SERVER.indexOf("app.get('/api/health/brain-wire'"));
    const decl = route.slice(0, route.indexOf('\n') + 1);
    assert.match(decl, /requireInfraRole\(/,
      'the brain-wire surface must be operator-gated, like /api/brain-absorb/diag (R-F2775)');
  });
});

describe('C-27 the surface reports the wire honestly', () => {
  it('reports configured:false when no brain target is set — never a silent ok', () => {
    const t = new ErrorTracker();
    t.configure({ brainBase: '', brainToken: '' });
    const s = t.brainWireStats();
    assert.equal(s.configured, false,
      'an unset ARIA_SERVICE_URL must READ as unconfigured, not as a healthy zero');
  });

  it('exposes the failure counters, not just the success one', () => {
    // A surface that showed only `delivered` would read as healthy while every
    // signal was being dropped — the shape of the defect being fixed.
    const t = new ErrorTracker();
    t.configure({ brainBase: '', brainToken: '' });
    const s = t.brainWireStats();
    for (const k of ['delivered', 'dropped', 'droppedNoTarget', 'throttled']) {
      assert.ok(k in s, `brainWireStats() must expose '${k}' so a failing wire is visible`);
    }
  });

  it('a drop with no target is COUNTED, so silence is distinguishable from health', async () => {
    const t = new ErrorTracker();
    t.configure({ brainBase: '', brainToken: '' });
    const before = t.brainWireStats().droppedNoTarget;
    await t._reportToBrain({ source: 'own_code', errorType: 'x', severity: 'critical', message: 'm' });
    assert.equal(t.brainWireStats().droppedNoTarget, before + 1,
      'a signal dropped for want of a target must increment a counter an operator can read');
  });
});
