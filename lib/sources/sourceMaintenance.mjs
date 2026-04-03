/**
 * CRUCIX — Procurement Portal Deduplication + Source Auto-Pruner
 * GAP 5 FIX: Content-hash dedup for procurement portals with TTL suppression
 * GAP 11 FIX: Auto-suspend sources with <15% reliability over 20+ sweeps
 *
 * Usage:
 *   import { ProcurementDedup, SourcePruner } from './lib/sources/sourceMaintenance.mjs';
 */

import crypto from 'crypto';

// ═══════════════════════════════════════════════════════════════════
// PART 1 — PROCUREMENT PORTAL DEDUPLICATION (GAP 5)
// ═══════════════════════════════════════════════════════════════════

const DEDUP_KEY_PREFIX  = 'crucix:procurement:dedup:';
const TENDER_KEY_PREFIX = 'crucix:procurement:tender:';

/**
 * Tender status lifecycle:
 * NEW → ACTIVE → CLOSING_SOON (< 48h) → AWARDED | CANCELLED
 */
const TENDER_STATUS = { NEW: 'NEW', ACTIVE: 'ACTIVE', CLOSING_SOON: 'CLOSING_SOON', AWARDED: 'AWARDED', CANCELLED: 'CANCELLED' };

export class ProcurementDedup {
  constructor(redis) {
    this._redis = redis;
  }

  /**
   * Process a raw tender from procurement_portals.mjs scraper.
   * Returns { isNew, isUpdated, status, tender } or null if duplicate with no change.
   */
  async processTender(rawTender) {
    const tender = this._normaliseTender(rawTender);
    if (!tender.dedupKey) {
      console.warn('[ProcDedup] Tender missing dedup key — skipping:', tender.title?.slice(0, 60));
      return null;
    }

    const storeKey = `${DEDUP_KEY_PREFIX}${tender.dedupKey}`;
    const existing = await this._redis.get(storeKey);

    // ── New tender ─────────────────────────────────────────────────
    if (!existing) {
      tender.status     = TENDER_STATUS.NEW;
      tender.firstSeen  = new Date().toISOString();
      tender.lastSeen   = tender.firstSeen;
      await this._storeTender(storeKey, tender);
      return { isNew: true, isUpdated: false, status: TENDER_STATUS.NEW, tender };
    }

    const prev = JSON.parse(existing);

    // ── Closing soon — re-alert even if seen before ─────────────────
    const deadlineMs    = tender.deadline ? new Date(tender.deadline).getTime() : null;
    const hoursToClose  = deadlineMs ? (deadlineMs - Date.now()) / 3600000 : Infinity;
    const isClosingSoon = hoursToClose > 0 && hoursToClose <= 48;

    if (isClosingSoon && prev.status !== TENDER_STATUS.CLOSING_SOON) {
      tender.status    = TENDER_STATUS.CLOSING_SOON;
      tender.firstSeen = prev.firstSeen;
      tender.lastSeen  = new Date().toISOString();
      await this._storeTender(storeKey, tender);
      return { isNew: false, isUpdated: true, status: TENDER_STATUS.CLOSING_SOON, tender };
    }

    // ── Value/title changed — re-alert as update ────────────────────
    const contentHash = this._hashContent(tender);
    if (contentHash !== prev.contentHash) {
      tender.status    = TENDER_STATUS.ACTIVE;
      tender.firstSeen = prev.firstSeen;
      tender.lastSeen  = new Date().toISOString();
      tender.changeNote = `Updated from previous: value/details changed`;
      await this._storeTender(storeKey, tender);
      return { isNew: false, isUpdated: true, status: TENDER_STATUS.ACTIVE, tender };
    }

    // ── No change — suppress ────────────────────────────────────────
    await this._redis.set(storeKey, JSON.stringify({ ...prev, lastSeen: new Date().toISOString() }), { ex: 90 * 86400 });
    return null;
  }

  /**
   * Mark a tender as awarded or cancelled (called when portal shows result).
   */
  async updateTenderStatus(dedupKey, status, notes = '') {
    const storeKey = `${DEDUP_KEY_PREFIX}${dedupKey}`;
    const existing = await this._redis.get(storeKey);
    if (!existing) return false;
    const tender = JSON.parse(existing);
    tender.status     = status;
    tender.statusNote = notes;
    tender.closedAt   = new Date().toISOString();
    await this._redis.set(storeKey, JSON.stringify(tender), { ex: 365 * 86400 });  // keep for 1 year for reference
    return true;
  }

  /**
   * Returns all active tenders (NEW + ACTIVE + CLOSING_SOON) for a given market.
   */
  async getActiveTenders(market = null) {
    const pattern = `${DEDUP_KEY_PREFIX}*`;
    const keys    = await this._redis.keys(pattern);
    const tenders = [];
    for (const key of keys) {
      const raw = await this._redis.get(key);
      if (!raw) continue;
      try {
        const tender = JSON.parse(raw);
        if ([TENDER_STATUS.NEW, TENDER_STATUS.ACTIVE, TENDER_STATUS.CLOSING_SOON].includes(tender.status)) {
          if (!market || tender.market === market) tenders.push(tender);
        }
      } catch { continue; }
    }
    return tenders.sort((a, b) => {
      if (a.status === TENDER_STATUS.CLOSING_SOON) return -1;
      if (b.status === TENDER_STATUS.CLOSING_SOON) return 1;
      return new Date(b.firstSeen) - new Date(a.firstSeen);
    });
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  _normaliseTender(raw) {
    // Generate dedup key: portal domain + reference number (or title hash)
    const ref        = raw.reference || raw.ref || raw.tenderId || '';
    const domain     = raw.portal    || raw.source || 'unknown';
    const titleHash  = crypto.createHash('sha256').update(raw.title || '').digest('hex').slice(0, 12);
    const dedupKey   = ref ? `${domain}_${ref}` : `${domain}_${titleHash}`;

    return {
      dedupKey,
      title:      raw.title    || raw.name    || 'Untitled',
      market:     raw.market   || raw.country || 'unknown',
      portal:     domain,
      reference:  ref,
      value:      raw.value    || raw.amount  || null,
      currency:   raw.currency || null,
      deadline:   raw.deadline || raw.closingDate || raw.dueDate || null,
      description: raw.description?.slice(0, 500) || '',
      url:        raw.url      || raw.link    || null,
      contentHash: this._hashContent(raw),
    };
  }

  _hashContent(tender) {
    return crypto.createHash('sha256')
      .update([tender.title, String(tender.value), tender.deadline].join('|'))
      .digest('hex').slice(0, 16);
  }

  async _storeTender(key, tender) {
    await this._redis.set(key, JSON.stringify(tender), { ex: 90 * 86400 });   // 90-day TTL
  }
}


// ═══════════════════════════════════════════════════════════════════
// PART 2 — SOURCE AUTO-PRUNER (GAP 11)
// ═══════════════════════════════════════════════════════════════════

const PRUNE_KEY_PREFIX     = 'crucix:sources:prune:';
const SUSPEND_KEY          = 'crucix:sources:suspended';
const MIN_RELIABILITY_PCT  = 15;     // suspend if below this
const MIN_SWEEPS_BEFORE_PRUNE = 20;  // need at least 20 data points
const RECOVER_THRESHOLD    = 40;     // re-enable when reliability rises above this

export class SourcePruner {
  constructor(redis, alertFn = null) {
    this._redis   = redis;
    this._alertFn = alertFn;
  }

  /**
   * Record a fetch result for a source (call after every runSource()).
   */
  async recordFetch(sourceName, success, latencyMs = 0) {
    const key  = `${PRUNE_KEY_PREFIX}${sourceName}`;
    const raw  = await this._redis.get(key);
    const data = raw ? JSON.parse(raw) : {
      sourceName,
      totalFetches:       0,
      successfulFetches:  0,
      latencies:          [],
      suspended:          false,
      suspendedAt:        null,
      recoveryAttempts:   0,
    };

    data.totalFetches++;
    if (success) data.successfulFetches++;
    data.latencies.push(latencyMs);
    if (data.latencies.length > 50) data.latencies.shift();

    const reliability = (data.successfulFetches / data.totalFetches) * 100;
    data.reliabilityPct   = Math.round(reliability);
    data.avgLatencyMs     = Math.round(data.latencies.reduce((a, b) => a + b, 0) / data.latencies.length);
    data.lastFetchAt      = new Date().toISOString();
    data.lastFetchSuccess = success;

    // ── Auto-suspend logic ─────────────────────────────────────────────────
    if (!data.suspended &&
        data.totalFetches >= MIN_SWEEPS_BEFORE_PRUNE &&
        reliability < MIN_RELIABILITY_PCT) {
      data.suspended   = true;
      data.suspendedAt = new Date().toISOString();
      await this._setSuspended(sourceName, true);
      if (this._alertFn) {
        await this._alertFn(
          `⚠ *SOURCE AUTO-SUSPENDED*\n\n` +
          `*Source:* \`${sourceName}\`\n` +
          `*Reliability:* ${reliability.toFixed(1)}% over ${data.totalFetches} sweeps\n` +
          `*Status:* Suspended — will skip \`runSource()\` calls\n\n` +
          `_To re-enable: Admin panel → Sources → Enable ${sourceName}_`
        ).catch(() => {});
      }
    }

    // ── Auto-recover logic ─────────────────────────────────────────────────
    if (data.suspended && success) {
      data.recoveryAttempts++;
      if (data.recoveryAttempts >= 3 && reliability >= RECOVER_THRESHOLD) {
        data.suspended         = false;
        data.suspendedAt       = null;
        data.recoveryAttempts  = 0;
        await this._setSuspended(sourceName, false);
        if (this._alertFn) {
          await this._alertFn(
            `✅ *SOURCE RECOVERED*\n\n` +
            `*Source:* \`${sourceName}\`\n` +
            `*Reliability now:* ${reliability.toFixed(1)}%\n` +
            `_Source has been re-enabled automatically._`
          ).catch(() => {});
        }
      }
    }

    await this._redis.set(key, JSON.stringify(data), { ex: 90 * 86400 });
    return data;
  }

  /**
   * Check if a source is suspended before running it.
   * Use in your source runner:
   *   if (await sourcePruner.isSuspended(sourceName)) return { skipped: true };
   */
  async isSuspended(sourceName) {
    const suspended = await this._redis.hget(SUSPEND_KEY, sourceName);
    return suspended === 'true';
  }

  /**
   * Admin: manually enable or disable a source.
   */
  async setSourceEnabled(sourceName, enabled) {
    await this._setSuspended(sourceName, !enabled);
    const key  = `${PRUNE_KEY_PREFIX}${sourceName}`;
    const raw  = await this._redis.get(key);
    if (raw) {
      const data = JSON.parse(raw);
      data.suspended          = !enabled;
      data.manuallyOverridden = true;
      data.overriddenAt       = new Date().toISOString();
      await this._redis.set(key, JSON.stringify(data), { ex: 90 * 86400 });
    }
  }

  /**
   * Returns full source health report for admin panel.
   */
  async getSourceHealthReport() {
    const pattern     = `${PRUNE_KEY_PREFIX}*`;
    const keys        = await this._redis.keys(pattern);
    const report      = [];
    const suspendHash = await this._redis.hgetall(SUSPEND_KEY) || {};

    for (const key of keys) {
      const raw = await this._redis.get(key);
      if (!raw) continue;
      try {
        const data = JSON.parse(raw);
        report.push({
          ...data,
          suspended:        suspendHash[data.sourceName] === 'true',
          recommendAction:  this._getRecommendedAction(data),
        });
      } catch { continue; }
    }

    return report.sort((a, b) => a.reliabilityPct - b.reliabilityPct);   // worst first
  }

  _getRecommendedAction(data) {
    const r = data.reliabilityPct || 0;
    if (r >= 80)  return 'HEALTHY — no action needed';
    if (r >= 50)  return 'MONITOR — reliability dropping, check API key/quota';
    if (r >= 30)  return 'INVESTIGATE — auth failure or structural change likely';
    if (r >= 15)  return 'WARNING — approaching auto-suspend threshold';
    return 'SUSPENDED or SUSPEND SOON — action required';
  }

  async _setSuspended(sourceName, suspended) {
    if (suspended) {
      await this._redis.hset(SUSPEND_KEY, sourceName, 'true');
    } else {
      await this._redis.hdel(SUSPEND_KEY, sourceName);
    }
  }
}

// ── Integration wrapper for existing runSource() ────────────────────────────
// Replace your current source runner loop with this:
/*
import { SourcePruner } from './lib/sources/sourceMaintenance.mjs';
const pruner = new SourcePruner(redis, telegramNotify);

async function runSourceSafely(source, sweepId) {
  const name = source.name || source.id;

  // GAP 11: Skip suspended sources
  if (await pruner.isSuspended(name)) {
    console.log(`[SourceRunner] ${name} is suspended — skipping`);
    return { skipped: true, source: name };
  }

  const t0 = Date.now();
  try {
    const result = await source.run();
    await pruner.recordFetch(name, true, Date.now() - t0);
    errorTracker.recordSuccess(name);
    return result;
  } catch (error) {
    await pruner.recordFetch(name, false, Date.now() - t0);
    errorTracker.record(name, 'fetch_error', error, sweepId);
    return { error: error.message, source: name };
  }
}
*/
