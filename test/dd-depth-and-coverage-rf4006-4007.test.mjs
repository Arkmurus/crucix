// test/dd-depth-and-coverage-rf4006-4007.test.mjs
//
// R-F4006 (C-85) — the depth cards never said what each mode PRODUCES.
//
// Standard reads "Core 7-layer screen: sanctions, registry, identity & risk.
// Fast." and Deep reads "Standard + all forensic primitives (FATF, TBML, RCA,
// Benford…)". Both describe which CHECKS run. Neither mentions the difference
// that actually changes what the customer receives, which the orchestrator itself
// states internally: standard-mode research is budget-limited to gathering
// citations and "cannot reach article analysis", while deep raises
// deep_researcher to "thorough" (Claude-pinned), widens the compliance and
// digital budgets, and runs link_investigator over the subject's own site.
//
// Measured end-to-end 2026-08-13: standard 304s with zero synthesis subcalls;
// deep 448s and the only mode that moves the Anthropic counter.
//
// Standard REMAINS the default (operator, 2026-08-14). The point is not to push
// people to Deep — it is that choosing Standard should be a decision rather than
// an inherited hidden-input value.
//
// NO PRICES IN THE UI. Customers are metered by ddRunsPerMonth, not per run, so a
// dollar figure would be an internal number presented as if it were their bill.
// Duration is the honest customer-facing cost.
//
// R-F4007 (C-86) — sanctions coverage had no structured field to render, so the
// UI could only show it as markdown prose. dd_schema now emits
// `sanctions_coverage`; this asserts the page reads it.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const DD = fs.readFileSync(new URL('../public/dd-reports.html', import.meta.url), 'utf8');
const CSS = fs.readFileSync(new URL('../public/css/aria.css', import.meta.url), 'utf8');

/**
 * The two depth option cards, markup only.
 *
 * Bounded by the hidden mode input that follows the grid, NOT by the first
 * `</div>` after the deep card: that closes the deep card's TITLE div, so the
 * slice stopped before its description and the duration assertion failed on copy
 * that was actually present. Fourth time in this workstream a slice heuristic has
 * misreported correct code (R-F3858's class) — bound to a real landmark, never to
 * the next closing tag.
 */
/**
 * The body of renderSanctionsCoverage, bounded by the function rather than by a
 * character count.
 *
 * The first version sliced `indexOf('sanctions_coverage'), +1600` and broke the
 * moment the function gained an explanatory comment — the same fixed-window
 * fragility as R-F3858, and the fifth time in this workstream that a slice
 * heuristic has misreported correct code. Anchor to a real boundary.
 */
function coverageRenderer() {
  const at = DD.indexOf('function renderSanctionsCoverage(');
  assert.ok(at > 0, 'the coverage renderer should exist');
  const end = DD.indexOf('\nfunction ', at + 10);
  return DD.slice(at, end > at ? end : at + 4000);
}

function depthCards() {
  const at = DD.indexOf('class="sc-optgrid"');
  assert.ok(at > 0, 'the depth option grid should exist');
  const end = DD.indexOf('id="dd-r-mode"', at);
  assert.ok(end > at, 'the hidden mode input should follow the grid');
  return DD.slice(at, end);
}

describe('R-F4006 — the depth cards say what each mode produces', () => {

  it('Standard is still the default', () => {
    // Operator decision, pinned so a later edit cannot quietly move it.
    assert.match(DD, /id="dd-r-mode"[^>]*value="standard"/,
      'standard must remain the default depth');
    const cards = depthCards();
    const stdAt = cards.indexOf('data-mode="standard"');
    const activeAt = cards.indexOf('active');
    assert.ok(activeAt > -1 && Math.abs(activeAt - stdAt) < 120,
      'the Standard card must be the pre-selected one');
  });

  it('THE DEFECT: each card states what the customer actually gets', () => {
    const cards = depthCards();
    // Standard must be honest about the research ceiling the orchestrator
    // enforces — it gathers citations but does not read the articles.
    assert.match(cards, /does not read|no article|citation/i,
      'the Standard card must disclose that its research gathers citations '
      + 'rather than reading articles');
    // Deep must say what it adds, in the customer's terms.
    assert.match(cards, /reads|analys/i,
      'the Deep card must say it adds article-level reading/analysis');
  });

  it('each card gives an expected duration', () => {
    // The real cost to a customer is their time, and a 7-minute run with no
    // stated duration reads as a hung page.
    const cards = depthCards();
    const mins = [...cards.matchAll(/(\d+)\s*(?:–|-|to\s|~)?\s*(\d+)?\s*min/gi)];
    assert.ok(mins.length >= 2, 'both cards should carry an expected duration');
  });

  it('no monetary figure is shown — customers are metered by DD runs, not dollars', () => {
    const cards = depthCards();
    assert.doesNotMatch(cards, /[$£€]\s*\d/,
      'a per-run price would present an internal cost as if it were the bill');
  });

  it('the disclosure does not scare the user off the default', () => {
    // Standard is the default and is a legitimate product. The copy must not
    // read as a warning that the default is broken.
    const cards = depthCards();
    assert.doesNotMatch(cards, /\b(no analysis|not analysed|incomplete|limited report)\b/i,
      'Standard is a real product, not a degraded one — describe it, do not disparage it');
  });
});

describe('R-F4007 — sanctions coverage is a first-class element', () => {

  it('THE DEFECT: the page reads the structured coverage field', () => {
    assert.match(DD, /sanctions_coverage/,
      'the report view must render dd_schema\'s sanctions_coverage rather than '
      + 'leaving coverage as a grey markdown bullet');
  });

  it('it shows answered-of-total, not just a pass/fail', () => {
    const block = coverageRenderer();
    assert.match(block, /\.answered/, 'the strip must show how many lists answered');
    assert.match(block, /\.total/, 'and out of how many');
  });

  it('an incomplete screen is visually distinct from a complete one', () => {
    const block = coverageRenderer();
    assert.match(block, /\.complete/,
      'complete and incomplete coverage must not render identically — that is the '
      + 'whole defect');
    assert.match(CSS, /\.dd-cov/, 'the strip needs its own styling hook');
  });

  it('ABSENCE IS NOT COVERAGE: a legacy report renders nothing, never "0 of 0"', () => {
    const block = coverageRenderer();
    assert.match(block, /if\s*\(\s*!?\s*cov|cov\s*\?|!cov/,
      'the renderer must guard on the field being absent — dd_schema returns null '
      + 'for a report written before the coverage existed, and "0 of 0 answered" '
      + 'would invent a measurement nobody took');
  });

  it('the unanswered lists are named, not just counted', () => {
    // A count tells the reader something is missing; the names tell them WHICH
    // register was not searched, which is what a compliance decision needs.
    const block = coverageRenderer();
    assert.match(block, /unavailable/,
      'the strip must name the lists that did not answer');
  });
});
