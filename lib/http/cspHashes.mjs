// lib/http/cspHashes.mjs
//
// R-F3840 — compute `'sha256-…'` CSP source expressions for every inline
// <script> block this server serves, so `script-src` can drop 'unsafe-inline'.
//
// ── WHY HASHES AND NOT A NONCE OR EXTERNALISATION ────────────────────────────
// A nonce must be unique per RESPONSE, so it means templating every page at
// request time. These pages are STATIC files (express.static and res.sendFile),
// so that would mean reading and rewriting HTML on every request and giving up
// static caching — a real cost to close a hygiene gap.
//
// Externalising all 34 inline blocks into .js files is the other option the
// R-F1919 comment anticipated. It touches 29 files, cannot be verified without
// loading each page in a browser, and is exactly the kind of refactor the Cure
// Protocol freeze exists to refuse.
//
// Hashing needs NO page edits at all: the browser hashes the script body it
// received and matches it against the policy. Verified applicable here — a scan
// found 34 inline blocks across 29 files and ZERO scripts created dynamically
// (`document.createElement('script')`), which is the one pattern hashes cannot
// cover.
//
// ── WHY AT BOOT, NOT CHECKED IN ──────────────────────────────────────────────
// The browser hashes the EXACT BYTES between `>` and `</script>`, so line
// endings are part of the hash. This repo has no `*.html` rule in
// .gitattributes: the files are CRLF on a Windows checkout and LF in the Linux
// production image. A hash list generated on a dev box would therefore be wrong
// in production and would blank every page. Computing at boot, from the same
// file the server is about to serve, is immune to that by construction.
//
// ── FAILURE MODE, STATED ─────────────────────────────────────────────────────
// Hashes and 'unsafe-inline' are mutually exclusive: once ANY hash is present a
// browser ignores 'unsafe-inline' entirely. So a MISSED block is not a partial
// weakening, it is a dead page. That is why the extractor works on bytes rather
// than a decoded string, and why the capability test asserts coverage of every
// block in every served file rather than spot-checking.

import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

/**
 * `<script>` open tags that carry no `src=` — i.e. blocks with an inline body.
 * `latin1` keeps one byte per char, so the indices this yields are BYTE offsets.
 */
const OPEN_TAG = /<script\b(?![^>]*\bsrc\s*=)[^>]*>/gi;
const CLOSE_TAG = '</script>';

/** Recursively list every .html file under `dir`. */
function htmlFilesIn(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;   // missing directory is not fatal — caller keeps 'unsafe-inline'
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...htmlFilesIn(full));
    else if (extname(e.name).toLowerCase() === '.html') out.push(full);
  }
  return out;
}

/**
 * Extract the byte-exact body of every inline <script> in one HTML buffer.
 *
 * Decoded as latin1 so the regex indices map 1:1 onto bytes; the slices are
 * re-encoded as latin1 to recover the original bytes, including any UTF-8
 * multi-byte sequences, unchanged.
 *
 * @param {Buffer} buf
 * @returns {string[]} bodies, as latin1 strings (byte-preserving)
 */
export function inlineScriptBodies(buf) {
  const text = buf.toString('latin1');
  const bodies = [];
  OPEN_TAG.lastIndex = 0;
  let m;
  while ((m = OPEN_TAG.exec(text)) !== null) {
    const start = m.index + m[0].length;
    const end = text.indexOf(CLOSE_TAG, start);
    if (end === -1) break;              // unterminated tag — nothing to hash
    const body = text.slice(start, end);
    if (body.trim()) bodies.push(body);
    OPEN_TAG.lastIndex = end + CLOSE_TAG.length;
  }
  return bodies;
}

/** CSP source expression for one inline script body. */
export function sha256Source(bodyLatin1) {
  const digest = createHash('sha256').update(Buffer.from(bodyLatin1, 'latin1')).digest('base64');
  return `'sha256-${digest}'`;
}

/**
 * Every inline-script hash needed to serve `dir` with 'unsafe-inline' removed.
 *
 * @param {string} dir  the static root (PUBLIC_DIR)
 * @returns {{hashes: string[], files: number, blocks: number}}
 */
export function computeInlineScriptHashes(dir) {
  const seen = new Set();
  let blocks = 0;
  const files = htmlFilesIn(dir);
  for (const f of files) {
    let buf;
    try {
      if (statSync(f).size > 8 * 1024 * 1024) continue;
      buf = readFileSync(f);
    } catch {
      continue;
    }
    for (const body of inlineScriptBodies(buf)) {
      blocks += 1;
      seen.add(sha256Source(body));
    }
  }
  return { hashes: [...seen], files: files.length, blocks };
}
