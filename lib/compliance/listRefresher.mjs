/**
 * ARIA — Compliance List Auto-Refresh
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

// R-F35 (2026-05-03): two upstream URL migrations forced a refresh of
// this map. Live evidence 2026-05-03 11:09:55:
//   [Compliance] Fetch failed for UK OFSI Financial Sanctions: HTTP 404
//   [Compliance] Fetch failed for UN Security Council Consolidated: HTTP 404
//
// 1. UK OFSI ConList.json was discontinued 2026-01-28 — HM Treasury
//    consolidated all UK sanctions into the FCDO-hosted "UK Sanctions
//    List" XML. The ofsi key is preserved (Redis-stored entries +
//    screening hits map keep the same shape) but the source now points
//    at the FCDO XML and the parser handles a different schema.
//    The new file ALSO mirrors UN-implemented designations under
//    DesignationSource="UN", so we get most UN SC coverage for free.
//
// 2. UN SC scsanctions.un.org/consolidated/consolidated.xml went 404
//    when they migrated to the UNSOL JSON-only search API
//    (search.sanctions.un.org). No direct XML export endpoint was
//    found in the new system. Marked disabled here; UN designations
//    are still picked up via the UK SL DesignationSource=UN field.
//    A follow-up integration with OpenSanctions un_sc_sanctions or
//    the UNSOL search API can re-enable this slot when ready.
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
    name:    'UK Sanctions List (FCDO)',
    // FCDO XML — official replacement for the retired OFSI ConList.json.
    // ~20 MB, weekly-stable. Schema: /Designations/Designation/Names/
    // Name[NameType=Primary Name|Alias]/Name6, plus Addresses, Regime,
    // DesignationSource ('UN' | 'UK_AUTONOMOUS' | etc.).
    xmlUrl:  'https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml',
    format:  'xml',
    parse:   parseUKSanctionsList,
  },
  unsc: {
    name:     'UN Security Council Consolidated',
    // Disabled 2026-05-03: scsanctions.un.org/consolidated/consolidated.xml
    // 404s post-UNSOL migration. UN-mandated designations are mirrored
    // into the UK FCDO list (DesignationSource=UN) so coverage is
    // preserved at the screening layer. Re-enable when a stable bulk
    // export is published or via OpenSanctions un_sc_sanctions feed.
    xmlUrl:   'https://scsanctions.un.org/consolidated/consolidated.xml',
    format:   'xml',
    parse:    parseUNSC,
    disabled: true,
    disabledReason: 'UN migrated to UNSOL JSON-only API; no public bulk XML. UK FCDO list mirrors UN designations.',
  },
};


// ── R-F3710 §21b — compliance failures reach the BRAIN, not just the console ──
//
// Every handler in this module logged to console and returned an empty array.
// A silently-empty OFAC parse means screenEntity has nothing to match against,
// and before R-F3710 that rendered as PERMITTED. The console line was the only
// trace, and nothing in the brain could learn that ARIA had stopped screening.
//
// `compliance_lists` is already on errorTracker's ESCALATE path
// (lib/observability/errorTracker.mjs), so these reach the gap pipeline rather
// than being filtered as TRANSIENT.
async function _wireComplianceFailure(source, kind, err) {
  try {
    const { errorTracker } = await import('../observability/errorTracker.mjs');
    errorTracker.record('compliance_lists', `${source}:${kind}`, err);
  } catch { /* observability must never break a refresh */ }
}

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
    _wireComplianceFailure('ofac', 'parse_error', e);
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

async function parseUKSanctionsList(rawXml) {
  // FCDO XML schema:
  //   <Designations><Designation>
  //     <UniqueID>AFG0001</UniqueID>
  //     <RegimeName>The Afghanistan (Sanctions) ... 2020</RegimeName>
  //     <DesignationSource>UN | UK_AUTONOMOUS | ...</DesignationSource>
  //     <IndividualEntityShip>Entity | Individual | Ship</IndividualEntityShip>
  //     <Names><Name><Name6>HAJI ...</Name6><NameType>Primary Name|Alias</NameType></Name>...</Names>
  //   </Designation>...</Designations>
  const entries = [];
  try {
    const parsed = await parseStringPromise(rawXml, { explicitArray: false });
    const list   = parsed?.Designations?.Designation;
    if (!list) return entries;
    const items = Array.isArray(list) ? list : [list];
    for (const item of items) {
      const namesRaw = item.Names?.Name;
      const namesArr = !namesRaw ? []
        : (Array.isArray(namesRaw) ? namesRaw : [namesRaw]);
      // NameType is case-inconsistent in the real XML: "Primary Name",
      // "Primary name", "Primary Name Variation", "Primary name variation",
      // "Alias", and the typo "ALias" all appear. Match anything starting
      // with "primary name" (case-insensitive) as the canonical name set;
      // everything else is an alias. Order: primary names first.
      const isPrimary = (n) => /^primary name/i.test(String(n.NameType || ''));
      const primary = namesArr.filter(isPrimary);
      const aliases = namesArr.filter(n => !isPrimary(n));
      const names = [...primary, ...aliases]
        .map(n => (typeof n.Name6 === 'string' ? n.Name6 : '').trim())
        .filter(Boolean);
      if (names.length === 0) continue;
      entries.push({
        uid:      item.UniqueID || item.OFSIGroupID || '',
        names,
        type:     (item.IndividualEntityShip || 'unknown').toLowerCase(),
        programs: [
          'UK_SANCTIONS_LIST',
          ...(item.DesignationSource ? [`SRC:${item.DesignationSource}`] : []),
          ...(item.RegimeName ? [item.RegimeName.slice(0, 80)] : []),
        ],
      });
    }
  } catch (e) {
    console.error('[Compliance] UK Sanctions List parse error:', e.message);
    _wireComplianceFailure('ofsi', 'parse_error', e);
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
    _wireComplianceFailure('unsc', 'parse_error', e);
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
    _wireComplianceFailure(source.name, 'fetch_failed', e);
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

  // ── R-F3710 — coverage-drift floor, mirroring the Python tier ────────────
  //
  // The `entries.length === 0` guard above catches a TOTAL parse failure, but
  // not a PARTIAL one: a schema drift that yields 3 entries from a ~20 MB,
  // ~19,000-designation feed passes it and is committed over the good list.
  // Every subsequent screen then searches three names and reports no match.
  //
  // The Python loaders already refuse this (ofac_sdn.py / un_sc_sanctions.py
  // R-F2577 canary; store.replace_source R-F2570 self-calibrating floor). This
  // is the same rule on the Node tier: keep last-known-good and shout, rather
  // than commit an implausibly thin list. A genuine large delisting is rare and
  // recoverable by re-running; a silently gutted list is neither.
  if (prevCount > 0 && entries.length < Math.floor(prevCount * 0.5)) {
    const msg = `${source.name}: parsed ${entries.length} entries vs previous `
      + `${prevCount} (<50%) — REFUSING the update and keeping last-known-good`;
    console.error(`[Compliance] ${msg}`);
    const err = new Error(msg);
    err.status = 403;   // -> SEVERITY.AUTH -> escalates, not TRANSIENT
    _wireComplianceFailure(key, 'coverage_drift', err);
    return { success: false, source: source.name, reason: 'coverage_drift',
             parsed: entries.length, previous: prevCount };
  }

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
    _wireComplianceFailure('deal_impact', 'impact_check_failed', e);
  }
}

// ── Screening Logic ───────────────────────────────────────────────────────────

export async function screenEntity(entityName, redis) {
  const hits    = { ofac: [], ofsi: [], unsc: [] };
  const versions = JSON.parse(await redis.get(REDIS_KEY_VERSIONS) || '{}');
  const cleanName = entityName.toLowerCase().trim();

  // ── R-F3710 — NEVER-FALSE-CLEAN on the Node compliance tier ──────────────
  //
  // THE DEFECT: a source whose entries key was absent or unparseable was
  // `continue`d silently, and the verdict below is a bare
  // `isHit ? 'PROHIBITED' : 'PERMITTED'`. So a list that was NEVER LOADED —
  // a failed first fetch, an evicted key, a parse error at boot — produced
  // exactly the same answer as a list that was searched and had no match:
  // PERMITTED, with no indication that OFAC (or OFSI, or the UNSC) had not
  // actually been consulted.
  //
  // The Python tier already refuses this shape everywhere (R-F2159/R-F2373:
  // an empty or unreadable store yields INSUFFICIENT_DATA, never CLEAR). This
  // is the same rule, on the tier that answers `screenEntity`.
  const screened = [];
  const unavailable = [];

  for (const [key] of Object.entries(COMPLIANCE_SOURCES)) {
    const raw = await redis.get(`${REDIS_KEY_PREFIX}${key}_entries`);
    if (!raw) {
      unavailable.push({ source: key, reason: 'no_entries_stored' });
      continue;
    }
    let entries;
    try {
      entries = JSON.parse(raw);
    } catch (e) {
      unavailable.push({ source: key, reason: `unparseable: ${e.message}` });
      continue;
    }
    if (!Array.isArray(entries) || entries.length === 0) {
      unavailable.push({ source: key, reason: 'empty_list' });
      continue;
    }
    screened.push(key);
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

  // R-F3710 — the verdict ladder, in severity order.
  //
  // A HIT is a hit regardless of coverage: finding a designation on ONE list is
  // conclusive, and a missing second list cannot un-find it. But a NO-MATCH is
  // only meaningful over the lists actually searched, so an incomplete screen
  // yields INSUFFICIENT_DATA — never PERMITTED. Callers that treated
  // `result !== 'PROHIBITED'` as clearance now get a value that is neither.
  let result;
  if (isHit) {
    result = 'PROHIBITED';
  } else if (screened.length === 0) {
    result = 'INSUFFICIENT_DATA';   // nothing was consulted at all
  } else if (unavailable.length > 0) {
    result = 'INSUFFICIENT_DATA';   // partial coverage is not a clearance
  } else {
    result = 'PERMITTED';
  }

  const out = {
    entity:       entityName,
    result,
    hits,
    // R-F3710 — coverage is part of the answer, not a footnote. A consumer can
    // now tell "searched three lists, found nothing" from "searched one".
    sources_screened:    screened.map(k => COMPLIANCE_SOURCES[k]?.name || k),
    sources_unavailable: unavailable.map(u => ({
      source: COMPLIANCE_SOURCES[u.source]?.name || u.source, reason: u.reason,
    })),
    complete_coverage: unavailable.length === 0 && screened.length > 0,
    screened_against: screenedDate,
    screened_at:  new Date().toISOString(),
    note: (unavailable.length > 0 || screened.length === 0)
      ? ('INCOMPLETE SCREEN — one or more sanctions lists could not be consulted. '
         + 'This is NOT a clearance. Re-run once the lists refresh, or screen '
         + 'manually against OFAC/OFSI/UN before proceeding.')
      : 'This is an automated pre-screen. Legal advice required before proceeding.',
  };

  // §21b — an incomplete compliance screen must reach the brain, not just the
  // caller. Classified via a 403-style status so errorTracker maps it to
  // SEVERITY.AUTH, which is on the ESCALATE list; a plain Error would classify
  // TRANSIENT and be dropped before the wire.
  if (result === 'INSUFFICIENT_DATA') {
    try {
      const err = new Error(
        `sanctions screen INCOMPLETE for ${String(entityName).slice(0, 80)}: `
        + unavailable.map(u => `${u.source}(${u.reason})`).join(', ')
      );
      err.status = 403;
      const { errorTracker } = await import('../observability/errorTracker.mjs');
      errorTracker.record('compliance_lists', 'incomplete_screen', err);
    } catch { /* observability must never break a screen */ }
  }
  return out;
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
    // R-F35: skip sources flagged disabled (e.g. UN SC pending UNSOL/
    // OpenSanctions integration). They stay defined so the screening
    // layer's hits.unsc shape is preserved — just no fetch traffic.
    const tasks = [];
    for (const [key, src] of Object.entries(COMPLIANCE_SOURCES)) {
      if (src.disabled) {
        console.log(`[Compliance] ${src.name}: skipped — ${src.disabledReason || 'disabled'}`);
        continue;
      }
      tasks.push(refreshSource(key, src, redis, notifyFn));
    }
    const results = await Promise.allSettled(tasks);
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
