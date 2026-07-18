// R-F2713 — Batch B, §north-star (measure independence, not repetition).
//
// Symptom: apis/briefing.mjs counted "cross-source confirmation" by ARRAY LENGTH /
// occurrence count (titleIndex[nt].push(source.name); srcCount = .length), so N repeats
// of one item from ONE collector — or several mirrors of one publisher — masqueraded as
// N independent confirmations, falsely inflating ARIA's confidence. Signals/alerts were
// only sorted (not deduped), and pushSignalsToBrain collected raw items, so repeats also
// consumed the 30-signal brain budget and were absorbed as if independent.
//
// This proves the independence primitive (publisherFamily) is correct AND drives the real
// pushSignalsToBrain dedup path with a mocked fetch (no real brain delivery).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const { publisherFamily, pushSignalsToBrain } = await import('../apis/briefing.mjs');

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'apis', 'briefing.mjs'), 'utf-8');

describe('R-F2713 corroboration integrity', () => {
  it('publisherFamily collapses MIRRORS of one outlet to a single family', () => {
    // www + a feeds subdomain of the same registrable domain → ONE family (the fix's point:
    // several mirrors of one publisher must NOT read as independent corroboration).
    assert.equal(publisherFamily({ url: 'https://www.reuters.com/a' }, 'X'), 'reuters.com');
    assert.equal(publisherFamily({ url: 'https://feeds.reuters.com/b' }, 'X'), 'reuters.com');
    assert.equal(
      publisherFamily({ url: 'https://www.reuters.com/a' }, 'X'),
      publisherFamily({ url: 'https://feeds.reuters.com/b' }, 'Y'),
    );
    // genuinely different outlets → different families (real independence preserved)
    assert.notEqual(
      publisherFamily({ url: 'https://apnews.com/x' }, 'X'),
      publisherFamily({ url: 'https://reuters.com/y' }, 'X'),
    );
  });

  it('publisherFamily keeps distinct registrable orgs under a compound TLD distinct', () => {
    // mod.gov.uk and data.gov.uk are DIFFERENT UK entities → different publishers.
    // (Collapsing all of gov.uk to one family would UNDER-count genuine independence.)
    assert.equal(publisherFamily({ url: 'https://www.mod.gov.uk/x' }, 'X'), 'mod.gov.uk');
    assert.equal(publisherFamily({ url: 'https://data.gov.uk/y' }, 'X'), 'data.gov.uk');
    assert.notEqual(
      publisherFamily({ url: 'https://mod.gov.uk/x' }, 'X'),
      publisherFamily({ url: 'https://data.gov.uk/y' }, 'X'),
    );
  });

  it('publisherFamily falls back to the COLLECTOR when there is no URL', () => {
    // no url → a single collector can never look like an independent second family
    assert.equal(publisherFamily({ title: 'no url here' }, 'Lusophone'), 'collector:lusophone');
    assert.equal(publisherFamily({}, 'ProcurementTenders'), 'collector:procurementtenders');
  });

  it('STRUCTURAL: corroboration uses distinct-FAMILY count, not array length', () => {
    assert.match(SRC, /titleFamilies\[nt\]\?\.size/, 'confirmation must be the distinct-family Set size');
    assert.doesNotMatch(SRC, /titleIndex\[nt\]\?\.length/, 'the old array-length corroboration must be gone');
    assert.match(SRC, /function dedupByTitle/, 'signals/alerts must be deduped, not just sorted');
  });

  it('pushSignalsToBrain dedups repeats within AND across sources before the brain budget', async () => {
    const dupTitle = 'Ministry awards frigate contract to Acme Naval';
    const sweepOutput = {
      sources: {
        // one whitelisted collector emits the SAME headline 3× (no independence)
        Lusophone: {
          updates: [
            { title: dupTitle, url: 'https://jornal.ao/1' },
            { title: dupTitle, url: 'https://jornal.ao/2' },  // repeat
            { title: dupTitle, url: 'https://jornal.ao/3' },  // repeat
            { title: 'A genuinely different item', url: 'https://jornal.ao/9' },
          ],
        },
        // a second whitelisted collector MIRRORS the same headline (still the same intel)
        ProcurementTenders: {
          tenders: [{ title: dupTitle, url: 'https://mirror.example/1' }],
        },
      },
    };

    let postedSignals = null;
    const origFetch = global.fetch;
    global.fetch = async (_url, opts) => {
      try { postedSignals = JSON.parse(opts.body).signals; } catch { /* non-bulk path */ }
      return { ok: true, status: 202, json: async () => ({}) };
    };
    try {
      const res = await pushSignalsToBrain(sweepOutput);
      // Unique signals built = 2 (the deduped headline + the one genuinely different item),
      // regardless of whether delivery succeeded — without R-F2713 it would be 5 (3+1+1).
      const total = (res?.delivered || 0) + (res?.failed || 0);
      assert.equal(total, 2, `expected 2 unique signals after dedup, got ${total}`);
      if (postedSignals) assert.equal(postedSignals.length, 2, 'exactly 2 unique signals posted to the brain');
    } finally {
      global.fetch = origFetch;
    }
  });
});
