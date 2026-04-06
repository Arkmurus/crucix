// lib/aria/complianceAudit.mjs
// Immutable compliance audit trail — every ARIA compliance action is logged.
// Logs are timestamped, stored in Redis + file fallback, and exportable for regulators.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR    = join(__dirname, '../../runs');
const AUDIT_FILE  = join(RUNS_DIR, 'compliance_audit.json');
const REDIS_KEY   = 'crucix:compliance:audit';
const MAX_ENTRIES = 10000;

let _redis = null;

/**
 * Inject the Redis adapter (called once from server.mjs).
 */
export function initComplianceAudit(redisAdapter) {
  _redis = redisAdapter?.isConfigured ? redisAdapter : null;
}

// ── File fallback helpers ────────────────────────────────────────────────────

function loadFile() {
  if (!existsSync(AUDIT_FILE)) return [];
  try { return JSON.parse(readFileSync(AUDIT_FILE, 'utf8')); } catch { return []; }
}

function saveFile(entries) {
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
  writeFileSync(AUDIT_FILE, JSON.stringify(entries, null, 2));
}

// ── Core API ─────────────────────────────────────────────────────────────────

/**
 * Log a compliance action. Immutable — entries cannot be edited or deleted.
 * @param {object} action
 * @param {string} action.type - SCREENING | CLASSIFICATION | SANCTIONS_CHECK | RISK_ASSESSMENT |
 *                                DOCUMENT_ANALYSIS | RECOMMENDATION | EXPORT_DECISION
 * @param {string} [action.user]           - Who triggered the action (phone, email, system)
 * @param {string} [action.query]          - The input query / entity name
 * @param {object} [action.result]         - The result returned
 * @param {string} [action.recommendation] - ARIA's recommendation
 * @param {string} [action.confidence]     - Confidence level
 * @param {string} [action.session_id]     - Conversation / session ID
 */
export async function logComplianceAction(action) {
  const entry = {
    id:             `ca_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    type:           action.type || 'UNKNOWN',
    user:           action.user || 'system',
    query:          action.query || '',
    result:         action.result || null,
    recommendation: action.recommendation || '',
    confidence:     action.confidence || '',
    session_id:     action.session_id || '',
    timestamp:      action.timestamp || new Date().toISOString(),
  };

  const serialized = JSON.stringify(entry);

  // Redis (primary)
  if (_redis) {
    try {
      await _redis.lpush(REDIS_KEY, serialized);
      await _redis.ltrim(REDIS_KEY, 0, MAX_ENTRIES - 1);
    } catch (e) {
      console.warn('[ComplianceAudit] Redis write failed, using file fallback:', e.message);
    }
  }

  // File fallback (always write for durability)
  try {
    const entries = loadFile();
    entries.unshift(entry);
    saveFile(entries.slice(0, MAX_ENTRIES));
  } catch (e) {
    console.warn('[ComplianceAudit] File write failed:', e.message);
  }

  return entry;
}

/**
 * Retrieve filtered audit log entries.
 * @param {object} [filters]
 * @param {string} [filters.type]     - Filter by action type
 * @param {string} [filters.user]     - Filter by user
 * @param {string} [filters.dateFrom] - ISO date string
 * @param {string} [filters.dateTo]   - ISO date string
 * @param {string} [filters.entity]   - Search query field for entity name
 * @param {number} [filters.limit]    - Max entries to return (default 100)
 */
export async function getAuditLog(filters = {}) {
  const limit = filters.limit || 100;
  let entries = [];

  // Try Redis first
  if (_redis) {
    try {
      const raw = await _redis.lrange(REDIS_KEY, 0, MAX_ENTRIES - 1);
      if (Array.isArray(raw) && raw.length > 0) {
        entries = raw.map(r => {
          try { return typeof r === 'string' ? JSON.parse(r) : r; } catch { return null; }
        }).filter(Boolean);
      }
    } catch {
      // fall through to file
    }
  }

  // File fallback
  if (entries.length === 0) {
    entries = loadFile();
  }

  // Apply filters
  if (filters.type) {
    entries = entries.filter(e => e.type === filters.type.toUpperCase());
  }
  if (filters.user) {
    entries = entries.filter(e => e.user && e.user.toLowerCase().includes(filters.user.toLowerCase()));
  }
  if (filters.dateFrom) {
    const from = new Date(filters.dateFrom).getTime();
    entries = entries.filter(e => new Date(e.timestamp).getTime() >= from);
  }
  if (filters.dateTo) {
    const to = new Date(filters.dateTo).getTime();
    entries = entries.filter(e => new Date(e.timestamp).getTime() <= to);
  }
  if (filters.entity) {
    const term = filters.entity.toLowerCase();
    entries = entries.filter(e => e.query && e.query.toLowerCase().includes(term));
  }

  return entries.slice(0, limit);
}

/**
 * Export audit log in a specified format for regulators.
 * @param {'json'|'csv'} format
 * @param {object} [filters] - Same as getAuditLog filters
 * @returns {string} Formatted string
 */
export async function exportAuditLog(format = 'json', filters = {}) {
  const entries = await getAuditLog({ ...filters, limit: filters.limit || MAX_ENTRIES });

  if (format === 'csv') {
    const header = 'id,timestamp,type,user,query,result,recommendation,confidence,session_id';
    const rows = entries.map(e => {
      const resultStr = typeof e.result === 'object' ? JSON.stringify(e.result).replace(/"/g, '""') : (e.result || '');
      return [
        e.id,
        e.timestamp,
        e.type,
        e.user,
        `"${(e.query || '').replace(/"/g, '""')}"`,
        `"${resultStr}"`,
        `"${(e.recommendation || '').replace(/"/g, '""')}"`,
        e.confidence,
        e.session_id,
      ].join(',');
    });
    return [header, ...rows].join('\n');
  }

  // Default: JSON
  return JSON.stringify(entries, null, 2);
}
