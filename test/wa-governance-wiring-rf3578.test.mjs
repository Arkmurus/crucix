import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const server = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const listener = fs.readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
const page = fs.readFileSync(new URL('../public/wa-connections.html', import.meta.url), 'utf8');
const dockerfile = fs.readFileSync(new URL('../Dockerfile.wa', import.meta.url), 'utf8');

test('R-F3578 QR creation is gated at web and listener boundaries', () => {
  assert.match(server, /linkedGrantState\(user\?\.waLinkedGrant\)/);
  assert.match(server, /body: JSON\.stringify\(\{ name: req\.body\?\.name \|\| 'My WhatsApp', governance: user\.waLinkedGrant \}\)/);
  assert.match(listener, /linkedGrantState\(governance\)/);
  assert.match(listener, /linkedMessageAllowed\(account\.governance/);
});

test('R-F3578 user sees official default and explicit advanced risk flow', () => {
  assert.match(page, /Recommended/);
  // R-F3603 - assert the PROPERTY, not the exact label. This pinned
  // "Advanced · Experimental" and broke when the cards were relabelled to
  // "Option 2 · Advanced, experimental" during the two-option restructure -
  // while the thing it guards, that the linked-device path is explicitly marked
  // advanced AND experimental, was never in question.
  assert.match(page, /Advanced/i);
  assert.match(page, /Experimental/i);
  assert.match(page, /accept_linked_risk/);
  assert.match(page, /authenticator code/);
  assert.match(page, /No QR can be generated/);
});

test('R-F3578 listener image includes the shared enforcement module', () => {
  assert.match(dockerfile, /COPY lib\/whatsapp\/waGovernance\.mjs/);
});

test('R-F3578 raw seven-day WhatsApp message retention and preview logs are absent', () => {
  assert.doesNotMatch(listener, /crucix:wa_listener:messages/);
  assert.doesNotMatch(listener, /7 \* 86400, JSON\.stringify\(entry\)/);
  assert.doesNotMatch(listener, /senderName\}: \$\{text\.slice/);
  assert.doesNotMatch(listener, /text\.slice\(0, 80\)/);
});

test('R-F3578 ownerless Baileys session is disabled by default', () => {
  assert.match(listener, /if \(process\.env\.WA_PRIMARY_LINKED_ENABLED === '1'\)/);
  assert.match(listener, /ownerless primary linked-device session disabled/);
});
