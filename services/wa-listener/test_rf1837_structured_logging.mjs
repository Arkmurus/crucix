/**
 * R-F1837 — aria-wa structured logging (pino).
 * ═══════════════════════════════════════════════════════════════════════════
 * The listener had 85 unstructured console.* calls; pino was a dependency but
 * only used as Baileys' silent logger. This wires a real structured app logger
 * and routes console.* through it so every line of aria-wa output is queryable
 * JSON (service tag + ISO timestamp + level), matching the rest of the ecosystem.
 *
 * The listener boots the WA socket on import and is not unit-invokable in
 * isolation (mirrors test_rf1564 / test_rf1551), so these are source-assertion
 * tests proving the logger is configured and console.* is routed through it.
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SOURCE_PATH = path.resolve('services/wa-listener/aria_wa_listener.mjs');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');

describe('R-F1837 aria-wa structured logging', () => {
  it('creates a structured app logger with a service tag + ISO timestamps', () => {
    assert.match(source, /const log = pino\(\{/, 'a structured app logger `log` must exist');
    assert.match(source, /base:\s*\{\s*service:\s*'aria-wa'\s*\}/, 'logs must carry service: aria-wa');
    assert.match(source, /timestamp:\s*pino\.stdTimeFunctions\.isoTime/, 'logs must use ISO timestamps');
  });

  it('level is env-tunable via WA_LOG_LEVEL (default info)', () => {
    assert.match(source, /process\.env\.WA_LOG_LEVEL\s*\|\|\s*'info'/);
  });

  it('routes every console.* method through pino', () => {
    for (const m of ['log', 'info', 'warn', 'error', 'debug']) {
      const re = new RegExp(`console\\.${m}\\s*=\\s*\\(\\.\\.\\.a\\)\\s*=>\\s*log\\.`);
      assert.match(source, re, `console.${m} must delegate to the structured logger`);
    }
    // error/warn must map to the matching pino levels, not be flattened to info.
    assert.match(source, /console\.error\s*=\s*\(\.\.\.a\)\s*=>\s*log\.error\(/);
    assert.match(source, /console\.warn\s*=\s*\(\.\.\.a\)\s*=>\s*log\.warn\(/);
  });

  it('keeps the Baileys logger silent (separate from the app logger)', () => {
    assert.match(source, /const logger = pino\(\{ level: 'silent' \}\)/,
      'Baileys must keep its own silent logger');
  });

  it('does NOT route the QR code through the JSON logger (must stay scannable)', () => {
    // QR is printed straight to stdout via qrcode.generate, never console.*
    assert.match(source, /qrcode\.generate\(qr/, 'QR must be printed via qrcode.generate');
    assert.doesNotMatch(source, /console\.log\(qr/, 'QR must not go through the JSON logger');
  });

  it('Error objects are folded to their stack, single object args passed through', () => {
    assert.match(source, /a instanceof Error \? \(a\.stack \|\| a\.message\)/);
    assert.match(source, /args\.length === 1 && args\[0\] !== null && typeof args\[0\] === 'object'/);
  });
});
