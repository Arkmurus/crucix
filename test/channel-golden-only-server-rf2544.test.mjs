// R-F2544 — public Telegram intel must be Golden Intel only.

import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const SRC = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

test('server defines Golden-only Telegram intel guard enabled by default', () => {
  assert.match(SRC, /const TELEGRAM_GOLDEN_INTEL_ONLY = !\['0', 'false', 'off', 'no'\]/);
  assert.match(SRC, /process\.env\.TELEGRAM_GOLDEN_INTEL_ONLY \?\? '1'/);
  assert.match(SRC, /function blockNonGoldenTelegramIntel/);
  assert.match(SRC, /reason: 'golden_intel_only'/);
});

test('manual public-channel posting endpoints are blocked by the Golden-only guard', () => {
  for (const marker of [
    "app.post('/api/admin/channel/post'",
    "app.post('/api/admin/channel/daily-brief'",
    "app.post('/api/admin/channel/media/post-with-image'",
    "app.post('/api/admin/channel/interactive/poll'",
    "app.post('/api/admin/channel/welcome'",
    "app.post('/api/admin/channel/post-template'",
  ]) {
    const start = SRC.indexOf(marker);
    assert.ok(start > -1, `${marker} route exists`);
    const block = SRC.slice(start, start + 420);
    assert.match(block, /TELEGRAM_GOLDEN_INTEL_ONLY/);
    assert.match(block, /blockNonGoldenTelegramIntel/);
  }
});

test('automatic non-Golden Telegram intel lanes are guarded', () => {
  const sweepStart = SRC.indexOf('telegramAlerter.onSweepComplete(currentData)');
  assert.ok(sweepStart > -1, 'sweep alert lane exists');
  assert.match(SRC.slice(sweepStart - 180, sweepStart + 180), /!TELEGRAM_GOLDEN_INTEL_ONLY/);

  const digestStart = SRC.indexOf('sendMorningDigest(telegramAlerter, currentData)');
  assert.ok(digestStart > -1, 'morning digest lane exists');
  assert.match(SRC.slice(digestStart - 260, digestStart + 160), /TELEGRAM_GOLDEN_INTEL_ONLY/);

  const explorerStart = SRC.indexOf('formatExplorerFindingsForTelegramIfTop(findings)');
  assert.ok(explorerStart > -1, 'explorer Telegram lane exists');
  assert.match(SRC.slice(explorerStart - 220, explorerStart + 160), /!TELEGRAM_GOLDEN_INTEL_ONLY/);
});
