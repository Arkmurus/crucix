// R-F3655 / R-F3656 — every brain call from the Node tier must carry auth.
//
// Found by a 15-cycle live log sweep (2026-08-03), not by a test: the brain logged
// `GET /api/aria/curiosity HTTP/1.1" 401 Unauthorized` from an internal fdaa: 6PN
// address, repeatedly. Direct probe confirmed /api/aria/curiosity and
// /api/aria/identity both 401 without a Bearer token.
//
// Why no existing test caught it: both call sites wrap the fetch in try/catch and
// degrade — explorerScheduler opens a circuit breaker and logs "Brain circuit open",
// telegramCommands falls back to the local LLM. Neither surfaces "401". The failure
// presented as an unreachable brain / a quiet brain, never as an auth bug (§22).
//
// These are source-contract tests: driving them for real needs a live brain plus a
// valid token, which the offline suite (net_guard) deliberately forbids.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const EXPLORER = read('../lib/self/explorerScheduler.mjs');
const TELEGRAM = read('../lib/telegram/telegramCommands.mjs');

// A raw `fetch(`${BRAIN_URL}...`)` outside the helper is the bug being pinned.
function rawBrainFetches(src) {
  // NB: the trailing [^\n]* matters — without it the match stops at ${BRAIN_URL}
  // and the helper-exclusion below can never see the rest of the line.
  return (src.match(/^(?![ \t]*(?:\/\/|\*))[^\n]*\bfetch\(`\$\{BRAIN_URL\}[^\n]*/gm) || [])
    // the helper itself is the one legitimate site
    .filter(l => !/return fetch\(`\$\{BRAIN_URL\}\$\{path\}`/.test(l));
}

for (const [name, src] of [['explorerScheduler', EXPLORER], ['telegramCommands', TELEGRAM]]) {
  describe(`${name} — brain calls are authenticated`, () => {
    it('defines a BRAIN_TOKEN from the standard chain', () => {
      assert.match(src, /const BRAIN_TOKEN\s*=/,
        'module must resolve a brain token (mirror lib/self/learning_store.mjs:420)');
      assert.match(src, /ARIA_API_TOKEN/);
      assert.match(src, /ARIA_INTERNAL_TOKEN/);
    });

    it('routes brain calls through a helper that attaches the Bearer header', () => {
      assert.match(src, /function brainFetch\(/, 'brainFetch helper missing');
      const helper = src.slice(src.indexOf('function brainFetch('));
      assert.match(helper.slice(0, 400), /Authorization.*Bearer \$\{BRAIN_TOKEN\}/,
        'brainFetch must attach the Bearer header');
    });

    it('has no unauthenticated raw fetch left against BRAIN_URL', () => {
      const raw = rawBrainFetches(src);
      assert.equal(raw.length, 0,
        `found ${raw.length} raw BRAIN_URL fetch(es) that bypass brainFetch:\n` +
        raw.map(l => '  ' + l.trim()).join('\n'));
    });

    it('preserves caller-supplied headers when adding auth', () => {
      // The POST call sites set Content-Type; losing it would break the brain's
      // JSON parsing — i.e. a fix that swapped one silent failure for another.
      const helper = src.slice(src.indexOf('function brainFetch('), src.indexOf('function brainFetch(') + 400);
      assert.match(helper, /\.\.\.\(opts\.headers \|\| \{\}\)/,
        'brainFetch must spread caller headers, not replace them');
    });
  });
}

describe('R-F3655 — the curiosity loop actually consumes the fetched threads', () => {
  it('still filters unresolved threads and returns early when empty', () => {
    assert.match(EXPLORER, /threads\s*=\s*\(data\.open_threads \|\| \[\]\)\.filter/);
    assert.match(EXPLORER, /No open curiosity threads/);
  });
});
