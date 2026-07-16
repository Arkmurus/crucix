import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../public/dd-reports.html', import.meta.url), 'utf8');

function functionBody(name) {
  const start = html.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `${name} must exist`);
  const brace = html.indexOf('{', start);
  let depth = 0;
  for (let i = brace; i < html.length; i += 1) {
    if (html[i] === '{') depth += 1;
    if (html[i] === '}') depth -= 1;
    if (depth === 0) return html.slice(brace + 1, i);
  }
  throw new Error(`${name} body was not closed`);
}

// ── R-F2650 — re-run stuck on "Running" until a manual refresh ───────────────
// Root cause: loadReports()/renderTable() rebuild the row DOM but never reset
// `_expandedRow`, so a re-run started from an open detail left `_expandedRow`
// pointing at a detached node — truthy — and scheduleRunningRefresh's
// `if (_expandedRow) { … return; }` guard suspended the 8s poll forever.

test('R-F2650 collapseOpenDetail clears the stale _expandedRow reference', () => {
  const body = functionBody('collapseOpenDetail');
  assert.match(body, /_expandedRow\s*=\s*null/, 'must null out _expandedRow so the poll resumes');
  assert.match(body, /dd-detail-row/, 'must remove the open detail row from the DOM');
});

test('R-F2650 startRerun collapses the open detail before re-rendering the list', () => {
  const body = functionBody('startRerun');
  assert.match(body, /collapseOpenDetail\(\)/, 're-run must collapse the open detail so the poll is not suspended');
  // ordering: collapse must happen before the loadReports re-render that would
  // otherwise orphan _expandedRow.
  const iCollapse = body.indexOf('collapseOpenDetail()');
  const iLoad = body.indexOf('loadReports(true)');
  assert.ok(iCollapse !== -1 && iLoad !== -1 && iCollapse < iLoad,
    'collapseOpenDetail() must run before loadReports(true)');
});

test('R-F2650 the running-refresh poll still guards on _expandedRow (intent preserved)', () => {
  // We did NOT remove the guard — an open detail is still not yanked mid-view;
  // the fix is that re-run collapses first, so the guard no longer traps forever.
  const body = functionBody('scheduleRunningRefresh');
  assert.match(body, /if\s*\(\s*_expandedRow\s*\)/, 'guard preserved');
  assert.match(body, /loadReports\(true\)/, 'poll still reconciles when no detail is open');
});

// ── R-F2651 — never-false-clean report display ──────────────────────────────
// The backend LayerStatus enum is ok/partial/skipped/error and each report
// carries run_diagnostics; the page showed neither honestly.

test('R-F2651 renderSection flags EVERY non-ok LayerStatus (full enum)', () => {
  // The backend LayerStatus enum (dd_schema.py:42-48) is ok/partial/skipped/
  // error/prereq_fail/degraded. Every non-ok value must be styled + banner-flagged.
  // (Pass-2 caught that prereq_fail/degraded — set on the core compliance layer —
  //  were originally dropped to neutral 'info' with no banner.)
  for (const st of ['error', 'prereq_fail', 'skipped', 'partial', 'degraded']) {
    assert.match(html, new RegExp(`${st}:\\s*\\{\\s*cls:`),
      `LayerStatus '${st}' must be in the _STATUS map`);
  }
  assert.match(html, /did NOT complete \(ERROR\)/, 'ERROR layer must be labelled');
  assert.match(html, /PREREQUISITE MISSING/, 'prereq_fail layer must be labelled');
  assert.match(html, /was SKIPPED/, 'SKIPPED layer must be labelled');
  assert.match(html, /PARTIAL — this check did not fully complete/, 'PARTIAL layer must be labelled');
  assert.match(html, /DEGRADED — a prerequisite was missing/, 'degraded layer must be labelled');
  assert.match(html, /absence of findings here is NOT an all-clear/,
    'an incomplete layer must state that empty ≠ clean');
});

test('R-F2651 prereq_fail/degraded keep a non-neutral style (never neutral info)', () => {
  // Regression guard: 'degraded' used to map to warn (amber) and must not be
  // downgraded to neutral; 'prereq_fail' (zero signal) must be flagged too.
  assert.match(html, /degraded:\s*\{\s*cls:\s*'dd-section-warn'/, 'degraded → warn, not neutral info');
  assert.match(html, /prereq_fail:\s*\{\s*cls:\s*'dd-section-err'/, 'prereq_fail → error style (zero signal)');
});

test('R-F2651 empty-but-incomplete layer does not render a bare "No findings" all-clear', () => {
  assert.match(html, /st===['"]ok['"]\s*\?\s*['"]No findings recorded\.['"]/,
    'only an OK layer may say "No findings recorded."');
  assert.match(html, /did not fully run \(see status above\); this is NOT an all-clear/,
    'a non-OK empty layer must say it did not fully run');
});

test('R-F2651 renderRunDiagnostics surfaces coverage and is wired into the report', () => {
  const body = functionBody('renderRunDiagnostics');
  assert.match(body, /count_run/, 'coverage must show how many checks ran');
  assert.match(body, /count_skipped/, 'coverage must show skipped checks');
  assert.match(body, /registry/i, 'coverage must show registry hit/miss');
  assert.match(body, /Run coverage/, 'coverage panel must be titled');
  // wired into the detail render, fed from the structured view's run_diagnostics
  assert.match(html, /renderRunDiagnostics\(sv\.run_diagnostics\)/,
    'the coverage panel must be rendered from sv.run_diagnostics');
});
