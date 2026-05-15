// sync.mjs — fetches latest source files from GitHub during Seenode build
// Runs as part of the build command to bypass stale Docker layer cache.
// Uses Node.js built-in fetch (Node 18+). No extra deps required.

import { writeFileSync, mkdirSync } from 'fs';

const REPO  = 'Arkmurus/crucix';
const BRANCH = 'main';
const BASE  = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;

const FILES = [
  'lib/whatsapp/ariaWhatsApp.mjs',
  'lib/whatsapp/waListener.mjs',
  'services/wa-listener/aria_wa_listener.mjs',
  'lib/aria/emailReader.mjs',
  'lib/aria/proactive.mjs',
  'lib/aria/linkedinIntel.mjs',
  'lib/aria/query_evolution.mjs',
  'lib/aria/aria.mjs',
  'lib/aria/knowledge.mjs',
  'lib/aria/contacts.mjs',
  'lib/aria/competitors.mjs',
  'lib/aria/approach.mjs',
  'lib/aria/gtm_strategy.mjs',
  'lib/aria/intel_ledger.mjs',
  'lib/aria/training_data.mjs',
  'lib/aria/prompt_optimizer.mjs',
  'lib/aria/seed_knowledge.mjs',
  'lib/aria/entityMatcher.mjs',
  'lib/aria/webhooks.mjs',
  'lib/aria/pipeline.mjs',
  'lib/aria/backup.mjs',
  'dashboard/inject.mjs',
  'lib/alerts/telegram.mjs',
  'lib/alerts/email.mjs',
  'lib/aria/complianceAudit.mjs',
  'lib/intel/correlate.mjs',
  'lib/intel/dedup.mjs',
  'lib/intel/oem_db.mjs',
  'lib/search/engine.mjs',
  'lib/self/bd_intelligence.mjs',
  'lib/self/opportunity_engine.mjs',
  'apis/briefing.mjs',
  'apis/sources/afdb.mjs',
  'apis/sources/cyber_threats.mjs',
  'apis/sources/defense_news.mjs',
  'apis/sources/export_control_intel.mjs',
  'apis/sources/gdelt.mjs',
  'apis/sources/lusophone.mjs',
  'apis/sources/opensanctions.mjs',
  'apis/sources/port_congestion.mjs',
  'apis/sources/supply_chain.mjs',
  'apis/sources/telegram.mjs',
];

console.log(`[sync] Pulling ${FILES.length} files from github.com/${REPO}@${BRANCH}...`);

let ok = 0, fail = 0;

for (const f of FILES) {
  try {
    const res = await fetch(`${BASE}/${f}`, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const content = await res.text();
    const dir = f.includes('/') ? f.substring(0, f.lastIndexOf('/')) : null;
    if (dir) mkdirSync(dir, { recursive: true });
    writeFileSync(f, content, 'utf8');
    console.log(`[sync] ✓ ${f}`);
    ok++;
  } catch (err) {
    console.error(`[sync] ✗ ${f}: ${err.message}`);
    fail++;
  }
}

console.log(`[sync] Done: ${ok} synced, ${fail} failed`);

// R-F534 (2026-05-15) — capture the deployed commit at build time so
// /api/health reports the actual HEAD instead of a hand-edited
// CRUCIX_BUILD_REV constant that drifted (was reporting R-F433 while
// R-F532 was already live). Hit the GitHub API for the latest commit
// on main, stash sha + subject into build_rev.txt; server.mjs reads
// at startup and falls back to a clear sentinel if the file is
// missing.
try {
  const headRes = await fetch(
    `https://api.github.com/repos/${REPO}/commits/${BRANCH}`,
    {
      headers: { 'Accept': 'application/vnd.github+json', 'User-Agent': 'crucix-sync' },
      signal: AbortSignal.timeout(10000),
    },
  );
  if (headRes.ok) {
    const data = await headRes.json();
    const sha = (data.sha || '').slice(0, 8);
    const subject = ((data.commit && data.commit.message) || '').split('\n')[0].slice(0, 200);
    const dateIso = (data.commit && data.commit.author && data.commit.author.date) || new Date().toISOString();
    const rev = `${sha} · ${dateIso.slice(0, 10)} · ${subject}`;
    writeFileSync('build_rev.txt', rev, 'utf8');
    console.log(`[sync] build_rev.txt → ${rev}`);
  } else {
    console.warn(`[sync] build_rev fetch HTTP ${headRes.status} — leaving build_rev.txt unset`);
  }
} catch (err) {
  console.warn(`[sync] build_rev fetch failed: ${err.message} — leaving build_rev.txt unset`);
}
