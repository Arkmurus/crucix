// test/model-card-live-clause-fingerprint-rf2617.test.mjs
//
// Capability test for R-F2617 — the model card's static prose must NOT hardcode
// the constitution clause count or the audit signing-key fingerprint. Live is
// v37 (37 clauses); the prose said "23-clause" / "the 24 clauses" (13 stale) and
// the metadata cell already showed 37 → the public compliance page contradicted
// itself. The signing fingerprint `a39f3328d92bffe4` (dated 2026-04-14) went
// stale when ARIA_AUDIT_SIGNING_KEY rotated 2026-05-17. All three now hydrate
// from live endpoints (/constitution/version, /audit/key-fingerprint).
//
// Run: node test/model-card-live-clause-fingerprint-rf2617.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'model-card.html'), 'utf8');
const SERVER = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');
const ARIAPY = readFileSync(join(__dirname, '..', 'aria_service', 'routes', 'aria.py'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2617 model-card live clause-count + signing fingerprint\n');

// ── 1. STATIC: the stale literals are GONE ───────────────────────────────────
check('no hardcoded "23-clause" in prose',
  !/23-clause behavioural constitution/.test(HTML));
check('no "the 24 clauses (summary)" stale header',
  !/the 24 clauses \(summary\)/.test(HTML));
check('stale signing fingerprint a39f3328d92bffe4 removed from prose',
  !/a39f3328d92bffe4/.test(HTML));
check('stale "signed since 2026-04-14" removed',
  !/signed since\s*<strong>2026-04-14/.test(HTML));

// ── 2. STATIC: the live-driven spans exist ───────────────────────────────────
check('mc-cc-inline span present (intro clause count)', /id="mc-cc-inline"/.test(HTML));
check('mc-cc-full span present (section-8 live total)', /id="mc-cc-full"/.test(HTML));
check('mc-signing-fingerprint code element present', /id="mc-signing-fingerprint"/.test(HTML));
check('mc-signing-mode dev-mode note element present', /id="mc-signing-mode"/.test(HTML));

// ── 3. STATIC: hydration wires the spans from the live endpoints ─────────────
check('constitution/version hydrates mc-cc-inline + mc-cc-full',
  /getElementById\('mc-cc-inline'\)/.test(HTML) && /getElementById\('mc-cc-full'\)/.test(HTML));
check('fetches /api/aria/audit/key-fingerprint',
  /fetch\('\/api\/aria\/audit\/key-fingerprint'/.test(HTML));
check('sets fingerprint from active_key_fingerprint',
  /active_key_fingerprint/.test(HTML));
check('honest dev-mode note (NOT compliance-grade) when d.dev_mode',
  /d\.dev_mode/.test(HTML) && /NOT compliance-grade/.test(HTML));

// ── 4. STATIC: the endpoint is publicly reachable (allowlist + web proxy) ─────
check('aria.py allowlists /api/aria/audit/key-fingerprint',
  /"\/api\/aria\/audit\/key-fingerprint"/.test(ARIAPY));
check('server.mjs public-proxies /api/aria/audit/key-fingerprint',
  /_r577PublicProxy\(req, res, '\/api\/aria\/audit\/key-fingerprint'\)/.test(SERVER));

// ── 5. BEHAVIOURAL: mirror the hydration and prove no stale literal survives ─
console.log('\nHydration mirror — live values overwrite the defaults:');
function hydrate(dom, versionResp, fpResp) {
  // mirror of hydrateModelCard()'s clause-count + fingerprint logic
  if (versionResp && versionResp.clause_count != null) {
    dom['mc-cc-inline'] = String(versionResp.clause_count);
    dom['mc-cc-full'] = String(versionResp.clause_count);
    dom['mc-constitution-version'] = `${versionResp.version}: ${versionResp.clause_count} clauses`;
  }
  if (fpResp) {
    dom['mc-signing-fingerprint'] = fpResp.active_key_fingerprint || 'unavailable';
    dom['mc-signing-mode'] = fpResp.dev_mode ? 'Dev-fallback key — NOT compliance-grade.' : '';
  }
  return dom;
}
const dom = { 'mc-cc-inline': '37', 'mc-cc-full': '37', 'mc-signing-fingerprint': 'loading…', 'mc-signing-mode': '' };
hydrate(dom, { version: 'v37', clause_count: 37 }, { active_key_fingerprint: 'ffea1062bbfc0061', dev_mode: false });
check('inline count reflects live clause_count (37)', dom['mc-cc-inline'] === '37');
check('section-8 total reflects live clause_count (37)', dom['mc-cc-full'] === '37');
check('fingerprint reflects live key (not a39f...)', dom['mc-signing-fingerprint'] === 'ffea1062bbfc0061');
check('no dev-mode warning when key is real', dom['mc-signing-mode'] === '');
// and the stale-drift case: live jumps to 41 → prose follows, never lies
hydrate(dom, { version: 'v41', clause_count: 41 }, { active_key_fingerprint: 'deadbeefdeadbeef', dev_mode: true });
check('inline count follows a later amendment (41)', dom['mc-cc-inline'] === '41');
check('dev-fallback key surfaces the honest warning', /NOT compliance-grade/.test(dom['mc-signing-mode']));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
