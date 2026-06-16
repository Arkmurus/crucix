// test/web-async-chat-rf1615.test.mjs
//
// BEHAVIORAL capability test for R-F1615 — web chat async-complete-and-push.
//
// The /api/aria/chat handler in server.mjs used to hold ONE synchronous fetch
// open for up to 600s while the brain crawled + synthesised. A brain
// deploy/restart mid-answer tore that single connection down → the browser got
// a 502. R-F1615 switches the handler to the brain's async job protocol
// (POST async_mode:true → job_id → poll /chat/result/{job_id}), mirroring the
// proven WA listener path. This test drives the SHIPPING _ariaChatAsyncPoll
// function against a REAL local fake-brain HTTP server implementing the exact
// async contract and asserts the real outcomes.
//
// Why `new Function` extraction: server.mjs is a self-booting monolith (express
// listen + cron + Stripe) with no exports, so it can't be imported in a test
// without standing up the whole app. We lift the EXACT function source out of
// server.mjs and execute it with the same deps (fetch, ARIA_SERVICE_URL,
// _ariaHeaders) it uses in production — this runs the real code, not a copy.
//
// Run: node test/web-async-chat-rf1615.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import http from 'node:http';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

// ── Lift the real _ariaChatAsyncPoll source out of server.mjs ────────────────
function extractFn(name) {
  const start = SRC.indexOf(`async function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found in server.mjs`);
  // Walk braces from the first '{' after the signature to find the matching end.
  const open = SRC.indexOf('{', start);
  let depth = 0, i = open;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  return SRC.slice(start, i);
}

// Build a callable from the extracted source. We inject ARIA_SERVICE_URL and a
// no-op _ariaHeaders as locals so the function closes over the test's brain URL.
function buildPoll(brainUrl) {
  const fnSrc = extractFn('_ariaChatAsyncPoll');
  // The function references _ariaHeaders and ARIA_SERVICE_URL from module scope;
  // provide them, plus fetch, via a factory.
  const factory = new Function(
    'ARIA_SERVICE_URL', '_ariaHeaders', 'fetch', 'process',
    `${fnSrc}\nreturn _ariaChatAsyncPoll;`
  );
  return factory(
    brainUrl,
    () => ({ 'Content-Type': 'application/json' }),
    globalThis.fetch,
    { env: {} }   // default 600s budget
  );
}

// ── Fake brain implementing the async contract ──────────────────────────────
// behaviour: a tunable script keyed by job_id polls.
function makeBrain(opts = {}) {
  const {
    jobId = 'job12345678',
    pollsBeforeDone = 1,        // how many 'processing' polls before 'done'
    finalStatus = 'done',       // 'done' | 'failed'
    answer = 'Here is the real briefing.',
    failError = 'boom',
    blipPolls = 0,              // first N polls return 500 (mid-deploy blip)
    notFoundPolls = 0,          // first N polls return not_found
    dispatchStatus = 200,       // dispatch HTTP status
    dispatchBody = null,        // override dispatch JSON
  } = opts;
  let polls = 0;
  const seen = { dispatched: null };

  const server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/api/aria/chat') {
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        try { seen.dispatched = JSON.parse(body); } catch { seen.dispatched = body; }
        res.statusCode = dispatchStatus;
        res.setHeader('Content-Type', 'application/json');
        if (dispatchStatus !== 200) { res.end('dispatch error'); return; }
        res.end(JSON.stringify(dispatchBody || {
          async: true, job_id: jobId, status: 'processing',
          poll_url: `/api/aria/chat/result/${jobId}`,
        }));
      });
      return;
    }
    if (req.method === 'GET' && req.url === `/api/aria/chat/result/${jobId}`) {
      polls++;
      res.setHeader('Content-Type', 'application/json');
      if (polls <= blipPolls) { res.statusCode = 500; res.end('blip'); return; }
      const idx = polls - blipPolls;
      if (idx <= notFoundPolls) { res.end(JSON.stringify({ status: 'not_found', job_id: jobId })); return; }
      const realIdx = idx - notFoundPolls;
      if (realIdx <= pollsBeforeDone) { res.end(JSON.stringify({ status: 'processing', job_id: jobId })); return; }
      if (finalStatus === 'failed') {
        res.end(JSON.stringify({ status: 'failed', job_id: jobId, error: failError }));
      } else {
        res.end(JSON.stringify({
          status: 'done', job_id: jobId,
          result: { response: answer, session_id: 'sid', sources: [], confidence: 0.9 },
        }));
      }
      return;
    }
    res.statusCode = 404; res.end('nope');
  });
  return { server, seen, getPolls: () => polls };
}

function listen(server) {
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
}
function close(server) { return new Promise(r => server.close(r)); }

console.log('R-F1615 web async chat — behavioral capability tests\n');

// ── 0. Source-level guard: handler no longer uses a single 600s sync fetch ──
console.log('0. handler uses the async helper, not a single blocking fetch');
{
  const hStart = SRC.indexOf("app.post('/api/aria/chat'");
  const block = SRC.slice(hStart, hStart + 6000);
  check('chat handler calls _ariaChatAsyncPoll', block.includes('_ariaChatAsyncPoll('));
  check('async helper requests async_mode', extractFn('_ariaChatAsyncPoll').includes('async_mode: true'));
  check('async helper polls /chat/result/', extractFn('_ariaChatAsyncPoll').includes('/api/aria/chat/result/'));
}

// ── 1. Happy path: dispatch → processing → done returns the real answer ─────
console.log('\n1. dispatch → poll → done returns the brain answer');
{
  const { server, seen } = makeBrain({ pollsBeforeDone: 1, answer: 'Real DD result.' });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    const data = await poll('do a full DD', 'sid1', 'u1', 'broker');
    check('returns the brain result dict', !!data && typeof data === 'object');
    check('result carries the real answer', data && data.response === 'Real DD result.');
    check('dispatched with async_mode:true', seen.dispatched && seen.dispatched.async_mode === true);
    check('dispatched the message + session_id', seen.dispatched && seen.dispatched.message === 'do a full DD' && seen.dispatched.session_id === 'sid1');
    check('did NOT send a callback_url (server-side poll, SSRF-safe)',
          seen.dispatched && !('callback_url' in seen.dispatched));
  } finally { await close(server); }
}

// ── 2. Mid-deploy resilience: transient 500 blips during polling are retried ─
console.log('\n2. transient poll errors (mid-deploy blips) are retried, not fatal');
{
  const { server } = makeBrain({ blipPolls: 2, pollsBeforeDone: 1, answer: 'Survived the deploy.' });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    const data = await poll('q', 'sid2', '', '');
    check('answer delivered despite 2 transient 500s', data && data.response === 'Survived the deploy.');
  } finally { await close(server); }
}

// ── 3. Transient not_found tolerated (< 3 consecutive), then completes ──────
console.log('\n3. transient not_found (<3) tolerated, then completes');
{
  const { server } = makeBrain({ notFoundPolls: 2, pollsBeforeDone: 0, answer: 'OK.' });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    const data = await poll('q', 'sid3', '', '');
    check('answer delivered after 2 not_found blips', data && data.response === 'OK.');
  } finally { await close(server); }
}

// ── 4. Persistent not_found (>=3) → job expired error (caller falls back) ────
console.log('\n4. persistent not_found (>=3) raises chat job expired');
{
  const { server } = makeBrain({ notFoundPolls: 99, pollsBeforeDone: 0 });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    let threw = null;
    try { await poll('q', 'sid4', '', ''); } catch (e) { threw = e; }
    check('threw an error', !!threw);
    check('error is "chat job expired"', threw && /expired/.test(threw.message));
  } finally { await close(server); }
}

// ── 5. failed status surfaces the brain error (caller → fallback) ───────────
console.log('\n5. failed job surfaces the brain error');
{
  const { server } = makeBrain({ finalStatus: 'failed', pollsBeforeDone: 0, failError: 'synthesis crashed' });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    let threw = null;
    try { await poll('q', 'sid5', '', ''); } catch (e) { threw = e; }
    check('threw on failed job', !!threw);
    check('error carries brain message', threw && /synthesis crashed/.test(threw.message));
  } finally { await close(server); }
}

// ── 6. Old brain (sync response, no job_id) → returned directly ─────────────
console.log('\n6. legacy brain without async support (no job_id) is honoured');
{
  const { server } = makeBrain({ dispatchBody: { response: 'sync answer from old brain' } });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    const data = await poll('q', 'sid6', '', '');
    check('returns the sync dict', data && data.response === 'sync answer from old brain');
  } finally { await close(server); }
}

// ── 7. Dispatch HTTP error → throws (caller logs + falls through) ────────────
console.log('\n7. non-OK dispatch throws so caller can fall through');
{
  const { server } = makeBrain({ dispatchStatus: 502 });
  const url = await listen(server);
  try {
    const poll = buildPoll(url);
    let threw = null;
    try { await poll('q', 'sid7', '', ''); } catch (e) { threw = e; }
    check('threw on 502 dispatch', !!threw);
    check('error notes HTTP 502', threw && /502/.test(threw.message));
  } finally { await close(server); }
}

console.log(`\nR-F1615 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
