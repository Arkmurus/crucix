// Shared Federal Register API client for sweep sources (R-F2416).
//
// Free, no API key required. Returns REAL, dated US federal agency actions
// (sanctions notices, export-control rules, etc.) in the standard sweep-source
// shape ({ source, status, updates[], signals[], metrics, counts }).
//
// SHIP-GATE (R-F2411/R-F2416): honest-empty on failure. When the fetch fails,
// times out, or returns nothing, we return status 'error'/'degraded' with
// EMPTY updates/signals — never fabricated content. This is what lets the
// dashboard surface these feeds without ever showing a fabricated delta.

import '../utils/env.mjs';

const BASE = 'https://www.federalregister.gov/api/v1/documents.json';
const TIMEOUT_MS = 12000;

function isRecent(dateStr, days = 45) {
  const t = Date.parse(dateStr);
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) <= days * 86400000;
}

export async function fetchFederalRegister(opts) {
  const {
    sourceName,
    emoji = '📋',
    agencies = [],
    types = [],
    term = null,
    perPage = 12,
    searchUrl = null,
  } = opts;

  const params = new URLSearchParams();
  agencies.forEach((a) => params.append('conditions[agencies][]', a));
  types.forEach((t) => params.append('conditions[type][]', t));
  if (term) params.append('conditions[term]', term);
  params.append('per_page', String(perPage));
  params.append('order', 'newest');
  ['title', 'publication_date', 'html_url', 'document_number', 'type']
    .forEach((f) => params.append('fields[]', f));
  const url = `${BASE}?${params.toString()}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Crucix/1.0 (+https://intel.arkmurus.com)',
        Accept: 'application/json',
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const results = Array.isArray(data.results) ? data.results : [];

    const updates = results.map((r) => ({
      source: sourceName,
      title: `${emoji} ${r.title || sourceName + ' action'}`,
      content: `${r.type || 'Notice'} · ${r.publication_date || ''}${r.html_url ? ' · ' + r.html_url : ''}`,
      url: r.html_url || null,
      documentNumber: r.document_number || null,
      timestamp: r.publication_date ? Date.parse(r.publication_date) : Date.now(),
      priority: isRecent(r.publication_date) ? 'high' : 'normal',
    }));

    const signals = results
      .slice(0, 6)
      .map((r) => `${emoji} ${r.publication_date || ''}: ${r.title || ''}`.trim());

    return {
      source: sourceName,
      timestamp: new Date().toISOString(),
      status: updates.length ? 'active' : 'degraded',
      updates,
      signals,
      metrics: {
        recentActions: updates.length,
        latestAction: results[0]?.publication_date || null,
        totalMatched: typeof data.count === 'number' ? data.count : null,
        provider: 'Federal Register API',
        searchUrl,
      },
      counts: { updates: updates.length, signals: signals.length },
    };
  } catch (error) {
    // Honest failure — NO fabricated content (ship-gate).
    return {
      source: sourceName,
      timestamp: new Date().toISOString(),
      status: 'error',
      error: String((error && error.message) || error),
      updates: [],
      signals: [],
      metrics: { provider: 'Federal Register API', searchUrl },
      counts: { updates: 0, signals: 0 },
    };
  } finally {
    clearTimeout(timer);
  }
}
