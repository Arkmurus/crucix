// R-F2465 — automatic Telegram exploration pushes must be top-content only:
// high-signal, concrete, sourced, and not repeated within the dedup window.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs, { readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const TMP = path.join(os.tmpdir(), 'explorer-telegram-rf2465.json');
process.env.EXPLORER_TELEGRAM_STATE_PATH = TMP;

const {
  formatExplorerFindingsForTelegram,
  formatExplorerFindingsForTelegramIfTop,
  recordExplorerTelegramPost,
  selectTopExplorerFindings,
} = await import('../lib/self/web_explorer.mjs');

const topFinding = {
  runAt: '2026-07-08T06:00:00Z',
  queriesRun: 48,
  resultsFound: 120,
  insights: [{
    title: 'Poland opens short-range air defence procurement window',
    summary: 'The defence ministry published a procurement notice for NATO-interoperable short-range air defence systems.',
    relevance: 'HIGH',
    region: 'Poland',
    sourceUrl: 'https://example.com/poland-air-defence',
    timeline: 'Q3 2026',
  }],
  salesIdeas: [{
    title: 'Partner with a C-UAS OEM for Poland border units',
    market: 'Poland',
    buyer: 'Ministry of National Defence',
    productCategory: 'C-UAS sensors',
    nextStep: 'Contact the procurement office with a compliant OEM capability brief before the Q3 deadline.',
    urgency: 'HIGH',
  }],
};

test('automatic exploration post rejects thin or unsourced findings', () => {
  fs.rmSync(TMP, { force: true });
  const thin = {
    insights: [{ title: 'Generic defence news', summary: 'Something may happen.', relevance: 'LOW' }],
    salesIdeas: [{ title: 'Maybe sell something', nextStep: 'Monitor.' }],
  };
  const out = formatExplorerFindingsForTelegramIfTop(thin);
  assert.equal(out.shouldSend, false);
  assert.match(out.reason, /no new high-signal/);
});

test('automatic exploration post sends only concrete high-signal findings', () => {
  fs.rmSync(TMP, { force: true });
  const selected = selectTopExplorerFindings(topFinding);
  assert.equal(selected.insights.length, 1);
  assert.equal(selected.salesIdeas.length, 1);
  const out = formatExplorerFindingsForTelegramIfTop(topFinding);
  assert.equal(out.shouldSend, true);
  assert.match(out.text, /Poland opens short-range air defence/);
  assert.match(out.text, /Partner with a C-UAS OEM/);
  assert.ok(out.keys.length >= 2);
});

test('automatic exploration post suppresses repeated findings after recording', () => {
  fs.rmSync(TMP, { force: true });
  const first = formatExplorerFindingsForTelegramIfTop(topFinding);
  assert.equal(first.shouldSend, true);
  recordExplorerTelegramPost(first.keys);
  const second = formatExplorerFindingsForTelegramIfTop(topFinding);
  assert.equal(second.shouldSend, false);
});

test('manual exploration formatter keeps on-demand output available with honest cadence text', () => {
  const text = formatExplorerFindingsForTelegram(topFinding);
  assert.match(text, /Automatic pushes only when new high-signal findings appear/);
  assert.doesNotMatch(text, /Auto-runs Sundays 04:00 UTC/);
});

test('scheduled Telegram explorer path is blocked when Golden-only mode is active', () => {
  const src = readFileSync(path.join(__dirname, '..', 'server.mjs'), 'utf8');
  const start = src.indexOf('formatExplorerFindingsForTelegramIfTop(findings)');
  assert.ok(start > -1, 'scheduled Telegram quality gate must be wired');
  const block = src.slice(start - 180, start + 620);
  assert.ok(block.includes('!TELEGRAM_GOLDEN_INTEL_ONLY'));
  assert.ok(block.includes('const sent = await telegramAlerter.sendMessage(post.text)'));
  assert.ok(block.includes('if (sent?.ok !== false)'));
  assert.ok(block.indexOf('if (sent?.ok !== false)') < block.indexOf('recordExplorerTelegramPost(post.keys)'));
});
