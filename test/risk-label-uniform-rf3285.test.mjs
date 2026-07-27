import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

/**
 * R-F3285 — one source for how a risk classification is shown.
 *
 * THE defect, precisely: dd-reports.html built the pill's CSS class with
 *
 *     sev.toLowerCase().replace(/[^a-z_]/g, '')
 *
 * which STRIPS a hyphen instead of mapping it to an underscore. "AMBER-LIGHT"
 * became the class `amberlight`, while the stylesheet defined
 * `.dd-pill.amber_light`. The rule never matched, so amber verdicts rendered
 * with no colour at all. GREEN and RED are single words and matched fine,
 * which is exactly why this survived: the two loudest states looked right.
 *
 * dashboard.html had the same bug with a worse symptom. Its lookup table was
 * keyed 'amber-light' WITH the hyphen, so the miss fell through to
 * sc-badge-muted and every amber verdict rendered GREY, the colour reserved
 * for not having a verdict at all.
 *
 * The label was the same problem's other half. Every surface printed the
 * STORED value verbatim, so one verdict read "AMBER-LIGHT" in the library,
 * "AMBERLIGHT" on the PDF and "AMBER" on a finding pill.
 *
 * What does NOT change: the stored values. AMBER-LIGHT and AMBER-DARK are what
 * the engine computes and what every archived report holds, and rewriting them
 * would change the meaning of records already issued to customers.
 */

const appJs = readFileSync('public/js/app.js', 'utf8');

// The pages load this as a classic <script>, and package.json is type:module,
// so there is nothing to import — require() of it returns an empty namespace.
// Evaluate the shipped source directly; a test against a re-implementation
// would prove nothing about what actually renders.
const RISK = (() => {
  const grab = (n) => appJs.match(new RegExp(`function ${n}\\([\\s\\S]*?\\n\\}`))[0];
  return new Function(
    [grab('riskKey'), grab('riskTone'), grab('riskLabel')].join('\n')
    + '; return { riskKey, riskTone, riskLabel };')();
})();

test('THE regression: a hyphenated value produces a class that exists', () => {
  assert.equal(RISK.riskKey('AMBER-LIGHT'), 'AMBER_LIGHT');
  assert.equal(RISK.riskKey('AMBER-DARK'), 'AMBER_DARK');
  assert.equal(RISK.riskKey('hard stop'), 'HARD_STOP');
  assert.notEqual(RISK.riskKey('AMBER-LIGHT').toLowerCase(), 'amberlight',
    'the hyphen is being stripped again, so the CSS rule will not match');
});

test('every amber gradation shows the operator one word: AMBER', () => {
  for (const v of ['AMBER-LIGHT', 'AMBER-DARK', 'AMBER', 'AMBERLIGHT',
                   'amber_light', ' amber-light ']) {
    assert.equal(RISK.riskLabel(v), 'AMBER', `${v} did not render as AMBER`);
    assert.equal(RISK.riskTone(v), 'amber', `${v} did not get the amber tone`);
  }
});

test('amber is a tone of its own, not folded into red or green', () => {
  // The whole point of a traffic light is three distinguishable states.
  assert.equal(RISK.riskTone('RED'), 'red');
  assert.equal(RISK.riskTone('HARD_STOP'), 'red');
  assert.equal(RISK.riskTone('GREEN'), 'green');
  assert.notEqual(RISK.riskTone('AMBER-LIGHT'), RISK.riskTone('RED'));
  assert.notEqual(RISK.riskTone('AMBER-LIGHT'), RISK.riskTone('GREEN'));
});

test('an unknown or absent verdict is never coloured as a verdict', () => {
  // Rendering "no answer" in green is the false clean this codebase exists to
  // avoid; rendering it red would be an equally invented finding.
  for (const v of ['', null, undefined, 'PENDING', 'UNKNOWN', 'wat']) {
    assert.equal(RISK.riskTone(v), 'unknown', `${v} was given a verdict colour`);
  }
  assert.equal(RISK.riskLabel(''), '');
});

test('RED inside AMBER-DARK does not read as RED', () => {
  assert.equal(RISK.riskTone('AMBER-DARK'), 'amber');
  assert.equal(RISK.riskLabel('AMBER-DARK'), 'AMBER');
});

test('the amber pill has a style the generated class can actually hit', () => {
  const page = readFileSync('public/dd-reports.html', 'utf8');
  assert.match(page, /\.dd-pill\.amber\s*\{/, 'no .dd-pill.amber rule');
  assert.match(page, /\.dd-pill\.amber::before/, 'the amber dot has no colour');
  const rule = page.match(/\.dd-pill\.amber \{[^}]*\}/)[0];
  assert.match(rule, /245,\s*158,\s*11|#f59e0b|#d97706|#b45309/,
    `the amber pill is not an amber colour: ${rule}`);
});

test('no surface strips the hyphen out of a classification again', () => {
  // Stripping [^a-z_] is fine, and still necessary, ONCE the value has been
  // normalised. So the check is whether a normalisation reaches this line, not
  // whether the strip exists. Two earlier cuts of this test asserted the
  // latter and flagged vls-chain.html (whose R-F2065 fix was already correct)
  // plus this change's own explanatory comment. A guard that cries wolf gets
  // switched off, so it is worth getting the question right.
  const offenders = [];
  for (const p of readdirSync('public').filter((f) => f.endsWith('.html'))) {
    const lines = readFileSync(join('public', p), 'utf8').split('\n');
    lines.forEach((line, i) => {
      const t = line.trim();
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return;
      if (!/replace\(\/\[\^a-z_\]\/g/.test(line)) return;
      const near = lines.slice(Math.max(0, i - 4), i + 1).join('\n');
      if (!/riskKey|replace\(\/-\/g,\s*'_'\)/.test(near)) {
        offenders.push(`${p}:${i + 1} strips the separator: ${t.slice(0, 100)}`);
      }
    });
  }
  assert.deepEqual(offenders, [], offenders.join('\n'));
});

test('no surface prints the stored classification as its label', () => {
  // Comments legitimately name the stored values (this file's own do, at
  // length). What is checked is EXECUTED code putting a raw AMBER-LIGHT in
  // front of a person. Matching the stored value is also legitimate: the
  // report-markdown parser has to look for exactly that string.
  const offenders = [];
  for (const p of readdirSync('public').filter((f) => f.endsWith('.html'))) {
    let inBlock = false;
    readFileSync(join('public', p), 'utf8').split('\n').forEach((line, i) => {
      const t = line.trim();
      if (inBlock) { if (t.includes('*/') || t.includes('-->')) inBlock = false; return; }
      if (t.startsWith('/*') || t.startsWith('<!--')) {
        if (!t.includes('*/') && !t.includes('-->')) inBlock = true;
        return;
      }
      if (t.startsWith('//') || t.startsWith('*')) return;
      let code = line.replace(/\/\*[\s\S]*?\*\//g, '');
      const c = code.indexOf('//');
      if (c > -1) code = code.slice(0, c);
      if (!/AMBER-LIGHT/.test(code)) return;
      if (/riskLabel|riskTone|riskKey|\.includes\(|match\(|===|!==/.test(code)) return;
      offenders.push(`${p}:${i + 1} ${code.trim().slice(0, 90)}`);
    });
  }
  assert.deepEqual(offenders, [], offenders.join('\n'));
});

test('the amber badge exists in the shared palette, not just per page', () => {
  // dashboard.html named a class it then failed to look up, and the shared
  // stylesheet had no yellow at all: green, orange, red, muted.
  const css = readFileSync('public/css/aria.css', 'utf8');
  assert.match(css, /\.sc-badge-yellow\s*\{/, 'the shared palette still has no yellow');
  const rule = css.match(/\.sc-badge-yellow\s*\{[^}]*\}/)[0];
  assert.match(rule, /245,\s*158,\s*11|#f59e0b/, `not an amber colour: ${rule}`);

  const dash = readFileSync('public/dashboard.html', 'utf8');
  const map = dash.match(/const map = \{[^}]*\}/)[0];
  for (const cls of map.match(/'(sc-badge-[a-z]+)'/g).map((m) => m.slice(1, -1))) {
    assert.match(css, new RegExp(`\\.${cls}\\s*\\{`),
      `dashboard names ${cls}, which has no rule: the R-F3285 defect again`);
  }
});

test('the PDF calls the same verdict by the same name', () => {
  // A report a customer downloads and the page they read it on must not
  // disagree about the verdict's name. The PDF runs in Node and cannot reach a
  // browser global, so this is the one duplication in the change, and it is
  // ASSERTED to agree rather than assumed to.
  const pdf = readFileSync('lib/reports/pdf_generator.mjs', 'utf8');
  const pdfSrc = pdf.match(new RegExp('function riskLabel\\([\\s\\S]*?\\n\\}'))[0];
  const pdfLabel = new Function(pdfSrc + '; return riskLabel;')();

  for (const v of ['AMBER-LIGHT', 'AMBER-DARK', 'AMBERLIGHT', 'AMBER', 'GREEN',
                   'RED', 'HARD_STOP', 'UNKNOWN', '']) {
    assert.equal(pdfLabel(v), RISK.riskLabel(v),
      `PDF and page disagree on ${v}: ${pdfLabel(v)} vs ${RISK.riskLabel(v)}`);
  }
  assert.equal(pdfLabel('AMBER-LIGHT'), 'AMBER');

  // The colour still comes from the RAW value, so an AMBERLIGHT with no
  // separator keeps finding its colour even though the label is normalised.
  assert.match(pdf, /_verdictColour\(raw\)/,
    'the PDF colours from the normalised label, which loses AMBERLIGHT');
});
