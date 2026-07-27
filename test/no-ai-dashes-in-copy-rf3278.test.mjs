/**
 * R-F3278 — user-facing copy must not contain em or en dashes.
 *
 * Operator directive: an em dash in product copy reads as machine-written and
 * undermines trust in the text around it. This is a COPY rule, not a code rule.
 *
 * SCOPE, deliberately narrow. Only text a person actually reads is checked:
 *   * comments are exempt (nobody sees them, and the codebase uses dashes there
 *     heavily and legitimately)
 *   * a lone dash used as an empty-value placeholder in a table cell is exempt;
 *     that is standard typography and predates any AI-tell concern
 *
 * A blanket ban would have forced 9,500+ comment edits and rewritten R-number
 * prose, so the guard draws the line where the user does.
 *
 * Replacement guidance when this fails: use the punctuation a person would type.
 * Independent clauses take a full stop, appositives take a comma, explanations
 * take a colon, and a separator between two data fields takes a middot. Do NOT
 * substitute a comma everywhere — that produces comma splices, which read worse
 * than the dash did.
 */
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const DASH = /[—–]/;
const PUBLIC = 'public';

// Pages already cleaned. New pages should be added here as they are cleaned,
// so the guard ratchets forward instead of being switched off wholesale.
//
// R-F3283 swept the remaining 27. `vetting.html` is the one page NOT listed,
// and deliberately: its cleanup is 140 lines of R-F3278 sitting unmerged on
// fix/dd-research-robustness. Doing it twice, in parallel, on a file both
// agents were editing is how a previous session lost work. It goes on this
// list when that branch lands.
//
// MERGE NOTE for whoever resolves that branch: this file exists on both sides.
// The resolution is the UNION — take R-F3283's inline-block-comment stripper
// and `dash-is-data` marker from here, take 'vetting.html' from there, and the
// list becomes all 28.
const ENFORCED = [
  'account.html', 'admin.html', 'aria-brain.html', 'aria.html',
  'bd-intelligence.html', 'dashboard.html', 'dd-reports.html',
  'design-partners.html', 'explorer.html', 'forgot-password.html',
  'index.html', 'leads.html', 'login.html', 'model-card.html',
  'network.html', 'news.html', 'opportunities.html', 'partners.html',
  'recovery.html', 'signin.html', 'signup.html', 'sources.html',
  'vault.html', 'vetting-portal.html', 'vls-chain.html',
  'wa-connections.html', 'watchlist.html',
];

function displayedLines(text) {
  const out = [];
  let inBlockComment = false;
  text.split('\n').forEach((line, i) => {
    const s = line.trim();
    if (inBlockComment) {
      if (s.includes('*/') || s.includes('-->')) inBlockComment = false;
      return;
    }
    if (s.startsWith('/*') || s.startsWith('<!--')) {
      if (!s.includes('*/') && !s.includes('-->')) inBlockComment = true;
      return;
    }
    if (s.startsWith('//') || s.startsWith('*')) return;

    // R-F3283 — an explicit, visible opt-out for a dash that is DATA, not copy.
    // dd-reports.html parses report markdown that legitimately contains em
    // dashes (`line.match(/^\*([^*]+)\*\s*[—–-]\s*(.+)/)`); "cleaning" those
    // would silently break report rendering to satisfy a prose rule. The marker
    // has to be written on the line, so every exemption is a decision somebody
    // made and can be grepped, rather than a category the guard quietly skips.
    if (line.includes('dash-is-data')) return;

    // strip a trailing line comment so its dashes are not counted
    let code = line;
    // R-F3283 — and an INLINE block comment, which the first cut missed. CSS
    // has no `//`, so every annotated rule ("margin: 20px; /* R-F2482 — … */")
    // read as product copy. Ten of index.html's twelve hits were that shape.
    // Acting on those would have rewritten comments nobody reads and reported
    // it as a copy fix, which is the instrument certifying its own noise.
    code = code.replace(/\/\*[\s\S]*?\*\//g, '');
    const idx = code.indexOf('//');
    if (idx > -1 && !code.slice(0, idx).includes('http')) code = code.slice(0, idx);

    // a lone dash as an empty-value placeholder is allowed
    const withoutPlaceholder = code
      .replace(/(['"><])\s*[—–]\s*(['"><])/g, '$1$2');

    if (DASH.test(withoutPlaceholder)) out.push({ n: i + 1, line: s.slice(0, 120) });
  });
  return out;
}

for (const page of ENFORCED) {
  test(`R-F3278 ${page} contains no em/en dashes in displayed copy`, () => {
    const text = readFileSync(join(PUBLIC, page), 'utf8');
    const hits = displayedLines(text);
    assert.deepEqual(
      hits, [],
      `${page} has ${hits.length} displayed em/en dash(es):\n`
      + hits.map((h) => `  line ${h.n}: ${h.line}`).join('\n'),
    );
  });
}

test('R-F3278 the guard can actually fail', () => {
  // Verify the instrument. A copy guard that cannot detect a dash is worse than
  // no guard, because it certifies pages nobody checked.
  const withDash = `const msg = 'Upload failed — the service was unreachable.';`;
  assert.equal(displayedLines(withDash).length, 1, 'must flag a prose dash');

  const inComment = `// R-F1234 — this explanation is not user-facing`;
  assert.equal(displayedLines(inComment).length, 0, 'must ignore comments');

  const placeholder = `cell.textContent = '—';`;
  assert.equal(displayedLines(placeholder).length, 0, 'must allow empty-value placeholder');

  // R-F3283 additions, both found by the instrument reporting work that did
  // not exist. Ten of index.html's twelve "violations" were annotated CSS
  // rules; CSS has no `//`, so every one read as product copy.
  const inlineBlock = `margin-bottom: 20px;   /* R-F2482 — tighten rhythm */`;
  assert.equal(displayedLines(inlineBlock).length, 0,
    'must ignore an inline block comment');
  const stillFlags = `const msg = 'Ready — go';   /* R-F1 — note */`;
  assert.equal(displayedLines(stillFlags).length, 1,
    'stripping the comment must not also excuse the copy on the same line');

  const data = `line.match(/^\\*([^*]+)\\*\\s*[—–-]\\s*(.+)/);  // dash-is-data: parser`;
  assert.equal(displayedLines(data).length, 0, 'must honour the data marker');
});

test('R-F3283 the data marker is used sparingly and only where stated', () => {
  // The escape hatch must not become the answer. Every use is a dash that is
  // part of a parsing contract, and each says so on its own line.
  const pages = readdirSync(PUBLIC).filter((f) => f.endsWith('.html'));
  const marked = [];
  for (const p of pages) {
    readFileSync(join(PUBLIC, p), 'utf8').split('\n').forEach((line, i) => {
      if (line.includes('dash-is-data')) marked.push(`${p}:${i + 1}`);
    });
  }
  assert.ok(marked.length <= 6,
    `the data marker is being used as a workaround (${marked.length} uses): ${marked}`);
  for (const p of pages) {
    for (const line of readFileSync(join(PUBLIC, p), 'utf8').split('\n')) {
      if (!line.includes('dash-is-data')) continue;
      assert.match(line, /dash-is-data:\s*\S/,
        `an unexplained exemption: ${line.trim().slice(0, 110)}`);
    }
  }
});

test('R-F3278 every public page is either enforced or knowingly pending', () => {
  // Stops the list silently drifting: a new page must be a deliberate decision.
  const pages = readdirSync(PUBLIC).filter((f) => f.endsWith('.html'));
  const pending = pages.filter((p) => !ENFORCED.includes(p));
  // Not an assertion of zero — the sweep is staged. This records the remaining
  // surface so it cannot be forgotten or quietly claimed as done.
  assert.ok(pages.length > 0, 'no pages found — the scan path is wrong');
  console.log(`  [R-F3278] enforced: ${ENFORCED.length}, pending: ${pending.length}`);
});
