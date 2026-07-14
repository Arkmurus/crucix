// R-F2600 — SAM.gov official opportunities source.

import test from 'node:test';
import assert from 'node:assert/strict';

import { fetchSAMGovOpportunities } from '../apis/sources/procurement_tenders.mjs';

test('SAM.gov source is disabled honestly when no API key is configured', async () => {
  const originalKey = process.env.SAM_GOV_API_KEY;
  const originalAlt = process.env.SAM_API_KEY;
  const originalFetch = global.fetch;
  delete process.env.SAM_GOV_API_KEY;
  delete process.env.SAM_API_KEY;
  let fetched = false;
  global.fetch = async () => {
    fetched = true;
    throw new Error('should not fetch without key');
  };

  try {
    const out = await fetchSAMGovOpportunities(new Date('2026-07-14T12:00:00Z'));
    assert.deepEqual(out, []);
    assert.equal(fetched, false);
  } finally {
    if (originalKey == null) delete process.env.SAM_GOV_API_KEY;
    else process.env.SAM_GOV_API_KEY = originalKey;
    if (originalAlt == null) delete process.env.SAM_API_KEY;
    else process.env.SAM_API_KEY = originalAlt;
    global.fetch = originalFetch;
  }
});

test('SAM.gov source calls official opportunities endpoint and maps records', async () => {
  const originalKey = process.env.SAM_GOV_API_KEY;
  const originalFetch = global.fetch;
  process.env.SAM_GOV_API_KEY = 'test-key';
  const calls = [];
  global.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    return new Response(JSON.stringify({
      opportunitiesData: [{
        title: 'Night vision devices',
        noticeId: 'abc-123',
        fullParentPathName: 'Department of Defense',
        type: 'Solicitation',
        responseDeadLine: '2026-08-01',
        naicsCode: '334511',
        classificationCode: '5855',
        postedDate: '2026-07-14',
        uiLink: 'https://sam.gov/opp/abc-123/view',
      }],
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };

  try {
    const out = await fetchSAMGovOpportunities(new Date('2026-07-14T12:00:00Z'));
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /^https:\/\/api\.sam\.gov\/opportunities\/v2\/search\?/);
    assert.match(calls[0].url, /api_key=test-key/);
    assert.match(calls[0].url, /postedFrom=06%2F30%2F2026/);
    assert.match(calls[0].url, /postedTo=07%2F14%2F2026/);
    assert.equal(out.length, 1);
    assert.equal(out[0].source, 'SAM.gov');
    assert.equal(out[0].title, 'Night vision devices');
    assert.match(out[0].description, /Department of Defense/);
    assert.match(out[0].description, /deadline 2026-08-01/);
    assert.equal(out[0].url, 'https://sam.gov/opp/abc-123/view');
  } finally {
    if (originalKey == null) delete process.env.SAM_GOV_API_KEY;
    else process.env.SAM_GOV_API_KEY = originalKey;
    global.fetch = originalFetch;
  }
});
