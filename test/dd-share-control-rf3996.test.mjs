// test/dd-share-control-rf3996.test.mjs
//
// R-F3996 (C-77) — the DD sharing opt-out was honoured by the engine and
// unreachable by the customer.
//
// THE DEFECT. A DD report is company-visible by default: `share_to_company`
// defaults to True, and any colleague on the same email domain can read AND
// delete it (dd_orchestrator.list_reports, routes/aria.py::_dd_report_access_allowed).
// The engine has always honoured `share_to_company: false` — the HTTP route reads
// it from the request body and passes it to orchestrate_dd on both the async and
// the synchronous branch. But the string appeared ZERO times in the entire
// front-end, so the control existed and no customer could reach it.
//
// For a due-diligence product that is the wrong default to be stuck with: an M&A
// team screening an acquisition target, or anyone running DD on an internal
// counterparty, cannot keep it to themselves. And because the same predicate
// grants DELETE, a colleague can destroy a compliance artifact they did not run.
//
// ADDITIVE ONLY — THE DEFAULT DOES NOT MOVE. Flipping the default to private
// would silently remove access colleagues rely on today: reports already visible
// to a team would stay visible (the flag is stamped per report at run time), but
// every NEW report would vanish from their view with no announcement. That
// trades one silent behaviour for another. This change gives the user the choice
// at the moment they run the DD and leaves the default exactly where it was.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const DD = fs.readFileSync(new URL('../public/dd-reports.html', import.meta.url), 'utf8');
const ROUTE = fs.readFileSync(new URL('../aria_service/routes/aria.py', import.meta.url), 'utf8');

describe('R-F3996 — the DD sharing control is reachable', () => {

  it('THE DEFECT: the run modal exposes a share_to_company control', () => {
    assert.match(DD, /share_to_company/,
      'the run modal must let the user choose whether the report is shared with '
      + 'their organisation — the engine honours it and nothing surfaced it');
  });

  it('the control is a real labelled form element, not a bare checkbox', () => {
    const id = 'dd-r-share';
    assert.ok(DD.includes(`id="${id}"`), `the control needs a stable id (${id})`);
    assert.match(DD, new RegExp(`for="${id}"`),
      'the control must have an associated <label> — this is a confidentiality '
      + 'decision and must not be a mystery tick-box');
  });

  it('the run request SENDS the choice', () => {
    // A control that renders and is never transmitted is the same defect one
    // layer up.
    const submitIdx = DD.indexOf("dd-run-submit');");
    assert.ok(submitIdx > 0, 'the submit handler should exist');
    const handler = DD.slice(submitIdx, submitIdx + 3000);
    assert.match(handler, /share_to_company:/,
      'the orchestrate body must carry share_to_company');
    assert.match(handler, new RegExp("getElementById\\('dd-r-share'\\)"),
      'the value sent must come from the control, not a constant');
  });

  it('the DEFAULT is still shared — no silent confidentiality flip', () => {
    // The control ships CHECKED. Reports keep behaving exactly as they do today
    // unless the user decides otherwise, which is the whole point of an additive
    // change: nobody loses access they had this morning.
    const idx = DD.indexOf('id="dd-r-share"');
    assert.ok(idx > 0);
    const el = DD.slice(idx, idx + 200);
    assert.match(el, /checked/,
      'the sharing box must default to CHECKED; flipping the default to private '
      + 'would remove colleague access with no announcement');
  });

  it('the owner can SEE that a report came out private', () => {
    // A control whose effect is invisible is unverifiable: the user ticks it off
    // and has no way to confirm it took. The pre-existing "shared" badge answers
    // a different question (is this someone ELSE's report?).
    assert.match(DD, /privateBadge/, 'report rows must indicate a private report');
    assert.match(DD, /bi-lock/, 'the private marker should read as a lock, not as text alone');
  });

  it('a legacy report with no share flag is NOT labelled private', () => {
    // Absence is not privacy. The field postdates most stored reports and the
    // route treats a missing value as SHARED, so a loose truthiness test would
    // stamp a confidentiality guarantee on every report written before this
    // shipped. Same absence-is-not-evidence rule as the C-39 sanctions coverage.
    const idx = DD.indexOf('let privateBadge');
    assert.ok(idx > 0);
    const block = DD.slice(idx, idx + 400);
    assert.match(block, /r\.share_to_company === false/,
      'the private marker must test STRICT false, never a falsy/absent value');
  });

  it('the brain still reads the field the UI sends — the names must not drift', () => {
    // Pins the contract across the tier boundary. The failure this prevents is
    // the one being fixed: a control wired to a field nothing consumes, which
    // looks correct in the browser and changes nothing.
    assert.match(ROUTE, /body\.get\("share_to_company"\)/,
      'the orchestrate route must read share_to_company from the request body');
    assert.match(ROUTE, /share_to_company=_share_to_company/,
      'the parsed value must be passed through to orchestrate_dd');
  });

  it('an omitted field still means shared — the API default is unchanged', () => {
    // Other callers (WhatsApp, the CLI, the public API) do not send this field.
    // They must keep the behaviour they have.
    const idx = ROUTE.indexOf('_share_to_company = body.get("share_to_company")');
    assert.ok(idx > 0, 'the route should parse the field');
    const block = ROUTE.slice(idx, idx + 260);
    assert.match(block, /is None:\s*\n\s*_share_to_company = True/,
      'a request that omits share_to_company must still default to shared');
  });
});
