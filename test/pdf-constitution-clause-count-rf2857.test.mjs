// test/pdf-constitution-clause-count-rf2857.test.mjs
//
// R-F2857 — the customer-facing audit-grade PDF asserted a FALSE governance fact.
//
// `generateAuditGradeReport` printed, in its "Constitution discipline" section:
//     "ARIA operates under a 23-clause constitution that constrains output."
// The live behavioural constitution is v37 / 37 clauses
// (GET /api/aria/constitution/version -> {"version":"v37","clause_count":37}).
// So every DD PDF issued understated the constitution by 14 clauses, in the exact
// section a compliance officer reads to assess governance.
//
// This is the same defect class R-F221/R-F2617 fixed for public/model-card.html
// (which now hydrates the count from the live endpoint) — left unfixed in the
// artefact that actually reaches customers.
//
// ROOT CAUSE, NOT SYMPTOM (CLAUDE.md §1): swapping 23 -> 37 would drift again on
// the next amendment. The count must be DERIVED from the caller's live reading,
// and when it is unknown the PDF must say NOTHING about a count rather than
// assert one — ARIA's own USP is never presenting an unverified value as fact.
//
// Run: node --test test/pdf-constitution-clause-count-rf2857.test.mjs
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { generateAuditGradeReport } from '../lib/reports/pdf_generator.mjs';
const require = createRequire(import.meta.url);
const pdfParse = require('pdf-parse');

const BASE = { subject: 'Counterparty DD', userId: 'u1', sessionId: 'u1_1', messageIndex: 1 };

async function renderText(metadata = {}) {
  const pdf = await generateAuditGradeReport(
    'Counterparty screening summary for Example Entity Ltd.',
    { ...BASE, ...metadata },
    { classification: 'CONFIDENTIAL' },
  );
  const { text } = await pdfParse(pdf);
  return text;
}

describe('R-F2857 — the DD PDF must not assert an unverified constitution clause count', () => {
  it('never prints the stale hardcoded "23-clause" claim', async () => {
    const text = await renderText();
    assert.ok(
      !/23-clause/i.test(text),
      'THE BUG: the PDF still asserts a hardcoded 23-clause constitution (live is 37)',
    );
  });

  it('states the real count when the caller supplies the live reading', async () => {
    const text = await renderText({ constitutionClauseCount: 37 });
    assert.ok(
      /37-clause/.test(text),
      'a supplied live clause count must be reflected verbatim in the PDF',
    );
  });

  it('tracks the live value rather than a second hardcoded constant', async () => {
    // NEGATIVE CONTROL: if someone "fixes" this by hardcoding 37, this fails.
    const text = await renderText({ constitutionClauseCount: 41 });
    assert.ok(
      /41-clause/.test(text),
      'the count must be derived from the caller, not pinned to any constant',
    );
    assert.ok(!/37-clause/.test(text), 'a stale 37 must not survive alongside the live value');
  });

  it('the PDF route supplies the count from the LIVE endpoint, not a literal', async () => {
    // Source-level assertion, same technique as R-F2617's model-card test: the
    // render contract above cannot see who fills the field, so pin the wiring.
    const { readFileSync } = await import('node:fs');
    const src = readFileSync(new URL('../lib/reports/routes.mjs', import.meta.url), 'utf8');
    assert.match(src, /constitutionClauseCount:\s*await liveClauseCount\(\)/,
      'the route must pass a live reading into the PDF metadata');
    assert.match(src, /constitution\/version/,
      'the live reading must come from the constitution/version endpoint');
    assert.ok(!/constitutionClauseCount:\s*\d+/.test(src),
      'the route must never pin the clause count to a literal');
    // A transient failure must not be cached — that would pin the count absent.
    assert.match(src, /cache successes only/,
      'only successful reads may be cached');
  });

  it('asserts NO count at all when the live value is unavailable', async () => {
    // Honest degradation: an unknown count must not become a guessed one.
    const text = await renderText({ constitutionClauseCount: undefined });
    assert.ok(
      !/\d+-clause/.test(text),
      'with no live reading the PDF must omit the count, never invent one',
    );
    assert.ok(
      /constitution/i.test(text),
      'the Constitution discipline section must still be present',
    );
  });
});
