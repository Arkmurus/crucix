// test/wa-verified-senders-rf3586.test.mjs
//
// R-F3586 — ARIA engaged ANY sender who knew her number.
//
// `_waSenderAllowed()` existed and was consulted in exactly TWO places:
// handleCommand() and the keyword auto-response (default OFF). The path that
// actually answers people — free text -> askARIA -> sendReply — had NO sender
// check, and neither did the document, image or voice-note paths.
//
// So anyone with the number got unlimited LLM engagement on the $300/mo budget
// (CLAUDE.md §17) and could run `/teach` and `/correct`, which WRITE INTO ARIA'S
// PERMANENT MEMORY — a store that by §7 never evicts. Knowledge poisoning, not
// just spend.
//
// Two design constraints this pins:
//   1. LID-AWARE. With Baileys 7 the same person is `<phone>@s.whatsapp.net` OR
//      `<lid>@lid`. An allow-list written as phone numbers cannot match the LID
//      form, so matching one field would refuse legitimate senders — the same
//      shape as R-F3582, where one hard-coded suffix silenced every DM.
//   2. NEVER SILENT. A refused sender is told once per chat. R-F3582 was
//      invisible for hours precisely because a dropped message and a broken
//      listener look identical.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const SRC = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
const CODE = SRC.split(/\r?\n/)
  .filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
  .join('\n');

test('R-F3586 every engagement is gated BEFORE the branches, not per-path', () => {
  const gate = CODE.indexOf('if (!_waSenderAllowed(senderJid, msg))');
  assert.ok(gate > 0, 'the single pre-engagement authorisation gate is missing');

  // It must sit above the paths it protects, or a new branch inherits nothing.
  for (const marker of ['MENTIONS_RE.some(p => p.test(text))', 'const docMsg', 'const audioMsg']) {
    const idx = CODE.indexOf(marker);
    assert.ok(idx > gate, `"${marker}" appears BEFORE the gate — that path is unprotected`);
  }
});

test('R-F3586 the gate refuses by CONTINUE, not by falling through', () => {
  const gate = CODE.indexOf('if (!_waSenderAllowed(senderJid, msg))');
  const block = CODE.slice(gate, gate + 1200);
  assert.match(block, /continue;/, 'a refused sender must be dropped, not merely logged');
});

test('R-F3586 identity matching covers the LID form, not just the phone jid', () => {
  assert.match(CODE, /function _waSenderIdentities\(/);
  for (const field of ['participant', 'remoteJid', 'participantAlt', 'senderPn']) {
    assert.ok(CODE.includes(`'${field}'`),
      `${field} is not collected; a sender arriving in that form would be refused`);
  }
  // and the match must be ANY-of, not a single field
  assert.match(CODE, /identities\.some\(/,
    'matching must accept ANY identifier the message carries');
});

test('R-F3586 a refusal is announced, and only once per chat', () => {
  assert.match(CODE, /_waNotifyRefusalOnce\(/,
    'a silent refusal repeats the R-F3582 failure: indistinguishable from a broken listener');
  assert.match(CODE, /_WA_REFUSAL_NOTICE_MS/,
    'replying to every unauthorised message makes ARIA an amplifier and can loop between bots');
  const helper = CODE.slice(CODE.indexOf('function _waNotifyRefusalOnce'), CODE.indexOf('function _waSenderAllowed'));
  assert.match(helper, /size > \d+/, 'the notice map must be bounded — an unauthorised flood must not grow memory');
});

test('R-F3586 the refusal diagnostic names FIELDS, never identifiers', () => {
  assert.match(CODE, /function _waIdentityFields\(/);
  const gate = CODE.indexOf('R-F3586 engagement refused');
  const block = SRC.slice(gate - 200, gate + 700);
  assert.doesNotMatch(block, /\$\{senderJid\}/,
    'R-F3578 removed phone/chat identifiers from these paths; logging the sender '
    + 'would reintroduce one. Log which FIELDS were present, not who was refused.');
});

test('R-F3586 an unset allow-list still warns, and names what is exposed', () => {
  const warn = CODE.slice(CODE.indexOf('_waAllowWarned = true'), CODE.indexOf('_waAllowWarned = true') + 700);
  assert.match(warn, /teach/, 'the warning must name the memory-writing commands, not just "commands"');
  assert.match(warn, /ANY sender/);
});

test('R-F3586 the memory-writing commands exist and are therefore worth gating', () => {
  // Verify the premise rather than asserting it. If /teach and /correct are ever
  // removed, this reasoning needs revisiting instead of being inherited.
  for (const cmd of ["case 'teach'", "case 'correct'"]) {
    assert.ok(SRC.includes(cmd), `${cmd} not found — re-check what the gate is protecting`);
  }
});

test('R-F3586 the command path keeps its own check (defence in depth)', () => {
  // The pre-engagement gate makes this redundant today, and redundant is correct:
  // handleCommand is also reachable from other entry points.
  const handler = CODE.slice(CODE.indexOf('async function handleCommand'));
  assert.match(handler.slice(0, 600), /_waSenderAllowed\(senderJid\)/);
});
