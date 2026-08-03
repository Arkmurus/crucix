// R-F3651..R-F3654 — regressions found by the 2026-08-03 360 Prospector sweep.
//
// These are SOURCE-CONTRACT tests, and deliberately so: handleAriaMention and the
// mounted Express routes in lib/whatsapp/waListener.mjs cannot be driven without a
// live Baileys socket, and this file follows the pattern the repo already uses for
// that module (rf525-dd-timeout-route, wa-listener-teardown-rf461,
// stream-cut-marker-rf467). They are NOT a substitute for the operator confirming a
// real WhatsApp reply — see §23.
//
// What they pin is exactly the failure class that got past every existing gate:
// `node --check` and `npm run lint` are SYNTAX-only, and all four of these bugs
// produce perfectly parseable JavaScript. Three of the four were latent
// ReferenceErrors or dead branches that only fail at RUNTIME, on a path no test
// exercised.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const WA = readFileSync(
  fileURLToPath(new URL('../lib/whatsapp/waListener.mjs', import.meta.url)), 'utf8');
const SERVER = readFileSync(
  fileURLToPath(new URL('../server.mjs', import.meta.url)), 'utf8');

function sliceFn(src, header) {
  const i = src.indexOf(header);
  assert.ok(i !== -1, `could not locate ${header}`);
  // next top-level function/const declaration terminates the slice
  const rest = src.slice(i + header.length);
  const end = rest.search(/\n(?:async function |function |export function )/);
  return rest.slice(0, end === -1 ? rest.length : end);
}

describe('R-F3651 — handleAriaMention must actually call ARIA', () => {
  const body = sliceFn(WA, 'async function handleAriaMention(');

  it('declares rawReply and reply before the trace_id/send block reads them', () => {
    // The bug: R-F1770 merged its comment into `const ... isLong = ...` and took
    // R-F1760's whole strategy loop with it, leaving `rawReply` and `reply` used
    // but never declared → ReferenceError on EVERY @-mention.
    const declRaw = body.indexOf('let rawReply');
    const declReply = body.indexOf('let reply');
    const useRaw = body.indexOf('rawReply && typeof rawReply');
    assert.ok(declRaw !== -1, 'rawReply is never declared in handleAriaMention');
    assert.ok(declReply !== -1, 'reply is never declared in handleAriaMention');
    assert.ok(useRaw !== -1, 'trace_id extraction not found');
    assert.ok(declRaw < useRaw, 'rawReply is used before it is declared');
    assert.ok(declReply < useRaw, 'reply is used before it is declared');
  });

  it('actually invokes askARIA with the enriched text and group context', () => {
    assert.match(body, /askARIA\(\s*enrichedText,\s*recentMsgs,\s*senderName/,
      'handleAriaMention must call askARIA(enrichedText, recentMsgs, senderName, ...)');
  });

  it('keeps R-F1760 self-healing: multi-strategy loop + honest fallback', () => {
    assert.match(body, /conv\.hasMoreStrategies\(\)/, 'strategy loop missing');
    assert.match(body, /conv\.nextStrategy\(\)/, 'strategy advance missing');
    assert.match(body, /couldn't reach my brain/,
      'the honest all-strategies-failed fallback must remain');
  });

  it('does not reintroduce the mangled `const <comment> isLong` declaration', () => {
    assert.doesNotMatch(WA, /const\s*\/\/[^\n]*\n(?:\/\/[^\n]*\n)*\s*isLong\s*=/,
      'the comment-merged const that caused this bug is back');
  });
});

describe('R-F3652 — reportOutcome must exist in the module that calls it', () => {
  it('defines reportOutcome', () => {
    assert.match(WA, /async function reportOutcome\(/,
      'waListener calls reportOutcome but never defines or imports it — ' +
      'note `foo?.()` does NOT protect an undeclared binding, it still throws');
  });

  it('no longer uses optional-call syntax that implied a guard it never had', () => {
    // Anchor to real code lines — the fix's own comment explains the old form.
    // NB: `[ \t]*` not `\s*` — \s matches newlines, which lets the negative
    // lookahead slide past the comment line it was meant to exclude.
    assert.doesNotMatch(WA, /^(?![ \t]*(?:\/\/|\*))[^\n]*reportOutcome\?\./m,
      'reportOutcome?.() reads as "safe if missing" but throws ReferenceError');
  });

  it('reports on both the delivered and the failed branch (§25)', () => {
    assert.match(WA, /reportOutcome\([^)]*'delivered_real_answer'/s);
    assert.match(WA, /reportOutcome\([^)]*'send_failed'/s);
  });
});

describe('R-F3653 — no duplicate switch case can shadow a live handler', () => {
  it('declares `case \'investigate\'` exactly once', () => {
    // Line-anchored so the fix's own explanatory comment is not counted.
    const n = (WA.match(/^\s*case 'investigate'\s*:/gm) || []).length;
    assert.equal(n, 1,
      `found ${n} \`case 'investigate'\` labels — JS switch runs only the first, ` +
      'so any later one is dead code (this switch already hit that bug with /feedback)');
  });

  it('keeps the JID-mention sanitisation that lived in the shadowed block', () => {
    // WhatsApp anonymises @-mentions to phone numbers; without this an
    // investigation is handed "@201219301748858" instead of a name.
    assert.match(WA, /replace\(\/@\\d\{6,\}\/g, ''\)/,
      'JID-mention stripping was lost when the dead duplicate was removed');
  });
});

describe('R-F3654 — admin status change must reach the unsuspend branch', () => {
  it('tests the suspended→active case before the general active case', () => {
    const unsuspend = SERVER.indexOf("status === 'active' && existingUser.status === 'suspended'");
    assert.ok(unsuspend !== -1, 'the unsuspend branch is gone');
    // Find the general `status === 'active'` arm of the SAME chain.
    const general = SERVER.indexOf("if (status === 'active') {", unsuspend - 2000);
    assert.ok(general !== -1, 'the general active branch is gone');
    assert.ok(unsuspend < general,
      'the specific suspended→active branch must come FIRST — behind a bare ' +
      "`status === 'active'` it is unreachable, so reactivation sent the WELCOME " +
      "email and wrote `approve` into the audit log instead of `unsuspend`");
  });

  it('still sends the reactivation email and audits it as unsuspend', () => {
    assert.match(SERVER, /sendReactivationEmail\(/);
    assert.match(SERVER, /action: 'unsuspend'/);
  });
});
