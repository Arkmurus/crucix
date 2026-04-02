// lib/auth/audit.mjs
// Admin action audit log — persisted to runs/audit.json

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR  = join(__dirname, '../../runs');
const AUDIT_FILE = join(RUNS_DIR, 'audit.json');

const MAX_ENTRIES = 500;

function load() {
  if (!existsSync(AUDIT_FILE)) return [];
  try { return JSON.parse(readFileSync(AUDIT_FILE, 'utf8')); } catch { return []; }
}

function save(entries) {
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
  writeFileSync(AUDIT_FILE, JSON.stringify(entries, null, 2));
}

/**
 * Log an admin action.
 * @param {object} opts
 * @param {string} opts.adminId
 * @param {string} opts.adminEmail
 * @param {string} opts.action  - approve | suspend | unsuspend | reject | delete | force_logout | role_change
 * @param {string} opts.targetId
 * @param {string} opts.targetEmail
 * @param {string} opts.targetName
 * @param {string} [opts.notes]
 */
export function logAudit({ adminId, adminEmail, action, targetId, targetEmail, targetName, notes = '' }) {
  const entries = load();
  entries.unshift({
    ts:          new Date().toISOString(),
    adminId,
    adminEmail,
    action,
    targetId,
    targetEmail,
    targetName,
    notes,
  });
  save(entries.slice(0, MAX_ENTRIES));
}

export function getAuditLog(limit = 100) {
  return load().slice(0, limit);
}
