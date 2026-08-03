// R-F3664 — WhatsApp interim/progress messages must not fabricate tool use.
//
// LIVE INCIDENT (2026-08-03):
//   Antonio: "Online means everything, how are you, are you ok, are you
//             breathing, what are you thinking or doing"
//   ARIA:    "📡 Running the numbers — checking multiple sources. Results
//             coming shortly."
//   Antonio: "You dont need to run the numbers for two way conversations"
//
// Root cause: the interim messages fire on a pure TIMER (INTERIM_AFTER_MS =
// 7000ms), not on intent, and the poller has no job-kind flag — it cannot know
// what the brain is doing. So an ordinary conversational turn that took longer
// than 7 seconds was answered with a claim to be consulting sources.
//
// The same sentence is already banned on the brain side:
// aria_service/intel/tool_claim_guard.py:108 — "R-F1437: 'Running the numbers' /
// 'checking multiple sources' — fabricated". R-F1437 fixed the brain's OUTPUT;
// these canned Node strings re-introduced the identical claim where no guard
// could see it.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const WA = readFileSync(
  fileURLToPath(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url)), 'utf8');

// Phrases that assert ARIA is using tools/sources. A timer cannot know this.
const FABRICATED_TOOL_CLAIMS = [
  'Running the numbers',
  'checking multiple sources',
  'cross-referencing several databases',
  'cross-referencing',
  'Digging into this',
  "I'm researching this now",
  'sources take time to verify',
  'this is a deep dive',
];

// Only look at real code lines — the fix's own comment quotes the banned
// strings to explain what went wrong, and that must not fail the test.
function codeLines(src) {
  return src.split('\n').filter(l => {
    const t = l.trim();
    return t && !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*') && !t.startsWith('\\');
  }).join('\n');
}

describe('R-F3664 — interim messages claim only what is known', () => {
  const code = codeLines(WA);

  for (const phrase of FABRICATED_TOOL_CLAIMS) {
    it(`does not ship the fabricated claim: "${phrase}"`, () => {
      assert.ok(!code.includes(phrase),
        `aria_wa_listener still sends "${phrase}" — this is a timer-triggered ` +
        `message and cannot know whether any tool ran (tool_claim_guard R-F1437)`);
    });
  }

  it('still sends SOME interim so the user is not left in silence', () => {
    assert.match(WA, /_interimMessages\s*=\s*\[/, 'interim messages removed entirely');
    const block = WA.slice(WA.indexOf('_interimMessages'), WA.indexOf('_interimMessages') + 700);
    const entries = (block.match(/'[^']{10,}'/g) || []);
    assert.ok(entries.length >= 2, 'expected at least two interim variants');
  });

  it('interim copy states only that work is in progress', () => {
    const i = WA.indexOf('_interimMessages');
    const block = WA.slice(i, i + 700).toLowerCase();
    assert.ok(/still (with you|working)|one moment/.test(block),
      'interim copy should say work is ongoing, and nothing more');
  });

  it('progress messages do not claim depth or source verification', () => {
    const i = WA.indexOf('_progressMessages');
    assert.ok(i !== -1, 'progress messages removed entirely');
    const block = WA.slice(i, i + 700);
    assert.ok(!/deep dive|sources take time|verify/i.test(block),
      'progress copy still asserts what the brain is doing');
    assert.match(block, /Still (working|on it)/,
      'progress copy should report elapsed work only');
  });
});
