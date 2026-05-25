// test/wa-byte-truncation-banner-rf862.test.mjs
//
// R-F862 — when a WhatsApp document upload exceeds the 8MB byte cap, the
// listener clips it to the first 8MB BEFORE extraction. Pre-R-F862 this was
// silent: ARIA reviewed a clipped contract with no idea the tail (annexes,
// payment schedules, signature) was missing, and could assert "X is not in
// the contract" about a truncated doc (the R-F849/GESPI failure class).
//
// R-F862 prepends a [!PARTIAL EXTRACTION] banner to the cached text + warns
// the sender. The listener is the container entrypoint (top-level startup) so
// it can't be imported in-test — static-source guards per the repo convention.
//
// Run: node test/wa-byte-truncation-banner-rf862.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.log(`  ✗ ${label}`); failures++; }
}

console.log('R-F862 — WhatsApp 8MB byte-truncation banner\n');

check('computes a bytesTruncated flag from the 8MB cap',
  /const bytesTruncated = buffer\.length > MAX_BYTES;/.test(SRC));
check('prepends a [!PARTIAL EXTRACTION] banner to the cached text when truncated',
  /if \(bytesTruncated && _cacheText\)\s*\{[\s\S]{0,200}\[!PARTIAL EXTRACTION/.test(SRC));
check('the banner forbids absence claims on a clipped doc (clause-12 honesty)',
  /Do NOT assert any clause, party or term is absent/.test(SRC));
check('warns the sender the read was partial (>8MB)',
  /is large \(>8MB\) — I read the first 8MB only/.test(SRC));
check('caches via _cacheRecentDoc with the (possibly bannered) text',
  /_cacheRecentDoc\(chatId, senderName, filename, _cacheText\);/.test(SRC));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
