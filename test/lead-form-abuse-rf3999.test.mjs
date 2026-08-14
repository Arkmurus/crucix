// test/lead-form-abuse-rf3999.test.mjs
//
// R-F3999 (C-80) — the landing lead form could send mail to arbitrary addresses.
//
// THE DEFECT. `POST /api/leads` is unauthenticated by necessity (a prospect has
// no account) and sends a verification email to whatever address the body names.
// It sat on the GENERIC tier — 150 requests per 15 minutes for an anonymous
// caller — with no bot defence at all. So one IP could send 150 emails per
// quarter-hour to an address of its choosing, from our domain and our SMTP
// reputation, and fill the operator's access-request queue with plausible
// entries. Every other outbound-mail path in the app is either authenticated or
// on the strict `auth` tier (10/15min); this one was not, and nothing said why.
//
// WHAT THIS DELIBERATELY DOES NOT DO: add a CAPTCHA. CLAUDE.md §6 puts the burden
// of proof on any new third-party dependency, and a CAPTCHA is a third party
// watching the top of our funnel. It also taxes exactly the person this form
// exists to capture — a real prospect — to inconvenience a bot that can solve it
// for a fraction of a cent. A honeypot costs a legitimate user nothing, because
// they never see the field.
//
// THE BOUND HAS TO HOLD ON THE DESTINATION, NOT ONLY THE SOURCE. A per-IP limit
// alone does not protect the victim of a mail-bomb: rotating source addresses is
// trivial and the target is the constant. So the same address cannot be mailed
// repeatedly regardless of who asks.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const SERVER = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const RL = fs.readFileSync(new URL('../middleware/rateLimiter.mjs', import.meta.url), 'utf8');
const INDEX = fs.readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');

const { leadHoneypotTripped, leadDestinationBlocked, _resetLeadDestinations } =
  await import('../lib/auth/leadGuard.mjs');

function codeOf(src) {
  return src.split(/\r?\n/).filter(l => !l.trim().startsWith('//')).join('\n');
}

describe('R-F3999 — the lead form is bounded without blocking prospects', () => {

  it('THE DEFECT: /api/leads no longer sits on the generic anonymous tier', () => {
    const code = codeOf(RL);
    assert.match(code, /app\.use\(\s*'\/api\/leads'/,
      '/api/leads must have its own tighter rate tier, like every other '
      + 'unauthenticated mail-sending path');
  });

  it('a real prospect is NOT blocked — the tier is generous enough to be usable', () => {
    // The constraint that matters commercially. A limit low enough to stop a bot
    // and low enough to stop a shared office NAT is a limit that costs signups.
    const m = RL.match(/leads:\s*\{[^}]*max:\s*(\d+)/s);
    assert.ok(m, 'the leads tier should declare a max');
    const max = Number(m[1]);
    assert.ok(max >= 5, `a lead tier of ${max}/window would block legitimate shared-IP users`);
    assert.ok(max <= 30, `a lead tier of ${max}/window is not a meaningful bound on mail abuse`);
  });

  it('the honeypot field exists in the form and is hidden from humans', () => {
    assert.match(INDEX, /name="website_url"/,
      'the form needs a decoy field a bot will fill and a human never sees');
    // Hidden by CSS/aria rather than type=hidden: a bot that skips type=hidden
    // inputs would sail past, which defeats the purpose.
    assert.doesNotMatch(INDEX, /name="website_url"[^>]*type="hidden"/,
      'a type=hidden decoy is the one shape bots reliably skip');
    // Hidden from assistive technology, either on the field itself or on a
    // wrapper that contains it. `[^>]*` cannot express the wrapper form because
    // it stops at the first `>`, so this matches across the tag boundary.
    assert.match(INDEX, /aria-hidden="true"[\s\S]{0,400}?name="website_url"/,
      'the decoy must be removed from the accessibility tree, or a screen-reader '
      + 'user will be asked to fill the trap');
    assert.match(INDEX, /name="website_url"[^>]*tabindex="-1"/,
      'the decoy must be out of keyboard tab order');
    assert.match(INDEX, /\.lead-hp\s*\{[^}]*(display\s*:\s*none|position\s*:\s*absolute)/,
      'the decoy must actually be invisible — an unstyled honeypot is just a '
      + 'confusing extra field');
  });

  it('a filled honeypot is refused', () => {
    assert.equal(leadHoneypotTripped({ website_url: 'http://spam.example' }), true);
    assert.equal(leadHoneypotTripped({ website_url: '   ' }), false, 'whitespace is not a fill');
    assert.equal(leadHoneypotTripped({}), false, 'a normal submission has no decoy value');
    assert.equal(leadHoneypotTripped(null), false, 'a missing body must not be treated as a bot');
  });

  it('the same destination cannot be mailed repeatedly, whoever asks', () => {
    // Per-IP limits do not protect the VICTIM of a mail-bomb: the source rotates,
    // the target does not.
    _resetLeadDestinations();
    const victim = 'target@example.com';
    let allowed = 0;
    for (let i = 0; i < 20; i++) if (!leadDestinationBlocked(victim)) allowed++;
    assert.ok(allowed >= 1, 'a first, genuine request must always get through');
    assert.ok(allowed <= 5, `${allowed} mails to one address is a mail-bomb, not a signup`);
  });

  it('one abused address does not block everyone else', () => {
    // A global counter would turn one attacker into a denial of service against
    // every other prospect.
    _resetLeadDestinations();
    for (let i = 0; i < 20; i++) leadDestinationBlocked('flooded@example.com');
    assert.equal(leadDestinationBlocked('someone.else@example.com'), false,
      'an unrelated prospect must still be able to request access');
  });

  it('addresses are compared case- and whitespace-insensitively', () => {
    // Otherwise ' Target@Example.com ' is a fresh bucket and the bound is
    // decorative.
    _resetLeadDestinations();
    for (let i = 0; i < 10; i++) leadDestinationBlocked('victim@example.com');
    assert.equal(leadDestinationBlocked('  VICTIM@Example.COM  '), true,
      'trivial case/whitespace variation must not mint a new allowance');
  });

  it('the route consults both guards before sending anything', () => {
    const code = codeOf(SERVER);
    const start = code.indexOf("app.post('/api/leads'");
    assert.ok(start > 0, 'the leads route should exist');
    const end = code.indexOf('\n});', start);
    const route = code.slice(start, end);
    assert.match(route, /leadHoneypotTripped\(/, 'the route must check the honeypot');
    assert.match(route, /leadDestinationBlocked\(/, 'the route must bound per destination');
  });

  it('a refused bot gets the SAME response as a success — no oracle', () => {
    // Telling a bot it was detected teaches it what to change, and a distinct
    // response for the destination bound would let an attacker probe which
    // addresses have already been targeted.
    //
    // Asserted on the RESPONSE the guard branch sends, not on the surrounding
    // text: the first version of this checked the route source for /honeypot/i
    // and matched the FUNCTION NAME `leadHoneypotTripped`, failing a correct
    // implementation. A guard must test the behaviour it names.
    const code = codeOf(SERVER);
    const start = code.indexOf("app.post('/api/leads'");
    const route = code.slice(start, code.indexOf('\n});', start));
    // Slice from the RETURN, not from the condition: the condition names
    // `leadDestinationBlocked`, and an identifier containing "blocked" is not a
    // disclosure to the caller. Twice now this assertion has matched a function
    // name instead of a payload — the response is the only text that reaches a
    // user, so it is the only text worth asserting on.
    const guard = route.slice(route.indexOf('leadHoneypotTripped('));
    const sent = guard.slice(guard.indexOf('return'), guard.indexOf('}') + 1);
    assert.match(sent, /res\.status\(200\)/,
      'a refused submission must return 200, exactly like an accepted one');
    assert.match(sent, /ok:\s*true/,
      'the refusal payload must be shaped like a success');
    assert.doesNotMatch(sent, /error|blocked|reject|denied/i,
      'the refusal must not disclose that it was refused');
  });
});
