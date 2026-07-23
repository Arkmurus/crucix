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
// R-F2908 — window widened: the helper now carries a long rationale comment before
// the call, so a 900-char slice no longer reaches the code it asserts on.
const helper = SRC.slice(helperStart, helperStart + 2600);

// R-F2908 — the brief no longer hand-rolls the fetch or the gate. It calls the SAME
// helpers the channel uses (channelHooks.fetchGoldenIntelSignals ->
// selectPublishableGoldenIntel), which hit exactly this endpoint, carry the ARIA auth
// headers and bound the timeout internally. The original assertions pinned the
// literal URL/auth/shape INSIDE this helper; that wording is superseded, but the
// contract it protected — the brief sources Golden Intel from the Python signals
// endpoint and never fabricates it — is preserved and strengthened, because the gate
// is now shared rather than duplicated (the duplicate had drifted behind R-F2896 and
// R-F2899). See test/brief-golden-gate-rf2908.test.mjs for the behavioural coverage.
assert.ok(
  helper.includes('channelHooks.fetchGoldenIntelSignals'),
  'Telegram brief must fetch Python Golden Intel signals via the shared channel fetcher',
);
assert.ok(
  SRC.includes("`${base}/api/aria/intel/signals/recent?${q}`") === false,
  'sanity: the URL lives in channelServerHooks, not server.mjs',
);
assert.ok(helper.includes('timeoutMs: 8000'), 'Golden Intel fetch must be latency bounded');
assert.ok(
  helper.includes("selectPublishableGoldenIntel"),
  'Golden Intel must pass the same publishable gate as the channel',
);

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
