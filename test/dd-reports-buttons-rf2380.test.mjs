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

test('R-F2380 DD re-run buttons force a real async rerun and reject cached cases', () => {
  const body = functionBody('startRerun');

  assert.match(
    body,
    /Object\.assign\(\{\s*mode:\s*'standard',\s*async_mode:\s*true,\s*force:\s*true\s*\}/s,
    're-run must set force=true so /dd/orchestrate does not return existing_case',
  );
  assert.match(
    body,
    /if \(!body\.mode\) body\.mode = 'standard';/,
    'detail re-run must not overwrite standard mode with an undefined structured value',
  );
  assert.match(
    body,
    /started\.existing_case[\s\S]*!started\.run_id[\s\S]*started\.status\s*!==\s*'running'/,
    're-run must verify the backend actually started a running async DD',
  );
  assert.match(
    html,
    /querySelectorAll\('\.dd-rerun-btn'\)[\s\S]*await startRerun/,
    'row re-run buttons must be wired to the shared real helper',
  );
  assert.match(
    html,
    /data-entity-type="[^"]*escAttr\(entityType\)[\s\S]*data-jur="[^"]*escAttr\(jur\)/,
    'expanded detail actions must retain row entity type and jurisdiction fallbacks',
  );
  assert.match(
    html,
    /querySelector\('\[data-action="rerun"\]'\)\??\.addEventListener\('click'[\s\S]*await startRerun/,
    'detail re-run button must be wired to the shared real helper',
  );
});

test('R-F2410 DD re-run banner rejects unnamed placeholder names', () => {
  const body = functionBody('startRerun');

  assert.match(
    html,
    /function ddIsPlaceholderName\(value\)[\s\S]*'\(unnamed\)'[\s\S]*'unnamed'/,
    'DD reports page must classify unnamed display values as placeholders',
  );
  assert.match(
    body,
    /const rerunEntityName = ddDisplayEntityName\(\s*started\.entity_name,\s*started\.name,\s*body\.name,\s*ctx && ctx\.name,\s*\);/,
    're-run toast must prefer the backend-confirmed entity over stale UI context',
  );
  assert.doesNotMatch(
    body,
    /Re-run started for '\s*\+\s*\(ctx\.name \|\| 'entity'\)/,
    're-run toast must not echo ctx.name directly because it can be "(unnamed)"',
  );
  assert.match(
    html,
    /const rowEntityName = \(\(row\.querySelector\('\.dd-entity-name'\) \|\| \{\}\)\.textContent\) \|\| '';/,
    'expanded detail view must keep the visible row name as a fallback',
  );
  assert.match(
    html,
    /const entityName = ddDisplayEntityName\(sv\.entity_name, rowEntityName, sv\.name, sv\.entity, sv\.query\) \|\| '\(unknown\)';/,
    'expanded detail rerun must not trust a structured "(unnamed)" over the visible row name',
  );
});

test('R-F2380 DD delete buttons require verified deletion before success', () => {
  const verified = functionBody('deleteVerified');
  const remove = functionBody('removeDeletedReport');

  assert.match(
    verified,
    /result\.ok\s*===\s*true[\s\S]*result\.blob_deleted[\s\S]*index_entries_removed/,
    'delete success must be grounded in backend deletion evidence',
  );
  // R-F4002 (C-81) — was pinned to the literal `.filter(r => r && r.run_id !== runId)`.
  // R-F3532 then made the removal STRONGER: a delete cascades over a version chain,
  // so the code now filters every run the cascade removed
  // (`!_locallyDeletedRunIds.has(r.run_id)`), not just the one whose button was
  // clicked. Filtering by the single id was the very bug R-F3532 fixed — the
  // previous version resurfaced as a "new" row on the next poll.
  //
  // So this guard sat permanently red while pinning the WEAKER behaviour, which is
  // worse than no guard: a red test can never go green, so it can never carry
  // information either. Rewritten to the surviving intent — the deleted report
  // leaves local state immediately — without dictating which of the two correct
  // spellings achieves it.
  assert.match(
    remove,
    /_allReports\s*=[\s\S]*\.filter\(/,
    'verified delete must remove the report from local UI state immediately',
  );
  // R-F4002 (C-81) — pinned `removeDeletedReport(runId)` with exactly one argument.
  // R-F3532 gave it a second (`removeDeletedReport(runId, deleted)`) so the whole
  // cascade of deleted run_ids can be suppressed, not just the clicked row. The
  // ORDER is the contract worth guarding — verify, then remove — not the arity.
  assert.match(
    html,
    /querySelectorAll\('\.dd-delete-btn'\)[\s\S]*deleteVerified\(deleted\)[\s\S]*removeDeletedReport\(\s*runId/,
    'row delete must verify and then remove the local report',
  );
  assert.match(
    html,
    // R-F4002 (C-81) — same arity rot as the row-delete assertion above:
    // R-F3532 made this `removeDeletedReport(rid, deleted)` so the cascade is
    // suppressed too. Guard the ORDER (verify, then remove), not the argument list.
    /querySelector\('\[data-action="delete"\]'\)\??\.addEventListener[\s\S]*deleteVerified\(deleted\)[\s\S]*removeDeletedReport\(\s*rid/,
    'detail delete must verify and then remove the local report',
  );
});

test('DD report detail data-action controls are all wired to handlers', () => {
  const emittedActions = [...html.matchAll(/data-action="([^"]+)"/g)].map((match) => match[1]);
  const uniqueActions = [...new Set(emittedActions)].sort();

  assert.deepEqual(
    uniqueActions,
    // R-F2837 added 'pdf' and 'print' — reviewed and accounted for here, which is
    // exactly what this tripwire is for. The contract is WIDENED to the real set,
    // not weakened: every entry below still has to prove it is wired.
    // R-F4002 (C-81) — 'watchlist' added. This is the tripwire doing its job: the
    // control shipped without being accounted for here, so the guard went red and
    // demanded the review it exists to force. Reviewed — dd-reports.html:1743
    // binds it to openWatchlistAdd() with the entity, type, jurisdiction,
    // canonical id and source_ref, so it is genuinely wired, not a dead button.
    // The contract is WIDENED to the real set, never weakened: every entry below
    // still has to prove it is wired by the handler check that follows.
    ['case-file', 'copy', 'delete', 'pdf', 'print', 'rerun', 'showVaultCase',
      'vls-proof', 'vls-verify', 'watchlist'],
    'new DD data-action controls must be reviewed and explicitly accounted for',
  );

  // 'pdf' is deliberately absent from the click-handler check below: it is an
  // <a href> to /api/aria/dd/report/:id/pdf, so the browser performs the download
  // natively. Requiring a click handler for it would force pointless JS.
  for (const action of ['case-file', 'copy', 'delete', 'print', 'rerun', 'vls-proof', 'vls-verify']) {
    const selector = `querySelector('[data-action="${action}"]')`;
    const selectorAt = html.indexOf(selector);
    assert.notEqual(selectorAt, -1, `${action} selector must exist`);
    const handlerAt = html.indexOf("addEventListener('click'", selectorAt);
    assert.ok(handlerAt > selectorAt, `${action} must have a direct click handler`);
  }
  assert.match(
    html,
    /document\.addEventListener\('click'[\s\S]*closest\('\[data-action="showVaultCase"\]'\)/,
    'showVaultCase rows must be wired by delegated click handling',
  );
});

test('R-F2383 DD detail renders structured quality assessment', () => {
  assert.match(
    html,
    /const qa = sv\.quality_assessment \|\| null;/,
    'detail view must read the backend quality_assessment contract',
  );
  assert.match(
    html,
    /<b>Quality<\/b> Grade /,
    'quality grade must be visible in the report hero stats',
  );
  assert.match(
    html,
    /Quality blocked by:/,
    'non-A reports must surface blocking evidence gaps in the hero',
  );
});

// ── R-F2844 — the PDF export must not be a plain link ────────────────────────
// This page authenticates with a Bearer header via authed() (public/js/app.js).
// A native <a href> navigation sends NO header, so an anchor pointing at an
// authenticated API route returns 401 every time. R-F2837 shipped exactly that
// and the button silently did nothing. These tests pin the contract.

test('R-F2844: authenticated API exports are fetched, never plain <a href>', () => {
  // No anchor may point at an authenticated /api/aria/ route from this page.
  const anchors = [...html.matchAll(/<a\b[^>]*href=[^>]*?\/api\/aria\/[^>]*>/g)].map(m => m[0]);
  assert.deepEqual(
    anchors, [],
    'an <a href> to an authenticated API route cannot send the Bearer header '
    + '(authed() attaches it) and will 401 — fetch it and download the blob instead',
  );
});

test('R-F2844: the PDF control fetches through authed()', () => {
  assert.ok(
    html.includes("authed('/api/aria/dd/report/' + encodeURIComponent(rid) + '/pdf')"),
    'the PDF export must go through authed() so the Bearer token is attached',
  );
});

test('R-F2844: handler lookups are guarded so one missing control cannot kill the rest', () => {
  // These handlers register in sequence on one element. An unguarded
  // querySelector that returns null throws and every LATER handler silently
  // never registers — which is how adding the print button also broke copy,
  // delete and the VLS controls.
  // R-F2850: EVERY sequential handler lookup is now guarded (const+if for the
  // multi-statement ones, ?. optional-chaining for copy/delete/rerun). An
  // unguarded `.querySelector(...).addEventListener` on detailRow means one null
  // lookup can throw and silently kill every later handler — so the set must be
  // empty for ALL controls, not a subset.
  const unguarded = [...html.matchAll(
    /detailRow\.querySelector\('\[data-action="([^"]+)"\]'\)\.addEventListener/g,
  )].map(m => m[1]);
  assert.deepEqual(
    unguarded, [],
    'guard every data-action lookup (null-check before addEventListener) — '
    + `unguarded: ${unguarded.join(', ')}`,
  );
});
