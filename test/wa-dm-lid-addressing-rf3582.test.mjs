// test/wa-dm-lid-addressing-rf3582.test.mjs
//
// R-F3582 — ARIA answered in groups and was SILENT on every direct message.
//
// Live evidence from aria-wa (2026-07-31), which is what turned this from a
// guess into a diagnosis: two `inbound accepted type=group` events and NOT ONE
// `type=direct`, while the Baileys log showed "Closing open session in favor of
// incoming prekey bundle" — a 1:1 session being established. The DM reached the
// socket and the chat filter discarded it.
//
// Cause: aria-wa runs Baileys 7.0.0-rc13, and modern WhatsApp addresses users by
// LID (`<id>@lid`) as well as by phone jid (`<phone>@s.whatsapp.net`). The DM
// predicate tested only the phone form, so an @lid chat matched NEITHER
// `_isGroup` nor `_isDM` and hit the `continue`. The repo contained no
// occurrence of "@lid" anywhere.
//
// The deeper defect is the SILENCE: an unrecognised addressing scheme was
// indistinguishable from "no message arrived", so a live user-visible outage on
// the support channel left nothing in the logs to find.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const listener = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
const pkg = JSON.parse(readFileSync(new URL('../services/wa-listener/package.json', import.meta.url), 'utf8'));

test('R-F3582 a 1:1 chat is recognised by LID as well as by phone jid', () => {
  const dmPredicate = listener.match(/const _isDM\s*=\s*(.+);/)?.[1] || '';
  assert.match(dmPredicate, /@s\.whatsapp\.net/, 'the phone jid form must still count');
  assert.match(dmPredicate, /@lid/,
    'an @lid chat matches neither _isGroup nor _isDM and is dropped — that is '
    + 'exactly the defect: ARIA silent on every DM while groups worked.');
});

test('R-F3582 a group is still only @g.us, so @lid cannot be mistaken for one', () => {
  const groupPredicate = listener.match(/const _isGroup\s*=\s*(.+);/)?.[1] || '';
  assert.match(groupPredicate, /@g\.us/);
  assert.doesNotMatch(groupPredicate, /@lid/,
    '@lid identifies a USER; treating it as a group would send 1:1 replies down '
    + 'the group path and apply TARGET_GROUPS filtering to a private chat');
});

test('R-F3582 an unrecognised chat type is LOGGED, never silently dropped', () => {
  // The real fix. Whatever addressing WhatsApp introduces next must be visible.
  const idx = listener.indexOf('const _isDM');
  const block = listener.slice(idx, idx + 2000);
  assert.match(block, /R-F3582 dropped an unrecognised chat type/,
    'the drop path must say something. A silently discarded message class reads '
    + 'as "she never replied" and cost a live outage with nothing in the logs.');
});

test('R-F3582 the drop log records the SUFFIX only, never an identifier', () => {
  const idx = listener.indexOf('R-F3582 dropped an unrecognised chat type');
  const line = listener.slice(idx - 400, idx + 400);
  assert.match(line, /lastIndexOf\('@'\)/,
    'the log must derive a suffix, not print the jid');
  assert.doesNotMatch(line, /\$\{chatId\}/,
    'R-F3578 removed phone/chat identifiers from these paths; logging the raw '
    + 'jid would reintroduce one');
});

test('R-F3582 expected non-chat jids stay quiet', () => {
  const idx = listener.indexOf('R-F3582 dropped an unrecognised chat type');
  const block = listener.slice(idx - 600, idx + 200);
  assert.match(block, /@broadcast/);
  assert.match(block, /@newsletter/,
    'status/newsletter traffic is expected and must not spam the log, or the '
    + 'warning that matters gets lost in noise');
});

test('R-F3582 jid stripping handles every domain, not just the phone form', () => {
  assert.match(listener, /function _jidUser\(/,
    'one definition, so the call sites cannot drift apart again');
  // Strip `//` lines first. The fix's own comment QUOTES the old
  // `.replace('@s.whatsapp.net','')` to explain what was wrong, and a naive scan
  // reads that quotation as live code — a guard that asserts against
  // documentation instead of behaviour. Third occurrence of this trap in one
  // session, which is why it is written down here rather than just worked around.
  const codeOnly = listener
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
    .join('\n');
  assert.doesNotMatch(codeOnly, /replace\('@s\.whatsapp\.net',\s*''\)/,
    "a bare .replace('@s.whatsapp.net','') leaves a literal \"@lid\" in text "
    + "that reaches ARIA's records and the operator's screen");
});

test('R-F3582 the premise is real: the shipped Baileys uses LID addressing', () => {
  // Verify the instrument. If the WA tier is ever pinned back to a pre-LID
  // Baileys this diagnosis needs revisiting rather than assuming.
  const version = pkg.dependencies?.['@whiskeysockets/baileys'] || '';
  const major = parseInt(String(version).replace(/^\D*/, ''), 10);
  assert.ok(major >= 7, `baileys is ${version}; LID addressing arrived with 7.x`);
});


// ── R-F3584 — no env-derived const may be defined and never read ────────────

test('R-F3584 the listener defines no env flag it never reads', () => {
  // WA_LISTENER_AUTO_RESPOND was a LIVE fly secret whose const was defined and
  // referenced nowhere — R-F2061 replaced it with KEYWORD_AUTO_RESPONSE and left
  // the old one behind. A flag that promises control it does not have is the
  // same class as a surface describing a capability the code lacks: someone sets
  // it, sees no change, and stops trusting the flags that DO work.
  const codeOnly = listener
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
    .join('\n');

  const declared = [...codeOnly.matchAll(/^const\s+([A-Z][A-Z0-9_]*)\s*=\s*[^;]*process\.env\./gm)]
    .map((m) => m[1]);
  assert.ok(declared.length > 5, `only ${declared.length} env consts found — the scan has drifted`);

  const unread = declared.filter((name) => {
    const uses = [...codeOnly.matchAll(new RegExp('\\b' + name + '\\b', 'g'))].length;
    return uses <= 1;   // the declaration itself
  });
  assert.deepEqual(unread, [],
    `these env-derived consts are declared and never read: ${unread.join(', ')}. `
    + 'Either wire them or delete them — a dead flag is worse than no flag.');
});
