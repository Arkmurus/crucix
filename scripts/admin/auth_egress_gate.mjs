#!/usr/bin/env node
// R-F3658 — auth-on-egress gate for the Node tier.
//
// WHY THIS EXISTS
// ---------------
// R-F3655 / R-F3656 (found 2026-08-03 by a live log sweep, NOT by a test):
// lib/self/explorerScheduler.mjs made all 7 of its brain calls with no
// Authorization header. Every one returned 401. The catch recorded a circuit
// failure, the circuit opened, and from then on the loop logged
// "Brain circuit open — skipping run". A permanent AUTH bug therefore presented
// as an intermittently unreachable brain, and ARIA's entire curiosity loop was
// dead for months. lib/telegram/telegramCommands.mjs had the same defect, where
// the fallback to the local LLM made it look like the brain simply had nothing
// to say.
//
// Nothing checked. So: check.
//
// RULE
// ----
// A `fetch()` whose URL is built from a CROSS-SERVICE base (the brain) must
// either carry an Authorization header in the same call, or go through a helper
// that adds one (brainFetch / _ariaFetch / ariaProxy). Same-origin calls
// (localhost, the app's own port) are not in scope.
//
// Waiver: put `// auth-exempt: <reason>` on the line above the call. A waiver
// needs a reason, and it is visible in the diff.
//
// Usage:  node scripts/admin/auth_egress_gate.mjs [--json]
// Exit 0 = clean, 1 = violations.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

// Identifiers that denote ANOTHER service (the Python brain), not this process.
const CROSS_SERVICE_BASES = [
  'BRAIN_URL', 'BRAIN_SERVICE_URL', 'BRAIN_DIRECT_URL', 'ARIA_SERVICE_URL',
  'ARIA_BRAIN_URL', '_OUTCOME_BASE', 'ariaUrl', 'ariaServiceUrl', 'ariaBase',
  'brainUrl', 'BRAIN_BASE',
];
// Helpers that are known to attach auth themselves.
const AUTHED_HELPERS = ['brainFetch', '_ariaFetch', 'ariaProxy', 'ariaFetch'];
const EXCLUDE = ['node_modules/', 'public/vendor/', 'scripts/workflows/', 'test/'];

function tracked() {
  const out = execFileSync('git', ['ls-files', '*.mjs'], { encoding: 'utf8' });
  return out.split('\n').map(s => s.trim()).filter(Boolean)
    .filter(f => !EXCLUDE.some(x => f.includes(x)));
}

/** Return the source span of the call starting at the '(' index, balanced. */
function callSpan(src, openIdx) {
  let depth = 0;
  for (let i = openIdx; i < src.length && i < openIdx + 4000; i++) {
    const c = src[i];
    if (c === '(') depth++;
    else if (c === ')') {
      depth--;
      if (depth === 0) return src.slice(openIdx, i + 1);
    }
  }
  return src.slice(openIdx, openIdx + 4000);
}

const violations = [];
for (const file of tracked()) {
  let src;
  try { src = readFileSync(file, 'utf8'); } catch { continue; }
  if (!CROSS_SERVICE_BASES.some(b => src.includes(b))) continue;

  // Header-builder helpers defined in THIS file whose body sets Authorization
  // (e.g. server.mjs `_ariaHeaders()`). A call passing `headers: _ariaHeaders()`
  // is authenticated even though the literal never appears at the call site.
  const authedBuilders = new Set();
  {
    const defRe = /(?:function\s+([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\()/g;
    let d;
    while ((d = defRe.exec(src)) !== null) {
      const name = d[1] || d[2];
      if (!name) continue;
      const body = src.slice(d.index, d.index + 900);
      if (/Authorization/i.test(body)) authedBuilders.add(name);
    }
  }

  const re = /\bfetch\s*\(/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    // skip helper definitions/uses that already add auth
    const before = src.slice(Math.max(0, m.index - 24), m.index);
    if (AUTHED_HELPERS.some(h => before.endsWith(h)) || /[.\w]$/.test(before.trimEnd())) {
      // `brainFetch(` / `x.fetch(` — the \b in the regex still matches inside
      // an identifier ending in "fetch", so drop those.
      if (!/[^A-Za-z0-9_$]$/.test(before) && before.trim() !== '') continue;
    }
    const span = callSpan(src, m.index + m[0].length - 1);
    const base = CROSS_SERVICE_BASES.find(b =>
      new RegExp(`\\$\\{\\s*${b}\\b`).test(span) || new RegExp(`\\b${b}\\s*\\+`).test(span));
    if (!base) continue;
    if (/Authorization/i.test(span)) continue;
    if ([...authedBuilders].some(n => new RegExp(`\\b${n}\\s*\\(`).test(span))) continue;

    const line = src.slice(0, m.index).split('\n').length;
    const lines = src.split('\n');

    // Auth is very often assembled into a `headers` variable a few lines
    // earlier (`const headers = {...}; if (TOKEN) headers.Authorization = ...`)
    // or supplied by the enclosing authed helper. A literal-only check reported
    // brainFetch's own body as a violation. Look back over the enclosing scope.
    // 80 lines: waListener.mjs builds its `headers` const 43 lines above the
    // call inside a Promise.all — a 40-line window reported it as a violation.
    const windowStart = Math.max(0, line - 81);
    if (/Authorization/i.test(lines.slice(windowStart, line).join('\n'))) continue;

    // Waiver may sit anywhere in the comment block immediately above the call
    // (a one-line reason is rarely enough to justify skipping auth).
    const prev = lines.slice(Math.max(0, line - 9), line - 1).join('\n');
    if (/auth-exempt:/i.test(prev)) continue;

    violations.push({
      file, line, base,
      snippet: span.replace(/\s+/g, ' ').slice(0, 120),
    });
  }
}

if (process.argv.includes('--json')) {
  console.log(JSON.stringify(violations, null, 2));
  process.exit(violations.length ? 1 : 0);
}
if (violations.length === 0) {
  console.log('auth-egress gate: CLEAN — every cross-service fetch carries auth');
  process.exit(0);
}
console.error(`auth-egress gate: ${violations.length} UNAUTHENTICATED cross-service call(s)\n`);
for (const v of violations) {
  console.error(`  ${v.file}:${v.line}  (base: ${v.base})`);
  console.error(`      ${v.snippet}`);
}
console.error('\nFix: route through an authed helper (brainFetch/_ariaFetch), or add');
console.error('an Authorization header, or waive with `// auth-exempt: <reason>`.');
process.exit(1);
