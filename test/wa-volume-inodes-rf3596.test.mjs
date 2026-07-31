// test/wa-volume-inodes-rf3596.test.mjs
//
// R-F3596 — aria-wa's /data volume hit 100% INODES with 645MB of bytes free.
//
// Measured live 2026-07-31: 64512/64512 inodes used, 0 free, while
// statfs reported 645.6MB available. Every write failed ENOSPC. Cause: Baileys 7
// writes one `lid-mapping-<jid>.json` per contact and never prunes — 47,619 of
// them across ten ORPHANED QR-linked accounts (the listener knew of zero live
// accounts), 80% of every inode on the volume.
//
// It was invisible. WhatsApp auth updates, account metadata and the R-F3587
// binding store were all failing silently; wa-accounts-meta.json had been
// truncated to 2 bytes, which is why zero accounts restored. The boot fsck line
// printed "64512/64512 files" in every deploy log and nobody read it.
//
// Any byte-based disk check would have reported the volume nearly EMPTY.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const SRC = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
const CODE = SRC.split(/\r?\n/).filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*')).join('\n');

test('R-F3596 the volume check measures INODES, not bytes', () => {
  assert.match(CODE, /statfsSync/);
  assert.match(CODE, /ffree/, 'free inodes is the signal; bavail alone reported 645MB free while every write failed');
});

test('R-F3596 the alert states inodes AND remaining bytes together', () => {
  // "disk full" on a volume with 645MB free is the kind of contradiction that
  // gets an alert dismissed as a false positive.
  const i = CODE.indexOf('INODES');
  assert.ok(i > 0, 'the message must name inodes explicitly');
  const block = CODE.slice(i - 200, i + 700);
  assert.match(block, /bytesFreeMb/, 'the alert must show bytes free alongside, or it reads as wrong');
  assert.match(block, /ENOSPC/, 'name the symptom operators will actually see');
});

test('R-F3596 the check is actually SCHEDULED, not merely defined', () => {
  // A defined-but-never-called watchdog is the dead-code class this session
  // spent the evening removing. Boot + hourly.
  assert.match(CODE, /setTimeout\(_checkVolumeHeadroom/, 'no run at boot — the volume can already be full when we start, and it was');
  assert.match(CODE, /setInterval\(_checkVolumeHeadroom/, 'no periodic run');
});

test('R-F3596 it reports to the brain, both branches (§21a)', () => {
  const i = CODE.indexOf('function _checkVolumeHeadroom');
  const body = CODE.slice(i, i + 1600);
  assert.match(body, /_waBrainSignal\(/, 'the critical branch must reach the brain');
  assert.match(body, /catch/, 'a failing check must not be silent either');
});

test('R-F3596 a failed pairing persist ROLLS BACK the in-memory push', () => {
  // Found live: mint returned 503 persist_failed while the status endpoint
  // simultaneously reported pairingPending:true. Two surfaces disagreeing about
  // the same fact is worse than either answer alone — the user retries a code
  // that already works, then loses it on the next restart.
  const i = CODE.indexOf('_waPendingPairings.push(issued.pairing)');
  assert.ok(i > 0);
  const block = CODE.slice(i, i + 600);
  assert.match(block, /_waPendingPairings\.filter\(\(x\) => x !== issued\.pairing\)/,
    'the pairing stays in memory after a failed persist — the caller is told it '
    + 'was not stored while the listener would still honour it');
  assert.match(block, /persist_failed/);
});

test('R-F3596 the watchdog cannot hold the process open', () => {
  const i = CODE.indexOf('setInterval(_checkVolumeHeadroom');
  assert.match(CODE.slice(i, i + 120), /unref/,
    'matches the other periodic work here; a ref-ing timer blocks clean shutdown');
});
