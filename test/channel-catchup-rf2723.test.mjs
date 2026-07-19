// R-F2723 (§25, #10) — persisted slot-ledger + startup missed-slot catch-up.
// The 07:00/17:00 cron loses a slot if the process is down at that minute. A
// persisted per-London-day ledger + a boot-time catch-up run any due-but-unrun
// slot once (idempotent via content-dedup).

import { after, before, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-catchup-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
process.env.CHANNEL_OUTCOME_LEDGER_PATH = path.join(tempDir, 'outcomes.json');
const LEDGER = path.join(tempDir, 'slot.json');
process.env.CHANNEL_SLOT_LEDGER_PATH = LEDGER;

const { handleMorningSignalCron, runStartupCatchUp } =
  await import('../lib/telegram/channelServerHooks.mjs');

// Mirror the module's London-day/hour computation so tests are clock-consistent.
function londonNow() {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hour12: false,
  }).formatToParts(new Date());
  const get = t => parts.find(p => p.type === t)?.value;
  return { dateKey: `${get('year')}-${get('month')}-${get('day')}`, hour: Number(get('hour')) };
}

const bot = { botToken: 't', chatId: '1', channelId: '1' };
const originalFetch = global.fetch;
before(() => {
  global.fetch = async (url) => {
    if (String(url).includes('/getChat')) {
      return new Response(JSON.stringify({ ok: true, result: { id: -1001, type: 'channel', title: 'ARIA Intelligence' } }), { status: 200 });
    }
    if (String(url).includes('/api/aria/intel/signals/recent')) {
      return new Response(JSON.stringify({ ok: true, signals: [], freshness: { stale: false, stale_reasons: [] } }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
});
after(() => { global.fetch = originalFetch; fs.rmSync(tempDir, { recursive: true, force: true }); });
beforeEach(() => { try { fs.unlinkSync(LEDGER); } catch {} });

describe('R-F2723 slot ledger', () => {
  it('a slot ATTEMPT is persisted for the current London day', async () => {
    await handleMorningSignalCron({}, bot, { hour: 7 });
    const led = JSON.parse(fs.readFileSync(LEDGER, 'utf8'));
    const { dateKey } = londonNow();
    assert.ok(Array.isArray(led[dateKey]) && led[dateKey].includes(7), 'slot 7 must be recorded for today');
  });
});

describe('R-F2723 startup catch-up', () => {
  it('does NOT re-run a slot already recorded today (idempotent)', async () => {
    const { dateKey } = londonNow();
    fs.writeFileSync(LEDGER, JSON.stringify({ [dateKey]: [7, 17] }));
    const r = await runStartupCatchUp({}, bot);
    assert.deepEqual(r.recovered, [], 'both slots already ran — nothing to recover');
  });

  it('recovers exactly the DUE-but-unrun slots for an empty ledger', async () => {
    const { hour } = londonNow();
    const due = [7, 17].filter(s => hour >= s); // what the code will consider due now
    const r = await runStartupCatchUp({}, bot);
    assert.deepEqual(r.recovered, due, `should recover due slots ${JSON.stringify(due)} at London hour ${hour}`);
    // and after recovery those slots are now marked (a second catch-up is a no-op)
    const r2 = await runStartupCatchUp({}, bot);
    assert.deepEqual(r2.recovered, [], 'a second catch-up recovers nothing');
  });

  it('is a no-op when the channel is not configured', async () => {
    const r = await runStartupCatchUp({}, { botToken: '', channelId: '' });
    assert.equal(r.skipped, 'not_configured');
  });
});
