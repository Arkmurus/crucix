// lib/status/store.mjs
// Incident store for the public status page.
//
// Persisted to runs/incidents.json + Redis mirror. Same PersistStore
// shape as users / billing.
//
// Incident shape:
//   {
//     id: string (12-char hex),
//     title: string,
//     status: 'investigating' | 'identified' | 'monitoring' | 'resolved',
//     severity: 'minor' | 'major' | 'critical',
//     scope: 'chat' | 'sweep' | 'whatsapp' | 'email' | 'all',
//     startedAt: ISO,
//     resolvedAt: ISO | null,
//     updates: [{ ts, status, body }],
//     createdBy: userId,
//   }

import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { randomBytes } from 'node:crypto';
import { PersistStore } from '../persist/store.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
const INCIDENTS_FILE = join(RUNS_DIR, 'incidents.json');

const incidentsStore = new PersistStore('crucix:incidents', INCIDENTS_FILE, []);

export async function initIncidentsStore() {
  await incidentsStore.init();
}

function loadIncidents() { return incidentsStore.read() || []; }
function saveIncidents(items) { incidentsStore.write(items); }

function genId() { return randomBytes(6).toString('hex'); }

const VALID_STATUSES = ['investigating', 'identified', 'monitoring', 'resolved'];
const VALID_SEVERITIES = ['minor', 'major', 'critical'];
const VALID_SCOPES = ['chat', 'sweep', 'whatsapp', 'email', 'brain-bridge', 'all'];

export function listIncidents({ limit = 30, includeResolved = true } = {}) {
  const items = loadIncidents();
  const filtered = includeResolved ? items : items.filter(i => i.status !== 'resolved');
  return filtered
    .sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''))
    .slice(0, limit);
}

export function listRecentResolved(days = 30, limit = 20) {
  const cutoff = new Date(Date.now() - days * 86400 * 1000).toISOString();
  return loadIncidents()
    .filter(i => i.status === 'resolved' && (i.resolvedAt || '') >= cutoff)
    .sort((a, b) => (b.resolvedAt || '').localeCompare(a.resolvedAt || ''))
    .slice(0, limit);
}

export function listOpen() {
  return loadIncidents()
    .filter(i => i.status !== 'resolved')
    .sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''));
}

export function getIncident(id) {
  return loadIncidents().find(i => i.id === id) || null;
}

export function createIncident({ title, severity = 'minor', scope = 'all', initialBody = '', createdBy = '' }) {
  if (!title) throw new Error('title required');
  if (!VALID_SEVERITIES.includes(severity)) throw new Error('invalid severity');
  if (!VALID_SCOPES.includes(scope)) throw new Error('invalid scope');
  const now = new Date().toISOString();
  const inc = {
    id: genId(),
    title: String(title).slice(0, 200),
    status: 'investigating',
    severity,
    scope,
    startedAt: now,
    resolvedAt: null,
    updates: initialBody ? [{ ts: now, status: 'investigating', body: String(initialBody).slice(0, 1000) }] : [],
    createdBy: createdBy || null,
  };
  const items = loadIncidents();
  items.push(inc);
  saveIncidents(items);
  return inc;
}

export function postUpdate(id, { status, body, resolved = false }) {
  const items = loadIncidents();
  const idx = items.findIndex(i => i.id === id);
  if (idx === -1) return null;
  const inc = items[idx];
  const now = new Date().toISOString();
  let nextStatus = status || inc.status;
  if (resolved) nextStatus = 'resolved';
  if (!VALID_STATUSES.includes(nextStatus)) throw new Error('invalid status');
  inc.updates = inc.updates || [];
  inc.updates.push({ ts: now, status: nextStatus, body: String(body || '').slice(0, 1000) });
  inc.status = nextStatus;
  if (nextStatus === 'resolved' && !inc.resolvedAt) inc.resolvedAt = now;
  if (nextStatus !== 'resolved') inc.resolvedAt = null;
  saveIncidents(items);
  return inc;
}

// Compute a rolling-30-day uptime estimate from incident severity.
// Approximate: minor incidents count as 0% downtime (degraded but up),
// major as 25% within their window, critical as 100% within their window.
// "Window" = startedAt → resolvedAt (or now, if open).
export function computeUptime30d() {
  const horizonMs = 30 * 86400 * 1000;
  const start = Date.now() - horizonMs;
  const incidents = loadIncidents();
  let downtimeMs = 0;
  for (const i of incidents) {
    const begin = Math.max(start, new Date(i.startedAt || 0).getTime());
    const end = i.resolvedAt ? new Date(i.resolvedAt).getTime() : Date.now();
    if (end <= start) continue;
    const wMs = Math.max(0, end - begin);
    if (i.severity === 'critical') downtimeMs += wMs;
    else if (i.severity === 'major') downtimeMs += wMs * 0.25;
    // minor → 0
  }
  const upMs = horizonMs - downtimeMs;
  return Math.max(0, Math.min(100, (upMs / horizonMs) * 100));
}

export const _internal = { VALID_STATUSES, VALID_SEVERITIES, VALID_SCOPES };
