// lib/status/routes.mjs
// Public + admin routes for the status page.
//
//   GET  /api/status              — public; current state + recent history
//   POST /api/status/incidents    — admin; create new incident
//   POST /api/status/incidents/:id/update — admin; post update
//   POST /api/status/incidents/:id/resolve — admin; mark resolved
//
// The public GET aggregates: open incidents, last-30-day resolved, uptime
// estimate, and a real-time bridge-bridge / health probe (so a user can
// see "all systems operational" without us having to manually mark it).

import express from 'express';
import {
  listOpen,
  listRecentResolved,
  createIncident,
  postUpdate,
  getIncident,
  computeUptime30d,
} from './store.mjs';

export function createStatusRouter({ requireAdmin, getBrainBridgeVerdict }) {
  const router = express.Router();
  router.use(express.json({ limit: '50kb' }));

  // Public — anyone, no auth.
  router.get('/', (req, res) => {
    const open = listOpen();
    const recent = listRecentResolved(30, 20);
    const uptime = computeUptime30d();
    const bridge = getBrainBridgeVerdict ? (getBrainBridgeVerdict() || null) : null;

    // Current operational status — derived from open incidents and the
    // boot-time bridge verdict. Order of severity: critical > major > minor > ok.
    let overall = 'operational';
    let summary = 'All systems operational.';
    if (open.length) {
      const worst = open.reduce((acc, i) => {
        const sev = i.severity;
        if (sev === 'critical') return 'critical';
        if (sev === 'major' && acc !== 'critical') return 'major';
        if (sev === 'minor' && acc === 'operational') return 'minor';
        return acc;
      }, 'operational');
      overall = worst === 'critical' ? 'major_outage' : (worst === 'major' ? 'partial_outage' : 'degraded');
      summary = `${open.length} active ${open.length === 1 ? 'incident' : 'incidents'}.`;
    } else if (bridge && !bridge.healthy && bridge.has_token === false) {
      // Boot self-check found no token. Don't claim "operational" if the
      // sweep → brain learning loop is broken; flag it as degraded.
      overall = 'degraded';
      summary = 'Operational, but the seenode → brain learning bridge is misconfigured (signal absorption is impacted).';
    }

    res.json({
      overall,
      summary,
      uptime30dPct: Math.round(uptime * 100) / 100,
      generatedAt: new Date().toISOString(),
      open,
      recent,
      bridge: bridge ? {
        healthy: !!bridge.healthy,
        has_token: !!bridge.has_token,
        last_check: bridge.timestamp,
        reason: bridge.reason || '',
      } : null,
    });
  });

  // Admin-only mutations. requireAdmin already checks JWT + role==='admin'.
  router.post('/incidents', requireAdmin, (req, res) => {
    try {
      const inc = createIncident({
        title: req.body?.title,
        severity: req.body?.severity,
        scope: req.body?.scope,
        initialBody: req.body?.body,
        createdBy: req.user?.userId,
      });
      res.json(inc);
    } catch (e) {
      res.status(400).json({ error: e.message });
    }
  });

  router.post('/incidents/:id/update', requireAdmin, (req, res) => {
    try {
      const inc = postUpdate(req.params.id, {
        status: req.body?.status,
        body: req.body?.body,
        resolved: !!req.body?.resolved,
      });
      if (!inc) return res.status(404).json({ error: 'incident not found' });
      res.json(inc);
    } catch (e) {
      res.status(400).json({ error: e.message });
    }
  });

  router.post('/incidents/:id/resolve', requireAdmin, (req, res) => {
    const inc = postUpdate(req.params.id, {
      body: req.body?.body || 'Resolved.',
      resolved: true,
    });
    if (!inc) return res.status(404).json({ error: 'incident not found' });
    res.json(inc);
  });

  router.get('/incidents/:id', (req, res) => {
    const inc = getIncident(req.params.id);
    if (!inc) return res.status(404).json({ error: 'not found' });
    res.json(inc);
  });

  return router;
}
