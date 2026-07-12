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

async function _post(source, findings) {
  const url = _ingestUrl();
  if (!url) return { accepted: 0, skipped: 'no ARIA_SERVICE_URL' };
  if (!findings.length) return { accepted: 0, skipped: 'empty' };
  try {
    const res = await fetch(url, {
      method: 'POST', headers: _headers(),
      body: JSON.stringify({ source, findings }),
      signal: AbortSignal.timeout(12000),
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

// Build findings from a completed sweep + push them to the Python bridge.
export async function pushPromotionsToBrain(synthesized) {
  if (!synthesized) return { opportunities: 0, sanctions: 0 };
  const opps = (synthesized.opportunities || []).map(_mapOpportunity).filter(Boolean).slice(0, 30);
  const os = synthesized.opensanctions || {};
  const sanctionsEntries = [...(os.preDesignation || []), ...(os.recent || [])].slice(0, 20);
  const sanctions = sanctionsEntries.map(_mapSanctions).filter(Boolean);
  const [r1, r2] = await Promise.all([
    _post('bd_intelligence', opps),
    _post('opensanctions', sanctions),
  ]);
  console.log(`[PromotionBridge] pushed opportunities=${r1.accepted || 0} sanctions=${r2.accepted || 0}`);
  return { opportunities: r1.accepted || 0, sanctions: r2.accepted || 0 };
}

// exported for unit tests
export const _test = { _mapOpportunity, _mapSanctions };
