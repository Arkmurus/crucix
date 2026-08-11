// test/dd-poll-ceiling-false-complete-rf2820.test.mjs
//
// R-F2820 — CAPABILITY test: the DD panel must never declare a still-running run
// "Complete".
//
// THE DEFECT (verified in the shipped file before this fix):
//   public/dd-reports.html:1698 guarded the poll with
//       if (st === 'running' && tries < 150) { ...keep polling...; return; }
//   At the 150th poll (~12.5 min) with the run STILL `running`, the guard went
//   false and control fell into the terminal branch, which chose its message on
//   `st === 'failed'`. `st` was `'running'`, so the user got:
//       ✓ Complete. See your reports below.       + a SUCCESS toast
//   ...while the DD was still in flight, and while the list row underneath still
//   said "Running". A deep DD takes 10-15 min (see the DD orchestrator), so this
//   was the EXPECTED path, not an edge case.
//
//   Second, related strand: when the fetch failed at the ceiling, the trailing
//   `if (tries < 150) setTimeout(poll, 5000);` simply stopped, stranding
//   "⏳ Running… (745s elapsed)" on screen permanently.
//
// This runs the REAL poll loop extracted from public/dd-reports.html against a
// minimal DOM stub and a controllable clock (repo convention — no jsdom), and
// asserts the user-visible outcome.
//
// Run: node --test test/dd-poll-ceiling-false-complete-rf2820.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const HTML = readFileSync(path.join(ROOT, 'public', 'dd-reports.html'), 'utf8');

/** The real poll loop, lifted verbatim from the page. */
function extractPollBlock() {
  const start = HTML.indexOf('const MAX_TRIES = 150;');
  assert.ok(start > -1, 'dd-reports.html must still declare the poll ceiling');
  const endMarker = 'setTimeout(poll, 6000);';
  const end = HTML.indexOf(endMarker, start);
  assert.ok(end > -1, 'dd-reports.html must still kick off the poll');
  return HTML.slice(start, end + endMarker.length);
}

/**
 * Drive the real loop to a terminal state.
 * `statusSequence` supplies the status each poll observes; `null` = fetch failure.
 */
async function drivePoll({ statusSequence, polls = 400 }) {
  const live = {
    _t: '', _h: '',
    get textContent() { return this._t; },
    set textContent(v) { this._t = String(v); this._h = String(v); },
    get innerHTML() { return this._h; },
    set innerHTML(v) { this._h = String(v); this._t = String(v).replace(/<[^>]*>/g, ''); },
  };
  const toasts = [];
  let loadReportsCalls = 0;
  let idx = 0;

  // Controllable clock: run the queue synchronously instead of waiting 12.5 min.
  const queue = [];
  const setTimeoutStub = (fn) => { queue.push(fn); };

  const authed = async () => {
    const st = statusSequence(idx++);
    if (st === null) throw new Error('network blip');
    return { ok: true, json: async () => ({ status: st }) };
  };

  const src = extractPollBlock();
  // `Toast` is a page-level global (js/app.js) reached via `window.Toast` guard +
  // bare `Toast.show`; stub BOTH bindings or the real source throws ReferenceError.
  const Toast = { show: (msg, kind) => toasts.push({ msg, kind }) };
  // R-F2827 added followUpState() to the poll's terminal branch. Inject the REAL
  // one from the page so this suite exercises the integrated path rather than a
  // stub — otherwise a ReferenceError is swallowed by the poll's transient-error
  // catch and every run looks like it hit the ceiling.
  const fuSrc = HTML.slice(HTML.indexOf('function followUpState'),
    HTML.indexOf('async function ddStartFailureMessage'));
  const followUpState = new Function(`${fuSrc}; return followUpState;`)();
  // R-F3862 — the poll loop escapes the failure message with escText() (R-F3861
  // replaced an ad-hoc `<`-only escape). escText is defined elsewhere in the page,
  // outside this extracted slice, so the sandbox has to supply it — and it supplies
  // the PAGE'S OWN copy, never a reimplementation, so this cannot pass against an
  // escaper that has drifted from what production runs.
  const escTextSrc = (() => {
    const at = HTML.indexOf('function escText(');
    assert.ok(at > -1, 'dd-reports.html must still define escText');
    return HTML.slice(at, HTML.indexOf(String.fromCharCode(10), at));
  })();
  const escText = new Function(`${escTextSrc}; return escText;`)();
  const fn = new Function(
    'runId', 'name', 'authed', 'loadReports', 'document', 'window', 'Toast', 'setTimeout', 'Math',
    'followUpState', 'escText',
    `return (async () => { ${src} })();`,
  );
  await fn(
    'run-abc', 'Acme Ltd',
    authed,
    async () => { loadReportsCalls++; },
    { getElementById: (id) => (id === 'dd-run-live' ? live : null) },
    { Toast },
    Toast,
    setTimeoutStub,
    Math,
    followUpState,
    escText,
  );

  // Drain the controllable clock.
  for (let i = 0; i < polls && queue.length; i++) {
    const next = queue.shift();
    await next();
  }
  return { live, toasts, loadReportsCalls };
}

test('R-F2820 CAPABILITY — a run STILL RUNNING at the ceiling is not called Complete', async () => {
  // The operator's actual path: a deep DD that outlives the 12.5-minute watch window.
  const { live, toasts } = await drivePoll({ statusSequence: () => 'running' });

  // Match the AFFIRMATIVE completion claim only. A naive /complete/i also matches
  // the honest phrase "has NOT been reported complete", which would fail the fix
  // for saying the right thing — assert on the claim, not on a keyword.
  assert.ok(!/✓\s*Complete/i.test(live.textContent),
    `the panel says "${live.textContent}" for a run that never left 'running' — ` +
    'declaring an unobserved run complete is a false clean');
  assert.ok(!/\bis complete\b|\bcompleted\b/i.test(live.textContent),
    `the panel affirmatively claims completion: "${live.textContent}"`);
  assert.ok(!toasts.some((t) => /complete/i.test(t.msg) && t.kind === 'success'),
    `a SUCCESS toast fired for a still-running DD: ${JSON.stringify(toasts)}`);

  // It must say what is actually true: still running, we stopped watching.
  assert.match(live.textContent, /still running/i,
    'the panel must state the run is still running');
  assert.match(live.textContent, /stopped watching|has NOT been reported complete/i,
    'the panel must distinguish "we stopped watching" from "it finished"');
});

test('R-F2820 CAPABILITY — the spinner is never stranded when polling gives up', async () => {
  // Every poll fails outright; the old code silently stopped and left
  // "⏳ Running… (Nsec elapsed)" on screen forever.
  const { live } = await drivePoll({ statusSequence: () => null });
  assert.ok(!/^⏳ Running…/.test(live.textContent.trim()),
    `spinner stranded at "${live.textContent}" after polling gave up`);
  assert.match(live.textContent, /still running/i,
    'giving up must produce an honest terminal message, not silence');
});

test('R-F2820 — a genuinely completed run still reports Complete (not over-broad)', async () => {
  const { live, toasts } = await drivePoll({
    statusSequence: (i) => (i < 3 ? 'running' : 'done'),
  });
  assert.match(live.textContent, /Complete/,
    'a run that actually reached done must still be reported complete');
  assert.ok(toasts.some((t) => t.kind === 'success'),
    'a genuinely complete run must still fire the success toast');
});

test('R-F2820 — a failed run still reports failure', async () => {
  const { live, toasts } = await drivePoll({
    statusSequence: (i) => (i < 2 ? 'running' : 'failed'),
  });
  assert.match(live.textContent, /DD failed/,
    'a failed run must still be reported as failed');
  assert.ok(toasts.some((t) => t.kind === 'error'));
});

test('R-F2820 — the ceiling branch is structurally separate from the terminal branch', () => {
  // Strip comments first: the fix's own explanatory comment QUOTES the old guard
  // to document what was wrong, and a naive source scan matches that quote and
  // reports the bug as still present. Assert against CODE, never against prose.
  const block = extractPollBlock()
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  // The bug WAS the compound guard: `st === 'running' && tries < MAX` let a
  // running run fall into the terminal branch. Assert it is gone.
  assert.ok(!/st === 'running' && tries < /.test(block),
    'the compound running+ceiling guard is the bypassable form — a run that is ' +
    'still running must never fall through to the terminal branch');
  assert.ok(/stillRunning/.test(block),
    'the ceiling must have its own named outcome, distinct from done/failed');
});
