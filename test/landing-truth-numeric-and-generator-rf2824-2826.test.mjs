// test/landing-truth-numeric-and-generator-rf2824-2826.test.mjs
//
// R-F2824 — fabricated demo content must never be presented as a real result.
// R-F2825 — hero metrics must be true, and countable ones must be hydrated.
// R-F2826 — the truth guard must check NUMBERS and audit the GENERATOR.
//
// WHY THIS EXISTS. The landing page is the one surface where a product whose USP
// is "never a false clean" was making unbacked claims:
//   index.html:1552 asserted a sanctions clear for "Meridian Trading LLC" across
//     OFAC/EU/UN/OFSI with per-source confidences of 0.99, a grade of CONFIRMED and
//     a UTC timestamp to the minute — for an entity that does not exist. :1448
//     asserted "PEP: NEGATIVE" against a NAMED individual. Both sat under the label
//     "ARIA · live screening", and a grep of the file for illustrative|example|
//     fictional|not real returned ZERO matches. model-card.html:128 simultaneously
//     states ARIA "does not invent verifiable facts... director names".
//   The four hero metrics were hardcoded literals with no ids and no fetch. Two were
//     provably false: "24/7 autonomous watchlist monitoring" (WEEKLY-DD-WATCHLIST is
//     cron "0 7 * * mon") and "10 DD pipeline layers" (dd_orchestrator.py:5 says 7 —
//     and that false 10 also appeared in TWO PAID TIER feature lists).
//   The guard that was supposed to prevent all of this checked PHRASES ONLY, and only
//     in the OUTPUT — while every banned phrase still sat in the GENERATOR, one
//     `python scripts/build_landing_page.py` away from returning, guard green.
//
// The load-bearing tests here are the NEGATIVE CONTROLS: they mutate a copy of the
// landing page / generator back to each old defect and assert the guard FAILS. A
// guard that has never been seen to fail is not a guard.
//
// Run: node --test test/landing-truth-numeric-and-generator-rf2824-2826.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, cpSync, rmSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const LANDING = path.join(ROOT, 'public', 'index.html');
const GUARD = path.join(ROOT, 'scripts', 'audit', 'landing_claim_truth.mjs');
const html = readFileSync(LANDING, 'utf8');
const generator = readFileSync(path.join(ROOT, 'scripts', 'build_landing_page.py'), 'utf8');
/** Content only — the guard ignores HTML comments, and so must these assertions. */
const content = html.replace(/<!--[\s\S]*?-->/g, '');

function runGuard(cwd = ROOT) {
  try {
    execFileSync(process.execPath, [path.join(cwd, 'scripts', 'audit', 'landing_claim_truth.mjs')],
      { cwd, stdio: 'pipe' });
    return { ok: true, out: '' };
  } catch (e) {
    return { ok: false, out: String(e.stdout || '') + String(e.stderr || '') };
  }
}

/** A throwaway repo skeleton the guard can run against, with one file mutated. */
function guardOnMutation(mutate) {
  const dir = mkdtempSync(path.join(tmpdir(), 'landing-guard-'));
  try {
    for (const rel of [
      ['public', 'index.html'], ['public', 'capability-claims.json'],
      ['scripts', 'audit', 'landing_claim_truth.mjs'], ['scripts', 'build_landing_page.py'],
      ['aria_service', 'intel', 'dd_orchestrator.py'],
      ['aria_service', 'intel', 'political_risk_index.py'],
      ['aria_service', 'autonomous', 'tasks.yaml'],
    ]) {
      const dst = path.join(dir, ...rel);
      mkdirSync(path.dirname(dst), { recursive: true });
      cpSync(path.join(ROOT, ...rel), dst);
    }
    mutate({
      write: (rel, transform) => {
        const f = path.join(dir, ...rel);
        writeFileSync(f, transform(readFileSync(f, 'utf8')), 'utf8');
      },
    });
    return runGuard(dir);
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

describe('R-F2824 — fabricated demo content is labelled, not presented as real', () => {
  test('the "live screening" label is gone', () => {
    assert.ok(!/ARIA\s*·\s*live screening/.test(content),
      'fabricated example data must not be labelled a live screening result');
  });

  test('the fabricated named individual and PEP determination are gone', () => {
    assert.ok(!content.includes('Khalid Al-Rashid'),
      'a fabricated PEP determination against a named individual must not ship');
    assert.ok(!/PEP:\s*NEGATIVE/.test(content),
      'an invented PEP verdict must not be stated as a result');
  });

  test('the demo carries an explicit illustrative disclaimer', () => {
    assert.match(content, /Illustrative only/i,
      'the example card must say plainly that it is not a real screening result');
    assert.match(content, /not a real (entity|screening)/i);
  });

  test('no fabricated card claims a CONFIRMED evidence grade', () => {
    assert.ok(!/lp-grade-confirmed">CONFIRMED</.test(content),
      'illustrative content must not carry the CONFIRMED grade the real engine assigns');
  });

  test('the illustrative card still states the never-false-clean position', () => {
    // The USP must survive the rewrite — this is the sentence that makes the page
    // consistent with the engine's actual behaviour.
    assert.match(content, /absence of findings as a clean bill of health|never presents an absence/i);
  });
});

describe('R-F2825 — hero metrics are true and countable ones are hydrated', () => {
  test('the false 24/7 and 10-layer claims are gone everywhere, including paid tiers', () => {
    assert.ok(!/24\s*\/\s*7/.test(content), '"24/7" contradicts a weekly cron');
    assert.ok(!/10-layer|10 layer/.test(content),
      'the 10-layer claim was false against dd_orchestrator.py AND appeared in ' +
      'two paid tier feature lists — a billed feature described with a false number');
  });

  test('the DD layer count matches the orchestrator', () => {
    const src = readFileSync(path.join(ROOT, 'aria_service', 'intel', 'dd_orchestrator.py'), 'utf8');
    const truth = /The (\d+)-layer due-diligence orchestrator/.exec(src)[1];
    const claimed = /<div class="lp-metric-n">(\d+)<\/div>\s*<div class="lp-metric-l">DD pipeline layers</.exec(content);
    assert.ok(claimed, 'the layer metric must stay machine-readable for the guard');
    assert.equal(claimed[1], truth);
  });

  test('the records metric ships as an honest em-dash and is hydrated, not hardcoded', () => {
    assert.match(content, /id="lp-metric-records"[^>]*>&mdash;</,
      'the records metric must ship blank and be filled only by a real reading');
    assert.match(content, /\/api\/public\/metrics/, 'it must be hydrated from the live endpoint');
    const hydrate = content.slice(content.indexOf('hydrateRecords'));
    assert.match(hydrate, /if \(!r\.ok\) return;/,
      'a failed fetch must leave the em-dash, never a fallback number');
  });

  test('the countries claim does not exceed its backing', () => {
    const pri = readFileSync(path.join(ROOT, 'aria_service', 'intel', 'political_risk_index.py'), 'utf8');
    const seed = (pri.match(/^\s{4}"[^"]+":\s*\{/gm) || []).length;
    const claimed = /<div class="lp-metric-n">(\d+)<\/div>\s*<div class="lp-metric-l">Countries/.exec(content);
    if (claimed && seed > 0) {
      assert.ok(Number(claimed[1]) <= seed,
        `landing claims ${claimed[1]} countries but the seed holds ${seed}`);
    }
  });

  test('the generator produces the same honest page (no banned phrase survives)', () => {
    for (const p of ['No external dependencies', 'Sovereign LLM  AUTO', 'Nothing missed',
      'Every finding. <em>Fully traced.</em>', 'GDPR Compliant']) {
      const bare = p.replace(/<[^>]+>/g, '');
      assert.ok(!generator.includes(p) && !generator.includes(bare),
        `"${bare}" still in the generator — regenerating would reintroduce it`);
    }
    assert.ok(!/24\s*\/\s*7/.test(generator) && !/10-layer/.test(generator),
      'the generator must not reintroduce the false metrics');
  });
});

describe('R-F2826 — NEGATIVE CONTROLS: the guard actually fails on each regression', () => {
  test('baseline: the guard passes on the current tree', () => {
    const r = runGuard();
    assert.ok(r.ok, `guard should pass on HEAD but failed:\n${r.out}`);
  });

  test('re-introducing "24/7" while the cron is weekly FAILS the guard', () => {
    const r = guardOnMutation(({ write }) => write(['public', 'index.html'],
      (s) => s.replace('<div class="lp-metric-n">Weekly</div>', '<div class="lp-metric-n">24/7</div>')));
    assert.ok(!r.ok, 'a 24/7 claim over a weekly cron must fail the guard');
    assert.match(r.out, /24\/7|continuous/i);
  });

  test('inflating the DD layer count FAILS the guard', () => {
    const r = guardOnMutation(({ write }) => write(['public', 'index.html'],
      (s) => s.replace(/<div class="lp-metric-n">7<\/div>(\s*<div class="lp-metric-l">DD pipeline layers)/,
        '<div class="lp-metric-n">10</div>$1')));
    assert.ok(!r.ok, 'claiming more DD layers than the orchestrator has must fail');
    assert.match(r.out, /DD pipeline layers/i);
  });

  test('a hardcoded approximate scale figure FAILS the guard', () => {
    const r = guardOnMutation(({ write }) => write(['public', 'index.html'],
      (s) => s.replace('<div class="lp-metric-n" id="lp-metric-records">&mdash;</div>',
        '<div class="lp-metric-n">87K+</div>')));
    assert.ok(!r.ok, 'an unbacked "87K+" with no live source must fail');
    assert.match(r.out, /hardcoded approximate|live source/i);
  });

  test('re-labelling the demo "live screening" FAILS the guard', () => {
    const r = guardOnMutation(({ write }) => write(['public', 'index.html'],
      (s) => s.replace('ARIA · illustrative example', 'ARIA · live screening')));
    assert.ok(!r.ok, 'fabricated data labelled as live must fail');
    assert.match(r.out, /live screening/i);
  });

  test('a banned phrase returning to the GENERATOR FAILS the guard', () => {
    // The hole that made every other fix reversible by one regeneration.
    const r = guardOnMutation(({ write }) => write(['scripts', 'build_landing_page.py'],
      (s) => s.replace('<div class="lp-cert-name">GDPR-Aware</div>',
        '<div class="lp-cert-name">GDPR Compliant</div>')));
    assert.ok(!r.ok, 'the guard must audit the generator, not just its output');
    assert.match(r.out, /GENERATOR|build_landing_page/i);
  });

  test('the guard does NOT fire on its own explanatory comments', () => {
    // A guard that trips on a comment documenting the fix trains people to weaken it.
    const r = guardOnMutation(({ write }) => write(['public', 'index.html'],
      (s) => s.replace('<div class="lp-hero-metrics">',
        '<!-- historical note: this used to claim 24/7 and 10-layer -->\n<div class="lp-hero-metrics">')));
    assert.ok(r.ok, `the guard fired on an HTML comment:\n${r.out}`);
  });
});
