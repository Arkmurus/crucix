/**
 * R-F4361 (C-307) — /teach and /correct write ARIA's PERMANENT memory and are
 * gated only by the bootstrap allow-list.
 *
 * OPERATOR, 2026-08-26: *"for aria wa at the moment remove all limitations"*,
 * ahead of more users testing. The limitation actually blocking testers is
 * `WA_ALLOWED_SENDERS` — live it holds ONE number, and every other sender gets
 * "This ARIA number is restricted to verified users."
 *
 * REMOVING THAT LIST IS A ONE-WAY DOOR UNTIL THIS GATE EXISTS. `handleCommand`
 * checks `_waSenderAllowed` and nothing else, so opening the list also opens
 * `/teach` and `/correct` to anyone who knows the number — and CLAUDE.md §7
 * forbids eviction, so a poisoned fact is permanent. The listener's own warning
 * says exactly this:
 *
 *   "WA_ALLOWED_SENDERS unset — ARIA engages ANY sender who knows this number:
 *    ... every /command including /teach and /correct, which WRITE INTO HER
 *    PERMANENT MEMORY (§7, no eviction)."
 *
 * THE CAPABILITY ALREADY EXISTED AND NOTHING CONSULTED IT. `waCapability.mjs`
 * defines `CAP_MEMORY_WRITE` and `capabilityForCommand('teach') ->
 * CAP_MEMORY_WRITE`, and a repo-wide search finds NO production caller — only a
 * test. A classification with no consumer did not happen: the policy was
 * written down, tested against itself, and never enforced.
 *
 * So this does not invent a policy. It gives the existing one a consumer, which
 * is what lets the allow-list be opened for testers WITHOUT handing strangers a
 * permanent write into ARIA's memory. Everything else — chat, documents,
 * images, voice, DD, screening — opens freely.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  ROLE_ADMIN,
  ROLE_USER,
  CAP_MEMORY_WRITE,
  capabilityForCommand,
  mayWriteMemory,
} from '../lib/whatsapp/waCapability.mjs';

test('R-F4361: the memory-write capability finally has a consumer', () => {
  assert.equal(typeof mayWriteMemory, 'function',
    'CAP_MEMORY_WRITE was classified but nothing could act on it');
});

test('R-F4361: only a bound ADMIN may write permanent memory', () => {
  assert.equal(mayWriteMemory(ROLE_ADMIN), true);
  assert.equal(mayWriteMemory(ROLE_USER), false,
    'an ordinary sender could write a fact that §7 forbids ever removing');
});

test('R-F4361: fails CLOSED on anything it does not recognise', () => {
  // An unknown/absent role must never buy the strongest capability. `_waRole`
  // already yields ROLE_USER for unbound senders, so this is defence in depth.
  for (const role of [undefined, null, '', 'root', 'superuser', 0, {}]) {
    assert.equal(mayWriteMemory(role), false,
      `role ${JSON.stringify(role)} was granted permanent memory write`);
  }
});

test('R-F4361: the commands that write memory are exactly the ones gated', () => {
  // Pin the mapping this gate depends on, so a future command that writes
  // memory cannot be added without either classifying it or failing here.
  assert.equal(capabilityForCommand('teach'), CAP_MEMORY_WRITE);
  assert.equal(capabilityForCommand('correct'), CAP_MEMORY_WRITE);
  // ORDINARY commands must NOT be swept into the admin gate — that would be
  // this fix re-creating the limitation it exists to remove.
  assert.notEqual(capabilityForCommand('screen'), CAP_MEMORY_WRITE);
  assert.notEqual(capabilityForCommand('help'), CAP_MEMORY_WRITE);
});

test('R-F4361: the listener actually ENFORCES it (not just classifies)', async () => {
  // The defect was a policy nobody consulted, so a test that only checks the
  // helper would reproduce it. Assert the call site exists in the listener.
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(
    new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');

  assert.match(src, /mayWriteMemory\s*\(/,
    'aria_wa_listener never calls mayWriteMemory — the capability is still '
    + 'classified and unenforced, which is exactly C-307');
  assert.match(src, /capabilityForCommand\s*\(/,
    'the listener does not derive the capability from the command, so the '
    + 'gate would drift from waCapability.mjs the first time a command is added');
});
