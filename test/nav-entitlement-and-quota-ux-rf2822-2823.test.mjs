// test/nav-entitlement-and-quota-ux-rf2822-2823.test.mjs
//
// R-F2822 — the sidebar must not lie about what the user can do.
// R-F2823 — a quota block must read as a plan limit, not as a broken product.
//
// R-F2822 THE DEFECT: public/js/sidebar.js hand-maintained a `data-admin` flag on
//   3 of the 11 gated links, and revealed them on `user.role === 'admin'`. The
//   server gate uses lib/auth/operatorPages.mjs + roleSatisfies(). The two drifted
//   in BOTH directions:
//     shown-but-forbidden — /bd-intelligence.html, /vls-chain.html, /sources.html,
//       /vault.html, /aria-brain rendered to every user and then 302'd them back
//       to /dashboard.html with no message (server.mjs:4822). Reads as a broken link.
//     entitled-but-hidden — /leads.html and /design-partners.html are in
//       OPERATOR_VIEW_PAGES (poweruser OR admin), but the reveal keyed on
//       role === 'admin', so a poweruser could never reach them from the nav.
//   Fix: the SERVER answers which gated routes this caller may navigate to, from
//   the same table + roleSatisfies() the gate enforces. The browser does no
//   authorization reasoning; a new operator page needs no sidebar edit.
//
// R-F2823 THE DEFECT: the server returns { error: "ddRun cap reached (5/5) — resets
//   at next month", quota: {current, cap} } (server.mjs:3532, quotas.mjs:118-125)
//   and all three DD start/re-run paths discarded it, rendering
//   "DD failed to start (HTTP 429)". A user who merely used their plan was told the
//   product was broken, with no plan, no reset, no /account.html.
//
// Run: node --test test/nav-entitlement-and-quota-ux-rf2822-2823.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  navPagesForRole, OPERATOR_VIEW_PAGES, OPERATOR_ADMIN_PAGES,
} from '../lib/auth/operatorPages.mjs';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const SIDEBAR = readFileSync(path.join(ROOT, 'public', 'js', 'sidebar.js'), 'utf8');
const DDPAGE = readFileSync(path.join(ROOT, 'public', 'dd-reports.html'), 'utf8');
const SERVER = readFileSync(path.join(ROOT, 'server.mjs'), 'utf8');

const VIEW = OPERATOR_VIEW_PAGES.map(([r]) => r);
const ADMIN = OPERATOR_ADMIN_PAGES.map(([r]) => r);

describe('R-F2822 — nav entitlement is computed server-side', () => {
  test('a poweruser SEES the view pages they are entitled to (the hidden-link bug)', () => {
    const allowed = navPagesForRole('poweruser');
    for (const r of ['/design-partners.html']) {
      assert.ok(allowed.includes(r),
        `${r} is in OPERATOR_VIEW_PAGES (poweruser OR admin) but the nav would hide it`);
    }
    for (const r of VIEW) assert.ok(allowed.includes(r), `poweruser must see ${r}`);
  });

  test('a poweruser does NOT see admin-only pages', () => {
    const allowed = navPagesForRole('poweruser');
    for (const r of ADMIN) {
      assert.ok(!allowed.includes(r), `${r} is admin-only; a poweruser must not see it`);
    }
  });

  test('relationship intelligence stays admin-only because it contains PII', () => {
    assert.ok(!navPagesForRole('poweruser').includes('/leads.html'));
    assert.ok(navPagesForRole('admin').includes('/leads.html'));
  });

  test('an ordinary user sees NO gated link (the bouncing-link bug)', () => {
    for (const role of ['analyst', 'viewer', 'support', '', null, undefined]) {
      const allowed = navPagesForRole(role);
      for (const r of [...VIEW, ...ADMIN]) {
        assert.ok(!allowed.includes(r),
          `role "${role}" would be shown ${r}, which 302s them back to the dashboard`);
      }
    }
  });

  test('an admin sees everything', () => {
    const allowed = navPagesForRole('admin');
    for (const r of [...VIEW, ...ADMIN]) assert.ok(allowed.includes(r), `admin must see ${r}`);
  });

  test('EVERY gated link in the sidebar is marked, and every mark is a real gated route', () => {
    const marked = [...SIDEBAR.matchAll(/data-gated="([^"]+)"/g)].map((m) => m[1]);
    assert.ok(marked.length > 0, 'the sidebar must mark its gated links');
    const gatedRoutes = new Set([...VIEW, ...ADMIN]);
    for (const r of marked) {
      assert.ok(gatedRoutes.has(r),
        `sidebar marks ${r} as gated, but it is not in operatorPages.mjs — stale mark`);
    }
    // The converse: any gated route the sidebar LINKS to must be marked. This is
    // the drift-catcher — add an operator page and link it without marking, and
    // this fails.
    for (const route of gatedRoutes) {
      const linked = SIDEBAR.includes(`'${route}'`) || SIDEBAR.includes(`"${route}"`);
      if (!linked) continue;
      assert.ok(marked.includes(route),
        `sidebar links ${route} (a gated page) but never marks it data-gated — ` +
        'it would render to every user and then bounce them');
    }
  });

  test('gated links render HIDDEN and are revealed only on an explicit allow', () => {
    const marks = [...SIDEBAR.matchAll(/data-gated="[^"]+"([^>]*)>/g)].map((m) => m[1]);
    for (const attrs of marks) {
      assert.match(attrs, /display:\s*none/,
        'a gated link must ship hidden — fail closed, so a failed entitlement ' +
        'fetch hides it rather than showing a link that bounces');
    }
    assert.ok(/_applyNavEntitlement/.test(SIDEBAR), 'the reveal path must exist');
    assert.ok(/allow\.has\(el\.getAttribute\('data-gated'\)\)/.test(SIDEBAR),
      'reveal must be driven by the server allow-list, not by a client role check');
  });

  test('the browser performs NO authorization reasoning of its own', () => {
    const fn = SIDEBAR.slice(SIDEBAR.indexOf('_applyNavEntitlement'),
      SIDEBAR.indexOf('_bindEvents'));
    assert.ok(!/role\s*===\s*['"]admin['"]/.test(fn),
      'the reveal must not re-derive entitlement from a role string — that is the ' +
      'duplication that drifted from the server gate in the first place');
  });

  test('the endpoint delegates to the shared function (no second implementation)', () => {
    assert.ok(SERVER.includes("app.get('/api/auth/nav-pages'"), 'endpoint must exist');
    assert.ok(SERVER.includes('navPagesForRole(role)'),
      'the route must delegate to navPagesForRole, or the tables can fork again');
    assert.ok(/nav-pages[\s\S]{0,400}requireAuth/.test(SERVER),
      'the endpoint must require authentication');
  });
});

// ── R-F2823 ──────────────────────────────────────────────────────────────────

/** Run the page's real helper against a stubbed Response. */
async function runHelper(status, body) {
  const src = DDPAGE.slice(
    DDPAGE.indexOf('async function ddStartFailureMessage'),
    DDPAGE.indexOf('function escAttr'),
  );
  const fn = new Function(`${src}; return ddStartFailureMessage;`)();
  const resp = { status, clone: () => ({ json: async () => body }) };
  return fn(resp, 'Due diligence');
}

describe('R-F2823 — a quota block reads as a plan limit, not a broken product', () => {
  test('CAPABILITY: a 429 surfaces the reason, the usage and the upgrade path', async () => {
    const out = await runHelper(429, {
      error: 'ddRun cap reached (5/5) — resets at next month',
      quota: { current: 5, cap: 5 },
    });
    assert.equal(out.isQuota, true);
    assert.ok(!/HTTP 429/.test(out.text), `raw status code leaked to the user: "${out.text}"`);
    assert.ok(!/failed/i.test(out.text),
      `a quota block must not be described as a failure: "${out.text}"`);
    assert.match(out.text, /plan limit/i, 'must name the actual cause');
    assert.match(out.text, /cap reached \(5\/5\)/, 'must pass through the server reason');
    assert.match(out.text, /5 of 5 used/, 'must surface the quota numbers the server sent');
    assert.match(out.text, /\/account\.html/, 'must offer the upgrade path');
  });

  test('a 429 with no body still degrades honestly', async () => {
    const out = await runHelper(429, {});
    assert.equal(out.isQuota, true);
    assert.match(out.text, /plan limit reached/i);
    assert.match(out.text, /\/account\.html/);
  });

  test('a genuine server error is still reported as an error (not mislabelled)', async () => {
    const out = await runHelper(500, { error: 'orchestrator unavailable' });
    assert.equal(out.isQuota, false);
    assert.match(out.text, /HTTP 500/, 'a real fault must still show its status');
    assert.match(out.text, /orchestrator unavailable/);
    assert.ok(!/plan limit/i.test(out.text), 'a 500 must not be blamed on the plan');
  });

  test('an expired session says so, instead of showing a status code', async () => {
    for (const s of [401, 403]) {
      const out = await runHelper(s, {});
      assert.match(out.text, /session has expired/i);
      assert.ok(!new RegExp(`HTTP ${s}`).test(out.text));
    }
  });

  test('ALL THREE DD start/re-run paths use the helper (no site left behind)', () => {
    for (const stale of [
      "'DD failed to start (HTTP '",
      "'Re-run failed (HTTP '",
      "'Re-run did not start (HTTP '",
    ]) {
      assert.ok(!DDPAGE.includes(stale),
        `a DD start path still renders the raw status string ${stale} — ` +
        'fixing one call site and leaving the others is how this drifts back');
    }
    const uses = (DDPAGE.match(/ddStartFailureMessage\(/g) || []).length;
    assert.ok(uses >= 4, `expected the definition + 3 call sites, found ${uses}`);
  });
});
