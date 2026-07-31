import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  LINKED_SCOPES,
  REQUIRED_RISK_ACCEPTANCES,
  buildOperationalEvent,
  issueLinkedGrant,
  linkedGrantState,
  linkedMessageAllowed,
} from '../lib/whatsapp/waGovernance.mjs';

test('R-F3578 refuses QR consent until every risk is accepted and a scope is chosen', () => {
  assert.equal(issueLinkedGrant({ scopes: [], accepted: [] }).ok, false);
  const partial = issueLinkedGrant({ scopes: [LINKED_SCOPES[0]], accepted: REQUIRED_RISK_ACCEPTANCES.slice(1) });
  assert.equal(partial.ok, false);
  assert.equal(partial.code, 'risk_acceptance_incomplete');
});

test('R-F3578 operational telemetry cannot retain message, person or chat identifiers', () => {
  const seededPrivateContent = 'PRIVATE-SEED-DO-NOT-LOG';
  const event = buildOperationalEvent({
    eventId: 'evt-1', chatId: '447700900123@s.whatsapp.net', timestamp: 1,
    byteCount: Buffer.byteLength(seededPrivateContent), outcome: 'accepted',
    text: seededPrivateContent, sender: '+447700900123', groupName: 'Private family',
  });
  const serialized = JSON.stringify(event);
  assert.equal(serialized.includes(seededPrivateContent), false);
  assert.equal(serialized.includes('447700900123'), false);
  assert.deepEqual(Object.keys(event).sort(), ['byteCount', 'chatType', 'eventId', 'outcome', 'timestamp']);
});

test('R-F3578 issues a 30-day reversible linked grant', () => {
  const now = Date.UTC(2026, 6, 31, 17, 14);
  const result = issueLinkedGrant({ scopes: ['forwarded_or_tagged'], accepted: REQUIRED_RISK_ACCEPTANCES }, now);
  assert.equal(result.ok, true);
  assert.equal(linkedGrantState(result.grant, now).active, true);
  assert.equal(linkedGrantState(result.grant, Date.parse(result.grant.expiresAt)).code, 'consent_expired');
  assert.equal(result.grant.status, 'active');
});

test('R-F3578 denies unapproved chats and sensitive media outside the selected scope', () => {
  const now = Date.UTC(2026, 6, 31, 17, 14);
  const result = issueLinkedGrant({
    scopes: ['approved_chat_messages'],
    approvedChats: ['deal@g.us'],
    accepted: REQUIRED_RISK_ACCEPTANCES,
  }, now);
  assert.equal(linkedMessageAllowed(result.grant, { chatId: 'other@g.us' }, now).code, 'chat_not_approved');
  assert.equal(linkedMessageAllowed(result.grant, { chatId: 'deal@g.us', kind: 'attachment' }, now).code, 'attachment_scope_denied');
  assert.equal(linkedMessageAllowed(result.grant, { chatId: 'deal@g.us' }, now).code, 'approved');
});

// ── Claude review of R-F3578 ────────────────────────────────────────────────

test('R-F3578 review: the listener consent check is UNCONDITIONAL, not owner-gated', () => {
  const listener = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
  const create = listener.slice(listener.indexOf("app.post('/api/wa-listener/accounts'"));
  // Strip `//` comments before matching. The fix's own comment QUOTES the old
  // `if (_owner && !_grantState.active)` line to explain what was wrong, and a
  // naive scan matches that quotation — a guard that reads documentation as
  // code, which is the same defect class this review is about.
  const block = create.slice(0, create.indexOf('try {'))
    .split(/\r?\n/).filter((line) => !line.trim().startsWith('//')).join('\n');

  assert.doesNotMatch(block, /if\s*\(\s*_owner\s*&&\s*!_grantState\.active\s*\)/,
    'the consent check is gated on _owner again. _waUser() returns "" for an '
    + 'admin/internal caller, so presenting the service auth WITHOUT X-WA-User '
    + 'creates a linked device with NO consent grant — the bypass this change exists to close.');
  assert.match(block, /if\s*\(\s*!_grantState\.active\s*\)/,
    'the create route must refuse any request whose grant is not active');
});

test('R-F3578 review: Dockerfile claim about internal reachability is enforceable', () => {
  const dockerfile = readFileSync(new URL('../Dockerfile.wa', import.meta.url), 'utf8');
  if (!/cannot be reached around web consent/.test(dockerfile)) return;  // claim withdrawn, nothing to enforce
  const listener = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
  const create = listener.slice(listener.indexOf("app.post('/api/wa-listener/accounts'"));
  const codeOnly = create.slice(0, create.indexOf('try {'))
    .split(/\r?\n/).filter((line) => !line.trim().startsWith('//')).join('\n');
  assert.match(codeOnly, /if\s*\(\s*!_grantState\.active\s*\)/,
    'Dockerfile.wa claims the internal service cannot be reached around web '
    + 'consent. A surface may not describe a capability the code does not have.');
});

test('R-F3578 review: an ungoverned grant cannot satisfy linkedGrantState', () => {
  // The property the route now leans on, asserted directly rather than inferred.
  assert.equal(linkedGrantState(null).active, false);
  assert.equal(linkedGrantState(undefined).active, false);
  assert.equal(linkedGrantState({}).active, false);
  assert.equal(linkedGrantState({ mode: 'official' }).active, false);
});
