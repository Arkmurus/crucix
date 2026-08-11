// test/dd-followup-pending-rf2827.test.mjs
//
// R-F2827 — the DD panel must not claim "Complete" while an ASYNC FOLLOW-UP is
// still gathering evidence, or has failed.
//
// THE DEFECT. R-F2820 fixed the poll-CEILING case (a run still `running` at the
// 12.5-minute watch limit). This is the other half, and it is the NORMAL path:
//   dd_orchestrator.py:10739 sets
//       report.adverse_media = {"status": "in_progress",
//                               "framework_version": "R-F2657 async follow-up"}
//   and stashes `_am_followup` to run AFTER persist (R-F2657). The run therefore
//   reaches a terminal status while adverse-media evidence is still being gathered —
//   and per the live DD analysis this was `in_progress` on ALL THREE live runs.
//   public/dd-reports.html contained ZERO references to adverse_media / follow_up /
//   in_progress, so it rendered "✓ Complete. See your reports below." + a success
//   toast regardless. It told the user evidence-gathering had finished when it had
//   not — and adverse media is 20% of the evidence scorecard.
//
// THE TRAP THIS TEST GUARDS. On SUCCESS run_adverse_media_deep_search returns NO
// `status` key at all (0 occurrences in the function) — it returns `ok: true`. So
// "no status" cannot be read as "finished": that is the certified-by-an-absence
// shape that produced three fabricated Phase A gates this month (R-F2622/2640/2643).
// Completion must be positively evidenced by `ok === true`; anything unrecognised is
// UNKNOWN, never Complete.
//
// Run: node --test test/dd-followup-pending-rf2827.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const PAGE = readFileSync(path.join(ROOT, 'public', 'dd-reports.html'), 'utf8');

/** Load the page's real classifier. */
function loadClassifier() {
  const start = PAGE.indexOf('function followUpState');
  assert.ok(start > -1,
    'dd-reports.html must expose followUpState() — the page currently has no concept ' +
    'of a pending async follow-up (0 refs to adverse_media/in_progress)');
  const end = PAGE.indexOf('\nfunction ', start + 1);
  // R-F3866 — the appended return MUST start on its own line. The slice can end
  // with a trailing `//` comment (any comment sitting between this function and
  // the next one), and without the newline `; return followUpState;` lands INSIDE
  // that comment: the Function evaluates cleanly and returns undefined, so the
  // failure reads as 'loadClassifier(...) is not a function' rather than pointing
  // anywhere near the cause.
  return new Function(`${PAGE.slice(start, end)}
; return followUpState;`)();
}

// The exact shapes the pipeline produces.
const PENDING = { adverse_media: { status: 'in_progress', framework_version: 'R-F2657 async follow-up' } };
const TIMED_OUT = { adverse_media: { status: 'incomplete', error: 'adverse-media deep search timed out (budget 180s)' } };
const ERRORED = { adverse_media: { error: 'boom', framework_version: 'researcher.run_adverse_media_deep_search R-F159' } };
const PARTIAL = { adverse_media: { ok: true, partial: true, timed_out: true, findings_count: 2, templates_searched: 4 } };
const DONE = { adverse_media: { ok: true, partial: false, findings_count: 0, templates_searched: 30 } };
const ABSENT = {};
const GIBBERISH = { adverse_media: { something_unrecognised: 1 } };

describe('R-F2827 — async follow-up state is classified honestly', () => {
  test('in_progress is PENDING, not complete', () => {
    const s = loadClassifier()(PENDING);
    assert.equal(s.state, 'pending');
    assert.equal(s.blocksComplete, true, 'a pending follow-up must block the Complete claim');
  });

  test('a timed-out follow-up is FAILED and carries the reason', () => {
    const s = loadClassifier()(TIMED_OUT);
    assert.equal(s.state, 'failed');
    assert.equal(s.blocksComplete, true);
    assert.match(s.detail, /timed out|budget 180s/i,
      'the user must be told WHY, not just that something went wrong');
  });

  test('an errored follow-up is FAILED', () => {
    const s = loadClassifier()(ERRORED);
    assert.equal(s.state, 'failed');
    assert.equal(s.blocksComplete, true);
  });

  test('a PARTIAL sweep is reported as partial, never as a full result', () => {
    const s = loadClassifier()(PARTIAL);
    assert.equal(s.state, 'partial');
    assert.equal(s.blocksComplete, true,
      'a deadline-truncated sweep is honest PARTIAL evidence — presenting it as a ' +
      'completed sweep would imply coverage that was never achieved');
  });

  test('only a positively-evidenced ok:true sweep counts as done', () => {
    const s = loadClassifier()(DONE);
    assert.equal(s.state, 'done');
    assert.equal(s.blocksComplete, false);
  });

  test('ABSENCE is never read as completion (the certified-by-an-absence trap)', () => {
    for (const [name, shape] of [['absent', ABSENT], ['unrecognised', GIBBERISH]]) {
      const s = loadClassifier()(shape);
      assert.notEqual(s.state, 'done',
        `${name} adverse_media was read as DONE — on success the function returns ` +
        'ok:true and NO status key, so "no status" must never imply finished');
      assert.equal(s.state, 'unknown');
    }
  });

  test('an unknown state does not silently block, but is never called complete', () => {
    // Absence must not manufacture a scary banner on every legacy report either —
    // it must simply refrain from claiming completion.
    const s = loadClassifier()(ABSENT);
    assert.equal(s.blocksComplete, false, 'absence must not spam every historical report');
    assert.ok(!/complete/i.test(String(s.label || '')),
      'an unknown follow-up state must not be labelled complete');
  });
});

describe('R-F2827 — the panel wires the classifier into its terminal message', () => {
  test('the Complete message is gated on the follow-up state', () => {
    assert.ok(PAGE.includes('followUpState('),
      'the terminal render must consult followUpState()');
    const poll = PAGE.slice(PAGE.indexOf('const MAX_TRIES'), PAGE.indexOf('setTimeout(poll, 6000);'));
    assert.ok(/followUpState/.test(poll),
      'the poll terminal branch must classify the follow-up before claiming Complete');
  });

  test('the page no longer has a path that claims Complete unconditionally', () => {
    // The pre-fix form: st!=='failed' -> "✓ Complete" with nothing else consulted.
    const poll = PAGE.slice(PAGE.indexOf('const MAX_TRIES'), PAGE.indexOf('setTimeout(poll, 6000);'))
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    const completeLines = poll.split('\n').filter((l) => /✓ Complete/.test(l));
    assert.ok(completeLines.length > 0, 'the success message should still exist');
    for (const l of completeLines) {
      assert.ok(!/st === 'failed'\s*\)?\s*$/.test(l.trim()),
        'the Complete branch must not be selected by run status alone');
    }
  });

  test('R-F2820 ceiling honesty is preserved', () => {
    assert.ok(PAGE.includes('has NOT been reported complete'),
      'the poll-ceiling fix must not be lost while fixing the follow-up case');
  });
});
