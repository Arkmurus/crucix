// test/rail-reachable-and-explorer-nav-rf4009-4010.test.mjs
//
// R-F4009 (C-87) — the evidence rail was DISPLAY:NONE below 1100px.
//
// The landing page promises "Every finding carries the sources behind it". On the
// main chat product that source list lives in a right-hand rail, and
// `@media (max-width: 1100px) { .entity-rail { display: none; } }` removed it on
// every phone, tablet and sub-1100px laptop. The differentiator was desktop-only.
//
// THE ORIGINAL DECISION WAS RIGHT; THE IMPLEMENTATION OVERSHOT. The comment beside
// it explains the rail was hidden "so the chat column stays readable on mobile /
// laptops", which is correct — a 280px rail beside a chat column on a phone is
// unusable. The defect is that "not always visible" was implemented as "not
// reachable at all". Sources must remain REACHABLE on a narrow screen, not
// necessarily on screen at all times.
//
// So below 1100px the rail becomes a drawer with a toggle, and above 1100px
// nothing changes. A test pins the desktop behaviour precisely because that is
// what a careless narrow-screen fix would break.
//
// R-F4010 (C-88) — explorer.html was reachable only from the admin brain page.
//
// A 46 KB customer-facing surface (search, sanctions divergence, RCA screening,
// counter-intel scan — five endpoints, all resolving) sat outside the navigation
// entirely; its single inbound link was on /aria-brain, which is admin-only. So it
// was maintained, deployed, working, and invisible to the people it was built for.
// It is not an operator page (it is absent from operatorPages.mjs), so it belongs
// in the nav ungated, like Watchlist beside it.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const ARIA = fs.readFileSync(new URL('../public/aria.html', import.meta.url), 'utf8');
const SIDEBAR = fs.readFileSync(new URL('../public/js/sidebar.js', import.meta.url), 'utf8');
const EXPLORER = fs.readFileSync(new URL('../public/explorer.html', import.meta.url), 'utf8');
const OPERATOR_PAGES = fs.readFileSync(
  new URL('../lib/auth/operatorPages.mjs', import.meta.url), 'utf8');

/** The narrow-screen media block that governs the rail. */
function narrowRailRule() {
  const m = ARIA.match(/@media\s*\(max-width:\s*1100px\)\s*\{([\s\S]{0,900}?)\n\s{0,4}\}/);
  return m ? m[1] : '';
}

describe('R-F4009 — sources stay reachable on a narrow screen', () => {

  it('THE DEFECT: the rail is no longer simply removed below 1100px', () => {
    const rule = narrowRailRule();
    assert.ok(rule, 'the 1100px media block should exist');
    assert.doesNotMatch(rule, /\.entity-rail\s*\{\s*display:\s*none/,
      'display:none makes the evidence promise unreachable on every phone and '
      + 'tablet — hide it from the flow, do not delete it from the page');
  });

  it('a control exists to open it, and it is not an inline handler', () => {
    assert.match(ARIA, /id="entity-rail-toggle"/, 'a toggle control must exist');
    // CSP sets script-src-attr 'none' (R-F1919), so an inline onclick would be
    // silently dead — the exact failure R-F3852 found on the toast dismiss button.
    assert.doesNotMatch(ARIA, /id="entity-rail-toggle"[^>]*onclick/,
      'an inline onclick is blocked by CSP and would do nothing');
    assert.match(ARIA, /entity-rail-toggle[\s\S]{0,600}?addEventListener/,
      'the toggle must be wired with addEventListener');
  });

  it('the toggle is offered ONLY on narrow screens', () => {
    // On desktop the rail is already visible; a second control would be noise.
    assert.match(ARIA, /#entity-rail-toggle\s*\{[^}]*display:\s*none/,
      'the toggle must be hidden by default');
    assert.match(narrowRailRule(), /#entity-rail-toggle/,
      'and revealed inside the narrow-screen media block');
  });

  it('the toggle says how many sources there are', () => {
    // "Sources" alone gives no reason to tap it. The count is the signal.
    assert.match(ARIA, /entity-rail-toggle-count|toggleCount/,
      'the toggle must carry the source count');
  });

  it('it is not offered when there is nothing to show', () => {
    // An empty drawer is worse than no drawer: it promises evidence and delivers
    // an empty panel. The toggle must follow the same hasEntity gate the rail
    // card itself uses.
    const at = ARIA.indexOf('hasEntity');
    assert.ok(at > 0, 'the rail should gate on hasEntity');
    const block = ARIA.slice(at, at + 2000);
    assert.match(block, /entity-rail-toggle|railToggle/,
      'the toggle visibility must follow the same hasEntity gate as the card');
  });

  it('DESKTOP IS UNCHANGED — the rail still renders inline above 1100px', () => {
    // The regression a careless narrow-screen fix causes. The base rule must stay
    // a normal flex child, not become fixed/absolute for everyone.
    const base = ARIA.match(/\.entity-rail\s*\{([\s\S]{0,400}?)\}/);
    assert.ok(base, 'the base .entity-rail rule should exist');
    assert.doesNotMatch(base[1], /position:\s*(fixed|absolute)/,
      'the desktop rail must remain in the normal flow');
    assert.match(base[1], /width:\s*280px/, 'the desktop rail keeps its width');
  });

  it('motion is optional', () => {
    assert.match(ARIA, /prefers-reduced-motion[\s\S]{0,400}?entity-rail/,
      'a slide transition must be disabled for users who ask for reduced motion');
  });
});

describe('R-F4010 — the Intelligence Explorer is reachable', () => {

  it('THE DEFECT: explorer.html is in the navigation', () => {
    assert.match(SIDEBAR, /\/explorer\.html/,
      'a working 46 KB customer surface was reachable only from the admin brain page');
  });

  it('it is NOT gated — it is not an operator page', () => {
    // Wrapping it in data-gated would hide it from every non-admin, i.e. leave the
    // defect in place while looking fixed. operatorPages.mjs is the authority on
    // which pages are privileged, and explorer is absent from it.
    assert.doesNotMatch(OPERATOR_PAGES, /explorer/,
      'explorer is not an operator page, so the nav must not gate it');
    const idx = SIDEBAR.indexOf('/explorer.html');
    const before = SIDEBAR.slice(Math.max(0, idx - 260), idx);
    assert.doesNotMatch(before, /data-gated="[^"]*"[^>]*>\s*\$\{link\('explorer'/,
      'the explorer link must not be wrapped in a data-gated container');
  });

  it('the page still guards itself — nav reachability is not authorisation', () => {
    // Adding it to the nav must not be mistaken for making it public. The page
    // keeps its own client-side gate and its APIs stay server-gated.
    assert.match(EXPLORER, /Auth\.requireAuth\(\)/,
      'explorer.html must keep its own auth guard');
  });

  it('the link carries a label and an icon like its neighbours', () => {
    assert.match(SIDEBAR, /link\('explorer',\s*'\/explorer\.html',\s*'[^']+',\s*'[^']+'\)/,
      'the entry must follow the same link(page, href, icon, label) shape');
  });
});
