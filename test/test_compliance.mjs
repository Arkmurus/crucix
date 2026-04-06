/**
 * Tests for ARIA compliance endpoints — structure validation.
 *
 * These tests verify the API returns expected response shapes.
 * They require the ARIA service to be running on ARIA_URL (default http://localhost:8000).
 *
 * Run: ARIA_URL=http://localhost:8000 node --test test/test_compliance.mjs
 */
import { describe, it, before } from 'node:test';
import assert from 'node:assert/strict';

const BASE = process.env.ARIA_URL || 'http://localhost:8000';

async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  return { status: res.status, data: await res.json() };
}

// ── Health check (always works) ──────────────────────────────────────────────

describe('Health endpoint', () => {
  it('GET /health returns operational status', async () => {
    const { status, data } = await api('GET', '/health');
    assert.strictEqual(status, 200);
    assert.strictEqual(data.status, 'operational');
    assert.ok('llm_provider' in data, 'Should include llm_provider');
    assert.ok('llm_configured' in data, 'Should include llm_configured');
  });
});

// ── Compliance screen endpoint ───────────────────────────────────────────────

describe('POST /api/aria/compliance/screen', () => {
  it('returns expected structure for a clear entity', async () => {
    const { status, data } = await api('POST', '/api/aria/compliance/screen', {
      entity_name: 'Innocent Bakery',
      product_description: 'bread',
      destination_country: 'GB',
    });
    assert.strictEqual(status, 200);
    assert.ok('status' in data, 'Should have status field');
    assert.ok('sanctions' in data, 'Should have sanctions field');
    assert.ok('country_risk' in data, 'Should have country_risk field');
    assert.ok('product_classification' in data, 'Should have product_classification field');
    assert.ok('risk_factors' in data, 'Should have risk_factors field');
    assert.ok('screened_at' in data, 'Should have screened_at');
  });

  it('rejects empty entity name', async () => {
    const { status } = await api('POST', '/api/aria/compliance/screen', {
      entity_name: '',
    });
    assert.strictEqual(status, 400);
  });

  it('flags embargoed country', async () => {
    const { data } = await api('POST', '/api/aria/compliance/screen', {
      entity_name: 'Test Corp',
      destination_country: 'RU',
    });
    assert.strictEqual(data.country_risk.risk_level, 'embargoed');
    assert.ok(data.risk_factors.length > 0, 'Should have risk factors for embargoed country');
  });

  it('classifies military products', async () => {
    const { data } = await api('POST', '/api/aria/compliance/screen', {
      entity_name: 'Test Corp',
      product_description: 'armoured vehicle with radar system',
    });
    assert.ok(data.product_classification.controlled === true);
    assert.ok(data.product_classification.ml_categories.length > 0);
  });
});

// ── Neural stats ─────────────────────────────────────────────────────────────

describe('GET /api/aria/neural/stats', () => {
  it('returns neural network statistics', async () => {
    const { status, data } = await api('GET', '/api/aria/neural/stats');
    assert.strictEqual(status, 200);
    assert.ok('total_neurons' in data);
    assert.ok('total_edges' in data);
    assert.ok('categories' in data);
  });
});

// ── Neural graph (new endpoint) ──────────────────────────────────────────────

describe('GET /api/aria/neural/graph', () => {
  it('returns graph with nodes and edges arrays', async () => {
    const { status, data } = await api('GET', '/api/aria/neural/graph');
    assert.strictEqual(status, 200);
    assert.ok(Array.isArray(data.nodes), 'nodes should be an array');
    assert.ok(Array.isArray(data.edges), 'edges should be an array');
    assert.ok('meta' in data, 'Should include meta');
    assert.ok('total_neurons' in data.meta);
    assert.ok('categories' in data.meta);
  });

  it('respects limit parameter', async () => {
    const { data } = await api('GET', '/api/aria/neural/graph?limit=5');
    assert.ok(data.nodes.length <= 5, 'Should return at most 5 nodes');
  });
});

// ── Conversation search (new endpoint) ───────────────────────────────────────

describe('GET /api/aria/conversations/search', () => {
  it('rejects empty query', async () => {
    const { status } = await api('GET', '/api/aria/conversations/search?q=');
    assert.strictEqual(status, 400);
  });

  it('returns expected structure for valid query', async () => {
    const { status, data } = await api('GET', '/api/aria/conversations/search?q=test&limit=5');
    assert.strictEqual(status, 200);
    assert.ok('query' in data);
    assert.ok(Array.isArray(data.results));
    assert.ok('total_sessions_matched' in data);
    assert.ok('limit' in data);
  });
});

// ── Knowledge endpoint ───────────────────────────────────────────────────────

describe('GET /api/aria/knowledge', () => {
  it('returns knowledge stats', async () => {
    const { status, data } = await api('GET', '/api/aria/knowledge');
    assert.strictEqual(status, 200);
    assert.ok(typeof data === 'object');
  });
});
