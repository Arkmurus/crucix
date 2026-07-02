// R-F2312 — sanctions spotlight wires ARIA's Python screening engine into the
// daily post. It features ONE entity only if the real engine returns a verified
// blocking hit (never-false-clean), and stays silent otherwise. Lean: no hit → no
// post-pollution.
import { test } from 'node:test';
import assert from 'node:assert';
import { fetchSanctionsSpotlight } from '../lib/telegram/channelServerHooks.mjs';

const _origFetch = global.fetch;
function mock(body, ok = true) { global.fetch = async () => ({ ok, status: ok ? 200 : 503, json: async () => body }); }
const withRecent = (...names) => ({ opensanctions: { recent: names.map(name => ({ name })) } });

test.afterEach(() => { global.fetch = _origFetch; });

test('verified blocking hit → spotlight names the entity, lists, and a SCREEN CTA', async () => {
  mock({ screened: true, blocking_matches: [{ name: 'Rosoboronexport', list: 'us_ofac_cons', score: 1.0 }] });
  const s = await fetchSanctionsSpotlight(withRecent('Rosoboronexport'), { serviceUrl: 'http://x', token: 't' });
  assert.ok(s);
  assert.match(s.title, /Sanctions spotlight: Rosoboronexport/);
  assert.match(s.text, /1 blocking match/);
  assert.match(s.text, /us_ofac_cons/);
  assert.match(s.text, /SCREEN/);
});

test('screened but CLEAN (no blocking) → null (never feature a clean as a hit)', async () => {
  mock({ screened: true, blocking_matches: [], matches: [] });
  const s = await fetchSanctionsSpotlight(withRecent('QinetiQ Group'), { serviceUrl: 'http://x' });
  assert.equal(s, null);
});

test('unperformed/errored screen → null (never-false-clean, never fabricate)', async () => {
  mock({ screened: false, error: 'sanctions_source_unavailable' });
  const s = await fetchSanctionsSpotlight(withRecent('Acme'), { serviceUrl: 'http://x' });
  assert.equal(s, null);
});

test('no topical entity in the sweep → null (silent, no pollution)', async () => {
  mock({ screened: true, blocking_matches: [{ name: 'x', list: 'y' }] });
  assert.equal(await fetchSanctionsSpotlight({ opensanctions: { recent: [] } }, { serviceUrl: 'http://x' }), null);
  assert.equal(await fetchSanctionsSpotlight({}, { serviceUrl: 'http://x' }), null);
});

test('engine HTTP error → null, never throws', async () => {
  mock({}, false);
  const s = await fetchSanctionsSpotlight(withRecent('Acme'), { serviceUrl: 'http://x' });
  assert.equal(s, null);
});
