// R-F2601 — trade.gov Consolidated Screening List source.
//
// CSL is a SEARCH API, not a reliable bulk "recent changes" feed. This source
// therefore screens explicit public/operator-approved watchlist terms only. It is
// honest-disabled without a key or watchlist and never fabricates broad changes.

import '../utils/env.mjs';

const CSL_URL = 'https://api.trade.gov/consolidated_screening_list/search';
const MAX_TERMS = 25;
const MAX_RESULTS_PER_TERM = 5;

function _clean(v) {
  return String(v || '').trim();
}

export function parseWatchlist(raw = process.env.CSL_WATCHLIST || process.env.ARIA_CSL_WATCHLIST || '') {
  const text = _clean(raw);
  if (!text) return [];
  let terms = [];
  if (text.startsWith('[')) {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) terms = parsed;
    } catch {
      terms = [];
    }
  }
  if (!terms.length) terms = text.split(/[\n;,]/);
  const seen = new Set();
  const out = [];
  for (const term of terms) {
    const t = _clean(term);
    const key = t.toLowerCase();
    if (t.length < 3 || seen.has(key)) continue;
    seen.add(key);
    out.push(t);
    if (out.length >= MAX_TERMS) break;
  }
  return out;
}

function _resultName(r) {
  return _clean(r.name || r.title || r.entity_name || r.full_name || r.names?.[0]);
}

function _resultLists(r) {
  const vals = [
    r.source,
    r.source_list,
    r.list,
    r.program,
    r.list_name,
    ...(Array.isArray(r.sources) ? r.sources : []),
    ...(Array.isArray(r.programs) ? r.programs : []),
  ].flat().map(_clean).filter(Boolean);
  return [...new Set(vals)].slice(0, 8);
}

function _resultUrl(r) {
  const candidates = [
    r.source_list_url,
    r.federal_register_notice,
    r.url,
    r.link,
    r.id ? `https://developer.trade.gov/consolidated-screening-list.html#${encodeURIComponent(String(r.id))}` : '',
  ];
  return candidates.find(u => /^https?:\/\//i.test(String(u || ''))) || 'https://developer.trade.gov/';
}

function _normalizeResult(term, r) {
  const name = _resultName(r);
  if (!name) return null;
  const lists = _resultLists(r);
  const url = _resultUrl(r);
  return {
    id: _clean(r.id || r.entity_number || `${term}:${name}:${lists.join('|')}`),
    term,
    name,
    lists,
    sourceList: lists[0] || 'Consolidated Screening List',
    country: _clean(r.country || r.countries?.[0] || r.addresses?.[0]?.country),
    type: _clean(r.type || r.entity_type || r.schema),
    startDate: _clean(r.start_date || r.effective_date || r.issue_date),
    url,
    raw: r,
  };
}

export async function searchCSL(term, apiKey = process.env.TRADE_GOV_API_KEY || process.env.CSL_API_KEY || '') {
  const key = _clean(apiKey);
  if (!key) return { status: 'disabled_no_key', results: [] };
  const q = _clean(term);
  if (!q) return { status: 'empty_query', results: [] };
  const params = new URLSearchParams({
    api_key: key,
    q,
    size: String(MAX_RESULTS_PER_TERM),
  });
  const res = await fetch(`${CSL_URL}?${params.toString()}`, {
    headers: { Accept: 'application/json', 'User-Agent': 'Crucix/1.0' },
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    return { status: 'error', httpStatus: res.status, error: body.slice(0, 180), results: [] };
  }
  const data = await res.json();
  const rows = Array.isArray(data?.results)
    ? data.results
    : (Array.isArray(data?.data) ? data.data : (Array.isArray(data) ? data : []));
  return {
    status: 'ok',
    total: Number(data?.total || data?.total_results || rows.length) || rows.length,
    results: rows.map(r => _normalizeResult(q, r)).filter(Boolean).slice(0, MAX_RESULTS_PER_TERM),
  };
}

export async function briefing() {
  const apiKey = process.env.TRADE_GOV_API_KEY || process.env.CSL_API_KEY || '';
  if (!apiKey) {
    console.log('[CSL] disabled_no_key');
    return {
      source: 'trade.gov Consolidated Screening List',
      status: 'disabled_no_key',
      reason: 'TRADE_GOV_API_KEY or CSL_API_KEY not configured',
      updates: [],
      signals: [],
      recent: [],
      _subStatus: { ok: 0, total: 1, failed: ['TRADE_GOV_API_KEY'] },
    };
  }
  const terms = parseWatchlist();
  if (!terms.length) {
    console.log('[CSL] disabled_no_watchlist');
    return {
      source: 'trade.gov Consolidated Screening List',
      status: 'disabled_no_watchlist',
      reason: 'CSL_WATCHLIST or ARIA_CSL_WATCHLIST not configured',
      updates: [],
      signals: [],
      recent: [],
      _subStatus: { ok: 0, total: 1, failed: ['CSL_WATCHLIST'] },
    };
  }

  const settled = await Promise.allSettled(terms.map(t => searchCSL(t, apiKey)));
  const hits = [];
  const failed = [];
  settled.forEach((r, idx) => {
    const term = terms[idx];
    if (r.status !== 'fulfilled') {
      failed.push(term);
      return;
    }
    if (r.value.status !== 'ok') {
      failed.push(term);
      return;
    }
    hits.push(...r.value.results);
  });

  const unique = [];
  const seen = new Set();
  for (const h of hits) {
    const key = `${h.name}|${h.sourceList}|${h.id}`.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(h);
  }

  const updates = unique.map(h => ({
    title: `[CSL] ${h.name}: ${h.sourceList}`,
    source: 'trade.gov CSL',
    content: `${h.name} matched watchlist term "${h.term}" on ${h.sourceList}.`,
    url: h.url,
    timestamp: Date.now(),
    priority: 'high',
    type: 'csl_match',
  }));
  const signals = unique.slice(0, 10).map(h => ({
    text: `[CSL] ${h.name} matched ${h.sourceList}`,
    source: 'trade.gov CSL',
    priority: 'high',
  }));

  console.log(`[CSL] ${unique.length} official screening-list hit(s); failed_terms=${failed.length}`);
  return {
    source: 'trade.gov Consolidated Screening List',
    status: failed.length ? 'partial' : 'ok',
    checked: terms.length,
    failedTerms: failed,
    updates,
    signals,
    recent: unique.slice(0, 30),
    _subStatus: {
      ok: terms.length - failed.length,
      total: terms.length,
      failed,
    },
  };
}

if (process.argv[1]?.endsWith('csl.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
