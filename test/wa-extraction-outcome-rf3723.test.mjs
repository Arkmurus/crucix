// test/wa-extraction-outcome-rf3723.test.mjs
//
// Capability test for R-F3723 — WhatsApp document-extraction degradation must
// reach the brain, and the extractor libraries must be DECLARED.
//
// Symptom (Cure Protocol census, defects.md C-03, 2026-08-05):
// lib/whatsapp/waListener.mjs loads its three document extractors as dynamic
// imports that degrade to null:
//     :3050  await import('pdf-parse').then(m => m.default).catch(() => null)
//     :3068  await import('mammoth').then(m => m.default).catch(() => null)
//     :3095  await import('xlsx').then(m => m.default || m).catch(() => null)
//
// They ARE declared — in `optionalDependencies` (package.json:64-74). An earlier
// draft of this test claimed they were undeclared; that was wrong, and the
// audit that produced the claim had only read `dependencies`/`devDependencies`.
// Being optional is correct and deliberate: `npm install` does not fail when
// they cannot be built, so they are genuinely absent at runtime sometimes —
// which is exactly why the `.catch(() => null)` guards exist.
//
// The real defect is therefore observability, not declaration. When extraction
// degrades, every branch reports to console.warn ONLY. Per CLAUDE.md §21a a
// console line is DARK, not wired — so the brain cannot know ARIA answered a
// document question from FILENAME METADATA instead of the document, and §25
// proprioception cannot answer "did I actually read that file?". A dependency
// that is *expected* to go missing makes the missing wire worse, not better.
//
// This test asserts the honest outcome classification and that each degraded
// branch is wired to /api/aria/brain/signal.
//
// Run: node --test test/wa-extraction-outcome-rf3723.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

import { classifyExtractionOutcome } from '../lib/whatsapp/waListener.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const SRC = readFileSync(join(ROOT, 'lib', 'whatsapp', 'waListener.mjs'), 'utf8');

// ── 1. The pure classifier — the function that was missing ─────────────────

test('a missing extractor module is reported as module_missing, not success', () => {
  const r = classifyExtractionOutcome({
    fileType: 'pdf', moduleMissing: true, extractedChars: 0,
  });
  assert.equal(r.outcome, 'module_missing');
  assert.equal(r.degraded, true);
  assert.match(r.reason, /pdf/i);
});

test('extraction yielding nothing usable is metadata_only, and degraded', () => {
  const r = classifyExtractionOutcome({
    fileType: 'docx', moduleMissing: false, extractedChars: 0,
  });
  assert.equal(r.outcome, 'metadata_only');
  assert.equal(r.degraded, true);
});

test('a real extraction is not degraded', () => {
  const r = classifyExtractionOutcome({
    fileType: 'pdf', moduleMissing: false, extractedChars: 5000,
  });
  assert.equal(r.outcome, 'extracted');
  assert.equal(r.degraded, false);
});

test('the 50-char usable-text threshold matches the caller', () => {
  // waListener treats <= 50 chars as "metadata only" at the content: line.
  // If these disagree the brain is told a different story than the user gets.
  assert.equal(
    classifyExtractionOutcome({ fileType: 'pdf', moduleMissing: false, extractedChars: 50 }).degraded,
    true,
  );
  assert.equal(
    classifyExtractionOutcome({ fileType: 'pdf', moduleMissing: false, extractedChars: 51 }).degraded,
    false,
  );
});

test('classification never throws on malformed input', () => {
  // It runs on a user-controlled document path; a throw here would take out
  // the whole message handler.
  for (const bad of [undefined, null, {}, { extractedChars: NaN }, { fileType: 42 }]) {
    assert.doesNotThrow(() => classifyExtractionOutcome(bad));
  }
});

// ── 2. The wiring — console.warn alone is DARK per §21a ────────────────────

test('every degraded extraction branch reports to the brain', () => {
  // The reporter must exist and must post to the brain signal sink.
  assert.match(SRC, /async function reportExtractionOutcome/,
    'reportExtractionOutcome() must exist');
  const reporter = SRC.slice(SRC.indexOf('async function reportExtractionOutcome'));
  assert.match(reporter.slice(0, 2500), /\/api\/aria\/brain\/signal/,
    'the reporter must POST to /api/aria/brain/signal, not just console.warn');
  assert.match(reporter.slice(0, 2500), /capability_gap/,
    'degradation must be routed to capability_gaps so the coder can act on it');
});

test('the pdf and docx branches handle a missing module explicitly', () => {
  // Before R-F3723 the xlsx branch had an `else` that warned, but the pdf and
  // mammoth branches had NO else at all — a missing module there was not even
  // logged, let alone signalled.
  const pdfIdx = SRC.indexOf("import('pdf-parse')");
  const mamIdx = SRC.indexOf("import('mammoth')");
  assert.ok(pdfIdx > 0 && mamIdx > 0, 'both dynamic imports still present');
  assert.match(SRC.slice(pdfIdx, pdfIdx + 1400), /reportExtractionOutcome/,
    'the pdf-parse branch must report when the module is unavailable');
  assert.match(SRC.slice(mamIdx, mamIdx + 1400), /reportExtractionOutcome/,
    'the mammoth branch must report when the module is unavailable');
});

test('the metadata-only outcome is reported', () => {
  // The user-visible degradation: ARIA answers from the filename.
  const idx = SRC.indexOf('metadata only');
  assert.ok(idx > 0, 'the metadata-only path still exists');
  assert.match(SRC.slice(Math.max(0, idx - 1200), idx + 1200), /reportExtractionOutcome/,
    'reaching the metadata-only outcome must signal the brain');
});

// ── 3. The dependencies must be DECLARED, not hoisted ──────────────────────

test('pdf-parse, mammoth and xlsx stay declared as OPTIONAL dependencies', () => {
  // They are declared in `optionalDependencies` (package.json:64-74), which is
  // the correct choice: npm install does NOT fail when they cannot be built, so
  // they are legitimately absent at runtime sometimes. That is precisely WHY
  // the `.catch(() => null)` guards exist and why the degraded path must be
  // wired to the brain rather than merely logged.
  //
  // This guards the declaration without asserting the wrong home for it.
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
  const declared = {
    ...(pkg.dependencies || {}),
    ...(pkg.devDependencies || {}),
    ...(pkg.optionalDependencies || {}),
    ...(pkg.peerDependencies || {}),
  };
  for (const dep of ['pdf-parse', 'mammoth', 'xlsx']) {
    assert.ok(declared[dep], `${dep} is imported by lib/whatsapp/waListener.mjs but declared nowhere`);
  }
  for (const dep of ['pdf-parse', 'mammoth', 'xlsx']) {
    assert.ok((pkg.optionalDependencies || {})[dep],
      `${dep} must stay in optionalDependencies — promoting it to a hard dependency ` +
      'would make a failed native build break the whole aria-web install');
  }
});
