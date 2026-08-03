// R-F3658 — the auth-on-egress gate must stay green, and must be able to fail.
//
// Origin: R-F3655/R-F3656 were found by a live log sweep, not by a test. Seven
// brain calls in explorerScheduler.mjs carried no Authorization header; the 401
// tripped a circuit breaker whose log line then blamed an unreachable brain, so
// ARIA's whole curiosity loop was dead for months with nothing pointing at the
// real cause. R-F3661 then found 14 more in ariaWhatsApp.mjs (every /api/aria
// WhatsApp command answering 401) and 2 legacy fallbacks in server.mjs.
//
// A gate is only worth having if it can fail, so this drives the real script.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

const GATE = fileURLToPath(new URL('../scripts/admin/auth_egress_gate.mjs', import.meta.url));
const REPO = fileURLToPath(new URL('..', import.meta.url));

function runGate() {
  try {
    const out = execFileSync(process.execPath, [GATE, '--json'],
      { cwd: REPO, encoding: 'utf8' });
    return { code: 0, findings: JSON.parse(out) };
  } catch (e) {
    // non-zero exit = findings; stdout still holds the JSON
    return { code: e.status, findings: JSON.parse(String(e.stdout || '[]')) };
  }
}

describe('R-F3658 — auth-on-egress gate', () => {
  it('reports the Node tier clean', () => {
    const { findings } = runGate();
    assert.deepEqual(findings, [],
      'unauthenticated cross-service call(s):\n' +
      findings.map(f => `  ${f.file}:${f.line} (${f.base})  ${f.snippet}`).join('\n'));
  });

  it('exits 0 when clean', () => {
    assert.equal(runGate().code, 0);
  });

  it('recognises the authed helpers it depends on', () => {
    const src = readFileSync(GATE, 'utf8');
    for (const h of ['brainFetch', '_ariaFetch', 'ariaProxy']) {
      assert.ok(src.includes(`'${h}'`), `gate must know about the ${h} helper`);
    }
  });
});

describe('R-F3661 — the call sites the gate found stay authenticated', () => {
  const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');

  it('ariaWhatsApp routes every aria-intel call through _ariaFetch', () => {
    const src = read('../lib/whatsapp/ariaWhatsApp.mjs');
    assert.match(src, /function _ariaFetch\(/, '_ariaFetch helper missing');
    const raw = (src.match(/^(?![ \t]*(?:\/\/|\*))[^\n]*[^_\w]fetch\(`\$\{ariaServiceUrl\}[^\n]*/gm) || []);
    assert.equal(raw.length, 0,
      `raw unauthenticated call(s) to aria-intel:\n${raw.join('\n')}`);
  });

  it('explorerScheduler and telegramCommands keep their brainFetch helpers', () => {
    for (const p of ['../lib/self/explorerScheduler.mjs', '../lib/telegram/telegramCommands.mjs']) {
      assert.match(read(p), /function brainFetch\(/, `${p} lost its authed helper`);
    }
  });

  it('the health-probe waiver is scoped to /health only', () => {
    // The single auth-exempt in the tree. If brainFetchHealth is ever used for
    // a non-health path the waiver stops being true, so pin it.
    const src = read('../services/wa-listener/aria_wa_listener.mjs');
    const calls = src.match(/brainFetchHealth\(\s*[`'"]([^`'"]+)/g) || [];
    assert.ok(calls.length > 0, 'expected brainFetchHealth call sites');
    for (const c of calls) {
      assert.match(c, /\/health/,
        `brainFetchHealth used for a non-health path (${c}) — the auth-exempt waiver no longer holds`);
    }
  });
});
