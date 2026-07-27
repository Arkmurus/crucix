import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const vetting = readFileSync('public/vetting.html', 'utf8');
const vault = readFileSync('public/vault.html', 'utf8');
const watchlist = readFileSync('public/watchlist.html', 'utf8');
const reports = readFileSync('public/dd-reports.html', 'utf8');
const server = readFileSync('server.mjs', 'utf8');

test('R-F3225 removes irrelevant introductory and empty-assessment copy', () => {
  for (const text of [
    'Employment vetting',
    'Deterministic pre-employment screening',
    'No assessment loaded',
    'Select or create a case, then run an assessment.',
  ]) {
    assert.equal(vetting.includes(text), false, `vetting.html still contains: ${text}`);
  }
  assert.match(vetting, /<h1 class="vt-title">Vetting<\/h1>/);
  assert.equal((vetting.match(/<h1\b/g) || []).length, 1);
});

test('R-F3225 removes the obsolete vault registry subtitle', () => {
  assert.equal(
    vault.includes('Every site/portal that DD agents have signed up to: a unified registry.'),
    false,
  );
});

test('R-F3225 renders and wires an individual Recent Alerts delete action', () => {
  assert.match(watchlist, /class="wl-alert-delete"/);
  assert.match(watchlist, /data-alert-id/);
  assert.match(watchlist, /\/api\/aria\/dd\/watchlist\/alerts\//);
  assert.match(watchlist, /method:\s*'DELETE'/);
});

test('R-F3225 makes monitoring autonomous and user-scheduled', () => {
  assert.doesNotMatch(watchlist, /Re-screen All|wl-rescreen-btn|watchlist\/rescreen/);
  assert.match(watchlist, /href="\/vetting\.html"/);
  assert.match(watchlist, /review_interval_hours/);
  assert.match(watchlist, /\/schedule/);
  assert.match(watchlist, /Every 2 weeks/);
});

test('R-F3225 accepts entities from DD reports and vetting cases', () => {
  assert.match(reports, /dd-watch-btn/);
  assert.match(reports, /source:\s*'dd_report'/);
  assert.match(vetting, /data-watch=/);
  assert.match(vetting, /source:\s*'vetting_case'/);
  assert.match(watchlist, /new URLSearchParams\(location\.search\)/);
});

test('R-F3225 pins every customer watchlist mutation to the authenticated user', () => {
  assert.match(server, /app\.post\('\/api\/aria\/dd\/watchlist'/);
  assert.match(server, /app\.patch\('\/api\/aria\/dd\/watchlist\/:name\/schedule'/);
  assert.match(server, /app\.delete\('\/api\/aria\/dd\/watchlist\/alerts\/:alertId'/);
  assert.match(server, /_ddPinUserParams\(req\)/);
});
