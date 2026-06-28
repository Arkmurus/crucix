/**
 * R-F2070 — auto-resubmit a DIED document extraction once.
 * ═══════════════════════════════════════════════════════════════════════════
 * Extracted as a standalone, importable module (mirrors R-F2069 send-retry.mjs)
 * so the retry decision is capability-testable with fakes — the WA listener
 * itself boots a live socket on import and is not unit-invokable.
 *
 * Failure class this fixes (2026-06-28, Korvera redline): the WhatsApp document
 * path submitted an extraction job, the brain then had a transient blip / restart
 * (or the job hit the new R-F2070 brain-side 600s cap and FAILED), the listener's
 * poll threw, and the WHOLE document was dropped — the user was told "document
 * service didn't respond — resend". Since the listener STILL HOLDS the bytes, a
 * died job should be resubmitted automatically instead of pushing the work back
 * onto the user.
 *
 * Discipline: only a *died* job is resubmitted. A clean 15-min poll TIMEOUT means
 * the job is still grinding server-side — resubmitting would just double the load,
 * so it is re-thrown unchanged.
 */

// A died job leaves one of these messages (thrown by _submitAndPollDoc):
//   'extraction failed'              — brain marked the job failed (incl. R-F2070 cap)
//   'extraction job expired'         — 3 consecutive not_found (store blip / restart)
//   'brain unreachable during doc poll' — 3 consecutive health-check failures
// A clean 'extraction timed out after 15 minutes' is deliberately NOT matched.
export function isDiedRetryable(message) {
  return /extraction failed|job expired|brain unreachable/i.test(message || '');
}

/**
 * Run `attemptFn(attempt)` up to `maxAttempts` times. Resubmit ONLY when the
 * failure is a died-job (isDiedRetryable) and attempts remain; otherwise re-throw.
 * `sleep` and `log` are injectable for tests.
 *
 * @param {(attempt:number)=>Promise<any>} attemptFn  the submit+poll attempt
 * @param {object} [opts]
 * @returns {Promise<any>} the successful attempt's result
 */
export async function runDocWithResubmit(attemptFn, opts = {}) {
  const {
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
    maxAttempts = 2,
    backoffMs = 3000,
    log = () => {},
  } = opts;
  let lastErr = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await attemptFn(attempt);
    } catch (e) {
      lastErr = e;
      const msg = (e && e.message) || '';
      if (!isDiedRetryable(msg) || attempt >= maxAttempts - 1) throw e;
      log(`R-F2070 doc extraction died (${msg}) — auto-resubmitting once`);
      await sleep(backoffMs);
    }
  }
  throw lastErr;
}
