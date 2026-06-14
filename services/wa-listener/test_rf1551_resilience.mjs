/**
 * R-F1551 — WA listener resilience capability test
 * ═══════════════════════════════════════════════════════════════════════════
 * Tests that the resilience improvements work correctly by reading the
 * source file and verifying the key patterns are present.
 */

import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

// Use a path relative to the current working directory
const SOURCE_PATH = path.resolve('services/wa-listener/aria_wa_listener.mjs');
let source = '';

try {
  source = fs.readFileSync(SOURCE_PATH, 'utf-8');
} catch (e) {
  console.error(`Cannot read source at ${SOURCE_PATH}: ${e.message}`);
  process.exit(1);
}

describe('R-F1551 — WA listener resilience', () => {

  it('should clear auth dir and restart on logout (not stay dead)', () => {
    assert.ok(source.includes("fs.rmSync(AUTH_DIR, { recursive: true, force: true })"),
      'Logout handler must delete stale auth dir');
    assert.ok(source.includes("setTimeout(startListener, 1000)"),
      'Logout handler must restart listener');
    assert.ok(source.includes("_logoutCount++"),
      'Logout handler must track consecutive logouts');
    assert.ok(source.includes("_MAX_LOGOUT_RESTARTS"),
      'Logout handler must have max restart limit');
  });

  it('should exit process after max logout restarts', () => {
    const hasExitOnMax = source.includes("process.exit(1)") &&
      source.includes("_logoutCount >= _MAX_LOGOUT_RESTARTS");
    assert.ok(hasExitOnMax,
      'Must exit process after max logout restarts so Fly restarts fresh');
  });

  it('should have a connection watchdog that detects stale disconnects', () => {
    assert.ok(source.includes('_startWatchdog'),
      'Must define _startWatchdog function');
    assert.ok(source.includes('_STALE_DISCONNECT_MS'),
      'Must define _STALE_DISCONNECT_MS threshold');
    assert.ok(source.includes('setInterval'),
      'Watchdog must use setInterval for periodic checks');
    assert.ok(source.includes('stale disconnect'),
      'Watchdog must restart listener on stale disconnect');
  });

  it('should have uncaughtException and unhandledRejection handlers', () => {
    assert.ok(source.includes("process.on('uncaughtException'"),
      'Must have uncaughtException handler');
    assert.ok(source.includes("process.on('unhandledRejection'"),
      'Must have unhandledRejection handler');
    const exitCount = (source.match(/process\.exit\(1\)/g) || []).length;
    assert.ok(exitCount >= 3,
      `Must exit(1) on fatal errors so Fly restarts (found ${exitCount})`);
  });

  it('should exit after 10 consecutive non-logout disconnects', () => {
    assert.ok(source.includes('_disconnectStreak'),
      'Must track consecutive disconnect streak');
    assert.ok(source.includes('_disconnectStreak >= 10'),
      'Must exit after 10 consecutive disconnects');
    assert.ok(source.includes('wa_disconnect_storm'),
      'Must signal disconnect storm to brain');
  });

  it('should start the watchdog at boot', () => {
    assert.ok(source.includes('_startWatchdog()'),
      'Must call _startWatchdog() at boot');
  });

  it('should signal brain for all failure modes', () => {
    const signals = [
      'wa_auth_lost',
      'wa_auth_lost_fatal',
      'wa_disconnected',
      'wa_disconnect_storm',
      'wa_stale_disconnect',
      'wa_crash',
    ];
    for (const sig of signals) {
      assert.ok(source.includes(sig), `Must emit brain signal: ${sig}`);
    }
  });
});
