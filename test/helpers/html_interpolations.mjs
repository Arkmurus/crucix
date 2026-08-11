// test/helpers/html_interpolations.mjs
//
// R-F3845 — static analysis of `${…}` interpolations inside HTML-producing
// template literals, so "is every DOM-XSS sink on this page escaped?" is a
// MEASUREMENT rather than a reviewer's impression.
//
// C-18 originally closed the aria-brain.html sinks that were carrying obviously
// external data and recorded the remaining ~230 as "reviewed as a class, not
// proven one by one". This module is what turns that into a proof: it classifies
// every interpolation as escaped, intentionally-raw, or unescaped, and the guard
// test fails on any unescaped one.
//
// ── WHY CLASSIFICATION AND NOT "ESCAPE EVERYTHING" ───────────────────────────
// Some interpolations legitimately emit markup — a variable built earlier from
// already-escaped parts (`sens`, `unmapped`, `critBadge`), or a helper that
// returns a fragment (`seg(...)`). Escaping those double-encodes the page and
// renders `&lt;strong&gt;` to the user. Two live render tests
// (aria-brain-sensor-labels-rf3352, aria-brain-orphan-alert-rf3351) catch that,
// and did: the first version of this analysis escaped `sens` and broke the
// sensor banner.
//
// ── THE TWO BUGS THIS ANALYSER HAD TO GET RIGHT ──────────────────────────────
// Both were found by the render tests, not by reading:
//   1. A variable can HOLD markup while its own name contains none. Raw-ness is
//      decided by resolving the identifier's DECLARATION, not its use.
//   2. Scanning a declaration to its terminating `;` must skip string literals.
//      `color:#dc2626;font-size:…` contains a semicolon, which truncated
//      `critBadge`'s declaration and made a markup-emitting variable look plain.

const BT = String.fromCharCode(96);
const BS = String.fromCharCode(92);

const ALREADY = /^(escapeHtml|escHtml|encodeURIComponent)\s*\(/;
const MARKUP = /<\s*\/?\s*[a-zA-Z][^>]*>/;
/** Helpers whose return value is a markup fragment. */
const RAW_PREFIX = ['seg(', '_grid(', 'metricRow(', 'pts.map(', 'arr.map('];

/** Strip `//` line comments so a fix's own explanation is not analysed as code. */
export function stripLineComments(src) {
  return src.replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** [start, end) of every top-level template literal; end is the closing backtick. */
export function templateSpans(s) {
  const out = [];
  for (let i = 0; i < s.length; i += 1) {
    if (s[i] !== BT) continue;
    let j = i + 1;
    let depth = 0;
    while (j < s.length) {
      if (s[j] === BS) { j += 2; continue; }
      if (s[j] === '$' && s[j + 1] === '{') { depth += 1; j += 2; continue; }
      if (s[j] === '}' && depth) { depth -= 1; j += 1; continue; }
      if (s[j] === BT && !depth) break;
      j += 1;
    }
    out.push([i, j]);
    i = j;
  }
  return out;
}

/** Every `${…}` in `body`, as {expr, start, end} with offsets relative to body. */
function interpolations(body) {
  const out = [];
  const re = /\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g;
  let m;
  while ((m = re.exec(body)) !== null) {
    out.push({ expr: m[1].trim(), start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/**
 * Full source of everything assigned to `ident`: its declaration(s), any `+=`
 * appends, and any `.push(...)` (an array's declaration is `[]` and says nothing).
 */
export function declarationOf(src, ident) {
  const chunks = [];
  const decl = new RegExp(`(?:const|let|var)\\s+${ident}\\s*=`, 'g');
  let m;
  while ((m = decl.exec(src)) !== null) {
    let j = m.index + m[0].length;
    let depth = 0;
    for (; j < src.length; j += 1) {
      const ch = src[j];
      if (ch === '"' || ch === "'") {
        // Skip the literal. Without this a `;` inside a CSS string ends the scan
        // early and a markup-emitting declaration reads as plain.
        const q = ch;
        j += 1;
        while (j < src.length && src[j] !== q) j += src[j] === BS ? 2 : 1;
        continue;
      }
      if (ch === BT) {
        let k = j + 1;
        let d2 = 0;
        while (k < src.length) {
          if (src[k] === BS) { k += 2; continue; }
          if (src[k] === '$' && src[k + 1] === '{') { d2 += 1; k += 2; continue; }
          if (src[k] === '}' && d2) { d2 -= 1; k += 1; continue; }
          if (src[k] === BT && !d2) break;
          k += 1;
        }
        j = k;
        continue;
      }
      if ('([{'.includes(ch)) depth += 1;
      else if (')]}'.includes(ch)) depth -= 1;
      else if (ch === ';' && depth <= 0) break;
    }
    chunks.push(src.slice(m.index, j));
  }
  for (const mm of src.matchAll(new RegExp(`${ident}\\s*\\+=\\s*([^\\n]*)`, 'g'))) chunks.push(mm[1]);
  for (const mm of src.matchAll(new RegExp(`${ident}\\.push\\(([^\\n]*)`, 'g'))) chunks.push(mm[1]);
  return chunks.join('\n');
}

/**
 * Body of `function NAME(...) { … }`, brace-matched.
 *
 * A concatenation operand is often a CALL — `rowStatusPill(r, sev)` — and whether
 * that is a sink or a markup fragment is decided by what the function RETURNS.
 * Resolving only variables leaves every helper looking like an unescaped value.
 */
export function functionBodyOf(src, name) {
  const m = new RegExp(`function\\s+${name}\\s*\\(`).exec(src);
  if (!m) return '';
  let i = src.indexOf('{', m.index);
  if (i < 0) return '';
  let depth = 0;
  for (let j = i; j < src.length; j += 1) {
    if (src[j] === '{') depth += 1;
    else if (src[j] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(i, j + 1);
    }
  }
  return '';
}

/**
 * Classify operands of STRING-CONCATENATION HTML building.
 *
 * public/dd-reports.html does not use template literals at all — it builds markup
 * with `'<div>' + escText(x) + '</div>'`. A template-literal-only analyser reports
 * zero findings there and looks like a pass, which is the "guard whose universe is
 * empty always certifies" failure CLAUDE.md §16 records for route_audit. This
 * covers the other half.
 *
 * Only operands ADJACENT to a markup-bearing string literal are considered — that
 * is what makes an operand a DOM sink rather than ordinary string maths.
 *
 * @param {string} rawSrc
 * @param {(id:string)=>boolean} [isRawIdent] treat these identifiers as markup
 * @returns {{escaped: number, raw: string[], unescaped: {expr: string, line: number}[]}}
 */
export function classifyConcatOperands(rawSrc, isRawIdent = () => false) {
  const src = stripLineComments(rawSrc);
  const out = { escaped: 0, raw: [], unescaped: [] };
  const ESC_CALL = /^(escText|escAttr|escHtml|escapeHtml|encodeURIComponent|safeUrl)\s*\(/;
  /** Counts, sizes, rounded numbers, formatted dates — cannot carry markup. */
  const NUMERIC_OPERAND =
    /(\.length\b|\.size\b|^Math\.|\.toFixed\(|\.toLocaleString\(|\.toLocaleDateString\(|^Number\(|^parseInt\(|^parseFloat\(|_count\b|Count\b|^Array\.from\()/;
  // `<literal containing markup>' + OPERAND` and `OPERAND + '<literal…>`
  const re = /(['"])((?:(?!\1)[\s\S]){0,400}?<\s*\/?\s*[a-zA-Z][^>]*>(?:(?!\1)[\s\S]){0,400}?)\1\s*\+\s*([A-Za-z_$][\w$.]*(?:\([^()]*\))?)/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const operand = m[3].trim();
    const head = operand.split(/[.(]/)[0];
    const line = src.slice(0, m.index).split('\n').length;
    // Match on the NAME, not on captured parens: `escText(a.b(c))` has nested
    // parens, so the operand regex captures the bare name and an ESC_CALL test
    // against the full text would wrongly report an escaped sink as unescaped.
    if (ESC_CALL.test(operand) || ESC_CALL.test(head + '(')) { out.escaped += 1; continue; }
    if (isRawIdent(operand) || isRawIdent(head)) { out.raw.push(operand); continue; }
    if (MARKUP.test(declarationOf(src, head)) || MARKUP.test(functionBodyOf(src, head))) {
      out.raw.push(operand);
      continue;
    }
    // Provably not a string the caller controls: a count, a size, a rounded
    // number, a formatted date.
    if (NUMERIC_OPERAND.test(operand)) { out.escaped += 1; continue; }
    // `chain.map(escText)` — the mapper IS the escaper.
    if (/\.map\(\s*(escText|escAttr|escHtml|escapeHtml)\s*\)/.test(operand)) { out.escaped += 1; continue; }
    // A variable whose own declaration escapes: `const _emsg = ' — ' + escText(…)`.
    // Also accepts dd-reports.html's older ad-hoc `.replace(/</g,'&lt;')`, which
    // is sufficient in TEXT position (it cannot open a tag) though it is weaker
    // than escText and should be migrated.
    const decl = declarationOf(src, head);
    if (ESC_CALL.test(decl) || /\b(escText|escAttr|escHtml|escapeHtml)\s*\(/.test(decl)
        || /\.replace\(\s*\/</.test(decl)) {
      out.escaped += 1;
      continue;
    }
    if (/\.replace\(\s*\/</.test(operand)) { out.escaped += 1; continue; }
    out.unescaped.push({ expr: operand, line });
  }
  return out;
}

/**
 * Classify every interpolation in every HTML-producing template literal.
 *
 * @param {string} rawSrc  the page source
 * @returns {{escaped: number, raw: string[], unescaped: {expr: string, line: number}[]}}
 */
export function classifyHtmlInterpolations(rawSrc) {
  const src = stripLineComments(rawSrc);
  const spans = templateSpans(src).filter(([a, b]) => /<\s*\/?\s*[a-zA-Z]/.test(src.slice(a, b)));

  // Which bare identifiers are interpolated? Their declarations decide raw-ness.
  const idents = new Set();
  for (const [a, b] of spans) {
    for (const { expr } of interpolations(src.slice(a, b))) {
      if (/^[A-Za-z_$][\w$]*$/.test(expr)) idents.add(expr);
      const j = /^([A-Za-z_$][\w$]*)\.join\(/.exec(expr);
      if (j) idents.add(j[1]);   // the RECEIVER decides, not the .join call
    }
  }
  const rawIdents = new Set(
    [...idents].filter((i) => MARKUP.test(declarationOf(src, i))),
  );

  const isRaw = (e) => {
    if (rawIdents.has(e)) return true;
    const j = /^([A-Za-z_$][\w$]*)\.join\(/.exec(e);
    if (j && rawIdents.has(j[1])) return true;
    if (RAW_PREFIX.some((p) => e.startsWith(p))) return true;
    return MARKUP.test(e);   // the expression builds markup inline
  };

  const lineOf = (idx) => src.slice(0, idx).split('\n').length;
  const out = { escaped: 0, raw: [], unescaped: [] };
  for (const [a, b] of spans) {
    for (const { expr, start } of interpolations(src.slice(a, b))) {
      if (!expr) continue;
      if (ALREADY.test(expr)) { out.escaped += 1; continue; }
      if (isRaw(expr)) {
        out.raw.push(expr);
        // A raw expression is often `x.map(v => `<span>${v.field}</span>`)`.
        // The OUTER expression is legitimately markup, but the interpolations
        // NESTED inside its own template literal are ordinary sinks and must be
        // classified too. Skipping them is how `${g.tier}` sat unescaped inside
        // a chip renderer while the outer map read as "intentionally raw".
        if (expr.includes(BT) && expr.includes('${')) {
          const inner = classifyHtmlInterpolations(expr);
          out.escaped += inner.escaped;
          out.raw.push(...inner.raw);
          for (const u of inner.unescaped) {
            out.unescaped.push({ expr: u.expr, line: lineOf(a + start) });
          }
        }
        continue;
      }
      out.unescaped.push({ expr, line: lineOf(a + start) });
    }
  }
  return out;
}
