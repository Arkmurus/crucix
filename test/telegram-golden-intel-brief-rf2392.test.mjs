// R-F2392 — Telegram /brief consumes the same Python Golden Intel feed as web.
//
// Importing server.mjs boots Express and background jobs, so this capability
// guard reads the active monkey-patched /brief implementation and verifies the
// source-to-output contract stays wired.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');

const helperStart = SRC.indexOf('async function fetchGoldenIntelForBrief');
assert.ok(helperStart > -1, 'server must define fetchGoldenIntelForBrief');
const helper = SRC.slice(helperStart, helperStart + 900);

assert.ok(
  helper.includes('/api/aria/intel/signals/recent?limit='),
  'Telegram brief must fetch Python Golden Intel signals',
);
assert.ok(helper.includes('headers: _ariaHeaders()'), 'Golden Intel fetch must use ARIA auth headers');
assert.ok(helper.includes('AbortSignal.timeout(8000)'), 'Golden Intel fetch must be latency bounded');
assert.ok(helper.includes('Array.isArray(data.signals)'), 'Golden Intel fetch must verify response shape');

const briefStart = SRC.indexOf('telegramAlerter._handleBrief = async function');
assert.ok(briefStart > -1, 'server must patch the active Telegram /brief handler');
const brief = SRC.slice(briefStart, briefStart + 7000);

for (const marker of [
  'fetchGoldenIntelForBrief(5)',
  'GOLDEN INTEL',
  'quality_label',
  'action_horizon',
  'corroboration',
  'recommended_action',
]) {
  assert.ok(brief.includes(marker), `Telegram brief must include ${marker}`);
}

assert.ok(
  brief.indexOf('GOLDEN INTEL') < brief.indexOf('*2. EXECUTIVE THESIS*'),
  'Golden Intel decision signals must appear before the broad executive thesis',
);
