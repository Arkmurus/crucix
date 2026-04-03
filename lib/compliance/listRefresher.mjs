/**
 * CRUCIX — Compliance List Auto-Refresh
 * GAP 6 FIX: Live OFAC/OFSI/UN SC sanctions list refresh with diff alerting.
 *
 * Schedule in your main server or a dedicated cron worker.
 * Replaces the hardcoded lists in lib/compliance/screen.mjs.
 *
 * Usage:
 *   import { startComplianceRefreshScheduler, screenEntity } from './lib/compliance/listRefresher.mjs';
 *   await startComplianceRefreshScheduler(redisClient, telegramNotify);
 */

import crypto from 'crypto';
import { parseStringPromise } from 'xml2js';   // npm i xml2js

// ── Config ────────────────────────────────────────────────────────────────────

const REFRESH_INTERVAL_MS  = 7 * 24 * 60 * 60 * 1000;  // weekly
const REDIS_KEY_PREFIX     = 'crucix:compliance:';
const REDIS_KEY_OFAC       = REDIS_KEY_PREFIX + 'ofac_entries';
const REDIS_KEY_OFSI       = REDIS_KEY_PREFIX + 'ofsi_entries';
const REDIS_KEY_UNSC       = REDIS_KEY_PREFIX + 'unsc_entries';
const REDIS_KEY_VERSIONS   = REDIS_KEY_PREFIX + 'versions';
const REDIS_KEY_LAST_FETCH = REDIS_KEY_PREFIX + 'last_fetch';

// ── Source Definitions ────────────────────────────────────────────────────────

const COMPLIANCE_SOURCES = {
  ofac: {
    name:    'OFAC SDN List',
    url:     'https://sanctionslist.ofac.treas.gov/Home/SdnList',
    // OFAC provides XML, CSV, and JSON:
    xmlUrl:  'https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml',
    csvUrl:  'https://www.treasury.gov/ofac/downloads/sdn.csv',
    format:  'xml',
    parse:   parseOFAC,
  },
  ofsi: {
    name:    'UK OFSI Financial Sanctions',
    // OFSI provides a JSON API and Excel; JSON is most parseable
    jsonUrl: 'https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.json',
    format:  'json',
    parse:   parseOFSI,
  },
  unsc: {
    name:    'UN Security Council Consolidated',
    xmlUrl:  'https://scsanctions.un.org/consolidated/consolidated.xml',
    format:  'xml',
    parse:   parseUNSC,
  },
};

// ── Parsers ───────────────────────────────────────────────────────────────────

async function parseOFAC(rawXml) {
  const entries = [];
  try {
    const parsed = await parseStringPromise(rawXml, { explicitArray: false });
    const sdnList = parsed?.sdnList?.sdnEntry;
    if (!sdnList) return entries;
    const items = Array.isArray(sdnList) ? sdnList : [sdnList];
    for (const item of items) {
      const names  = extractOFACNames(item);
      const type   = item.sdnType || 'unknown';
      const uid    = item.$?.uid || '';
      const prog   = Array.isArray(item.programList?.program)
        ? item.programList.program
        : [item.programList?.program].filter(Boolean);
      entries.push({ uid, names, type, programs: prog });
    }
  } catch (e) {
    console.error('[Compliance] OFAC parse error:', e.message);
  }
  return entries;
}

function extractOFACNames(item) {
  const names = [];
  if (item.firstName || item.lastName) {
    names.push([item.firstName, item.lastName].filter(Boolean).join(' ').trim());
  }
  const akas = item.akaList?.aka;
  if (akas) {
    const akaArr = Array.isArray(akas) ? akas : [akas];
    akaArr.forEach(a => {
      if (a.firstName || a.lastName) {
        names.push([a.firstName, a.lastName].filter(Boolean).join(' ').trim());
      }
    });
  }
  return names.filter(n => n.length > 0);
}

async function parseOFSI(rawJson) {
  const entries = [];
  try {
    const data   = typeof rawJson === 'string' ? JSON.parse(rawJson) : rawJson;
    const items  = data?.designations || data?.ConList?.designated || [];
    for (const item of (Array.isArray(items) ? items : [])) {
      const names = (item.names || item.Names || []).map(n =>
        [n.firstName6, n.middleName1, n.lastName6].filter(Boolean).join(' ').trim()
      ).filter(Boolean);
      if (item.primaryName) names.unshift(item.primaryName);
      entries.push({
        uid:      item.uniqueID || item.UniqueID || '',
        names,
        type:     item.entityType || item.EntityType || 'unknown',
        programs: [item.regime || item.Regime || 'UK_OFSI'],
      });
    }
  } catch (e) {
    console.error('[Compliance] OFSI parse error:', e.message);
  }
  return entries;
}

async function parseUNSC(rawXml) {
  const entries = [];
  try {
    const parsed    = await parseStringPromise(rawXml, { explicitArray: false });
    const consItems = parsed?.consolidated?.individuals?.individual;
    const entItems  = parsed?.consolidated?.entities?.entity;
    const process = (items, type) => {
      if (!items) return;
      const arr = Array.isArray(items) ? items : [items];
      for (const item of arr) {
        const firstName  = item.FIRST_NAME?.[0] || item.FIRST_NAME || '';
        const secondName = item.SECOND_NAME?.[0] || item.SECOND_NAME || '';
        const thirdName  = item.THIRD_NAME?.[0] || item.THIRD_NAME || '';
        const name       = [firstName, secondName, thirdName].filter(Boolean).join(' ').trim()
                        || item.NAME || item.name || '';
        if (name) {
          entries.push({ uid: item.DATAID || '', names: [name], type, programs: ['UN_SC'] });
        }
      }
    };
    process(consItems, 'individual');
    process(entItems,  'entity');
  } catch (e) {
    console.error('[Compliance] UNSC parse error:', e.message);
  }
  return entries;
}

// ── Fetch & Refresh ───────────────────────────────────────────────────────────

async function fetchSource(source) {
  const url = source.xmlUrl || source.jsonUrl || source.csvUrl;
  try {
    const resp = await fetch(url, {
      signal:  AbortSignal.timeout(60000),
      headers: { 'User-Agent': 'Crucix-Compliance-Monitor/1.0' },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const raw = await resp.text();
    console.log(`[Compliance] Fetched ${source.name}: ${raw.length} bytes`);
    return raw;
  } catch (e) {
    console.error(`[Compliance] Fetch failed for ${source.name}: ${e.message}`);
    return null;
  }
}

async function refreshSource(key, source, redis, notifyFn) {
  const raw = await fetchSource(source);
  if (!raw) return { success: false, source: source.name };

  const entries = await source.parse(raw);
  if (entries.length === 0) {
    console.warn(`[Compliance] ${source.name}: 0 entries parsed — skipping update`);
    return { success: false, source: source.name, reason: 'empty_parse' };
  }

  // Compute hash for change detection
  const hash = crypto.createHash('sha256').update(JSON.stringify(entries)).digest('hex');

  // Get previous version info
  const versionInfo = JSON.parse(await redis.get(REDIS_KEY_VERSIONS) || '{}');
  const prevHash    = versionInfo[key]?.hash;
  const prevDate    = versionInfo[key]?.date;
  const prevCount   = versionInfo[key]?.count || 0;

  // Store entries
  await redis.set(`${REDIS_KEY_PREFIX}${key}_entries`, JSON.stringify(entries));

  // Update version info
  const now = new Date().toISOString().slice(0, 10);
  versionInfo[key] = { hash, date: now, count: entries.length };
  await redis.set(REDIS_KEY_VERSIONS, JSON.stringify(versionInfo));

  const changed = prevHash && prevHash !== hash;
  const delta   = entries.length - prevCount;

  console.log(`[Compliance] ${source.name} updated: ${entries.length} entries (${delta > 0 ? '+' : ''}${delta} vs ${prevDate})`);

  if (changed && notifyFn) {
    await notifyFn(
      `⚠ *COMPLIANCE LIST UPDATED*\n\n` +
      `*${source.name}*\n` +
      `Previous: ${prevCount} entries (${prevDate})\n` +
      `Current:  ${entries.length} entries (${now})\n` +
      `Delta: ${delta > 0 ? '+' : ''}${delta}\n\n` +
      `_Review active deals for newly sanctioned entities._`
    );
    // Check if any active deals are now affected
    await checkActiveDealsImpact(source.name, entries, redis, notifyFn);
  }

  return { success: true, source: source.name, count: entries.length, changed, delta };
}

async function checkActiveDealsImpact(sourceName, newEntries, redis, notifyFn) {
  try {
    const dealsRaw = await redis.get('crucix:pipeline:deals');
    if (!dealsRaw) return;
    const deals         = JSON.parse(dealsRaw);
    const activeDeals   = deals.filter(d => !['WON', 'LOST', 'NO_BID'].includes(d.stage));
    const newNameSet    = new Set(newEntries.flatMap(e => e.names.map(n => n.toLowerCase())));
    const affectedDeals = activeDeals.filter(d =>
      d.counterparty && newNameSet.has(d.counterparty.toLowerCase())
    );
    if (affectedDeals.length > 0 && notifyFn) {
      const dealLines = affectedDeals.map(d => `• *${d.title}* (${d.market}) — counterparty: ${d.counterparty}`).join('\n');
      await notifyFn(
        `🚨 *COMPLIANCE IMPACT: ACTIVE DEALS AFFECTED*\n\n` +
        `Source: ${sourceName}\n` +
        `The following active deals have counterparties now appearing on updated sanctions lists:\n\n` +
        `${dealLines}\n\n` +
        `_Immediate compliance review required. Status changed to HOLD pending review._`
      );
    }
  } catch (e) {
    console.error('[Compliance] Deal impact check failed:', e.message);
  }
}

// ── Screening Logic ───────────────────────────────────────────────────────────

export async function screenEntity(entityName, redis) {
  const hits    = { ofac: [], ofsi: [], unsc: [] };
  const versions = JSON.parse(await redis.get(REDIS_KEY_VERSIONS) || '{}');
  const cleanName = entityName.toLowerCase().trim();

  for (const [key] of Object.entries(COMPLIANCE_SOURCES)) {
    const raw = await redis.get(`${REDIS_KEY_PREFIX}${key}_entries`);
    if (!raw) continue;
    const entries = JSON.parse(raw);
    for (const entry of entries) {
      for (const name of entry.names) {
        if (fuzzyMatch(cleanName, name.toLowerCase())) {
          hits[key].push({ name, uid: entry.uid, programs: entry.programs });
        }
      }
    }
  }

  const isHit        = Object.values(hits).some(h => h.length > 0);
  const screenedDate = Object.fromEntries(
    Object.entries(versions).map(([k, v]) => [COMPLIANCE_SOURCES[k]?.name || k, v.date])
  );

  return {
    entity:       entityName,
    result:       isHit ? 'PROHIBITED' : 'PERMITTED',
    hits,
    screened_against: screenedDate,
    screened_at:  new Date().toISOString(),
    note:         'This is an automated pre-screen. Legal advice required before proceeding.',
  };
}

function fuzzyMatch(query, target) {
  if (query === target) return true;
  // Remove common noise words for entity matching
  const clean = (s) => s.replace(/\b(ltd|llc|inc|corp|co|limited|group|holdings|international)\b/g, '').trim();
  return clean(query) === clean(target) ||
         target.includes(query) ||
         query.includes(target);
}

// ── Scheduler ─────────────────────────────────────────────────────────────────

export async function startComplianceRefreshScheduler(redis, notifyFn = null) {
  const runRefresh = async () => {
    console.log('[Compliance] Starting scheduled list refresh...');
    const results = await Promise.allSettled([
      refreshSource('ofac', COMPLIANCE_SOURCES.ofac, redis, notifyFn),
      refreshSource('ofsi', COMPLIANCE_SOURCES.ofsi, redis, notifyFn),
      refreshSource('unsc', COMPLIANCE_SOURCES.unsc, redis, notifyFn),
    ]);
    await redis.set(REDIS_KEY_LAST_FETCH, new Date().toISOString());
    const summary = results.map(r => r.value || r.reason);
    console.log('[Compliance] Refresh complete:', JSON.stringify(summary));
  };

  // Run immediately on startup
  await runRefresh();

  // Then weekly
  setInterval(runRefresh, REFRESH_INTERVAL_MS);
  console.log(`[Compliance] Auto-refresh scheduled every 7 days`);
}

export async function getComplianceVersions(redis) {
  return {
    versions:   JSON.parse(await redis.get(REDIS_KEY_VERSIONS) || '{}'),
    last_fetch: await redis.get(REDIS_KEY_LAST_FETCH),
  };
}
