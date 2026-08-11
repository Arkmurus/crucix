// test/helpers/aria_brain_page.mjs
//
// R-F3839/R-F3840 — shared sandbox scaffolding for the tests that run a SLICE of
// public/aria-brain.html inside a vm.
//
// Those tests extract one renderer at a time (`html.slice(indexOf(a), indexOf(b))`)
// and execute it against a hand-built context. That is a good pattern — it drives
// the real render path and asserts on emitted markup rather than on source text —
// but it means the slice loses every helper defined elsewhere in the page.
//
// `escapeHtml` (aria-brain.html, near the R-F89 panel) sits outside every slice, so
// the moment a renderer escapes its output the sandbox throws
// "escapeHtml is not defined". Three tests broke that way when R-F3839 escaped the
// XSS sinks.
//
// The escaper is lifted from the PAGE rather than reimplemented here, so a test can
// never pass against a copy that has drifted from what production actually runs —
// the same rule lib/vetting/portalPath.mjs states for the vetting validator.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

/** Raw text of public/aria-brain.html. */
export function pageHtml() {
  return readFileSync(join(here, '..', '..', 'public', 'aria-brain.html'), 'utf8');
}

/**
 * Source text of the page's own `escapeHtml`, to be prepended to a vm slice.
 *
 * Returns the declaration verbatim so the sandbox runs the shipped implementation.
 */
export function escapeHtmlSource(html = pageHtml()) {
  const start = html.indexOf('function escapeHtml(');
  if (start < 0) throw new Error('aria-brain.html no longer defines escapeHtml');
  // Walk to the closing brace of the declaration.
  let depth = 0;
  for (let i = html.indexOf('{', start); i < html.length; i += 1) {
    if (html[i] === '{') depth += 1;
    else if (html[i] === '}') {
      depth -= 1;
      if (depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error('could not delimit escapeHtml in aria-brain.html');
}

/**
 * A DOM element stub that accepts listeners.
 *
 * R-F3839 replaced two CSP-dead inline `onclick` attributes with delegated
 * `addEventListener` calls, so a stub without one now throws. Listeners are
 * recorded rather than discarded so a test can fire them if it wants to.
 */
export function elementStub(extra = {}) {
  const listeners = [];
  return {
    innerHTML: '',
    classList: { remove() {}, add() {}, contains: () => false },
    addEventListener(type, fn) { listeners.push([type, fn]); },
    contains: () => true,
    listeners,
    ...extra,
  };
}
