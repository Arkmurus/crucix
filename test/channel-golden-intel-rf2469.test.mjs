// R-F2469 — Telegram channel must publish only fresh, decision-grade Golden Intel.

import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-channel-golden-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
process.env.CHANNEL_EDITORIAL_STATE_PATH = path.join(tempDir, 'editorial.json');

const {
  formatGoldenIntelChannelPost,
  handleCaseFileCron,
  handleMorningSignalCron,
  runChannelSweep,
  selectTelegramGoldenIntel,
} = await import('../lib/telegram/channelServerHooks.mjs');
const { dedupKey, recordPosted } = await import('../lib/telegram/postDedup.mjs');

function goodSignal(overrides = {}) {
  return {
    id: 'sig-1',
    signal_type: 'active_tender',
    priority: 'HIGH',
    confidence: 'HIGH',
    quality_label: 'decision-grade single-source',
    source_tier: 'tier_1b',
    score: 88,
    decision_summary: 'Angola launches armoured vehicle tender',
    why_it_matters: 'Procurement activity may create a near-term commercial window.',
    recommended_action: 'Qualify opportunity',
    target: 'Angola',
    source: 'US DoD Daily Contracts',
    url: 'https://example.com/angola-tender',
    detected_at: '2026-07-07T10:00:00Z',
    customer_value: {
      score: 88,
      segments: ['procurement_team'],
      problems: ['bid_opportunity'],
      aria_added: ['procurement_implication'],
      rejection_reasons: [],
      telegram_ready: true,
    },
    ...overrides,
  };
}

describe('Telegram Golden Intel gate', () => {
  const originalFetch = global.fetch;

  before(() => {
    fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
    fs.writeFileSync(process.env.CHANNEL_EDITORIAL_STATE_PATH, '{}');
  });

  after(() => {
    global.fetch = originalFetch;
    delete process.env.CHANNEL_POST_DEDUP_PATH;
    delete process.env.CHANNEL_EDITORIAL_STATE_PATH;
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it('rejects stale or backfilled Golden Intel before Telegram can post it', () => {
    const fresh = {
      ok: true,
      freshness: { stale: false, backfilled: false },
      signals: [goodSignal()],
    };
    assert.equal(selectTelegramGoldenIntel(fresh)?.id, 'sig-1');

    assert.equal(selectTelegramGoldenIntel({ ...fresh, freshness: { stale: true } }), null);
    assert.equal(selectTelegramGoldenIntel({ ...fresh, freshness: { stale: false, backfilled: true } }), null);
    assert.equal(selectTelegramGoldenIntel({ ...fresh, signals: [goodSignal({ _backfilled: true })] }), null);
  });

  it('rejects weak public-channel signals even when the feed is fresh', () => {
    const base = { ok: true, freshness: { stale: false, backfilled: false } };
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ quality_label: 'watch-grade' })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ priority: 'MEDIUM' })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ source_tier: 'tier_4' })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ recommended_action: '' })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ url: '' })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ customer_value: { score: 79, rejection_reasons: ['customer_value_below_telegram_threshold'] } })] }), null);
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [goodSignal({ customer_value: { score: 90, rejection_reasons: ['generic_action'] } })] }), null);
  });

  it('rejects already-posted Golden Intel even when re-ingested with a new id', () => {
    fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
    recordPosted(dedupKey(goodSignal()));
    const duplicate = goodSignal({
      id: 'sig-new-ingestion-id',
      url: 'https://example.com/angola-tender?utm_source=newsletter',
    });
    assert.equal(selectTelegramGoldenIntel({
      ok: true,
      freshness: { stale: false, backfilled: false },
      signals: [duplicate],
    }), null);
  });

  it('formats the Telegram post around decision, impact, action, and evidence', () => {
    const text = formatGoldenIntelChannelPost(goodSignal(), { newest_signal_at: '2026-07-07T10:00:00Z' });
    assert.match(text, /GOLDEN INTEL/);
    assert.match(text, /Angola launches armoured vehicle tender/);
    assert.match(text, /Why it matters:/);
    assert.match(text, /Action: \*Qualify opportunity\*/);
    assert.match(text, /Evidence: US DoD Daily Contracts/);
  });

  it('R-F2602: blocks / neutralizes evidence-URL Markdown link injection', () => {
    const base = { ok: true, freshness: { stale: false, backfilled: false } };
    // Realistic attack: a crafted evidence.url smuggling a Telegram Markdown link.
    // It is not a valid http(s) URL, so the channel gate drops the whole signal —
    // the injected hyperlink can never reach the public @ARIAIntelligence channel.
    const injected = goodSignal({ url: '[Emergency refund click here](https://phishing.example)' });
    assert.equal(selectTelegramGoldenIntel({ ...base, signals: [injected] }), null);
    // A URL with a scheme but smuggled Markdown metacharacters passes the gate but
    // the formatter _md()-escapes it, so no attacker-labelled link ever renders.
    const smuggled = goodSignal({ url: 'https://evil.example/x[label](https://phishing.example)' });
    const text = formatGoldenIntelChannelPost(smuggled, {});
    assert.ok(!text.includes('[label]('), 'raw Markdown link syntax must not survive into the post');
    assert.ok(text.includes('\\[label\\]'), 'evidence-URL brackets must be backslash-escaped');
  });

  it('morning cron posts fresh Golden Intel and no fallback content', async () => {
    fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
    const calls = [];
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
      if (String(url).includes('/api/aria/intel/signals/recent')) {
        return new Response(JSON.stringify({
          signals: [goodSignal()],
          freshness: { stale: false, backfilled: false },
        }), { status: 200 });
      }
      const contentType = String(opts.headers?.['Content-Type'] || opts.headers?.['content-type'] || '');
      if (String(url).includes('/sendPhoto') && contentType.includes('multipart/form-data')) {
        return new Response(JSON.stringify({ ok: true, result: { photo: [{ file_id: 'golden-card-id' }] } }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
    };

    await handleMorningSignalCron({}, { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' });

    const telegramMessages = calls.filter(c => c.url.includes('/sendMessage'));
    assert.equal(telegramMessages.length, 1);
    assert.match(String(telegramMessages[0].opts.body), /GOLDEN INTEL/);
    assert.doesNotMatch(String(telegramMessages[0].opts.body), /Hidden in the supply chain/);
  });

  it('morning cron sends nothing when Golden Intel is missing or stale', async () => {
    fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
    const calls = [];
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
      if (String(url).includes('/api/aria/intel/signals/recent')) {
        return new Response(JSON.stringify({
          signals: [goodSignal()],
          freshness: { stale: true, stale_reasons: ['poll_stale'] },
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
    };

    const result = await handleMorningSignalCron({}, { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' });
    const telegramMessages = calls.filter(c => c.url.includes('/sendMessage') || c.url.includes('/sendPhoto'));
    assert.equal(result?.skipped, true);
    assert.equal(telegramMessages.length, 0);
  });

  it('morning cron refuses to use private chat ID as public channel fallback', async () => {
    const calls = [];
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
      return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
    };

    const result = await handleMorningSignalCron({}, { botToken: 'test-token', chatId: 'private-ops-chat' });
    const telegramMessages = calls.filter(c => c.url.includes('api.telegram.org'));

    assert.equal(result?.reason, 'missing_channel_id');
    assert.equal(telegramMessages.length, 0);
  });

  it('static scheduled case files are blocked by the Golden-only rule', async () => {
    const calls = [];
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
      return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
    };

    const result = await handleCaseFileCron({ botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' });

    const telegramMessages = calls.filter(c => c.url.includes('/sendMessage'));
    assert.equal(result?.reason, 'golden_intel_only');
    assert.equal(telegramMessages.length, 0);
  });

  it('sweep-triggered channel publishing is blocked by the Golden-only rule', async () => {
    const result = await runChannelSweep({
      correlations: [{ severity: 'critical', title: 'Critical non-golden item' }],
      opensanctions: { recent: [{ name: 'Example Entity', datasets: ['x', 'y'] }] },
    }, { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' });
    assert.deepEqual(result, { posted: 0, errors: 0, skipped: true, reason: 'golden_intel_only' });
  });
});
