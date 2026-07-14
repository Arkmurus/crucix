// R-F2601 — trade.gov CSL official source.

import test from 'node:test';
import assert from 'node:assert/strict';

import { briefing, parseWatchlist, searchCSL } from '../apis/sources/csl.mjs';

test('CSL watchlist parser accepts comma, newline, and JSON array input', () => {
  assert.deepEqual(parseWatchlist('ACME,  Baykar\nRosoboronexport;ACME'), ['ACME', 'Baykar', 'Rosoboronexport']);
  assert.deepEqual(parseWatchlist('["Entity A","Entity B"]'), ['Entity A', 'Entity B']);
});

test('CSL briefing is honest-disabled when API key is missing', async () => {
  const key = process.env.TRADE_GOV_API_KEY;
  const alt = process.env.CSL_API_KEY;
  const wl = process.env.CSL_WATCHLIST;
  const originalFetch = global.fetch;
  delete process.env.TRADE_GOV_API_KEY;
  delete process.env.CSL_API_KEY;
  process.env.CSL_WATCHLIST = 'ACME';
  let fetched = false;
  global.fetch = async () => {
    fetched = true;
    throw new Error('should not fetch without key');
  };

  try {
    const out = await briefing();
    assert.equal(out.status, 'disabled_no_key');
    assert.deepEqual(out._subStatus, { ok: 0, total: 1, failed: ['TRADE_GOV_API_KEY'] });
    assert.equal(fetched, false);
  } finally {
    if (key == null) delete process.env.TRADE_GOV_API_KEY;
    else process.env.TRADE_GOV_API_KEY = key;
    if (alt == null) delete process.env.CSL_API_KEY;
    else process.env.CSL_API_KEY = alt;
    if (wl == null) delete process.env.CSL_WATCHLIST;
    else process.env.CSL_WATCHLIST = wl;
    global.fetch = originalFetch;
  }
});

test('CSL briefing is honest-disabled when no watchlist is configured', async () => {
  const key = process.env.TRADE_GOV_API_KEY;
  const wl = process.env.CSL_WATCHLIST;
  const ariaWl = process.env.ARIA_CSL_WATCHLIST;
  const originalFetch = global.fetch;
  process.env.TRADE_GOV_API_KEY = 'test-key';
  delete process.env.CSL_WATCHLIST;
  delete process.env.ARIA_CSL_WATCHLIST;
  let fetched = false;
  global.fetch = async () => {
    fetched = true;
    throw new Error('should not fetch without watchlist');
  };

  try {
    const out = await briefing();
    assert.equal(out.status, 'disabled_no_watchlist');
    assert.deepEqual(out._subStatus, { ok: 0, total: 1, failed: ['CSL_WATCHLIST'] });
    assert.equal(fetched, false);
  } finally {
    if (key == null) delete process.env.TRADE_GOV_API_KEY;
    else process.env.TRADE_GOV_API_KEY = key;
    if (wl == null) delete process.env.CSL_WATCHLIST;
    else process.env.CSL_WATCHLIST = wl;
    if (ariaWl == null) delete process.env.ARIA_CSL_WATCHLIST;
    else process.env.ARIA_CSL_WATCHLIST = ariaWl;
    global.fetch = originalFetch;
  }
});

test('CSL search calls official endpoint and normalizes result rows', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    return new Response(JSON.stringify({
      total: 1,
      results: [{
        id: 'csl-1',
        name: 'ACME Defence LLC',
        source: 'BIS Entity List',
        country: 'AE',
        source_list_url: 'https://www.bis.gov/entity-list',
      }],
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };

  try {
    const out = await searchCSL('ACME', 'test-key');
    assert.equal(out.status, 'ok');
    assert.match(calls[0].url, /^https:\/\/api\.trade\.gov\/consolidated_screening_list\/search\?/);
    assert.match(calls[0].url, /api_key=test-key/);
    assert.match(calls[0].url, /q=ACME/);
    assert.equal(out.results[0].name, 'ACME Defence LLC');
    assert.equal(out.results[0].sourceList, 'BIS Entity List');
    assert.equal(out.results[0].url, 'https://www.bis.gov/entity-list');
  } finally {
    global.fetch = originalFetch;
  }
});

test('CSL briefing maps official hits into updates and recent hits', async () => {
  const key = process.env.TRADE_GOV_API_KEY;
  const wl = process.env.CSL_WATCHLIST;
  const originalFetch = global.fetch;
  process.env.TRADE_GOV_API_KEY = 'test-key';
  process.env.CSL_WATCHLIST = 'ACME';
  global.fetch = async () => new Response(JSON.stringify({
    results: [{
      id: 'csl-1',
      name: 'ACME Defence LLC',
      source: 'BIS Entity List',
      source_list_url: 'https://www.bis.gov/entity-list',
    }],
  }), { status: 200, headers: { 'content-type': 'application/json' } });

  try {
    const out = await briefing();
    assert.equal(out.status, 'ok');
    assert.equal(out.recent.length, 1);
    assert.match(out.updates[0].title, /\[CSL\] ACME Defence LLC/);
    assert.equal(out.signals[0].priority, 'high');
  } finally {
    if (key == null) delete process.env.TRADE_GOV_API_KEY;
    else process.env.TRADE_GOV_API_KEY = key;
    if (wl == null) delete process.env.CSL_WATCHLIST;
    else process.env.CSL_WATCHLIST = wl;
    global.fetch = originalFetch;
  }
});
