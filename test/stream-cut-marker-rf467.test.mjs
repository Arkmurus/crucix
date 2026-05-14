// test/stream-cut-marker-rf467.test.mjs
//
// Capability test for R-F467 — visible mid-stream cut marker.
//
// Web/UI audit P0 (next_session_pickup_2026_05_15): mid-stream errors
// after the first chunk were silently dropped on BOTH the web /chat/stream
// path (public/aria.html) AND the WhatsApp listener side (waListener.mjs).
// Pre-R-F467:
//   - aria.html had `if (!fullText) fullText = '⚠️ ' + err`. The guard
//     skipped the error message entirely whenever ANY chunk had landed.
//   - waListener.mjs logged streamError but sent fullText to WhatsApp as
//     if it were complete — user saw mid-sentence cutoff with no signal.
//
// Both paths now append `[!stream cut: <reason>]` so the user can tell
// truncation from complete.
//
// Run: node test/stream-cut-marker-rf467.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ARIA_HTML = readFileSync(
  join(__dirname, '..', 'public', 'aria.html'),
  'utf8'
);
const WA_LISTENER = readFileSync(
  join(__dirname, '..', 'lib', 'whatsapp', 'waListener.mjs'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F467 capability tests\n');

// ── 1. Web /chat/stream — aria.html appends cut marker on every error ─
console.log('1. aria.html: cut marker on every error event');
{
  const eIdx = ARIA_HTML.indexOf("evt.type === 'error'");
  check('error branch present', eIdx > -1);
  const block = ARIA_HTML.slice(eIdx, eIdx + 800);
  check('appends [!stream cut: ...] marker',
        block.includes('[!stream cut:'));
  // The OLD guard `if (!fullText) fullText = ...` would skip the error
  // on partial content. Make sure the new code path runs unconditionally.
  check('no guard skips the marker when fullText already has content',
        !/if\s*\(\s*!fullText\s*\)\s*fullText\s*=\s*['"]⚠️/.test(block));
}

// ── 2. WhatsApp side — waListener.mjs appends marker on mid-stream error
console.log('\n2. waListener.mjs: cut marker on mid-stream error');
{
  // Find the R-F467 patch block — anchor on the new marker
  check('R-F467 marker referenced',  WA_LISTENER.includes('R-F467'));
  check('appends [!stream cut: ...]', WA_LISTENER.includes('[!stream cut:'));
  // The original no-fullText path must still throw for fallback
  check('preserves no-fullText fallback throw',
        /if\s*\(\s*!fullText\s*\)[\s\S]+?throw new Error\(streamError\)/.test(WA_LISTENER));
}

// ── 3. Marker copy is operator-visible (emoji + bracket-delim) ───────
console.log('\n3. marker text discipline');
{
  // The shape `⚠️ [!stream cut: <reason>]` makes it greppable in audit
  // logs and recognisable in the chat history. Sanity-check both files.
  check('aria.html marker uses ⚠️ + bracket',
        /⚠️[\s\S]{0,20}\[!stream cut:/.test(ARIA_HTML));
  check('waListener.mjs marker uses ⚠️ + bracket',
        /⚠️[\s\S]{0,20}\[!stream cut:/.test(WA_LISTENER));
}

console.log(`\nR-F467 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
