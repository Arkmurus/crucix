// sync.mjs — write build_rev.txt for /api/health on seenode.
//
// R-F570 (2026-05-16) — runtime GitHub-API dependency removed.
//
// History
// ───────
//   Original (pre-R-F542): fetched 42 source files from GitHub's
//     raw-content host at every build to "bypass stale Docker layer
//     cache". Seenode actually clones the repo itself, so the fetch
//     loop was redundant — the files were already on disk from the
//     seenode checkout.
//   R-F542 (2026-05-15): also fetched HEAD from the GitHub REST
//     API to populate build_rev.txt for /api/health surfacing.
//   R-F547 (2026-05-15): same-day prod outage when the API call
//     silently failed; added a date-stamp fallback.
//   R-F570 (2026-05-16): operator directive [[aria_mirrors_claude]] —
//     "if Claude Code doesn't depend on it, ARIA shouldn't either."
//     Replace runtime fetch with `git rev-parse HEAD` against the
//     local checkout. Zero network calls at build time. Falls back to
//     a date stamp if .git isn't present (e.g. a shallow Docker
//     build without the git context).

import { writeFileSync } from 'fs';
import { execSync } from 'child_process';

function gitCmd(args, timeoutMs = 5000) {
  return execSync(args, {
    encoding: 'utf8',
    timeout: timeoutMs,
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

let rev;
let usedFallback = false;

try {
  const sha = gitCmd('git rev-parse --short HEAD');
  if (!sha) throw new Error('empty sha');
  // %cs is committer date in short ISO form (YYYY-MM-DD). %s is subject.
  const date = gitCmd('git log -1 --pretty=%cs');
  const subject = gitCmd('git log -1 --pretty=%s').slice(0, 200);
  rev = `${sha} · ${date} · ${subject}`;
  console.log(`[sync R-F570] build_rev.txt → ${rev}`);
} catch (err) {
  usedFallback = true;
  rev = `unknown-sha · ${new Date().toISOString().slice(0, 10)} · git-unavailable-at-build`;
  console.warn(`[sync R-F570] git unavailable (${err.message}) — falling back: ${rev}`);
}

try {
  writeFileSync('build_rev.txt', rev, 'utf8');
} catch (err) {
  console.error(`[sync R-F570] build_rev.txt write failed: ${err.message}`);
  // Non-fatal — server.mjs handles a missing build_rev.txt gracefully
  // by reporting the CRUCIX_BUILD_REV env var or UNKNOWN-BUILD.
}

if (usedFallback) {
  // Exit clean — a missing git context is not a build failure. The
  // operator gets the date stamp + a WARNING in the log to triage.
  process.exit(0);
}
