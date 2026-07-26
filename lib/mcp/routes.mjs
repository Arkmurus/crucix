// lib/mcp/routes.mjs
// R-F3140 — ARIA MCP server (Model Context Protocol) over Streamable HTTP.
//
// ── Why this lives in aria-web and not in the Python brain ────────────────
// MCP is a second PROTOCOL onto capabilities we already expose, not a second
// product. Everything an MCP client needs before it may call anything — the
// API-key store, the proIntel tier gate, the per-key rate limiter, the daily
// quota counter, the R-F99 anomaly recorder, and now the R-F3139 scopes —
// already lives in lib/api_keys. Putting MCP in aria-intel would mean a
// SECOND authorization path over the same data, and the one that drifts is
// always the one nobody is watching. So: same `crx_…` keys, same middleware,
// different wire format.
//
// ── Scope filtering is not cosmetic ──────────────────────────────────────
// `tools/list` returns only the tools the presented key is scoped for. A key
// without the 'vetting' scope does not merely fail when it calls a vetting
// tool — it never learns the tool exists. That matters because an MCP client
// is usually an LLM: a tool it can see is a tool it will try, and a refusal
// it can see is context it will reason about out loud. Vetting tools return
// criminal-conviction data about named individuals; the correct posture for
// an unscoped key is invisibility, not a 403.
//
// Transport: Streamable HTTP (POST of a single JSON-RPC 2.0 message, JSON
// response). No SSE stream: every ARIA tool here is request/response, so the
// spec's optional streaming leg would be dead code.

import express from 'express';

const PROTOCOL_VERSION = '2025-06-18';
const SUPPORTED_PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26'];
const SERVER_INFO = Object.freeze({ name: 'aria', version: '1.0.0' });

// JSON-RPC 2.0 reserved codes.
const PARSE_ERROR = -32700;
const INVALID_REQUEST = -32600;
const METHOD_NOT_FOUND = -32601;
const INVALID_PARAMS = -32602;
const INTERNAL_ERROR = -32603;

function rpcResult(id, result) {
  return { jsonrpc: '2.0', id, result };
}

function rpcError(id, code, message, data = undefined) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  return { jsonrpc: '2.0', id, error };
}

/** A tool result. `isError: true` is how MCP reports a TOOL failure — as a
 *  result the model can read and react to, not as a protocol error. Getting
 *  this backwards makes ordinary failures look like transport faults. */
function toolText(text, isError = false) {
  return { content: [{ type: 'text', text: String(text) }], isError };
}

// ── Tool catalogue ────────────────────────────────────────────────────────
//
// `scope` is enforced twice: at list time (the tool is hidden) and at call
// time (the call is refused). Two checks because a client may have cached an
// older list from when the key was more privileged.
function buildTools({ chatProxy, vettingProxy }) {
  const tools = [];

  if (chatProxy) {
    tools.push({
      scope: 'chat',
      definition: {
        name: 'aria_chat',
        description:
          'Ask ARIA an intelligence question. Returns ARIA\'s answer with its '
          + 'own sourcing and verification applied.',
        inputSchema: {
          type: 'object',
          properties: {
            message: { type: 'string', description: 'The question to ask.' },
            session_id: {
              type: 'string',
              description: 'Optional session id to continue a conversation.',
            },
          },
          required: ['message'],
        },
      },
      handler: async ({ args, userId }) => {
        const message = (args?.message || '').toString().trim();
        if (!message) return toolText('message is required', true);
        const result = await chatProxy({
          userId,
          message,
          sessionId: (args?.session_id || '').toString(),
        });
        return toolText(result?.response ?? JSON.stringify(result));
      },
    });
  }

  if (vettingProxy) {
    const call = async ({ method, path, userId, body, query }) => {
      const { status, payload } = await vettingProxy({
        method, path, userId, body, query, raw: true,
      });
      const text = typeof payload === 'string'
        ? payload : JSON.stringify(payload, null, 2);
      return toolText(text, status >= 400);
    };

    tools.push({
      scope: 'vetting',
      definition: {
        name: 'vetting_list_packs',
        description:
          'List the screening packs (jurisdiction rule sets) available, with '
          + 'their lifecycle status and whether each may support an employment '
          + 'decision. A FRAMEWORK_ONLY pack organises evidence but never '
          + 'issues decision-readiness.',
        inputSchema: { type: 'object', properties: {} },
      },
      handler: ({ userId }) =>
        call({ method: 'GET', path: '/packs', userId }),
    });

    tools.push({
      scope: 'vetting',
      definition: {
        name: 'vetting_list_cases',
        description: 'List your own vetting cases (summaries only).',
        inputSchema: {
          type: 'object',
          properties: {
            limit: { type: 'integer', description: 'Max cases (default 50).' },
          },
        },
      },
      handler: ({ userId }) =>
        call({ method: 'GET', path: '/cases', userId }),
    });

    tools.push({
      scope: 'vetting',
      definition: {
        name: 'vetting_get_case',
        description: 'Fetch one vetting case you own, by case id.',
        inputSchema: {
          type: 'object',
          properties: { case_id: { type: 'string' } },
          required: ['case_id'],
        },
      },
      handler: ({ args, userId }) => {
        const caseId = (args?.case_id || '').toString().trim();
        if (!caseId) return toolText('case_id is required', true);
        return call({
          method: 'GET',
          path: `/case/${encodeURIComponent(caseId)}`,
          userId,
        });
      },
    });

    tools.push({
      scope: 'vetting',
      definition: {
        name: 'vetting_assess_case',
        description:
          'Run the deterministic screening assessment on a case you own. '
          + 'Returns clause-referenced findings, blockers, sign-off triggers '
          + 'and the pinned rule pack. This never issues a pass or fail on a '
          + 'person — the terminal good state is READY_FOR_CONTROLLER_REVIEW, '
          + 'and a human makes the employment decision.',
        inputSchema: {
          type: 'object',
          properties: {
            case_id: { type: 'string' },
            as_of: {
              type: 'string',
              description:
                'ISO date (YYYY-MM-DD) to assess as of. Defaults to today. '
                + 'Supplying it makes the assessment reproducible.',
            },
          },
          required: ['case_id'],
        },
      },
      handler: ({ args, userId }) => {
        const caseId = (args?.case_id || '').toString().trim();
        if (!caseId) return toolText('case_id is required', true);
        const asOf = (args?.as_of || '').toString().trim();
        return call({
          method: 'POST',
          path: `/case/${encodeURIComponent(caseId)}/assess`,
          userId,
          query: asOf ? { as_of: asOf } : {},
        });
      },
    });
  }

  return tools;
}

/**
 * @param {object} deps
 * @param {Function} deps.authenticate  (req) => { keyRecord, user, scopes } | null
 * @param {Function} deps.chatProxy
 * @param {Function} deps.vettingProxy
 * @param {Function} deps.enabled       () => boolean
 */
export function createMcpRouter({
  authenticate, chatProxy, vettingProxy, enabled,
}) {
  if (typeof authenticate !== 'function') {
    throw new Error('createMcpRouter: authenticate required');
  }
  const router = express.Router();
  const tools = buildTools({ chatProxy, vettingProxy });
  const byName = new Map(tools.map(t => [t.definition.name, t]));

  router.use(express.json({ limit: '1mb' }));

  // Discovery for humans/clients probing the mount point. Deliberately says
  // nothing about which tools exist — that requires a key.
  router.get('/', (req, res) => {
    res.json({
      service: 'aria-mcp',
      transport: 'streamable-http',
      protocolVersion: PROTOCOL_VERSION,
      enabled: enabled ? !!enabled() : true,
      hint: 'POST JSON-RPC 2.0 here with Authorization: Bearer crx_…',
    });
  });

  router.post('/', async (req, res) => {
    if (enabled && !enabled()) {
      return res.status(503).json(
        rpcError(null, INTERNAL_ERROR, 'public API not enabled'));
    }

    const message = req.body;
    if (!message || typeof message !== 'object' || Array.isArray(message)) {
      // Batches are not supported: every ARIA tool is a single round trip, and
      // a partially-applied batch of vetting writes is not a state worth having.
      return res.status(400).json(
        rpcError(null, INVALID_REQUEST,
                 'expected a single JSON-RPC 2.0 object'));
    }
    const { id = null, method } = message;
    if (typeof method !== 'string') {
      return res.status(400).json(
        rpcError(id, INVALID_REQUEST, 'method must be a string'));
    }

    // `initialize` is the only method a client may send before it is known to
    // us, but we still require the key first: an unauthenticated caller should
    // not be able to enumerate protocol support either.
    const auth = await authenticate(req);
    if (!auth) {
      return res.status(401).json(
        rpcError(id, INVALID_REQUEST,
                 'missing or invalid Authorization: Bearer crx_… key'));
    }
    if (auth.error) {
      return res.status(auth.status || 403).json(
        rpcError(id, INVALID_REQUEST, auth.error));
    }

    const granted = new Set(auth.scopes || []);
    const visible = tools.filter(t => granted.has(t.scope));

    try {
      switch (method) {
        case 'initialize': {
          const requested = message.params?.protocolVersion;
          const version = SUPPORTED_PROTOCOL_VERSIONS.includes(requested)
            ? requested : PROTOCOL_VERSION;
          return res.json(rpcResult(id, {
            protocolVersion: version,
            capabilities: { tools: { listChanged: false } },
            serverInfo: SERVER_INFO,
          }));
        }

        case 'notifications/initialized':
        case 'notifications/cancelled':
          // Notifications carry no id and MUST NOT get a response body.
          return res.status(202).end();

        case 'ping':
          return res.json(rpcResult(id, {}));

        case 'tools/list':
          return res.json(rpcResult(id, {
            tools: visible.map(t => t.definition),
          }));

        case 'tools/call': {
          const name = message.params?.name;
          const args = message.params?.arguments || {};
          if (typeof name !== 'string' || !name) {
            return res.json(
              rpcError(id, INVALID_PARAMS, 'params.name is required'));
          }
          const tool = byName.get(name);
          // Unknown and unscoped are the SAME answer on purpose — see the
          // module header. A key without 'vetting' must not be able to probe
          // for which vetting tools exist by reading the error text.
          if (!tool || !granted.has(tool.scope)) {
            return res.json(
              rpcError(id, METHOD_NOT_FOUND, `unknown tool: ${name}`));
          }
          const result = await tool.handler({
            args, userId: auth.user.id, keyRecord: auth.keyRecord,
          });
          return res.json(rpcResult(id, result));
        }

        default:
          return res.json(
            rpcError(id, METHOD_NOT_FOUND, `unknown method: ${method}`));
      }
    } catch (err) {
      console.error('[mcp] handler error:', err);
      // A THROWN handler is an infrastructure fault, so it is a protocol
      // error. A tool that ran and failed returns isError:true above instead.
      return res.status(500).json(
        rpcError(id, INTERNAL_ERROR, 'internal error',
                 String(err?.message || err).slice(0, 200)));
    }
  });

  return router;
}

export { PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS };
