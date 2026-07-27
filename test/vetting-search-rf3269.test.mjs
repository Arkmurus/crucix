import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/vetting.html', 'utf8');
const dd = readFileSync('public/dd-reports.html', 'utf8');

// R-F3269 — the vetting queue had no way to find a named file. Every other
// list surface in the product has one, and the DD library's is the house
// pattern, so this is that control, not a second dialect of it.
//
// The one place vetting must NOT copy DD is what the filter is allowed to
// touch. DD's list is the whole page; vetting's list sits under a stat strip
// that answers "what needs me today?". Filtering that strip would let a search
// for one name render "Blocked 0" — a clean-looking caseload produced by a
// text box. The strip stays on the full caseload; only the list narrows.

test('the search input exists and sits in the toolbar with the actions', () => {
  assert.match(page, /id="case-search"/, 'no search input on the vetting page');
  const toolbar = page.indexOf('class="vt-toolbar"');
  const search = page.indexOf('id="case-search"');
  const newCase = page.indexOf('id="new-case-btn"');
  assert.ok(toolbar >= 0 && search > toolbar, 'search must live in the toolbar');
  assert.ok(search < newCase, 'search leads the toolbar, as on the DD library');
});

test('it is a type=search box, so the browser offers a clear affordance', () => {
  assert.match(page, /id="case-search"[^>]*type="search"|type="search"[^>]*id="case-search"/);
});

test('the design matches the DD library search field, value for value', () => {
  // Same control, same product. Divergent padding/radius/ring is how two
  // surfaces start to look like two products.
  const ddRule = dd.match(/\.dd-search \{([\s\S]*?)\}/);
  const vtRule = page.match(/\.vt-search \{([\s\S]*?)\}/);
  assert.ok(ddRule, 'could not read .dd-search — the reference design moved');
  assert.ok(vtRule, 'no .vt-search rule');

  const norm = (s) => s.replace(/\s+/g, ' ').trim();
  for (const decl of norm(ddRule[1]).split(';').map((d) => d.trim()).filter(Boolean)) {
    assert.ok(norm(vtRule[1]).includes(decl),
      `.vt-search is missing the DD field's "${decl}"`);
  }

  const ddFocus = dd.match(/\.dd-search:focus \{([\s\S]*?)\}/);
  const vtFocus = page.match(/\.vt-search:focus \{([\s\S]*?)\}/);
  assert.ok(vtFocus, 'no focus style — the field loses the house focus ring');
  assert.equal(norm(vtFocus[1]), norm(ddFocus[1]), 'focus ring differs from DD');
});

test('typing filters live, exactly like the DD library', () => {
  assert.match(page, /getElementById\('case-search'\)\.addEventListener\('input'/);
});

test('it matches the case reference as well as the name', () => {
  // The reference is printed on the face of every card and is how officers
  // refer to a file out loud. Name-only would make half the visible text
  // unsearchable.
  const fn = page.match(/function applyCaseSearch\(\)[\s\S]*?\n  \}/);
  assert.ok(fn, 'no applyCaseSearch()');
  assert.match(fn[0], /applicant_name/);
  assert.match(fn[0], /case_id/);
});

test('THE honesty property: the queue stat strip is never filtered', () => {
  // renderQueue must always be handed the full caseload. If a search could
  // narrow it, "Blocked 0" would be a statement about the search box that
  // reads as a statement about the caseload.
  const fn = page.match(/function applyCaseSearch\(\)[\s\S]*?\n  \}/)[0];
  assert.doesNotMatch(fn, /renderQueue\(\s*(filtered|matches|shown)/,
    'the stat strip is being filtered — a search must not be able to empty it');
  assert.match(page, /renderQueue\(CASES\)/,
    'the stat strip must be computed from the full caseload');
});

test('no matches reads differently from no cases', () => {
  // R-F2293 made this distinction on the DD library for the same reason: an
  // empty screening queue and an unlucky spelling are not the same news.
  const fn = page.match(/function applyCaseSearch\(\)[\s\S]*?\n  \}/)[0];
  assert.match(fn, /No case matches/,
    'a search with no hits must say so, not claim the queue is empty');
  assert.match(page, /No vetting cases yet/, 'the true-empty message must survive');
});

test('a filtered list says how many of how many are shown', () => {
  // A list that silently drops files is the worst bug this page can have.
  // While a filter is active the page states the size of what it is hiding.
  const fn = page.match(/function applyCaseSearch\(\)[\s\S]*?\n  \}/)[0];
  assert.match(fn, /CASES\.length/, 'the filtered view must reference the full count');
  assert.match(page, /vt-search-note/, 'no "showing N of M" line');
});

test('the list is not silently truncated under the search', () => {
  // The endpoint defaults to 50 and caps at 500. Filtering client-side over a
  // 50-case window would answer "no case matches" for a case that exists — the
  // one answer a screening queue must never give. Ask for the full window.
  assert.match(page, /vetting\/cases\?limit=500/,
    'the page still requests the default 50-case window');
});

test('the query survives a refresh', () => {
  // Refresh re-renders the list. Dropping the filter there would silently
  // widen what the officer is looking at without them touching anything.
  assert.match(page, /renderCases\(\)/);
  const load = page.match(/async function loadCases\(\)[\s\S]*?\n  \}/)[0];
  assert.match(load, /renderCases\(\)/,
    'loadCases must re-render through the filter, not around it');
});
