// R-F2557 — Golden Intel promotion bridge (Node -> brain push).
//
// aria-web (Node) and aria-intel (Python) share NO store, so BD Intelligence +
// OpenSanctions findings reach the Python promotion bridge only via this HTTP push
// to POST /api/aria/intel/promote/ingest. The Python bridge normalizes, dedups and
// gates them; a SERVER-SIDE honesty policy additionally caps OpenSanctions to the
// Mining Queue (heuristic feed, OPENSANCTIONS_API_KEY unset in prod).
//
// Honesty / privacy notes:
//  - Opportunities are mapped to GENERIC market/procurement signals. Arkmurus-specific
//    OEM matches and internal scores are deliberately NOT put in the public why_it_matters
//    or entities — a public feed must not leak our BD positioning.
//  - Opportunities pending export-control review are downgraded + flagged, never HIGH.

const INGEST_PATH = '/api/aria/intel/promote/ingest';
const PROMOTION_TIMEOUT_MS = 20000;

function _ingestUrl() {
  const base = (process.env.ARIA_SERVICE_URL || process.env.ARIA_BRAIN_URL || '').replace(/\/+$/, '');
  return base ? `${base}${INGEST_PATH}` : '';
}

function _headers() {
  const token = (
    process.env.ARIA_INTERNAL_TOKEN
    || process.env.ARIA_SERVICE_TOKEN
    || process.env.ARIA_API_TOKEN
    || ''
  ).trim();
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function _procurementUrl(opp) {
  const src = (opp.sources || []).find(s => s && s.isProcurement && s.url)
    || (opp.sources || []).find(s => s && s.url);
  return src && /^https?:\/\//i.test(String(src.url)) ? src.url : '';
}

// Opportunity -> generic market/procurement signal (no Arkmurus OEM/score leak).
function _mapOpportunity(opp) {
  if (!opp || !opp.market) return null;
  const score = Number(opp.score) || 0;
  const url = _procurementUrl(opp);
  const reviewRequired = opp.complianceStatus === 'REVIEW_REQUIRED';
  let priority = 'LOW';
  if (score >= 65 && url && !reviewRequired) priority = 'HIGH';
  else if (score >= 45) priority = 'MEDIUM';
  const confidence = score >= 72 ? 'HIGH' : score >= 50 ? 'MEDIUM' : 'LOW';
  const events = (opp.conflict && opp.conflict.events) || 0;
  const needs = (opp.procurementNeeds || []).filter(n => n && n !== 'monitoring').slice(0, 3);
  const why = [
    events > 0 ? `${events} active conflict events` : null,
    needs.length ? `procurement needs: ${needs.join(', ')}` : null,
    reviewRequired ? 'export-control review required before engagement' : null,
  ].filter(Boolean).join('; ') || `${opp.explorerSignals || 0} intel signal(s) detected`;
  return {
    signal_type: 'programme_signal',
    // R-F2557 (review #5): do NOT forward the raw Arkmurus composite score to the
    // public signal — let the Python normalizer derive score from confidence.
    priority, confidence,
    source_tier: 'tier_2',
    source: `Opportunity: ${opp.market}`,
    title: `${opp.market}: active procurement/market window`,
    why_it_matters: why,
    recommended_action: reviewRequired
      ? 'Compliance review required — screen export-control before engagement.'
      : 'Assess market entry — review the linked procurement notice and deadlines.',
    target: opp.market,
    entities: { countries: [opp.market], products: [], oems: [] },
    evidence_url: url,
    url,
    // R-F2557 (review #1, HIGH): STABLE ref. opp.id embeds Date.now()
    // (opportunity_engine.mjs:616) which changes every sweep and would defeat the
    // Python dedup → re-promote + flood the 500-slot list AND Distribution Ready
    // every sweep. Anchor on the procurement URL (a specific notice) else the market,
    // so the same market's opportunity dedups within the cooldown window.
    ref: url || `${opp.iso2 || ''}-${String(opp.market || '').toLowerCase()}`,
    detected_at: opp.detectedAt || new Date().toISOString(),
    evidence_count: 1,
    category: 'bd_opportunity',
  };
}

// OpenSanctions multi-list / pre-designation entry -> sanctions_change finding.
// Python caps this source to MEDIUM/tier_2 (Mining Queue) regardless of these values.
function _mapSanctions(entry) {
  const name = entry && entry.name ? String(entry.name).trim() : '';
  if (!name) return null;
  const lists = Array.isArray(entry.datasets) ? entry.datasets : [];
  const evidenceUrl = entry.citation_url || entry.url || 'https://sanctionssearch.ofac.treas.gov/';
  return {
    signal_type: 'sanctions_change',
    priority: 'MEDIUM', confidence: 'MEDIUM', score: 55,
    source_tier: 'tier_2',
    source: 'OpenSanctions / OFAC',
    title: `${name}: multi-list sanctions appearance`,
    why_it_matters: entry.text
      || `${name} appears on ${lists.length || 'multiple'} sanctions list(s)`
         + (lists.length ? ` (${lists.slice(0, 4).join(', ')})` : ''),
    recommended_action: 'Monitor for official designation; screen counterparties before engagement.',
    target: name,
    entities: { countries: entry.country ? [entry.country] : [], products: [], oems: [] },
    evidence_url: evidenceUrl,
    url: /^https?:\/\//i.test(String(entry.citation_url || entry.url || '')) ? (entry.citation_url || entry.url) : '',
    ref: entry.id || `${name}|${entry.lastChange || ''}`,
    detected_at: entry.lastChange || new Date().toISOString(),
    evidence_count: 1,
    category: 'sanctions',
  };
}

function _mapCSLHit(hit) {
  const name = hit && hit.name ? String(hit.name).trim() : '';
  if (!name) return null;
  const lists = Array.isArray(hit.lists) ? hit.lists.filter(Boolean).slice(0, 5) : [];
  const sourceList = String(hit.sourceList || lists[0] || 'Consolidated Screening List').trim();
  const url = /^https?:\/\//i.test(String(hit.url || '')) ? hit.url : 'https://developer.trade.gov/';
  const term = String(hit.term || name).trim();
  return {
    signal_type: 'sanctions_change',
    priority: 'HIGH',
    confidence: 'HIGH',
    score: 90,
    source_tier: 'tier_1a',
    source: `trade.gov CSL: ${sourceList}`,
    title: `${name}: official CSL match`,
    why_it_matters: `${name} matched public watchlist term "${term}" on ${sourceList}. This is an official US export/sanctions screening source.`,
    recommended_action: 'Screen counterparties; pause export or bid activity until compliance review is complete.',
    target: name,
    entities: { countries: hit.country ? [hit.country] : [], products: [], oems: [] },
    evidence_url: url,
    url,
    ref: hit.id || `${name}|${sourceList}|${term}`,
    detected_at: new Date().toISOString(),
    evidence_count: 1,
    category: 'export_control',
    customer_value: {
      score: 90,
      segments: ['compliance_officer', 'defence_exporter', 'broker_or_intermediary'],
      problems: ['export_control_risk', 'sanctions_risk', 'counterparty_risk'],
      aria_added: ['compliance_implication', 'watchlist_match'],
    },
  };
}

// R-F3545 — BIS export-control RULES become graded intel.
//
// The Federal Register feed was fetched every sweep, rendered on the dashboard,
// and promoted nowhere: `pushPromotionsToBrain` pushed opportunities,
// OpenSanctions and CSL, so `synthesized.exportControlActions` reached a widget
// and stopped. It is official primary evidence (the US government's publication
// of record) sitting one mapper away from being Grade-A intelligence, and it is
// the one alarming class nothing else in ARIA covers: a designation lands in the
// sanctions diff, but "BIS just rewrote drone export controls" or "the UAE now
// gets enhanced favourable treatment" appears in no other lane.
//
// Typed `sanctions_change` deliberately. It is a change in what you may lawfully
// ship and to whom, which is the same decision a designation forces, and that
// signal type already routes to the compliance lane carrying `export_control_risk`
// (golden_intel_bridge._customer_value_lane).
const _EC_COUNTRY_HINTS = [
  'United Arab Emirates', 'Saudi Arabia', 'South Korea', 'North Korea',
  'United Kingdom', 'Hong Kong', 'Cambodia', 'China', 'Russia', 'Belarus',
  'Iran', 'Israel', 'India', 'Turkey', 'Ukraine', 'Venezuela', 'Cuba',
  'Syria', 'Myanmar', 'Nicaragua', 'Pakistan', 'Japan', 'Taiwan',
];

function _mapExportControlRule(u) {
  const url = String(u?.url || '').trim();
  // The channel gate and the grader BOTH require a real evidence URL; a rule we
  // cannot link to is not publishable, and shipping it without one would fail
  // closed further downstream anyway.
  if (!/^https?:\/\//i.test(url)) return null;
  // Titles arrive with a leading emoji from the shared Federal Register mapper.
  const title = String(u?.title || '').replace(/^[^\p{L}\p{N}]+/u, '').trim();
  if (!title) return null;
  const doc = String(u?.documentNumber || '').trim();
  const countries = _EC_COUNTRY_HINTS.filter((c) => title.includes(c));
  const recent = String(u?.priority || '').toLowerCase() === 'high';
  return {
    source_key: 'bis_export_controls',
    source: 'US BIS export-control rule (Federal Register)',
    signal_type: 'sanctions_change',
    priority: recent ? 'HIGH' : 'MEDIUM',
    confidence: 'HIGH',
    source_tier: 'tier_1a',
    title,
    why_it_matters:
      'A published US export-control rule changes what may lawfully be exported, '
      + 'to whom, and under which licence exception. Existing licence positions, '
      + 'quotes and in-flight shipments may no longer be valid.',
    recommended_action:
      'Review licence positions and classifications against this rule before '
      + 'quoting or shipping affected items.',
    target: countries[0] || 'US export controls',
    // The named artefact IS the rule: a specific, citable Federal Register
    // document. Countries are added when the title names one, which is what
    // makes it findable against a portfolio.
    entities: { countries, products: [], oems: [], events: doc ? [doc] : [title.slice(0, 80)] },
    evidence_url: url,
    url,
    ref: doc || url,
    detected_at: u?.timestamp ? new Date(u.timestamp).toISOString() : new Date().toISOString(),
    evidence_count: 1,
    category: 'export_control',
    customer_value: {
      score: 88,
      segments: ['compliance_officer', 'defence_exporter', 'broker_or_intermediary'],
      problems: ['export_control_risk', 'sanctions_risk'],
      aria_added: ['compliance_implication'],
    },
  };
}

async function _post(source, findings) {
  const url = _ingestUrl();
  if (!url) return { accepted: 0, skipped: 'no ARIA_SERVICE_URL' };
  if (!findings.length) return { accepted: 0, skipped: 'empty' };
  try {
    const res = await fetch(url, {
      method: 'POST', headers: _headers(),
      body: JSON.stringify({ source, findings }),
      signal: AbortSignal.timeout(PROMOTION_TIMEOUT_MS),
    });
    if (!res.ok) {
      console.warn(`[PromotionBridge] ingest ${source} -> HTTP ${res.status}`);
      return { accepted: 0, status: res.status };
    }
    const j = await res.json().catch(() => ({}));
    return { accepted: j.accepted || 0 };
  } catch (err) {
    console.warn(`[PromotionBridge] ingest ${source} failed: ${err.message}`);
    return { accepted: 0, error: err.message };
  }
}

// R-F2718 — send each source batch CONCURRENTLY with per-source isolation. Each
// _post carries its OWN AbortSignal timeout + try/catch and never throws, so a slow
// or failing source (e.g. OpenSanctions timing out at 20s) can no longer delay or
// zero-out the others (e.g. the stronger official Trade.gov CSL feed). Wall-clock
// becomes the slowest SINGLE source, not the sum of all sources.
async function _postParallel(batches) {
  const results = await Promise.all(
    batches.map(async (batch) => [batch.source, await _post(batch.source, batch.findings)]),
  );
  return Object.fromEntries(results);
}

// Build findings from a completed sweep + push them to the Python bridge.
export async function pushPromotionsToBrain(synthesized) {
  if (!synthesized) return { opportunities: 0, sanctions: 0, csl: 0, exportControls: 0 };
  const opps = (synthesized.opportunities || []).map(_mapOpportunity).filter(Boolean).slice(0, 30);
  const os = synthesized.opensanctions || {};
  const sanctionsEntries = [...(os.preDesignation || []), ...(os.recent || [])].slice(0, 20);
  const sanctions = sanctionsEntries.map(_mapSanctions).filter(Boolean);
  const cslHits = Array.isArray(synthesized.csl?.recent) ? synthesized.csl.recent : [];
  const csl = cslHits.map(_mapCSLHit).filter(Boolean).slice(0, 20);
  // R-F3545 — the fourth lane. Fetched and rendered since R-F2416, promoted never.
  const ecUpdates = Array.isArray(synthesized.exportControlActions?.updates)
    ? synthesized.exportControlActions.updates : [];
  const exportControls = ecUpdates.map(_mapExportControlRule).filter(Boolean).slice(0, 20);
  const posted = await _postParallel([
    { source: 'bd_intelligence', findings: opps },
    { source: 'opensanctions', findings: sanctions },
    { source: 'trade_gov_csl', findings: csl },
    { source: 'bis_export_controls', findings: exportControls },
  ]);
  const r1 = posted.bd_intelligence || {};
  const r2 = posted.opensanctions || {};
  const r3 = posted.trade_gov_csl || {};
  const r4 = posted.bis_export_controls || {};
  console.log(`[PromotionBridge] pushed opportunities=${r1.accepted || 0} sanctions=${r2.accepted || 0} csl=${r3.accepted || 0} export_controls=${r4.accepted || 0}`);
  return {
    opportunities: r1.accepted || 0,
    sanctions: r2.accepted || 0,
    csl: r3.accepted || 0,
    exportControls: r4.accepted || 0,
    errors: Object.fromEntries(
      Object.entries(posted)
        .filter(([, r]) => r && (r.error || r.status))
        .map(([source, r]) => [source, r.error || `HTTP ${r.status}`])
    ),
  };
}

// exported for unit tests
export const _test = { _mapOpportunity, _mapSanctions, _mapCSLHit, _mapExportControlRule, _postParallel };
