// lib/aria/pipeline.mjs
// ARIA Deal Pipeline — tracks opportunities from signal to outcome.
//
// Stages: SIGNAL -> QUALIFIED -> PURSUING -> PROPOSAL -> NEGOTIATION -> WON / LOST
//
// Storage: Redis (crucix:pipeline:deals) + file fallback (runs/pipeline.json)
// Max 500 deals, pruned when limit is exceeded (oldest LOST/WON removed first).

import { join } from 'path';
import { PersistStore } from '../persist/store.mjs';
import { pushWebhook } from './webhooks.mjs';

const PIPELINE_FILE = join(process.cwd(), 'runs', 'pipeline.json');
const PIPELINE_KEY  = 'crucix:pipeline:deals';
const MAX_DEALS     = 500;

const VALID_STAGES = ['SIGNAL', 'QUALIFIED', 'PURSUING', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST'];

const store = new PersistStore(PIPELINE_KEY, PIPELINE_FILE, () => ({ deals: [], version: 1 }));

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// ── Init ────────────────────────────────────────────────────────────────────

export async function initPipeline() {
  await store.init();
  const data = store.read();
  console.log(`[Pipeline] Initialized (${data.deals?.length || 0} deals)`);
}

// ── CRUD ────────────────────────────────────────────────────────────────────

export async function addDeal(deal) {
  const data = store.read();
  const now = new Date().toISOString();

  const newDeal = {
    id:                deal.id || genId(),
    title:             deal.title || 'Untitled deal',
    market:            deal.market || '',
    product:           deal.product || '',
    value_estimate:    deal.value_estimate || null,
    stage:             VALID_STAGES.includes(deal.stage) ? deal.stage : 'SIGNAL',
    created_at:        now,
    updated_at:        now,
    source_signals:    deal.source_signals || [],
    compliance_status: deal.compliance_status || 'PENDING',
    relationship_tier: deal.relationship_tier || 'NONE',
    notes:             deal.notes || [],
    next_action:       deal.next_action || '',
    win_probability:   deal.win_probability ?? 0,
  };

  data.deals.unshift(newDeal);

  // Prune if over limit — remove oldest terminal deals first
  if (data.deals.length > MAX_DEALS) {
    const terminal = data.deals.filter(d => d.stage === 'WON' || d.stage === 'LOST');
    if (terminal.length > 0) {
      const oldest = terminal[terminal.length - 1];
      data.deals = data.deals.filter(d => d.id !== oldest.id);
    } else {
      data.deals = data.deals.slice(0, MAX_DEALS);
    }
  }

  store.write(data);

  // Fire webhook
  pushWebhook({ type: 'DEAL_UPDATE', data: { action: 'created', deal: newDeal } }).catch(() => {});

  return newDeal;
}

export async function updateDealStage(dealId, stage, notes) {
  if (!VALID_STAGES.includes(stage)) {
    throw new Error(`Invalid stage: ${stage}. Valid: ${VALID_STAGES.join(', ')}`);
  }
  const data = store.read();
  const deal = data.deals.find(d => d.id === dealId);
  if (!deal) return null;

  const prevStage = deal.stage;
  deal.stage = stage;
  deal.updated_at = new Date().toISOString();
  if (notes) {
    deal.notes.push({ text: notes, stage, ts: deal.updated_at });
  }

  // Auto-adjust win probability based on stage progression
  const stageProbs = { SIGNAL: 0.05, QUALIFIED: 0.15, PURSUING: 0.30, PROPOSAL: 0.50, NEGOTIATION: 0.70, WON: 1.0, LOST: 0.0 };
  if (stageProbs[stage] !== undefined) {
    deal.win_probability = stageProbs[stage];
  }

  store.write(data);

  // Fire webhook
  pushWebhook({
    type: 'DEAL_UPDATE',
    data: { action: 'stage_change', dealId, from: prevStage, to: stage, notes },
  }).catch(() => {});

  return deal;
}

export async function getDeal(dealId) {
  const data = store.read();
  return data.deals.find(d => d.id === dealId) || null;
}

export async function listDeals(filters = {}) {
  const data = store.read();
  let deals = data.deals;

  if (filters.stage) {
    deals = deals.filter(d => d.stage === filters.stage);
  }
  if (filters.market) {
    const m = filters.market.toLowerCase();
    deals = deals.filter(d => (d.market || '').toLowerCase().includes(m));
  }
  if (filters.product) {
    const p = filters.product.toLowerCase();
    deals = deals.filter(d => (d.product || '').toLowerCase().includes(p));
  }
  if (filters.compliance_status) {
    deals = deals.filter(d => d.compliance_status === filters.compliance_status);
  }

  return deals;
}

export async function getPipelineStats() {
  const data = store.read();
  const deals = data.deals;

  const byStage = {};
  let totalValue = 0;
  let weightedValue = 0;

  for (const d of deals) {
    byStage[d.stage] = (byStage[d.stage] || 0) + 1;
    const val = Number(d.value_estimate) || 0;
    totalValue += val;
    weightedValue += val * (d.win_probability || 0);
  }

  const activeDeals = deals.filter(d => d.stage !== 'WON' && d.stage !== 'LOST');
  const wonDeals = deals.filter(d => d.stage === 'WON');
  const lostDeals = deals.filter(d => d.stage === 'LOST');

  return {
    total_deals:       deals.length,
    active_deals:      activeDeals.length,
    won_deals:         wonDeals.length,
    lost_deals:        lostDeals.length,
    by_stage:          byStage,
    total_value:       totalValue,
    weighted_value:    Math.round(weightedValue),
    win_rate:          wonDeals.length + lostDeals.length > 0
      ? Math.round((wonDeals.length / (wonDeals.length + lostDeals.length)) * 100)
      : 0,
    top_markets:       Object.entries(
      deals.reduce((acc, d) => { if (d.market) acc[d.market] = (acc[d.market] || 0) + 1; return acc; }, {})
    ).sort((a, b) => b[1] - a[1]).slice(0, 10),
  };
}

// ── Express Routes ──────────────────────────────────────────────────────────

export function mountPipelineRoutes(app) {
  console.log('[Pipeline] Mounting deal pipeline routes...');

  const INT_TOKEN = process.env.ARIA_INTERNAL_TOKEN || '';

  const requireAuth = (req, res, next) => {
    if (req.headers.authorization !== `Bearer ${INT_TOKEN}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    next();
  };

  // GET /api/pipeline — list all deals with filters
  app.get('/api/pipeline', requireAuth, async (req, res) => {
    try {
      const filters = {};
      if (req.query.stage)             filters.stage = req.query.stage;
      if (req.query.market)            filters.market = req.query.market;
      if (req.query.product)           filters.product = req.query.product;
      if (req.query.compliance_status) filters.compliance_status = req.query.compliance_status;
      const deals = await listDeals(filters);
      res.json({ deals, count: deals.length });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // POST /api/pipeline — create deal
  app.post('/api/pipeline', requireAuth, async (req, res) => {
    try {
      const deal = await addDeal(req.body || {});
      res.json({ ok: true, deal });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // GET /api/pipeline/stats — pipeline analytics
  app.get('/api/pipeline/stats', requireAuth, async (_req, res) => {
    try {
      const stats = await getPipelineStats();
      res.json(stats);
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // GET /api/pipeline/:id — get deal detail
  app.get('/api/pipeline/:id', requireAuth, async (req, res) => {
    try {
      const deal = await getDeal(req.params.id);
      if (!deal) return res.status(404).json({ error: 'Deal not found' });
      res.json(deal);
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // PUT /api/pipeline/:id — update deal stage
  app.put('/api/pipeline/:id', requireAuth, async (req, res) => {
    try {
      const { stage, notes } = req.body || {};
      if (!stage) return res.status(400).json({ error: 'stage required' });
      const deal = await updateDealStage(req.params.id, stage, notes);
      if (!deal) return res.status(404).json({ error: 'Deal not found' });
      res.json({ ok: true, deal });
    } catch (e) { res.status(400).json({ error: e.message }); }
  });

  console.log('[Pipeline] Routes mounted — /api/pipeline/*');
}
