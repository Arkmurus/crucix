import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..');
const landingRaw = readFileSync(join(repoRoot, 'public', 'index.html'), 'utf8');
// R-F2826 — audit CONTENT, not commentary. HTML comments legitimately quote the
// very strings this guard bans (a comment documenting why "24/7" was removed must
// not itself trip the "24/7" check), and a guard that fires on its own explanatory
// notes trains people to weaken it. Structural checks below that need the raw
// markup use `landingRaw`.
const landing = landingRaw.replace(/<!--[\s\S]*?-->/g, '');
const manifest = JSON.parse(
  readFileSync(join(repoRoot, 'public', 'capability-claims.json'), 'utf8'),
);

const forbiddenPhrases = [
  'No external dependencies',
  'Sovereign LLM  AUTO',
  'Nothing missed',
  'Every finding. <em>Fully traced.</em>',
  'GDPR Compliant',
];

const requiredLandingPhrases = [
  'Decisions you can trace back to evidence.',
  'Uncertainty stays visible.',
  'Human judgement stays in control',
  '20 DD runs / month',
  '100 DD runs / month',
];

// R-F3302 — this used to be a sixth exact phrase, 'with sources, confidence and
// limits made visible'. The property that matters is that the hero promises all
// three of source, confidence and limit; pinning one sentence meant any deliberate
// copy edit read as a truth regression, and a guard that cries wolf on ordinary
// editing is a guard somebody eventually deletes. The concepts are checked in the
// hero paragraph specifically, so the words cannot be satisfied from elsewhere on
// the page.
const requiredHeroConcepts = ['source', 'confidence', 'limit'];

function fail(message) {
  console.error(`landing claim truth failed: ${message}`);
  process.exitCode = 1;
}

for (const phrase of forbiddenPhrases) {
  if (landing.includes(phrase)) {
    fail(`unsupported absolute phrase remains: ${phrase}`);
  }
}

for (const phrase of requiredLandingPhrases) {
  if (!landing.includes(phrase)) {
    fail(`required honest framing missing: ${phrase}`);
  }
}

const heroParagraph = (landing.match(/<div class="hero-content">[\s\S]*?<p>([\s\S]*?)<\/p>/) || [])[1];
if (!heroParagraph) {
  fail('the hero paragraph could not be located, so its honest framing is unchecked');
} else {
  const heroText = heroParagraph.replace(/<[^>]+>/g, ' ').toLowerCase();
  for (const concept of requiredHeroConcepts) {
    if (!heroText.includes(concept)) {
      fail(`the hero states no "${concept}" commitment: ${heroText.trim().slice(0, 120)}`);
    }
  }
}

if (!Array.isArray(manifest.claims) || manifest.claims.length < 5) {
  fail('capability-claims.json must contain at least 5 claim records');
}

const allowedStatuses = new Set([
  'live',
  'live_hardening',
  'live_degraded',
  'conditional',
  'roadmap',
]);

for (const claim of manifest.claims || []) {
  for (const key of ['id', 'label', 'status', 'public_wording']) {
    if (!claim[key]) fail(`claim missing ${key}: ${JSON.stringify(claim)}`);
  }
  if (!allowedStatuses.has(claim.status)) {
    fail(`claim ${claim.id} has invalid status ${claim.status}`);
  }
  if (!Array.isArray(claim.evidence) || claim.evidence.length === 0) {
    fail(`claim ${claim.id} has no evidence references`);
  }
  if (!Array.isArray(claim.required_to_call_complete) || claim.required_to_call_complete.length === 0) {
    fail(`claim ${claim.id} has no completion requirements`);
  }
}

// ── R-F2826 — the guard used to check PHRASES ONLY, and only in the OUTPUT ────
//
// Two holes, both load-bearing:
//
//  (1) It never inspected a NUMBER. So all four hero metrics passed unexamined,
//      including two that were provably false against the shipped code:
//      "24/7 autonomous watchlist monitoring" (WEEKLY-DD-WATCHLIST is
//      cron "0 7 * * mon") and "10 DD pipeline layers" (dd_orchestrator.py:5 says
//      seven). A truth guard that cannot see a number cannot guard a claim like
//      "80+ countries".
//
//  (2) It ran against public/index.html — the OUTPUT — while every banned phrase
//      still sat in scripts/build_landing_page.py, the GENERATOR. The entire
//      forbidden set was one `python scripts/build_landing_page.py` away from
//      returning, with the guard green the whole time. Auditing the artefact and
//      not the thing that produces it is how a fixed defect comes back.
//
// The numeric checks below read the SOURCE OF TRUTH (tasks.yaml, the orchestrator,
// political_risk_index) rather than a number written here, so they cannot go stale
// on their own — if the product changes, the guard fails and the page gets updated.
// That is deliberately better than a live fetch for these three: they are product
// facts, not counts, and a CI failure is a stronger contract than a runtime hydrate.

const generatorPath = join(repoRoot, 'scripts', 'build_landing_page.py');
let generator = '';
try { generator = readFileSync(generatorPath, 'utf8'); } catch { generator = ''; }

if (generator) {
  for (const phrase of forbiddenPhrases) {
    // 'Every finding. <em>Fully traced.</em>' is HTML-shaped; compare on its text.
    const bare = phrase.replace(/<[^>]+>/g, '');
    if (generator.includes(phrase) || generator.includes(bare)) {
      fail(
        `forbidden phrase still present in the GENERATOR (${'scripts/build_landing_page.py'}): ` +
        `"${bare}" — regenerating the landing page would reintroduce it`,
      );
    }
  }
} else {
  fail('could not read scripts/build_landing_page.py — the generator must be audited too');
}

// ── Numeric claims, each checked against the file that defines the truth ──────
function readOrEmpty(...parts) {
  try { return readFileSync(join(repoRoot, ...parts), 'utf8'); } catch { return ''; }
}

// DD pipeline layers — dd_orchestrator.py's own header is the definition.
const orchestrator = readOrEmpty('aria_service', 'intel', 'dd_orchestrator.py');
const layerMatch = /The (\d+)-layer due-diligence orchestrator/.exec(orchestrator);
if (!layerMatch) {
  fail('could not read the DD layer count from dd_orchestrator.py — claim unverifiable');
} else {
  const layers = layerMatch[1];
  const claimed = /<div class="lp-metric-n">(\d+)<\/div>\s*<div class="lp-metric-l">DD pipeline layers</.exec(landing);
  if (claimed && claimed[1] !== layers) {
    fail(`landing claims ${claimed[1]} DD pipeline layers; dd_orchestrator.py says ${layers}`);
  }
}

// Watchlist cadence — the cron in tasks.yaml is the definition. A weekly cron may
// never be sold as continuous.
const tasks = readOrEmpty('aria_service', 'autonomous', 'tasks.yaml');
// Window must clear the task's multi-line description block (~15 lines) before
// reaching its cron; 400 chars stopped short and made the claim "unverifiable",
// which would have let the 24/7 claim through on a technicality.
const watchlistCron = /id:\s*WEEKLY-DD-WATCHLIST[\s\S]{0,2500}?cron:\s*"([^"]+)"/.exec(tasks);
if (!watchlistCron) {
  fail('could not read WEEKLY-DD-WATCHLIST cron from tasks.yaml — cadence claim unverifiable');
} else {
  const isContinuous = /^\S+\s+\S+\s+\*\s+\*\s+\*$/.test(watchlistCron[1]);
  if (!isContinuous && /24\s*\/\s*7/.test(landing)) {
    fail(
      `landing advertises "24/7" but WEEKLY-DD-WATCHLIST runs on cron "${watchlistCron[1]}" ` +
      '— a weekly job must not be sold as continuous monitoring',
    );
  }
}

// Countries — political_risk_index._SEED is the only countable backing.
const pri = readOrEmpty('aria_service', 'intel', 'political_risk_index.py');
if (/countries_covered/.test(pri)) {
  const claimedCountries = /<div class="lp-metric-n">(\d+)<\/div>\s*<div class="lp-metric-l">Countries/.exec(landing);
  if (claimedCountries) {
    const seedEntries = (pri.match(/^\s{4}"[^"]+":\s*\{/gm) || []).length;
    // Only assert when the seed shape is machine-readable; never fail on a parse miss.
    if (seedEntries > 0 && Number(claimedCountries[1]) > seedEntries) {
      fail(
        `landing claims ${claimedCountries[1]} countries; political_risk_index._SEED ` +
        `holds ${seedEntries} — a coverage number must not exceed its backing`,
      );
    }
  }
}

// No unbacked "N+"-style scale claims may be hardcoded in the hero. Counts must be
// hydrated (id + live fetch) so they cannot silently drift, or stated exactly.
const heroBlock = /<div class="lp-hero-metrics">([\s\S]*?)<\/div>\s*<\/div>\s*<\/section>/.exec(landing);
if (heroBlock) {
  const approx = [...heroBlock[1].matchAll(/<div class="lp-metric-n"[^>]*>([^<]*\d[^<]*\+)</g)];
  for (const m of approx) {
    const el = m[0];
    if (!/id="lp-metric-/.test(el)) {
      fail(
        `hero metric "${m[1].trim()}" is a hardcoded approximate figure with no live source — ` +
        'give it an id and hydrate it, or state the exact verifiable number',
      );
    }
  }
}

// ── Fabricated demo content must never state an OUTCOME ──────────────────────
//
// R-F2831 — R-F2824 labelled the demo "illustrative" and left the invented results
// in place: the hero card still rendered `⤷ sanctions ✓ CLEAR`, a `CONFIRMED`
// evidence grade, `0.99` per-source confidences and an `AMBER · proceed with
// conditions` verdict, and the terminal demo carried a second `✓ CLEAR`. This guard
// PASSED the whole time, because it only banned the "live screening" LABEL.
//
// A label is not a control. A cropped screenshot of that card is indistinguishable
// from a real clean screening, and the disclaimer does not travel with the image.
// For a product whose USP is "never a false clean", a fabricated CLEAR on its own
// front page is the single most direct contradiction available.
//
// THE RULE: the illustration may show the pipeline's SHAPE (stage names, source
// names, where a verdict would appear). It may never state an invented OUTCOME —
// no verdict, no evidence grade, no confidence score.
if (/ARIA\s*·\s*live screening/.test(landing)) {
  fail('the hero demo is labelled "live screening" but renders fabricated example data');
}
for (const tell of ['Khalid Al-Rashid', 'Meridian Trading']) {
  if (landing.includes(tell)) {
    fail(`fabricated entity/individual "${tell}" presented in landing content`);
  }
}
const FABRICATED_OUTCOMES = [
  [/✓\s*CLEAR/i, 'a fabricated sanctions CLEAR verdict'],
  [/>\s*CONFIRMED\s*</, 'a fabricated CONFIRMED evidence grade'],
  [/>\s*ASSESSED\s*</, 'a fabricated ASSESSED evidence grade'],
  [/\b(AMBER|GREEN|RED)\b\s*[:·]/, 'a fabricated risk verdict'],
  [/confidence:\s*0\.\d+/, 'an invented numeric confidence score'],
  [/>\s*0\.\d{2}\s*</, 'an invented per-source confidence score'],
];
for (const [re, what] of FABRICATED_OUTCOMES) {
  if (re.test(landing)) {
    fail(
      `${what} appears in landing content. The illustration may show the pipeline's ` +
      'SHAPE but must never state an invented outcome — a screenshot of a fabricated ' +
      'CLEAR is indistinguishable from a real one, and labelling it does not fix that.',
    );
  }
}

if (!process.exitCode) {
  console.log(
    `landing claim truth passed: ${manifest.claims.length} claims, ` +
    'numeric metrics verified against source, generator audited',
  );
}
