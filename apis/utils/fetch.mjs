// Shared fetch utility with timeout, retries, and error handling

export async function safeFetch(url, opts = {}) {
  const { timeout = 15000, retries = 1, headers = {} } = opts;
  let lastError;
  for (let i = 0; i <= retries; i++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      const res = await fetch(url, {
        signal: controller.signal,
        headers: { 'User-Agent': 'Crucix/1.0', ...headers },
      });
      clearTimeout(timer);
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`);
      }
      const text = await res.text();
      try { return JSON.parse(text); } catch { return { rawText: text.slice(0, 500) }; }
    } catch (e) {
      lastError = e;
      // GDELT needs 5s between requests, others are fine with shorter delays
      if (i < retries) await new Promise(r => setTimeout(r, 2000 * (i + 1)));
    }
  }
  return { error: lastError?.message || 'Unknown error', source: url };
}

// ── robustFetch — R-F515 ────────────────────────────────────────────────────
//
// Per-host-gated fetch wrapper for the 48-source sweep. The plain `fetch()`
// scattered across `apis/sources/` shares a single global undici dispatcher;
// when `briefing.mjs` fires all sources in one Promise.allSettled, unrelated
// upstream domains (Polymarket, NVD, Polygon, AU PSC, GRIP, EU TED, World
// Bank, UK GOV) fail in lockstep with `fetch failed` / `UND_ERR_CONNECT_TIMEOUT`
// — connection-pool / DNS-resolver pressure, not per-source breakage.
// Live evidence 2026-05-14 09:25–09:40 sweeps.
//
// `robustFetch` adds three things on top of `fetch()`:
//   1) per-host in-flight cap (queues excess requests instead of stampeding)
//   2) retry with exponential backoff + jitter on transient transport errors
//      (ENOTFOUND, ECONNRESET, UND_ERR_CONNECT_TIMEOUT, AbortError on timeout)
//   3) fail-fast on rate-limit / forbidden status codes (no retry on 429/403/451)
//
// Returns the raw Response — caller decides how to parse and what to do with
// non-2xx. Throws on terminal transport failure (matches plain `fetch`
// semantics so existing try/catch in callers keeps working).
//
// Migration target: bare `fetch()` calls that go to a single host and would
// benefit from coordinated burst control. Don't migrate aggregators that
// already have hand-tuned fallback chains (Lusophone, ProcurementTenders).

const _hostInFlight = new Map(); // host → { active, max, waiters: [resolve, ...] }

function _acquireHostSlot(host, max) {
  let entry = _hostInFlight.get(host);
  if (!entry) {
    entry = { active: 0, max, waiters: [] };
    _hostInFlight.set(host, entry);
  }
  if (entry.active < entry.max) {
    entry.active++;
    return Promise.resolve();
  }
  return new Promise(resolve => entry.waiters.push(resolve));
}

function _releaseHostSlot(host) {
  const entry = _hostInFlight.get(host);
  if (!entry) return;
  if (entry.waiters.length > 0) {
    // Hand the slot directly to the next waiter; `active` stays the same.
    entry.waiters.shift()();
    return;
  }
  entry.active--;
  if (entry.active <= 0) _hostInFlight.delete(host);
}

const _RETRYABLE_CODES = new Set([
  'ENOTFOUND', 'ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EAI_AGAIN',
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET', 'UND_ERR_HEADERS_TIMEOUT',
  'AbortError', 'TimeoutError',
]);

const _FAIL_FAST_STATUS = new Set([400, 401, 403, 404, 429, 451]);

function _errorCode(err) {
  if (!err) return 'unknown';
  const cause = (err.cause && typeof err.cause === 'object') ? err.cause : {};
  return cause.code || cause.errno || err.code || err.name || 'unknown';
}

function _isRetryable(err, retryOn) {
  return retryOn.has(_errorCode(err));
}

function _backoff(attempt, baseMs) {
  // Exponential with full-jitter: random in [baseMs * 2^attempt, baseMs * 2^(attempt+1)]
  const ceiling = baseMs * Math.pow(2, attempt + 1);
  const floor = baseMs * Math.pow(2, attempt);
  const delay = floor + Math.random() * (ceiling - floor);
  return new Promise(r => setTimeout(r, delay));
}

// _testReset() — clears the per-host in-flight table. Test-only.
export function _testResetRobustFetch() {
  _hostInFlight.clear();
}

// _testInspectHost(host) — returns the current {active, max, waiters} for a host
// (or undefined). Test-only.
export function _testInspectHost(host) {
  const entry = _hostInFlight.get(host);
  if (!entry) return undefined;
  return { active: entry.active, max: entry.max, waiters: entry.waiters.length };
}

export async function robustFetch(url, opts = {}) {
  const {
    timeout = 15000,
    retries = 2,
    retryBackoffMs = 500,
    headers = {},
    maxConcurrentPerHost = 3,
    retryOn = _RETRYABLE_CODES,
    failFastStatus = _FAIL_FAST_STATUS,
    signal: externalSignal,
    method = 'GET',
    body,
  } = opts;

  let host;
  try { host = new URL(url).hostname; } catch { host = '__invalid__'; }

  await _acquireHostSlot(host, maxConcurrentPerHost);
  try {
    let lastErr;
    for (let attempt = 0; attempt <= retries; attempt++) {
      // Compose abort signals — timeout AND any externally-supplied signal.
      const timeoutSignal = AbortSignal.timeout(timeout);
      const combined = externalSignal
        ? AbortSignal.any([timeoutSignal, externalSignal])
        : timeoutSignal;
      try {
        const res = await fetch(url, {
          method,
          body,
          signal: combined,
          headers: { 'User-Agent': 'Crucix/1.0', ...headers },
        });
        if (failFastStatus.has(res.status)) {
          // Don't retry — hand back so caller can read the body / decide.
          return res;
        }
        if (res.status >= 500 && attempt < retries) {
          lastErr = new Error(`HTTP ${res.status}`);
          await _backoff(attempt, retryBackoffMs);
          continue;
        }
        return res;
      } catch (err) {
        lastErr = err;
        // External abort is terminal — don't retry, propagate.
        if (externalSignal?.aborted) throw err;
        if (!_isRetryable(err, retryOn) || attempt >= retries) throw err;
        await _backoff(attempt, retryBackoffMs);
      }
    }
    // Unreachable in practice — the loop either returns or throws.
    throw lastErr || new Error('robustFetch: exhausted retries');
  } finally {
    _releaseHostSlot(host);
  }
}

export function ago(hours) {
  return new Date(Date.now() - hours * 3600000).toISOString();
}

export function today() {
  return new Date().toISOString().split('T')[0];
}

export function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}
