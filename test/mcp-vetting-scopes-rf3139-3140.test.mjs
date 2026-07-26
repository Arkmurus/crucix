// R-F3139 / R-F3140 — public-API vetting endpoints + the ARIA MCP server.
//
// The property under test is NOT "the routes respond". It is that a key which
// was never granted the 'vetting' scope cannot reach vetting data by ANY of
// the three doors we just opened — /api/v1, MCP tools/call, MCP tools/list —
// and cannot learn from the refusal that the data exists.
//
// Vetting responses carry criminal-conviction and financial data about named
// individuals, and MCP clients are LLMs: a tool an LLM can see is a tool it
// will try, and an error it can read is context it will reason about aloud.

import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';

import { createMcpRouter } from '../lib/mcp/routes.mjs';
import { createV1Router } from '../lib/api_keys/routes.mjs';
import { scopesFor, keyHasScope, DEFAULT_SCOPES } from '../lib/api_keys/store.mjs';
import { allowLoopbackNetwork } from './helpers/net_guard.mjs';

// R-F2739 hatch: this suite boots an isolated server on an ephemeral port and
// talks to it over real HTTP, because the thing under test IS the HTTP surface.
// Loopback-only — it can never reach production or the LAN.
allowLoopbackNetwork();

// ── harness ───────────────────────────────────────────────────────────────

const CHAT_KEY = { id: 'ak_chat', userId: 'u-chat', scopes: ['chat'] };
const VET_KEY = { id: 'ak_vet', userId: 'u-vet', scopes: ['chat', 'vetting'] };
const LEGACY_KEY = { id: 'ak_old', userId: 'u-old' };   // pre-R-F3139, no scopes

const KEYS = { 'tok-chat': CHAT_KEY, 'tok-vet': VET_KEY, 'tok-old': LEGACY_KEY };

let vettingCalls = [];

async function fakeVettingProxy({ res, method, path, userId, query, raw }) {
  vettingCalls.push({ method, path, userId, query });
  const payload = { ok: true, path, tenant_seen_by_upstream: userId };
  if (raw) return { status: 200, payload };
  return res.status(200).json(payload);
}

async function fakeChatProxy({ userId, message }) {
  return { response: `echo:${message}`, session_id: `s_${userId}` };
}

function buildApp() {
  const app = express();

  app.use('/mcp', createMcpRouter({
    authenticate: async (req) => {
      const m = (req.headers.authorization || '').match(/^Bearer\s+(.+)$/i);
      if (!m) return null;
      const keyRecord = KEYS[m[1].trim()];
      if (!keyRecord) return null;
      return {
        keyRecord,
        user: { id: keyRecord.userId, role: 'admin' },
        scopes: scopesFor(keyRecord),
      };
    },
    chatProxy: fakeChatProxy,
    vettingProxy: fakeVettingProxy,
    enabled: () => true,
  }));

  // The REAL v1 router — its own auth middleware, tier gate, rate limiter and
  // scope gate all run. Only the key LOOKUP is injected, so the test does not
  // have to write test keys into the live runs/api_keys.json.
  app.use('/api/v1', createV1Router({
    findUserById: (id) => ({ id, role: 'admin', status: 'active', tier: 'proIntel' }),
    chatProxy: fakeChatProxy,
    vettingProxy: fakeVettingProxy,
    authenticateKeyFn: (presented) => KEYS[presented] || null,
  }));

  return app;
}

async function withServer(fn) {
  process.env.ENABLE_PUBLIC_API = '1';
  vettingCalls = [];
  const server = createServer(buildApp());
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await fn(base);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

const rpc = (base, token, body) => fetch(`${base}/mcp`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  },
  body: JSON.stringify(body),
});

// ── store-level scope defaults ────────────────────────────────────────────

test('R-F3139 a key issued before scopes existed gets chat only', () => {
  assert.deepEqual(scopesFor(LEGACY_KEY), [...DEFAULT_SCOPES]);
  assert.equal(keyHasScope(LEGACY_KEY, 'chat'), true);
  assert.equal(keyHasScope(LEGACY_KEY, 'vetting'), false,
    'a pre-existing key must not silently gain access to screening files');
});

test('R-F3139 an empty scopes array is not a wildcard', () => {
  assert.equal(keyHasScope({ id: 'x', scopes: [] }, 'vetting'), false);
});

// ── MCP protocol ──────────────────────────────────────────────────────────

test('R-F3140 MCP rejects an unauthenticated call', async () => {
  await withServer(async (base) => {
    const r = await rpc(base, null, { jsonrpc: '2.0', id: 1, method: 'initialize' });
    assert.equal(r.status, 401);
  });
});

test('R-F3140 initialize returns protocol version and server info', async () => {
  await withServer(async (base) => {
    const r = await rpc(base, 'tok-vet', {
      jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2025-06-18' },
    });
    assert.equal(r.status, 200);
    const body = await r.json();
    assert.equal(body.jsonrpc, '2.0');
    assert.equal(body.id, 1);
    assert.equal(body.result.protocolVersion, '2025-06-18');
    assert.equal(body.result.serverInfo.name, 'aria');
    assert.ok(body.result.capabilities.tools);
  });
});

test('R-F3140 an initialized notification gets 202 and no body', async () => {
  await withServer(async (base) => {
    const r = await rpc(base, 'tok-vet',
      { jsonrpc: '2.0', method: 'notifications/initialized' });
    assert.equal(r.status, 202);
    assert.equal((await r.text()).trim(), '');
  });
});

// ── the scope property ────────────────────────────────────────────────────

test('R-F3140 tools/list hides vetting tools from a chat-only key', async () => {
  await withServer(async (base) => {
    const r = await rpc(base, 'tok-chat',
      { jsonrpc: '2.0', id: 2, method: 'tools/list' });
    const names = (await r.json()).result.tools.map(t => t.name);
    assert.ok(names.includes('aria_chat'));
    assert.equal(names.some(n => n.startsWith('vetting_')), false,
      `chat-only key saw vetting tools: ${names.join(', ')}`);
  });
});

test('R-F3140 tools/list shows vetting tools to a scoped key', async () => {
  await withServer(async (base) => {
    const r = await rpc(base, 'tok-vet',
      { jsonrpc: '2.0', id: 3, method: 'tools/list' });
    const names = (await r.json()).result.tools.map(t => t.name);
    for (const expected of ['vetting_list_packs', 'vetting_assess_case',
                            'vetting_get_case', 'vetting_list_cases']) {
      assert.ok(names.includes(expected), `missing tool ${expected}`);
    }
  });
});

test('R-F3140 an unscoped tools/call is indistinguishable from unknown', async () => {
  await withServer(async (base) => {
    const unscoped = await (await rpc(base, 'tok-chat', {
      jsonrpc: '2.0', id: 4, method: 'tools/call',
      params: { name: 'vetting_assess_case', arguments: { case_id: 'C1' } },
    })).json();
    const unknown = await (await rpc(base, 'tok-chat', {
      jsonrpc: '2.0', id: 5, method: 'tools/call',
      params: { name: 'no_such_tool_at_all', arguments: {} },
    })).json();

    assert.equal(unscoped.error.code, -32601);
    assert.equal(unscoped.error.code, unknown.error.code,
      'an unscoped tool must not be distinguishable from a nonexistent one');
    assert.equal(vettingCalls.length, 0,
      'an unscoped tools/call must never reach the vetting upstream');
  });
});

test('R-F3140 a scoped tools/call reaches vetting as its own tenant', async () => {
  await withServer(async (base) => {
    const body = await (await rpc(base, 'tok-vet', {
      jsonrpc: '2.0', id: 6, method: 'tools/call',
      params: { name: 'vetting_assess_case',
                arguments: { case_id: 'C1', as_of: '2026-07-26' } },
    })).json();

    assert.equal(body.result.isError, false);
    assert.equal(vettingCalls.length, 1);
    const call = vettingCalls[0];
    assert.equal(call.path, '/case/C1/assess');
    assert.equal(call.query.as_of, '2026-07-26');
    // The tenant is the KEY OWNER, never anything the tool arguments said.
    assert.equal(call.userId, 'u-vet');
  });
});

test('R-F3140 tool arguments cannot select a different tenant', async () => {
  await withServer(async (base) => {
    await rpc(base, 'tok-vet', {
      jsonrpc: '2.0', id: 7, method: 'tools/call',
      params: { name: 'vetting_get_case',
                arguments: { case_id: 'C2', user_id: 'someone-else',
                             tenant_id: 'someone-else' } },
    });
    assert.equal(vettingCalls[0].userId, 'u-vet',
      'a forged tenant in tool arguments must be ignored');
  });
});

// ── /api/v1 ───────────────────────────────────────────────────────────────

test('R-F3139 /api/v1/vetting is 403 for a chat-only key', async () => {
  await withServer(async (base) => {
    const r = await fetch(`${base}/api/v1/vetting/packs`, {
      headers: { Authorization: 'Bearer tok-chat' },
    });
    assert.equal(r.status, 403);
    const body = await r.json();
    assert.match(body.error, /vetting/);
    assert.deepEqual(body.granted, ['chat']);
    assert.equal(vettingCalls.length, 0);
  });
});

test('R-F3139 /api/v1/vetting works for a scoped key and pins the tenant', async () => {
  await withServer(async (base) => {
    const r = await fetch(`${base}/api/v1/vetting/cases/C9`, {
      headers: { Authorization: 'Bearer tok-vet' },
    });
    assert.equal(r.status, 200);
    assert.equal((await r.json()).tenant_seen_by_upstream, 'u-vet');
    assert.equal(vettingCalls[0].path, '/case/C9');
  });
});

// ── R-F3150: MCP must pay the same budget as /api/v1 ──────────────────────

test('R-F3150 MCP authentication consumes the per-key budget', async () => {
  // The regression this locks: MCP authenticated a key and then called tools
  // without the rate limit or daily quota that the identical /api/v1 call
  // pays. The surface most likely to hammer a limiter (an LLM in a loop) was
  // the one exempt from it.
  const { consumeApiKeyBudget } = await import('../lib/api_keys/routes.mjs');
  assert.equal(typeof consumeApiKeyBudget, 'function',
    'the shared budget path must be exported so both protocols use ONE copy');

  const srv = readFileSync(
    new URL('../server.mjs', import.meta.url).pathname
      .replace(/^\/([A-Za-z]:)/, '$1'), 'utf8');
  const fn = srv.slice(srv.indexOf('async function _mcpAuthenticate'));
  const body = fn.slice(0, fn.indexOf('\napp.use(\'/mcp\''));
  assert.ok(/consumeApiKeyBudget\(keyRecord,\s*user\)/.test(body),
    '_mcpAuthenticate must consume the shared API-key budget');
  assert.ok(/status:\s*429/.test(body),
    'an exhausted budget must surface as 429 on the MCP path too');
});

test('R-F3150 an over-budget key is refused before any tool runs', async () => {
  await withServer(async (base) => {
    // authenticate() returning an error must stop the request BEFORE dispatch.
    const r = await rpc(base, 'tok-vet', {
      jsonrpc: '2.0', id: 99, method: 'tools/call',
      params: { name: 'vetting_assess_case', arguments: { case_id: 'C1' } },
    });
    // With the stub authenticate (no budget) this succeeds; the point of the
    // assertion is that the refusal path exists and short-circuits dispatch.
    assert.equal(r.status, 200);
  });
  // Now prove the refusal branch: an authenticate() that reports an error must
  // never reach a tool.
  const calls = [];
  const app = express();
  app.use('/mcp', createMcpRouter({
    authenticate: async () => ({ error: 'rate limit exceeded', status: 429 }),
    chatProxy: async () => { calls.push('chat'); return { response: 'x' }; },
    vettingProxy: async () => { calls.push('vetting'); return { status: 200, payload: {} }; },
    enabled: () => true,
  }));
  const server = createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const r = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer tok-vet' },
      body: JSON.stringify({
        jsonrpc: '2.0', id: 1, method: 'tools/call',
        params: { name: 'vetting_assess_case', arguments: { case_id: 'C1' } },
      }),
    });
    assert.equal(r.status, 429);
    assert.deepEqual(calls, [],
      'a budget refusal must short-circuit before any tool executes');
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test('R-F3139 a legacy key cannot reach /api/v1/vetting', async () => {
  await withServer(async (base) => {
    const r = await fetch(`${base}/api/v1/vetting/packs`, {
      headers: { Authorization: 'Bearer tok-old' },
    });
    assert.equal(r.status, 403);
    assert.equal(vettingCalls.length, 0);
  });
});
