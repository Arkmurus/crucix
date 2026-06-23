// lib/aria/backup.mjs
// ARIA Cold Backup — export all knowledge, neurons, audit logs, pipeline
// for regulatory discovery or disaster recovery.
//
// Admin-only endpoints. Returns full JSON snapshots of each data store.

import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { redisGet } from '../persist/store.mjs';

const RUNS = join(process.cwd(), 'runs');

// ── Helpers — load from file with Redis fallback ────────────────────────────

async function loadStore(filePath, redisKey) {
  // Try local file first (fastest)
  try {
    if (existsSync(filePath)) {
      return JSON.parse(readFileSync(filePath, 'utf8'));
    }
  } catch {}
  // Fall back to Redis
  if (redisKey) {
    try { return await redisGet(redisKey); } catch {}
  }
  return null;
}

// ── Full Backup ─────────────────────────────────────────────────────────────

export async function generateFullBackup() {
  const [knowledgeBase, intelLedger, pipeline, auditLog, neuralMemory] = await Promise.all([
    loadStore(join(RUNS, 'aria_knowledge.json'), 'crucix:aria:knowledge'),
    loadStore(join(RUNS, 'intel_ledger.json'), 'crucix:intel_ledger'),
    loadStore(join(RUNS, 'pipeline.json'), 'crucix:pipeline:deals'),
    loadStore(join(RUNS, 'audit.json'), null),
    loadStore(join(RUNS, 'memory', 'neural.json'), 'crucix:aria:neural_memory'),
  ]);

  // Gather conversation session summaries if available
  let conversations = null;
  try {
    conversations = await redisGet('crucix:aria:sessions');
  } catch {}

  return {
    knowledge_base:    knowledgeBase || { facts: [], queries: [], learnings: [] },
    neural_memory:     neuralMemory || { neurons: [], edges: [] },
    intel_ledger:      intelLedger || { signals: [] },
    compliance_audit:  auditLog || [],
    pipeline:          pipeline || { deals: [] },
    conversations:     conversations || [],
    metadata: {
      exported_at:  new Date().toISOString(),
      aria_version: '2.0',
      stats: {
        facts:    (knowledgeBase?.facts || []).length,
        signals:  (intelLedger?.signals || []).length,
        deals:    (pipeline?.deals || []).length,
        neurons:  (neuralMemory?.neurons || []).length,
        audit:    Array.isArray(auditLog) ? auditLog.length : 0,
      },
    },
  };
}

// ── Express Routes ──────────────────────────────────────────────────────────

export function mountBackupRoutes(app) {
  console.log('[Backup] Mounting cold backup routes...');

  const INT_TOKEN = process.env.ARIA_INTERNAL_TOKEN || '';

  // Admin-only auth guard
  const requireAdmin = (req, res, next) => {
    if (req.headers.authorization !== `Bearer ${INT_TOKEN}`) {
      return res.status(401).json({ error: 'Unauthorized — admin only' });
    }
    next();
  };

  // GET /api/admin/backup — full backup JSON
  app.get('/api/admin/backup', requireAdmin, async (_req, res) => {
    try {
      const backup = await generateFullBackup();
      res.setHeader('Content-Disposition', `attachment; filename="crucix_backup_${new Date().toISOString().split('T')[0]}.json"`);
      res.json(backup);
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // GET /api/admin/backup/knowledge — knowledge base only
  app.get('/api/admin/backup/knowledge', requireAdmin, async (_req, res) => {
    try {
      const kb = await loadStore(join(RUNS, 'aria_knowledge.json'), 'crucix:aria:knowledge');
      res.json(kb || { facts: [], queries: [], learnings: [] });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // GET /api/admin/backup/audit — compliance audit only
  app.get('/api/admin/backup/audit', requireAdmin, async (_req, res) => {
    try {
      const audit = await loadStore(join(RUNS, 'audit.json'), null);
      res.json(audit || []);
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  console.log('[Backup] Routes mounted — /api/admin/backup/*');
}
