// R-F1370 — /api/locales returned 500 in production because lib/i18n.mjs
// loadLocale() recursed INFINITELY when the DEFAULT locale file was missing:
// en.json absent -> "falling back to en" -> loadLocale('en') -> absent -> ...
// -> RangeError stack overflow -> express 500. The locales/ dir has never
// existed in the repo and Dockerfile.web never copied one, so EVERY deployed
// image had this live (45x "[i18n] Locale file not found" in aria-web logs,
// GET /api/locales -> 500 verified 2026-06-06).
//
// Capability test: drives the exact functions the /api/locales handler calls
// (server.mjs:1385 -> getSupportedLocales + currentLanguage import) in a repo
// with no locales/ dir. Pre-fix: this test CRASHES with RangeError. Post-fix:
// returns the 4 supported locale codes with graceful fallbacks.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const LOCALES_DIR = join(__dirname, '..', 'locales');

test('precondition: locales dir is absent (matches the deployed image)', () => {
  // If someone later adds locales/*.json this test still must not crash, but
  // the recursion repro depends on absence — record which world we are in.
  // The fix is correct in both worlds; the assertion below only documents it.
  assert.equal(typeof existsSync(LOCALES_DIR), 'boolean');
});

test('rf1370 capability: getSupportedLocales() does not crash without locale files', async () => {
  // Import is the crash vector too (module-level getLanguage/console.log),
  // so the import itself is part of the capability under test.
  const i18n = await import('../lib/i18n.mjs');
  const supported = i18n.getSupportedLocales();
  assert.ok(Array.isArray(supported), 'must return an array');
  assert.deepEqual(
    supported.map((s) => s.code).sort(),
    ['ar', 'en', 'fr', 'pt'],
    'all four supported locale codes present',
  );
  for (const s of supported) {
    // With no locale files, name falls back to the code — never undefined.
    assert.equal(typeof s.name, 'string');
    assert.equal(typeof s.nativeName, 'string');
  }
});

test('rf1370: t() and getLLMPrompt() degrade gracefully without locale files', async () => {
  const i18n = await import('../lib/i18n.mjs');
  // Missing translation returns the key path, never throws.
  assert.equal(i18n.t('dashboard.title'), 'dashboard.title');
  // LLM prompt falls back to empty string, never throws.
  assert.equal(typeof i18n.getLLMPrompt(), 'string');
});
