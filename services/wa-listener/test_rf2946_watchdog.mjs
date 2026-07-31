/**
 * R-F2946 — WA connection watchdog: open-but-dead detection + restart dedup.
 * ═══════════════════════════════════════════════════════════════════════════
 * The bug (live 2026-07-23): the R-F1551 watchdog returned early whenever
 * `isConnected` was true, so a Baileys socket sitting connection:'open' but
 * silently dead was invisible for ~22 min (heartbeat kept logging
 * "connected=true", heard-count frozen at 13) until a late code-428 close fired.
 * A second bug in the same window: the close-handler's setTimeout(startListener)
 * raced the watchdog's own startListener() → two sockets started 2s apart.
 *
 * These tests drive the ACTUAL decision function that now governs restarts
 * (watchdogAction, imported from the side-effect-free wa-watchdog.mjs — the
 * listener itself auto-connects on import so it cannot be unit-tested directly),
 * then assert the wiring is present in the listener source.
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { watchdogAction, WATCHDOG_DEFAULTS } from './wa-watchdog.mjs';

const MIN = 60 * 1000;
const T = WATCHDOG_DEFAULTS;  // staleDisconnectMs=5m, silentProbeMs=4m, silentRestartMs=10m

describe('R-F2946 — watchdogAction decision matrix', () => {

  it('never-connected socket is left alone (still starting up)', () => {
    const d = watchdogAction(1_000_000, { isConnected: false, lastConnectedTime: 0, lastInboundActivity: 0 });
    assert.equal(d.action, 'ok');
    assert.equal(d.reason, 'never-connected');
  });

  it('connected + recent inbound activity → ok (the healthy steady state)', () => {
    const now = 10 * MIN;
    const d = watchdogAction(now, { isConnected: true, lastConnectedTime: 1 * MIN, lastInboundActivity: now - 30_000 });
    assert.equal(d.action, 'ok');
    assert.equal(d.reason, 'fresh');
  });

  it('THE BUG: connected but silent past the ceiling → restart (open-but-dead)', () => {
    const now = 30 * MIN;
    // isConnected is a STALE true; last real inbound was 11 min ago (> 10m ceiling).
    const d = watchdogAction(now, { isConnected: true, lastConnectedTime: 1 * MIN, lastInboundActivity: now - 11 * MIN });
    assert.equal(d.action, 'restart', 'a dead-but-open socket MUST be restarted, not ignored');
    assert.equal(d.reason, 'silent-socket');
    assert.ok(d.silentMs > T.silentRestartMs);
  });

  it('connected but silent past the probe threshold (not yet the ceiling) → probe', () => {
    const now = 30 * MIN;
    const d = watchdogAction(now, { isConnected: true, lastConnectedTime: 1 * MIN, lastInboundActivity: now - 5 * MIN });
    assert.equal(d.action, 'probe');
    assert.equal(d.reason, 'silent-probe');
  });

  it('a recent inbound event OVERRIDES an old connect time (uses proven life, not the flag)', () => {
    const now = 100 * MIN;
    // Connected 99 min ago, but an event arrived 10s ago → still alive.
    const d = watchdogAction(now, { isConnected: true, lastConnectedTime: 1 * MIN, lastInboundActivity: now - 10_000 });
    assert.equal(d.action, 'ok');
  });

  it('with NO inbound yet, a fresh connect is the reference (no false restart right after open)', () => {
    const now = 2 * MIN;
    const d = watchdogAction(now, { isConnected: true, lastConnectedTime: now - 30_000, lastInboundActivity: 0 });
    assert.equal(d.action, 'ok');
  });

  it('disconnected but only briefly → ok (still reconnecting)', () => {
    const now = 10 * MIN;
    const d = watchdogAction(now, { isConnected: false, lastConnectedTime: now - 2 * MIN, lastInboundActivity: now - 2 * MIN });
    assert.equal(d.action, 'ok');
    assert.equal(d.reason, 'reconnecting');
  });

  it('disconnected past the stale threshold → restart (the original R-F1551 rule preserved)', () => {
    const now = 30 * MIN;
    const d = watchdogAction(now, { isConnected: false, lastConnectedTime: now - 6 * MIN, lastInboundActivity: now - 6 * MIN });
    assert.equal(d.action, 'restart');
    assert.equal(d.reason, 'stale-disconnect');
  });

  it('thresholds are overridable (so the listener can pass its own constants)', () => {
    const now = 100 * MIN;
    const d = watchdogAction(now, {
      isConnected: true, lastConnectedTime: 1 * MIN, lastInboundActivity: now - 90_000,
      silentProbeMs: 60_000, silentRestartMs: 120_000,
    });
    assert.equal(d.action, 'probe');  // 90s silent: past 60s probe, under 120s restart
  });
});

describe('R-F2946 — the listener wires the decision + dedup', () => {
  const SRC = fs.readFileSync(path.resolve('services/wa-listener/aria_wa_listener.mjs'), 'utf-8');

  it('imports and calls the pure decision function', () => {
    assert.ok(SRC.includes("from './wa-watchdog.mjs'"), 'must import wa-watchdog');
    assert.ok(SRC.includes('watchdogAction(Date.now()'), 'the watchdog must consult watchdogAction()');
  });

  it('tracks inbound activity from real socket events', () => {
    assert.ok(SRC.includes('function _markInbound()'), 'must define _markInbound');
    // messages.upsert (the core), plus the liveness-only taps that keep a quiet socket fresh
    const upsertHandler = SRC.slice(
      SRC.indexOf('async function onMessagesUpsert('),
      SRC.indexOf('async function onMessagesUpsert(') + 240,
    );
    assert.ok(/async function onMessagesUpsert\([^)]*\)\s*\{\s*_markInbound\(\)/.test(upsertHandler),
      'the real messages.upsert handler must mark inbound before inspecting the event');
    assert.ok(SRC.includes("sock.ev.on('message-receipt.update', () => _markInbound())"), 'receipts must mark inbound');
    assert.ok(SRC.includes("sock.ev.on('presence.update',        () => _markInbound())"), 'presence must mark inbound');
  });

  it('routes every non-logout restart through the guarded, deduped path', () => {
    assert.ok(SRC.includes('function _restartListener('), 'must define _restartListener');
    assert.ok(SRC.includes('if (_restartArmed)'), 'must guard against a duplicate concurrent restart');
    assert.ok(SRC.includes("_restartListener(`disconnect code ${code}`"), 'close-handler must use the guarded path');
    assert.ok(!SRC.includes('setTimeout(startListener, reconnectDelay)'), 'the racy bare setTimeout must be gone');
    assert.ok(SRC.includes('_restartArmed = false'), "'open' must clear the restart flag so the next restart can arm");
  });

  it('tightens the Baileys keepalive so a dead link is detected faster', () => {
    assert.ok(SRC.includes('keepAliveIntervalMs: 15000'), 'keepAliveIntervalMs must be tightened from the 30s default');
  });
});
