// test/wa-outbound-send-rf1288.test.mjs
//
// R-F1288 — the canonical aria-wa app was MISSING the outbound
// POST /api/wa-listener/send route. The brain's proactive/autonomous delivery
// (wa_notifier.py / delivery.py) POSTs {group_id, message} to
// aria-wa.internal:5070/api/wa-listener/send — which 404'd, because the route
// only existed on the legacy aria-web listener. This adds it to the isolated
// app, auth-gated, sending via the live Baileys socket, with §21b brain wiring
// on both outbound success and failure.
//
// Run: node test/wa-outbound-send-rf1288.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'), 'utf8');

let failures = 0;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) failures++; };

console.log('R-F1288 — WA: outbound /send route present + wired\n');

const idx = SRC.indexOf("app.post('/api/wa-listener/send'");
check('POST /api/wa-listener/send route exists', idx !== -1);

// scope to the route body (up to app.listen)
const body = idx !== -1 ? SRC.slice(idx, SRC.indexOf('app.listen(', idx)) : '';

check('route is auth-gated with requireAuth',
  /app\.post\('\/api\/wa-listener\/send',\s*requireAuth/.test(SRC));
check('accepts the brain payload field group_id', /group_id/.test(body));
check('accepts the brain payload field message', /\.message/.test(body));
check('sends to the target via sock.sendMessage', /sock\.sendMessage\(\s*target/.test(body));
check('returns 503 when WhatsApp is not connected',
  /isConnected/.test(body) && /503/.test(body));
check('400 when target/message missing', /400/.test(body));
check('§21b: wires outbound SUCCESS to brain', /wa_outbound_sent/.test(body));
check('§21b: wires outbound FAILURE to brain', /wa_outbound_failed/.test(body));
check('failure signal also fires on not-connected drop',
  (body.match(/wa_outbound_failed/g) || []).length >= 2);

console.log('');
if (failures) { console.error(`R-F1288: ${failures} check(s) FAILED`); process.exit(1); }
console.log('R-F1288: all checks passed');
