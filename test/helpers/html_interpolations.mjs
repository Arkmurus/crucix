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

// Every escaper name in use across public/. Pages do not agree on one: app.js
// exports the global `escHtml`, while dd-reports/watchlist define `escText`/
// `escAttr`, vetting/leads/design-partners define `esc`, and aria-brain/explorer/
// account/news define `escapeHtml`. Omitting a name here does not weaken the
// guard — it makes it report ALREADY-ESCAPED sinks as unescaped, and a fixer
// driven off that would wrap them twice and print `&amp;lt;` to users.
const ESCAPER_NAMES = new Set([
  'escapeHtml', 'escHtml', 'escText', 'escAttr', 'esc', 'escapeText',
  'encodeURIComponent', 'safeUrl', 'safeExternalUrl',
]);
const ALREADY = /^(escapeHtml|escHtml|escText|escAttr|escapeText|esc|encodeURIComponent|safeUrl|safeExternalUrl)\s*\(/;

/**
 * True when every value the expression can emit is already escaped.
 *
 * `cond ? escapeHtml(x) : 'none'` does not START with an escaper, so the simple
 * ALREADY test misses it — and wrapping it produces escapeHtml(escapeHtml(x)),
 * which prints `&amp;lt;` to the user. Strategy: delete string literals, numbers
 * and every escaper CALL (arguments included) from the expression; if no
 * identifier survives, nothing unescaped can reach the DOM.
 */
export function isFullyEscaped(expr) {
  if (!/[A-Za-z_$]/.test(expr)) return true;              // pure arithmetic
  if (![...ESCAPER_NAMES].some((n) => expr.includes(n + '('))) return false;
  let s = expr;
  // Remove escaper calls with balanced arguments, innermost first.
  for (let pass = 0; pass < 12; pass += 1) {
    const before = s;
    s = s.replace(
      new RegExp(`\\b(${[...ESCAPER_NAMES].join('|')})\\s*\\([^()]*\\)`, 'g'), '""');
    if (s === before) break;
  }
  s = s.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, '""');   // string literals
  s = s.replace(/\b\d+(\.\d+)?\b/g, '0');                        // numbers
  s = s.replace(/\b(true|false|null|undefined|typeof|new|void)\b/g, '');
  return !/[A-Za-z_$]/.test(s);
}
/**
 * Values inside `expr` that reach the DOM WITHOUT passing an escaper.
 *
 * R-F3861 — closes the last false-negative class. Raw-ness is decided by finding
 * markup in a declaration, so a declaration that holds BOTH markup and a value —
 * `const x = cond ? '<b>ok</b>' : userInput` — excused the whole variable and
 * `userInput` was never reported. Neither scan direction catches it, because the
 * value sits in a TERNARY BRANCH rather than beside a `+`.
 *
 * That shape hid a live XSS: the aria-brain error banner (R-F3855).
 *
 * Method: delete escaper calls (arguments included), string literals, numbers and
 * keywords; whatever identifier-shaped tokens survive are values with no escaper
 * between them and innerHTML. Names that resolve to markup-emitting helpers are
 * dropped, since those are fragments, not values.
 *
 * @param {string} src   whole-file source, for resolving helper names
 * @param {string} expr  the declaration/expression to inspect
 * @returns {string[]} unescaped value tokens, deduped
 */
export function unescapedRemainder(src, expr) {
  // ORDER MATTERS. Literals go first: a paren inside a STRING —
  // `escText(x || '(unnamed)')` — breaks any `[^()]*` escaper strip, leaving the
  // argument behind and reporting an escaped value as unescaped. Three real call
  // sites read that way before this was corrected.
  let s = expr;
  s = s.replace(/`(?:[^`\\]|\\.)*`/g, ' "" ');                     // template literals
  s = s.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, ' "" ');   // string literals
  s = s.replace(/\/(?:[^/\\\n]|\\.)+\/[gimsuy]*/g, ' RE ');        // regex literals

  // Now strip escaper CALLS with BALANCED arguments, innermost outward, so a
  // nested call like escHtml(truncate(u.title, 110)) removes the whole thing.
  for (let pass = 0; pass < 24; pass += 1) {
    let changed = false;
    for (const name of ESCAPER_NAMES) {
      const at = s.indexOf(name + '(');
      if (at < 0) continue;
      // Only a standalone identifier, not a suffix of a longer name.
      if (at > 0 && /[\w$.]/.test(s[at - 1])) continue;
      let depth = 0;
      let j = at + name.length;
      for (; j < s.length; j += 1) {
        if (s[j] === '(') depth += 1;
        else if (s[j] === ')') { depth -= 1; if (!depth) { j += 1; break; } }
      }
      s = s.slice(0, at) + ' "" ' + s.slice(j);
      changed = true;
    }
    if (!changed) break;
  }
  s = s.replace(/\b\d+(\.\d+)?\b/g, '0');
  const KEYWORD = /^(const|let|var|function|return|true|false|null|undefined|typeof|new|void|if|else|for|of|in|this|Math|Object|Array|JSON|String|Number|Boolean|Date|document|window|console|RE)$/;
  const SAFE_METHOD = /^(length|size|join|trim|slice|map|filter|push|concat|split|toFixed|toUpperCase|toLowerCase|toLocaleString|toLocaleDateString|replace|indexOf|includes|forEach|reverse|sort|keys|values|entries|from|isArray|round|min|max|abs|floor|ceil)$/;
  // Report PROPERTY READS only (`obj.field`), not bare identifiers.
  //
  // A bare lowercase name inside a declaration is almost always a lambda
  // parameter, a loop variable or a local flag; reporting those buried the real
  // findings in ~230 tokens of noise, and a signal nobody can read is not a
  // control. API data arrives as a property read, which is the shape that
  // matters — `scope.subject_name`, `u.title`, `e.msg`.
  const out = new Set();
  for (const m of s.matchAll(/\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\b/g)) {
    const tok = m[1];
    const head = tok.split('.')[0];
    const tail = tok.split('.').pop();
    if (KEYWORD.test(head) || KEYWORD.test(tok)) continue;
    if (SAFE_METHOD.test(tail)) continue;
    if (ESCAPER_NAMES.has(head)) continue;
    // A helper that RETURNS markup is a fragment, not a value.
    if (MARKUP.test(functionBodyOf(src, head)) || MARKUP.test(declarationOf(src, head))) continue;
    out.add(tok);
  }
  return [...out];
}

const MARKUP = /<\s*\/?\s*[a-zA-Z][^>]*>/;
/**
 * Markup detection for CONCATENATED builders, where a single tag is split across
 * operands: `'<li class="' + cls + '">'`. Neither half contains a complete tag,
 * so MARKUP misses both and a markup-emitting helper reads as a plain value.
 *
 * Anchored on a quote so ordinary prose ("a < b") cannot match.
 */
const MARKUP_FRAGMENT = /['"`]\s*<\s*\/?\s*[a-zA-Z][\w-]*/;
/** Helpers whose return value is a markup fragment. */
const RAW_PREFIX = ['seg(', '_grid(', 'metricRow(', 'pts.map(', 'arr.map('];

/**
 * Blank out `//` line comments so a fix's own explanation is not analysed as code.
 *
 * LENGTH-PRESERVING on purpose: comment text is replaced by spaces rather than
 * removed, so every offset this module reports is valid against the ORIGINAL
 * file. A fixer driven off shrunken offsets writes into the wrong place, and the
 * corruption is silent because the result is still valid JavaScript.
 */
export function stripLineComments(src) {
  return src.replace(/(^|[^:])(\/\/[^\n]*)/g, (_, pre, comment) => pre + ' '.repeat(comment.length));
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

/**
 * Every `${…}` in `body`, relative to body.
 *
 * `innerStart` is where the UNTRIMMED expression text begins (just past `${`).
 * The fixer needs it to reach interpolations NESTED inside a raw expression — a
 * `.map()` that emits markup is raw, but the values it interpolates are not.
 */
function interpolations(body) {
  const out = [];
  const re = /\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g;
  let m;
  while ((m = re.exec(body)) !== null) {
    const inner = m[1];
    const expr = inner.trim();
    out.push({
      expr,
      start: m.index,
      end: m.index + m[0].length,
      innerStart: m.index + 2 + inner.indexOf(expr),
    });
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
    // Bound the scan. If a bracket never balances — which happens when the match
    // is not really a declaration, or when the expression runs into the page's
    // static markup — an unbounded walk swallows the rest of the FILE and reports
    // tokens from unrelated HTML (`placeholder="e.g. Acme"` surfaced as a
    // property read `e.g`). No real declaration in this tree approaches this.
    const limit = Math.min(src.length, j + 4000);
    for (; j < limit; j += 1) {
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
  // (?<![\w$.]) — WORD BOUNDARY, and it is load-bearing. Without it the collector
  // for `s` also matched `rows +=`, `cites +=` and every other identifier ENDING
  // in that name, concatenating unrelated code into one 5,600-character
  // "declaration" and reporting tokens from static markup elsewhere in the page.
  for (const mm of src.matchAll(new RegExp(`(?<![\\w$.])${ident}\\s*\\+=\\s*([^\\n]*)`, 'g'))) chunks.push(mm[1]);
  for (const mm of src.matchAll(new RegExp(`(?<![\\w$.])${ident}\\.push\\(([^\\n]*)`, 'g'))) chunks.push(mm[1]);
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
  // Walk PAST the parameter list first. Taking the next `{` grabs a destructured
  // parameter instead of the body — `function avatar(user, { size = '' } = {})`
  // resolved to "{ size = '', isOnline = false }", which contains no markup, so a
  // markup-returning helper read as a plain value and its callers were reported
  // as unescaped sinks.
  let p = m.index + m[0].length - 1;   // at the '('
  let pd = 0;
  for (; p < src.length; p += 1) {
    if (src[p] === '(') pd += 1;
    else if (src[p] === ')') { pd -= 1; if (!pd) { p += 1; break; } }
  }
  let i = src.indexOf('{', p);
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
  // Derived from the ONE list, not retyped. A second hand-maintained copy is how
  // `esc(` — the escaper vetting/leads/design-partners use — came to be reported
  // as unescaped here while the template classifier accepted it.
  const ESC_CALL = new RegExp(`^(${[...ESCAPER_NAMES].join('|')})\\s*\\(`);
  /** Counts, sizes, rounded numbers, formatted dates — cannot carry markup. */
  const NUMERIC_OPERAND =
    /(\.length\b|\.size\b|^Math\.|\.toFixed\(|\.toLocaleString\(|\.toLocaleDateString\(|^Number\(|^parseInt\(|^parseFloat\(|_count\b|Count\b|^Array\.from\()/;
  /**
   * Read one operand starting at `i`, balancing parentheses.
   *
   * A regex with `\([^()]*\)` truncates `items.map(x => `<li>${x}</li>`)` to
   * `items.map` — which then classifies as an unescaped value when it is in fact
   * a markup-emitting call. Twelve operands across public/ hit exactly that.
   */
  const balanced = (i) => {
    let depth = 0;
    let j = i;
    for (; j < src.length; j += 1) {
      if (src[j] === '(') depth += 1;
      else if (src[j] === ')') { depth -= 1; if (!depth) { j += 1; break; } }
    }
    return j;
  };
  const readOperand = (i) => {
    // A PARENTHESISED operand — `'<code>' + (e.msg || 'unknown') + '</code>'` —
    // starts with '(' and matched no identifier, so it was invisible. That is how
    // the aria-brain error banner shipped with a raw `e.msg` in innerHTML.
    if (src[i] === '(') return src.slice(i, balanced(i));
    const m0 = /^[A-Za-z_$][\w$.]*/.exec(src.slice(i));
    if (!m0) return null;
    let j = i + m0[0].length;
    if (src[j] === '(') j = balanced(j);
    return src.slice(i, j);
  };

  // `<literal containing markup>' + OPERAND`
  // Accepts a PARENTHESISED operand too: `'<code>' + (e.msg || 'x')` matched no
  // identifier and was invisible — that is how the aria-brain error banner
  // shipped with a raw `e.msg` in innerHTML.
  const re = /(['"])((?:(?!\1)[\s\S]){0,400}?<\s*\/?\s*[a-zA-Z][^>]*>(?:(?!\1)[\s\S]){0,400}?)\1\s*\+\s*(?=[A-Za-z_$(])/g;
  // Direction 2: `OPERAND + '</div>…'`. A guard that reads only the literal-first
  // direction misses every value that PRECEDES its closing tag — which is half of
  // every concatenated builder in this tree (126 live sites when measured).
  /** 1-indexed line for an absolute offset; shared by both scan directions. */
  const lineAt = (idx) => src.slice(0, idx).split(String.fromCharCode(10)).length;
  const before = /([A-Za-z_$][\w$.]*(?:\([^()]*\))?|\)[^+]{0,4})\s*\+\s*(['"])\s*<\s*\/?\s*[a-zA-Z][^>]{0,200}?>/g;
  const seenAt = new Set();

  let m;
  while ((m = re.exec(src)) !== null) {
    const operandRaw = readOperand(m.index + m[0].length);
    if (!operandRaw) continue;
    seenAt.add(m.index + m[0].length);
    const operand = operandRaw.trim();
    const head = operand.split(/[.(]/)[0];
    const line = src.slice(0, m.index).split('\n').length;
    // Match on the NAME, not on captured parens: `escText(a.b(c))` has nested
    // parens, so the operand regex captures the bare name and an ESC_CALL test
    // against the full text would wrongly report an escaped sink as unescaped.
    if (ESC_CALL.test(operand) || ESC_CALL.test(head + '(')) { out.escaped += 1; continue; }
    // Same precision the template classifier gained: a ternary whose branches
    // are escaped is escaped, and a call to a markup-returning helper is raw.
    if (isFullyEscaped(operand)) { out.escaped += 1; continue; }
    if (isRawIdent(operand) || isRawIdent(head)) { out.raw.push(operand); continue; }
    // MARKUP_FRAGMENT, not MARKUP: a concatenated builder splits one tag across
    // operands, so neither half holds a complete `<tag …>`.
    if (MARKUP_FRAGMENT.test(operand)
        || MARKUP_FRAGMENT.test(declarationOf(src, head))
        || MARKUP_FRAGMENT.test(functionBodyOf(src, head))) {
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

  // Direction 2 pass. Reuses the SAME classification below via classifyOne so the
  // two directions cannot disagree about what counts as escaped.
  let m2;
  while ((m2 = before.exec(src)) !== null) {
    const operand = m2[1].trim().replace(/^\)/, '');
    if (!operand || /^[)\s]*$/.test(operand)) continue;
    // A BARE method call is the tail of a longer chain — `esc(x).trim()` yields
    // `trim()`. The chain's head is classified by the forward pass; reporting
    // the tail as its own operand is noise, not a finding.
    if (/^(join|trim|slice|replace|toUpperCase|toLowerCase|toLocaleString|toLocaleDateString|toFixed|map|filter|concat|split|padStart|padEnd|repeat)\s*\(/.test(operand)) continue;
    if (seenAt.has(m2.index)) continue;
    const head = operand.split(/[.(]/)[0];
    const line = lineAt(m2.index);
    if (ESC_CALL.test(operand) || ESC_CALL.test(head + '(')) { out.escaped += 1; continue; }
    if (isFullyEscaped(operand)) { out.escaped += 1; continue; }
    if (isRawIdent(operand) || isRawIdent(head)) { out.raw.push(operand); continue; }
    if (MARKUP_FRAGMENT.test(operand)
        || MARKUP_FRAGMENT.test(declarationOf(src, head))
        || MARKUP_FRAGMENT.test(functionBodyOf(src, head))) { out.raw.push(operand); continue; }
    if (NUMERIC_OPERAND.test(operand)) { out.escaped += 1; continue; }
    if (/\.map\(\s*(escText|escAttr|escHtml|escapeHtml|escapeText|esc)\s*\)/.test(operand)) { out.escaped += 1; continue; }
    const d2 = declarationOf(src, head);
    if (ESC_CALL.test(d2) || /(escText|escAttr|escHtml|escapeHtml|escapeText|esc)\s*\(/.test(d2)
        || /\.replace\(\s*\/</.test(d2)) { out.escaped += 1; continue; }
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

  /**
   * A call to a helper whose body emits markup returns a fragment, not a value.
   *
   * Resolves BOTH shapes: `function card(v) {…}` and `const card = (v) => …`.
   * Checking only the first missed every arrow-function renderer, and an arrow
   * renderer passed to `.map()` is the most common way these pages build lists.
   */
  const helperEmitsMarkup = (name) => (
    MARKUP.test(functionBodyOf(src, name)) || MARKUP.test(declarationOf(src, name))
  );
  const callReturnsMarkup = (e) => {
    for (const m of e.matchAll(/([A-Za-z_$][\w$]*)\s*\(/g)) {
      if (ESCAPER_NAMES.has(m[1])) continue;
      if (helperEmitsMarkup(m[1])) return true;
    }
    // `rows.map(caseCard).join('')` — the MAPPER is named, never called here.
    for (const m of e.matchAll(/\.\s*map\(\s*([A-Za-z_$][\w$]*)\s*\)/g)) {
      if (helperEmitsMarkup(m[1])) return true;
    }
    return false;
  };

  const isRaw = (e) => {
    if (rawIdents.has(e)) return true;
    const j = /^([A-Za-z_$][\w$]*)\.join\(/.exec(e);
    if (j && rawIdents.has(j[1])) return true;
    if (RAW_PREFIX.some((p) => e.startsWith(p))) return true;
    if (MARKUP.test(e)) return true;   // the expression builds markup inline
    return callReturnsMarkup(e);
  };

  const lineOf = (idx) => src.slice(0, idx).split('\n').length;
  // `start`/`end` are ABSOLUTE offsets into the (comment-stripped) source, so the
  // fixer can drive off the same classifier the guard uses. Two implementations
  // would drift and the fix would stop matching what the test accepts.
  const out = { escaped: 0, raw: [], unescaped: [] };
  for (const [a, b] of spans) {
    for (const { expr, start, end, innerStart } of interpolations(src.slice(a, b))) {
      if (!expr) continue;
      if (ALREADY.test(expr) || isFullyEscaped(expr)) { out.escaped += 1; continue; }
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
          // Rebase the nested offsets onto the outer source so a nested sink is
          // addressable — `${g.tier}` lived exactly here.
          const base = a + innerStart;
          for (const u of inner.unescaped) {
            out.unescaped.push({
              expr: u.expr,
              line: lineOf(base + (u.start ?? 0)),
              start: base + u.start,
              end: base + u.end,
              innerStart: base + (u.innerStart ?? u.start),
            });
          }
        }
        continue;
      }
      out.unescaped.push({
        expr,
        line: lineOf(a + start),
        start: a + start,
        end: a + end,
        // The fixer rewrites [innerStart, end-1) — the UNTRIMMED expression — so
        // an interpolation whose expression starts on the NEXT line is replaced
        // correctly rather than having the newline swallowed into the call.
        innerStart: a + innerStart,
      });
    }
  }
  return out;
}
