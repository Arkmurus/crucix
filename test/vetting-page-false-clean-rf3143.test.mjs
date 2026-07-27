// R-F3143 — the vetting page must never show a clean verdict it did not measure.
//
// ── Provenance ───────────────────────────────────────────────────────────
// This is R-F2819 carried forward. That guard was written for public/status.html
// after the page shipped `<div id="banner" class="st-banner operational">` in
// its STATIC markup, so an unreachable /api/status left the user looking at a
// green "All systems operational" banner next to a failure message. R-F3142
// retired that page; the property it protected is not retired with it.
//
// It matters MORE here. A status page claiming false health costs credibility.
// A screening page claiming a false clean on a named individual is the failure
// mode the whole module exists to prevent: the terminal good state is
// READY_FOR_CONTROLLER_REVIEW, and a human relies on it to decide whether
// someone gets a job.
//
// Runs the REAL inline loader from public/vetting.html against a minimal DOM
// stub (repo convention — no jsdom dependency) with fetch forced to fail, and
// asserts what the USER ends up seeing.
//
// Run: node --test test/vetting-page-false-clean-rf3143.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const HTML = readFileSync(path.join(ROOT, 'public', 'vetting.html'), 'utf8');

/** Every status the engine can return that is NOT a problem state. */
const CLEAN_STATES = ['ready_for_controller_review', 'evidence_complete'];

// ── static markup ─────────────────────────────────────────────────────────

test('R-F3143 — every unassessed case ships in the UNKNOWN queue', () => {
  assert.match(HTML, /key:\s*'UNKNOWN'/);
  assert.match(HTML, /match:\s*\(c\)\s*=>\s*!c\.last_status/);
  assert.match(HTML, /Not assessed/);
  assert.match(HTML, /status is unknown, not clear/);
});

test('R-F3143 — no clean verdict wording is baked into static markup', () => {
  // The static HTML is what a user sees before (and if) JS ever resolves.
  const forbidden = [
    'Ready for controller review',
    'Evidence complete',
    'No findings',
    'No blockers',
  ];
  // Only inspect the markup ABOVE the loader script; the loader legitimately
  // contains these strings as data it may render AFTER a successful read.
  const markup = HTML.slice(0, HTML.indexOf('<script src="js/app.js'));
  for (const claim of forbidden) {
    assert.ok(!markup.includes(claim),
      `vetting.html ships the assertion "${claim}" as static markup — it must ` +
      'only appear after an assessment has actually been read back');
  }
});

test('R-F3143 — the shipped banner icon is not a green tick', () => {
  const unknownBranch = HTML.slice(
    HTML.indexOf('if (!known)'),
    HTML.indexOf('const c = a.counts'),
  );
  assert.match(unknownBranch, /bi-question-circle/);
  assert.ok(!/bi-check-circle/.test(unknownBranch),
    'the unknown per-case verdict must not use a check-circle icon');
});

// ── the real per-case verdict renderer ───────────────────────────────────

function realVerdictStrip() {
  const start = HTML.indexOf('function verdictStrip(a)');
  const end = HTML.indexOf('function stagesSection(a)', start);
  assert.ok(start >= 0 && end > start, 'the per-case verdict renderer must exist');
  const source = HTML.slice(start, end);
  return new Function(
    'STATUS_TEXT', 'ICONS', 'esc',
    `${source}; return verdictStrip;`,
  )(
    {
      READY_FOR_CONTROLLER_REVIEW: ['Ready for controller review', 'Measured'],
      EVIDENCE_COMPLETE: ['Evidence complete', 'Measured'],
    },
    {
      READY_FOR_CONTROLLER_REVIEW: 'bi-check-circle-fill',
      EVIDENCE_COMPLETE: 'bi-check-circle',
    },
    (value) => String(value == null ? '' : value),
  );
}

test('R-F3143 CAPABILITY — an unreachable vetting API leaves no clean verdict', async () => {
  const rendered = realVerdictStrip()(null);
  assert.match(rendered, /vt-vstrip unknown/);
  assert.match(rendered, /Assessment unavailable/);
  assert.doesNotMatch(rendered, /Ready for controller review|Evidence complete/);
});

test('R-F3143 CAPABILITY — an HTTP error on assess does not read as clear', async () => {
  assert.match(HTML, /if \(!a\)\s*\{\s*body\.innerHTML = verdictStrip\(null\)/);
  assert.match(HTML, /assessment could not be completed/);
});

test('R-F3143 CAPABILITY — an unrecognised status is UNKNOWN, not clean', async () => {
  const rendered = realVerdictStrip()({ status: 'SOMETHING_NEW' });
  assert.match(rendered, /vt-vstrip unknown/);
  assert.doesNotMatch(rendered, /Ready for controller review|Evidence complete/);
});

// ── R-F3168: the page shell and card view ────────────────────────────────

test('R-F3168 the page initialises the shared nav', () => {
  // The live symptom: vetting.html rendered with NO menu at all, because it
  // never called Sidebar.init. Every other app page does; nothing checked it.
  assert.match(HTML, /Sidebar\.init\(\s*['"]vetting['"]\s*\)/,
    'vetting.html must call Sidebar.init or the rail never renders');
  assert.match(HTML, /id="sidebar-placeholder"/,
    'the rail needs its placeholder element');
});

test('R-F3168 every badge class used actually exists in the shared stylesheet', () => {
  // A class that does not exist renders as unstyled text, which reads as a
  // missing feature rather than a typo.
  const css = readFileSync(path.join(ROOT, 'public', 'css', 'aria.css'), 'utf8');
  const used = [...HTML.matchAll(/sc-badge-([a-z]+)/g)].map((m) => m[0]);
  assert.ok(used.length, 'the card view should use the shared badge classes');
  for (const cls of new Set(used)) {
    assert.ok(css.includes(`.${cls}`), `${cls} is used but not defined in aria.css`);
  }
});

test('R-F3168 an unassessed case is grouped as unknown, never as clear', () => {
  // Same rule as the verdict banner: absence of an assessment is not a pass.
  assert.match(HTML, /Not yet assessed/,
    'there must be a section for cases with no assessment');
  assert.match(HTML, /status is unknown, not clear/,
    'the unassessed section must say what its absence means');
  assert.ok(!/last_status\s*\|\|\s*['"]READY/.test(HTML),
    'a missing cached status must never default to a ready state');
});

test('R-F3168 no case can silently vanish from the queue', () => {
  assert.match(HTML, /orphans/,
    'cases matching no section must still be rendered — a case that '
    + 'disappears from a screening queue is the worst possible bug here');
});

test('R-F3168 applicant photographs are deliberately not rendered', () => {
  // Extracting a face from a held passport would be biometric processing
  // (Art. 9) and would contradict our own AI Act assessment.
  // R-F3186 — narrowed from a blanket "<img" ban, which fired on the QR-code
  // image. The property is "no APPLICANT PHOTOGRAPHS", not "no images": a QR
  // for an upload link carries no personal data. Any <img> must be one we can
  // name, so a future photo cannot slip in behind a generic allowance.
  assert.ok(!/avatarUrl|photo_url|applicant_photo|passport_image|facial/i.test(HTML),
    'the card view must not render applicant photographs');
  const imgs = [...HTML.matchAll(/<img[^>]*alt="([^"]*)"/gi)].map((m) => m[1]);
  for (const alt of imgs) {
    assert.match(alt, /QR code/i,
      `unexpected image on the page ("${alt}") — only the QR code is allowed`);
  }
  assert.match(HTML, /vt-avatar/, 'initials avatars provide the affordance instead');
});

// ── R-F3170: no primitive browser dialogs ────────────────────────────────

test('R-F3170 the page uses no window.prompt / alert / confirm', () => {
  // R-F2293 established Modal/Toast precisely to replace these. Shipping a
  // window.prompt chain for a four-field decision was worse than ugly: the
  // officer answered from memory with no sight of the file, and a typo in
  // question two could not be fixed without abandoning the sequence.
  // Strip line comments so the prose ABOUT the old popups is not mistaken for
  // the popups themselves.
  const code = HTML.split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .join('\n');
  for (const bad of ['window.prompt', 'window.alert', 'window.confirm']) {
    assert.ok(!code.includes(bad), `${bad} must not be used`);
  }
  assert.ok(!/(^|[^.\w])prompt\s*\(/.test(code), 'bare prompt() must not be used');
  assert.ok(!/(^|[^.\w])alert\s*\(/.test(code), 'bare alert() must not be used');
});

test('R-F3170 it uses the SHARED modal + toast system', () => {
  assert.match(HTML, /Modal\.form\(/, 'forms must use the shared Modal.form');
  assert.match(HTML, /Toast\.show\(/, 'feedback must use the shared Toast');
});

test('R-F3170 the decision dialog shows the engine state as context', () => {
  // The officer must decide WITH the file in front of them, not from memory.
  assert.match(HTML, /Current assessment/,
    'the decision dialog must show the current assessment');
  assert.match(HTML, /type: 'static'/,
    'read-only context is rendered as a static field');
});

test('R-F3170 adverse-decision rules are enforced client-side too', () => {
  // The same rules the server enforces, surfaced BEFORE the round-trip, so a
  // user is never bounced by a rule they could not see.
  assert.match(HTML, /A rejection requires a stated reason/);
  assert.match(HTML, /A rejection requires a second reviewer/);
  assert.match(HTML, /cannot be the sole decision-maker/);
});

test('R-F3170 case creation validates dates against each other', () => {
  assert.match(HTML, /must precede the employment start date/);
  assert.match(HTML, /at least 16/);
});

// ── R-F3183: the applicant's full name is never clipped ──────────────────

test('R-F3183 the applicant name is not truncated', () => {
  // Reported live: "Antonio Magalhaes Cande Correa" rendered clipped. On a
  // screening file the name IS the identity being verified — against a
  // passport, a reference, a register entry — and it is the one field an
  // officer must not have to guess at.
  const rule = /\.vt-name\s*\{([^}]*)\}/.exec(HTML);
  assert.ok(rule, '.vt-name must be styled');
  const body = rule[1];
  assert.ok(!/text-overflow\s*:\s*ellipsis/.test(body),
    '.vt-name must not ellipsis-truncate the applicant name');
  assert.ok(!/white-space\s*:\s*nowrap/.test(body),
    '.vt-name must be allowed to wrap');
  assert.ok(/overflow-wrap|word-break/.test(body),
    'a very long single-token name must still wrap rather than overflow');
});

test('R-F3183 the card renders the full name, unabbreviated', () => {
  // The initials avatar is an ADDITION, not a replacement for the name.
  assert.match(HTML, /class="vt-name">\$\{esc\(c\.applicant_name/,
    'the card must render applicant_name in full');
});

// ── R-F3185/R-F3186: sharing a link ──────────────────────────────────────

test('R-F3186 the share dialog exists and offers the real channels', () => {
  assert.match(HTML, /data-share=/, 'each card must offer a share action');
  assert.match(HTML, /Share a secure upload link/);
  for (const ch of ['link', 'email', 'whatsapp']) {
    assert.ok(HTML.includes(`value: '${ch}'`), `missing channel ${ch}`);
  }
});

test('R-F3186 SMS is not offered, and says why', () => {
  // No SMS provider exists in the tree or package.json. Offering the option
  // and failing silently would be worse than not offering it.
  assert.ok(!/value: 'sms'/.test(HTML), 'SMS must not be offered');
  assert.match(HTML, /no SMS provider is configured/i,
    'the absence must be explained, not left as a silent gap');
});

test('R-F3186 the user is told the link is shown only once', () => {
  // There is no endpoint that can redisplay it, by design. Closing the dialog
  // without copying loses it, so the dialog has to say so.
  assert.match(HTML, /shown once/i);
  assert.match(HTML, /cannot be displayed again/i);
});

test('R-F3186 a referee link must name its period, checked before sending', () => {
  assert.match(HTML, /A referee link must name the period it covers/);
});

test('R-F3186 the share result warns that the link grants upload access', () => {
  assert.match(HTML, /do not post it in a shared channel/i);
});
