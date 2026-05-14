// R-F525 — DD + URL prompts must route to long-research budget (600s)
// so seenode doesn't abort fly's DD orchestration mid-flight.
//
// Live failure 2026-05-14 14:07-14:30 BST:
//   User: "Aria, do a full DD on https://adsm-sa.com/"
//   ARIA: ⚠ "fly.io ARIA is currently unreachable" (R-F160 fallback)
// Root cause: _LONG_RESEARCH_RE matched "due diligence" spelled out
// but NOT the abbreviation "DD" or URL-bearing requests. So WA listener
// gave the request 240s default budget instead of the long 360s budget.
// Even 360s wasn't enough for the 5-10 min DD wall time on fly side.
//
// R-F525 fix: regex now matches `\bdd\b` and `https?://`, long budget
// bumped to 600s, server.mjs proxy timeouts bumped to 600s.

import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const waListenerSrc = readFileSync(
  new URL('../lib/whatsapp/waListener.mjs', import.meta.url),
  'utf8',
);
const serverSrc = readFileSync(
  new URL('../server.mjs', import.meta.url),
  'utf8',
);

// Extract _LONG_RESEARCH_RE source text so we can re-instantiate it
// without dynamic-importing the whole module (which boots Baileys etc).
function extractLongResearchRegex() {
  const m = waListenerSrc.match(/const\s+_LONG_RESEARCH_RE\s*=\s*(\/.+?\/[gimsuy]*);/);
  assert.ok(m, '_LONG_RESEARCH_RE not found in waListener.mjs');
  return new Function('return ' + m[1])();
}

test('R-F525: "DD" abbreviation triggers long-research routing', () => {
  const re = extractLongResearchRegex();
  assert.ok(re.test('Aria, do a full DD on adsm-sa.com'),
    'Plain "DD" abbreviation should match — this is the WA-typical shape');
  assert.ok(re.test('run a DD on this company'),
    '"a DD on" form should match');
});

test('R-F525: URL-bearing messages trigger long-research routing', () => {
  const re = extractLongResearchRegex();
  assert.ok(re.test('Aria, do a full DD on https://adsm-sa.com/'),
    'The EXACT 14:07 BST live failure shape must match (URL → long budget)');
  assert.ok(re.test('check http://example.com'),
    'http:// also matches (not just https://)');
});

test('R-F525: pre-existing "due diligence" matches still work', () => {
  const re = extractLongResearchRegex();
  assert.ok(re.test('please run due diligence on Acme Corp'));
  assert.ok(re.test('investigate this company'));
  assert.ok(re.test('research the supplier'));
});

test('R-F525: short greetings still take fast path', () => {
  const re = extractLongResearchRegex();
  assert.ok(!re.test('hi aria'));
  assert.ok(!re.test('what time is it'));
  assert.ok(!re.test('thanks'));
});

test('R-F525: WA long budget defaults to 600s (env-tunable)', () => {
  // The waListener.mjs code path constructs `_longMs` from
  // WA_LONG_BUDGET_MS env or '600000' default. Source-string check
  // is cheaper than spinning up the module which depends on Baileys.
  assert.match(
    waListenerSrc,
    /WA_LONG_BUDGET_MS[^']+'600000'/,
    'WA long budget default must be 600000 (10 min)',
  );
});

test('R-F525: server.mjs /chat proxy timeout defaults to 600s (env-tunable)', () => {
  assert.match(
    serverSrc,
    /ARIA_CHAT_PROXY_TIMEOUT_MS[^']+'600000'/,
    'server.mjs /chat proxy must default to 600000ms',
  );
});

test('R-F525: server.mjs /dd/orchestrate proxy timeout defaults to 600s', () => {
  assert.match(
    serverSrc,
    /ARIA_DD_PROXY_TIMEOUT_MS[^']+'600000'/,
    'server.mjs /dd/orchestrate proxy must default to 600000ms',
  );
});

test('R-F525: server.mjs /chat/stream proxy timeout defaults to 600s', () => {
  assert.match(
    serverSrc,
    /ARIA_STREAM_PROXY_TIMEOUT_MS[^']+'600000'/,
    'server.mjs /chat/stream proxy must default to 600000ms',
  );
});
