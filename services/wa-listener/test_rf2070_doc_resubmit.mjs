/**
 * R-F2070 — WA document-read robustness capability tests.
 * ═══════════════════════════════════════════════════════════════════════════
 * Drives the ACTUAL broken path: runDocWithResubmit (the auto-resubmit logic that
 * readDocumentAsync now wraps every extraction in) with fake attempt functions,
 * plus source-assertions that the listener's two doc-FAILURE branches suppress the
 * caption re-route (the double-error the operator saw) and that the new sibling
 * module is COPY'd into the image (the recurring ERR_MODULE_NOT_FOUND trap).
 *
 * Run: node --test services/wa-listener/test_rf2070_doc_resubmit.mjs
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { runDocWithResubmit, isDiedRetryable } from './doc-resubmit.mjs';

const noSleep = () => Promise.resolve();   // don't actually wait in tests

describe('R-F2070 — runDocWithResubmit auto-resubmits a DIED extraction once', () => {

  it('resubmits once when the first attempt DIES, returns the retry result', async () => {
    let calls = 0;
    const result = await runDocWithResubmit(async (attempt) => {
      calls++;
      if (attempt === 0) throw new Error('extraction failed');   // died → resubmit
      return { extracted_text: 'Clause 1 Indemnity', ok: true };
    }, { sleep: noSleep });
    assert.equal(calls, 2, 'attemptFn must be called twice (original + one resubmit)');
    assert.equal(result.ok, true, 'the successful resubmit result is returned');
  });

  it('does NOT resubmit a clean timeout (job still grinding server-side)', async () => {
    let calls = 0;
    await assert.rejects(
      runDocWithResubmit(async () => {
        calls++;
        throw new Error('extraction timed out after 15 minutes');
      }, { sleep: noSleep }),
      /timed out/,
    );
    assert.equal(calls, 1, 'a clean timeout must NOT trigger a resubmit');
  });

  it('gives up after one resubmit if the retry also dies (no infinite loop)', async () => {
    let calls = 0;
    await assert.rejects(
      runDocWithResubmit(async () => {
        calls++;
        throw new Error('extraction job expired');
      }, { sleep: noSleep }),
      /job expired/,
    );
    assert.equal(calls, 2, 'at most two attempts total');
  });

  it('returns immediately on first-attempt success (no wasted resubmit)', async () => {
    let calls = 0;
    const r = await runDocWithResubmit(async () => { calls++; return { ok: true }; }, { sleep: noSleep });
    assert.equal(calls, 1);
    assert.equal(r.ok, true);
  });

  it('classifies all three died-job messages as retryable, timeout as NOT', () => {
    assert.equal(isDiedRetryable('extraction failed'), true);
    assert.equal(isDiedRetryable('extraction job expired'), true);
    assert.equal(isDiedRetryable('brain unreachable during doc poll'), true);
    assert.equal(isDiedRetryable('extraction timed out after 15 minutes'), false);
    assert.equal(isDiedRetryable(''), false);
    assert.equal(isDiedRetryable(undefined), false);
  });

  it('waits between attempts via the injected sleep (backoff for a restarting brain)', async () => {
    let slept = 0;
    await runDocWithResubmit(async (attempt) => {
      if (attempt === 0) throw new Error('brain unreachable during doc poll');
      return { ok: true };
    }, { sleep: async (ms) => { slept = ms; }, backoffMs: 3000 });
    assert.equal(slept, 3000, 'backoff sleep must fire before the resubmit');
  });
});

describe('R-F2070 — double-error suppression + image-COPY wiring (source)', () => {
  const SRC = fs.readFileSync(path.resolve('services/wa-listener/aria_wa_listener.mjs'), 'utf-8');

  it('the failed-read branch sets _docAnsweredCaption (no documentless chat re-route)', () => {
    // The honest "document service didn't respond" reply is followed by the flag.
    assert.ok(
      /document service didn't respond[\s\S]{0,800}?_docAnsweredCaption = true/.test(SRC),
      'the null-result failure branch must set _docAnsweredCaption = true',
    );
  });

  it('the catch branch also suppresses the caption re-route', () => {
    assert.ok(
      /I couldn't process \*\$\{filename\}\*[\s\S]{0,400}?_docAnsweredCaption = true/.test(SRC),
      'the document catch branch must set _docAnsweredCaption = true',
    );
  });

  it('readDocumentAsync routes every extraction through runDocWithResubmit', () => {
    assert.ok(/runDocWithResubmit\(\s*\(\)\s*=>\s*_submitAndPollDoc\(/.test(SRC),
      'readDocumentAsync must wrap _submitAndPollDoc in runDocWithResubmit');
  });

  it('the "Reading" ack is gated so it is sent at most once across resubmits', () => {
    assert.ok(/if \(!ack\.sent\) \{[\s\S]{0,200}?ack\.sent = true;[\s\S]{0,200}?📥 Reading/.test(SRC),
      'the Reading ack must be guarded by ack.sent');
  });

  it('doc-resubmit.mjs is COPY-ed into the WA image (ERR_MODULE_NOT_FOUND guard)', () => {
    const dockerfile = fs.readFileSync(path.resolve('Dockerfile.wa'), 'utf-8');
    assert.ok(/COPY services\/wa-listener\/doc-resubmit\.mjs/.test(dockerfile),
      'Dockerfile.wa must COPY doc-resubmit.mjs (sibling-import trap)');
  });
});
