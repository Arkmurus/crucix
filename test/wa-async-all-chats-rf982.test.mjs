// test/wa-async-all-chats-rf982.test.mjs
//
// R-F982 (2026-05-28) — every WhatsApp chat goes through the async job+poll
// path. Pre-R-F982 only URL / attached-doc / >6k-char messages went async
// (_needsAsyncChat); a PLAIN question that triggered heavy tool-use still used
// the 90s sync cap and aborted → live 2026-05-28 6× "Chat failed: The operation
// was aborted due to timeout" → users saw "⚠️ ARIA is temporarily unavailable."
// on ordinary questions (one brain trace ran 143s). The async job has no
// server-side cap, so all-async means a slow answer is never an outage; fast
// chats stay snappy via fast-first polling + a deferred interim acknowledgement.
//
// Run: node test/wa-async-all-chats-rf982.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'), 'utf8');

let failures = 0;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) failures++; };

console.log('R-F982 — WA: all chats async, no sync timeout\n');

// askARIA must delegate straight to askARIAAsync (no conditional sync branch).
const askBody = SRC.slice(SRC.indexOf('async function askARIA('), SRC.indexOf('async function askARIAAsync('));
check('askARIA calls askARIAAsync', /return await askARIAAsync\(message, senderJid, chatId\)/.test(askBody));
check('askARIA no longer gates on _needsAsyncChat', !/_needsAsyncChat/.test(askBody));
check('askARIA no longer has a 90s sync brainPost chat call',
  !/await brainPost\('\/api\/aria\/chat', \{ message, session_id: sid \}\)/.test(askBody));
check('askARIA no longer returns the "temporarily unavailable" outage string',
  !/return '⚠️ ARIA is temporarily unavailable\.'/.test(askBody));

// The dead async-trigger helpers must be gone.
check('_needsAsyncChat helper removed', !/function _needsAsyncChat/.test(SRC));
check('URL_RE / HEAVY_CHAT_CHARS constants removed',
  !/const URL_RE =/.test(SRC) && !/const HEAVY_CHAT_CHARS =/.test(SRC));

// Fast-first polling + deferred interim in askARIAAsync.
check('fast-first polling constants present (FAST_MS / FAST_PHASE_MS)',
  /FAST_MS\s*=\s*1000/.test(SRC) && /FAST_PHASE_MS\s*=\s*30000/.test(SRC));
check('interim "working on it" is deferred (INTERIM_AFTER_MS gate + interimSent)',
  /INTERIM_AFTER_MS/.test(SRC) && /interimSent/.test(SRC));
check('10-minute total poll window retained (MAX_MS = 600000)', /MAX_MS\s*=\s*600000/.test(SRC));

// Failure still observable to the brain (R-F925 signal preserved).
check('a chat failure still emits signalChatFailure', /signalChatFailure\(message, senderJid/.test(SRC));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
