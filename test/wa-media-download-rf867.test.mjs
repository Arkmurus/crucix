// test/wa-media-download-rf867.test.mjs
//
// R-F867 — Baileys media download. The listener called
// sock.downloadMediaMessage(msg), but downloadMediaMessage is a STANDALONE
// export of @whiskeysockets/baileys, not a socket method — so it threw
// "sock.downloadMediaMessage is not a function" on EVERY document/image, and
// no upload ever downloaded (root cause of the 7 failed contract attempts,
// finally surfaced by R-F856's error message).
//
// Run: node test/wa-media-download-rf867.test.mjs

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

console.log('R-F867 — WhatsApp media download uses the standalone fn\n');

check('imports downloadMediaMessage from baileys',
  /downloadMediaMessage,?\s*[\s\S]{0,80}from '@whiskeysockets\/baileys'/.test(SRC)
  || /downloadMediaMessage/.test(SRC.split("from '@whiskeysockets/baileys'")[0] || ''));
check('NO remaining sock.downloadMediaMessage( call (the bug)',
  !/sock\.downloadMediaMessage\(/.test(SRC));
check('calls the standalone downloadMediaMessage(msg, "buffer", …)',
  /await downloadMediaMessage\(msg, 'buffer'/.test(SRC));
check('passes reuploadRequest for media re-fetch resilience',
  /reuploadRequest:\s*sock\.updateMediaMessage/.test(SRC));
// Both the image AND document paths must be fixed (2 call sites).
check('both call sites converted (image + document)',
  (SRC.match(/await downloadMediaMessage\(msg, 'buffer'/g) || []).length >= 2);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
