// test/network-presence-rf2342.test.mjs
//
// Capability tests for R-F2342 — ARIA Network (opt-in presence + 1:1 DM).
//
// Drives the REAL functions the feature depends on (createUser/updateUser/
// listUsers/findUserById) plus the exact filter/gating contracts the
// /api/network/* routes and the socket presence layer use in server.mjs,
// and asserts the frontend actually consumes them. Privacy-by-default is the
// headline invariant: a user is invisible until they explicitly opt in.

import test from 'node:test';
import assert from 'node:assert';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Isolate the user store BEFORE importing users.mjs (it reads the path at load).
process.env.USERS_FILE_OVERRIDE = join(mkdtempSync(join(tmpdir(), 'net-rf2342-')), 'users.json');
const { createUser, updateUser, findUserById, listUsers } =
  await import('../lib/auth/users.mjs');

const mk = (n) => createUser({
  username: n, email: `${n}@example.io`, password: 'password123', fullName: `${n} Person`,
});

// Mirror of the /api/network/directory filter (server.mjs).
function directoryFor(meId) {
  return listUsers()
    .filter(u => u.status === 'active' && u.networkVisible && u.id !== meId)
    .map(u => u.id);
}
// Mirror of the socket presence-gating predicate (server.mjs _isVisible).
const isVisible = (id) => !!findUserById(id)?.networkVisible;

test('R-F2342: new users are INVISIBLE by default (privacy-by-default)', () => {
  const u = mk('alice');
  assert.equal(u.networkVisible, false, 'must default to hidden until opt-in');
  assert.equal(u.lastSeenAt, null);
  assert.equal('passwordHash' in u, false, 'cleanUser still strips secrets');
  assert.ok('networkVisible' in u, 'cleanUser exposes networkVisible to the frontend');
});

test('R-F2342: opting in flips visibility and persists to the store', () => {
  const u = mk('bob');
  const upd = updateUser(u.id, { networkVisible: true });
  assert.equal(upd.networkVisible, true);
  assert.equal(findUserById(u.id).networkVisible, true, 'persisted');
  const back = updateUser(u.id, { networkVisible: false });
  assert.equal(back.networkVisible, false, 'can opt back out');
});

test('R-F2342: directory shows ONLY opted-in active peers — excludes self, hidden, pending', () => {
  const me = mk('me1'), vis = mk('vis'), hid = mk('hid'), pen = mk('pen');
  updateUser(me.id,  { status: 'active', networkVisible: true });
  updateUser(vis.id, { status: 'active', networkVisible: true });
  updateUser(hid.id, { status: 'active', networkVisible: false });
  updateUser(pen.id, { status: 'pending_verification', networkVisible: true });

  const dir = directoryFor(me.id);
  assert.ok(dir.includes(vis.id), 'opted-in active peer appears');
  assert.ok(!dir.includes(me.id), 'self is excluded');
  assert.ok(!dir.includes(hid.id), 'hidden (opted-out) user is excluded');
  assert.ok(!dir.includes(pen.id), 'non-active user is excluded');
});

test('R-F2342: presence gating never announces a hidden user', () => {
  const on = mk('onn'), off = mk('offu');
  updateUser(on.id, { networkVisible: true });
  assert.equal(isVisible(on.id), true, 'opted-in user is broadcast');
  assert.equal(isVisible(off.id), false, 'hidden user is never broadcast to the network');
});

test('R-F2342: frontend wires presence, opt-in, and DM to the real backend', () => {
  const html = readFileSync(new URL('../public/network.html', import.meta.url), 'utf8');
  const js = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.match(html, /socket\.io\/socket\.io\.js/, 'loads the socket.io client');
  assert.match(html, /js\/network\.js/, 'loads network.js');
  assert.match(html, /self-toggle/, 'renders the opt-in presence toggle');
  assert.match(js, /\/api\/network\/directory/, 'fetches the opt-in directory');
  assert.match(js, /\/api\/network\/visibility/, 'posts the visibility toggle');
  assert.match(js, /emit\(['"]send_message['"]/, 'sends DMs over the socket');
  assert.match(js, /on\(['"]presence['"]/, 'handles presence updates');
  assert.match(js, /on\(['"]new_message['"]/, 'handles incoming messages');
  assert.match(js, /on\(['"]typing['"]/, 'handles typing indicators');
});

test('R-F2344: network routes guard against the localhost-bypass (no req.user)', () => {
  const srv = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  // both /api/network/* handlers must 401 when req.user is unset (bypass path)
  const guards = srv.match(/if \(!req\.user\?\.userId\) return res\.status\(401\)/g) || [];
  assert.ok(guards.length >= 2, `expected the guard on both network routes, found ${guards.length}`);
});

test('R-F2345: backend routes DMs addressed to ARIA into the brain', () => {
  const srv = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  assert.match(srv, /const ARIA_ID = 'aria'/, 'defines the ARIA pseudo-member id');
  assert.match(srv, /toId === ARIA_ID[\s\S]{0,60}_ariaChannelReply/, 'routes ARIA DMs to the brain');
  assert.match(srv, /_ariaChatAsyncPoll\([^)]*network_/, 'uses the async brain chat path with a network session');
  assert.match(srv, /ariaBusy\.has\(uid\)/, 'anti-stack: one in-flight ARIA request per user');
  assert.match(srv, /_ariaChunks/, 'chunks long analyses into deliverable messages');
  // ARIA replies are server-attributed — a user cannot forge a message "from ARIA"
  assert.match(srv, /fromFullName: 'ARIA'/, 'ARIA reply attribution is server-set');
});

test('R-F2345: ARIA is an in-thread conversation (no redirect), always online', () => {
  const js = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.doesNotMatch(js, /location\.href *= *['"]\/aria\.html/, 'ARIA no longer redirects out of the tab');
  assert.match(js, /id === ARIA\.id/, 'treats ARIA as a first-class conversation');
  assert.match(js, /userInfo\.set\(ARIA\.id, ARIA\)/, 'ARIA renders by name in the thread');
  assert.match(js, /screen a company/, 'ARIA composer invites screening/enrichment');
});

test('R-F2349: cleanUser derives ONE shared, versioned avatarUrl (null when no photo)', () => {
  const u = mk('photo1');
  assert.equal(u.avatarUrl, null, 'no photo → null (fall back to initials)');
  assert.equal(u.avatarUpdatedAt, null);
  const upd = updateUser(u.id, { avatarUpdatedAt: '2026-07-02T12:00:00.000Z', avatarMime: 'image/jpeg' });
  assert.match(upd.avatarUrl, new RegExp(`^/api/profile/photo/${u.id}\\?v=\\d+$`),
    'derived, versioned URL shared by profile + network + sidebar');
});

test('R-F2349: photo upload/serve wired end to end (routes + shared source + UI)', () => {
  const srv = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  assert.match(srv, /app\.post\('\/api\/profile\/photo'/, 'upload route (self only)');
  assert.match(srv, /app\.get\('\/api\/profile\/photo\/:id'/, 'public serve route (for <img>)');
  assert.match(srv, /app\.delete\('\/api\/profile\/photo'/, 'delete route');
  assert.match(srv, /const AVATAR_DIR =/, 'photos on the durable volume');
  assert.match(srv, /avatarUrl:[\s\S]{0,80}avatarUpdatedAt/, 'directory exposes the shared avatarUrl');
  const js = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.match(js, /net-photo-input/, 'the round avatar opens the file picker');
  assert.match(js, /\/api\/profile\/photo/, 'posts the photo');
  assert.match(js, /resizeSquare/, 'resizes client-side to keep the payload small');
  const html = readFileSync(new URL('../public/network.html', import.meta.url), 'utf8');
  assert.match(html, /self-av-edit/, 'the existing round self-avatar is the uploader');
  assert.match(html, /net-av-cam/, 'camera affordance on the circle');
  const sb = readFileSync(new URL('../public/js/sidebar.js', import.meta.url), 'utf8');
  assert.match(sb, /user\.avatarUrl/, 'sidebar shows the same shared photo');
});

test('R-F2356: editable profile — modal wired to PUT /api/auth/profile, updates touch points', () => {
  const html = readFileSync(new URL('../public/network.html', import.meta.url), 'utf8');
  assert.match(html, /pm-overlay/, 'edit-profile modal present');
  assert.match(html, /self-name-row/, 'the name is the click-to-edit trigger');
  assert.match(html, /pm-fullName[\s\S]*pm-jobTitle[\s\S]*pm-companyName[\s\S]*pm-sector/, 'profile fields present');
  const js = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.match(js, /function openProfileModal/, 'opens the modal');
  assert.match(js, /\/api\/auth\/profile/, 'saves to the profile endpoint');
  assert.match(js, /method: 'PUT'/, 'via PUT');
  assert.match(js, /nav-user-name/, 'updates the sidebar name too');
  assert.match(js, /crucix_user/, 'updates the shared cache so every page reflects it');
});

test('R-F2356: profile field edits persist + surface via updateUser/cleanUser', () => {
  const u = mk('prof2');
  const upd = updateUser(u.id, { fullName: 'Jane Analyst', jobTitle: 'Compliance Lead',
    companyName: 'Acme Defence', companyCountry: 'UK', sector: 'compliance_consultancy' });
  assert.equal(upd.fullName, 'Jane Analyst');
  assert.equal(upd.jobTitle, 'Compliance Lead');
  assert.equal(upd.companyName, 'Acme Defence');
  assert.equal(upd.sector, 'compliance_consultancy');
  assert.equal(findUserById(u.id).jobTitle, 'Compliance Lead', 'persisted');
});

test('R-F2353: activating presence gives clear feedback (empty state reacts, CTA hides)', () => {
  const js = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.match(js, /function renderEmptyState/, 'has a reactive empty state');
  assert.match(js, /cta\.style\.display = 'none'/, 'hides the CTA once presence is on');
  assert.match(js, /Presence on/, 'confirms activation to the user');
  // the "Turn on my presence" CTA and the toggle both call the visibility endpoint
  assert.match(js, /empty-cta[\s\S]{0,120}toggleVisibility/, 'the CTA activates presence');
});
