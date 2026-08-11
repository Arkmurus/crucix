// lib/util/safeUrl.mjs
//
// R-F3838 — scheme allowlist for a URL that will be rendered as an href.
//
// HTML-escaping a URL stops it BREAKING OUT of the attribute. It does not stop
// the URL itself being executable: `javascript:alert(1)` contains no quote, no
// angle bracket and no ampersand, so it survives escHtml() unchanged and fires
// on click. The two sinks this was written for sit on `/s/:token`, which is an
// UNAUTHENTICATED public page rendering `bd.brain.salesLeads[].portalUrl` and
// `bd.tenders[].url` — values that arrive from scrapes and LLM output, not from
// the operator's keyboard.
//
// An allowlist, not a blocklist: `javascript:` has too many spellings to filter
// (case, leading whitespace, embedded TAB/NEWLINE/NUL, HTML entities — browsers
// strip control characters before resolving the scheme). Naming the two schemes
// that are allowed refuses all of them at once, including the next one.

/** The only schemes that may appear in a rendered href. */
const ALLOWED = new Set(['http:', 'https:']);

/**
 * Returns `url` if it is a safe absolute http(s) link, otherwise ''.
 *
 * The caller still HTML-escapes the result: this guards the SCHEME, escaping
 * guards the attribute. Both are needed.
 *
 * @param {unknown} url
 * @returns {string} the original string, or '' when it must not be linked
 */
export function safeExternalUrl(url) {
  if (typeof url !== 'string') return '';
  const raw = url.trim();
  if (!raw) return '';
  // Browsers ignore embedded control characters when resolving a scheme, so
  // `java\tscript:` runs. Refuse anything containing one rather than trying to
  // reproduce each browser's stripping rules.
  if (/[\0-\x1f\x7f]/.test(raw)) return '';
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return '';   // relative or malformed — not a link we are willing to emit
  }
  if (!ALLOWED.has(parsed.protocol.toLowerCase())) return '';
  return url;
}
