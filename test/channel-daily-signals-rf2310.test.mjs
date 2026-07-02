// R-F2310 — the daily post must draw from the REAL sweep fields. Audit R-F2309
// found it read currentData.sanctions / opportunities[].title, none of which the
// sweep produces → empty posts. buildDailySignals maps the correct fields in the
// customer-acquisition priority order.
import { test } from 'node:test';
import assert from 'node:assert';
import { buildDailySignals } from '../lib/telegram/channelServerHooks.mjs';

test('real sanctions designation (opensanctions.recent) renders with its lists', () => {
  const s = buildDailySignals({ opensanctions: { recent: [{ name: 'Rosoboronexport', datasets: ['us_ofac_sdn', 'eu_fsf'] }] } });
  assert.equal(s.length, 1);
  assert.match(s[0].title, /Sanctions exposure: Rosoboronexport/);
  assert.match(s[0].text, /2 sanctions\/watchlist datasets/);
  assert.match(s[0].text, /SCREEN Rosoboronexport/);  // conversion CTA
});

test('the OLD broken field (currentData.sanctions) produces nothing — bug is fixed', () => {
  const s = buildDailySignals({ sanctions: [{ title: 'x', summary: 'y' }] });
  assert.equal(s.length, 0);
});

test('verified tender renders; unverified tender is excluded', () => {
  const s = buildDailySignals({
    bdIntelligence: { tenders: [
      { title: 'Naval radar RFP', summary: 'Frigate radar tender', url: 'http://t/1', verified: 'VERIFIED', date: '2026-07-01' },
      { title: 'Unverified rumor', verified: 'UNVERIFIED' },
    ] },
  });
  assert.equal(s.length, 1);
  assert.match(s[0].title, /Procurement signal: Naval radar RFP/);
  assert.match(s[0].text, /2026-07-01/);
});

test('opportunity fallback ONLY when sanctions + tenders are dry', () => {
  const withReal = buildDailySignals({
    opensanctions: { recent: [{ name: 'X', datasets: ['a', 'b'] }] },
    opportunities: [{ market: 'Poland', score: 90, tier: 'HOT' }],
  });
  assert.ok(withReal.every(x => !/Market watch/.test(x.title)), 'opportunity must not appear when sanctions present');

  const fallback = buildDailySignals({ opportunities: [{ market: 'Poland', score: 90, tier: 'HOT', procurementNeeds: ['radar', 'UAV'] }] });
  assert.equal(fallback.length, 1);
  assert.match(fallback[0].title, /Market watch: Poland/);
  assert.match(fallback[0].text, /radar/);
});

test('nothing material → empty (cron skips the post)', () => {
  assert.equal(buildDailySignals({}).length, 0);
  assert.equal(buildDailySignals({ opensanctions: { recent: [] }, bdIntelligence: { tenders: [] }, opportunities: [] }).length, 0);
});

test('sanctions take priority and cap at 2', () => {
  const s = buildDailySignals({ opensanctions: { recent: [
    { name: 'A', datasets: ['x', 'y'] }, { name: 'B', datasets: ['x', 'y'] }, { name: 'C', datasets: ['x', 'y'] },
  ] } });
  assert.equal(s.length, 2);
});
