// Anthropic Claude Provider — raw fetch, no SDK

import { LLMProvider } from './provider.mjs';
import { ariaFetch } from '../aria/_ariaFetch.mjs';

// R-F2885 — report Node-tier Claude spend to the Python brain's cost ledger.
//
// The Node tier has no cost tracking of its own, so every token billed here was
// invisible to cost_tracker — and therefore to BOTH the $300 monthly cap and the
// R-F2888 daily cap. Anthropic returns exact usage on every response; we forward
// it so cross-tier spend lands on one ledger (§21b: no dark engines).
//
// Fire-and-forget by design: a cost-reporting failure must never break a user's
// reply. A dropped report is one lost metric, and the vendor bill is still the
// backstop — but the call is cheap and in-cluster, so this is rare.
async function reportSpend(model, usage, latencyMs, ok, err) {
  try {
    await ariaFetch('/api/aria/cost/record-web-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        provider: 'anthropic',
        input_tokens: usage?.inputTokens || 0,
        output_tokens: usage?.outputTokens || 0,
        latency_ms: latencyMs,
        feature: 'web_tier',
        success: ok,
        error: (err || '').substring(0, 200),
      }),
    });
  } catch (e) {
    console.warn('[LLM] anthropic spend report failed:', e?.message || e);
  }
}

export class AnthropicProvider extends LLMProvider {
  constructor(config) {
    super(config);
    this.name = 'anthropic';
    this.apiKey = config.apiKey;
    this.model = config.model || 'claude-sonnet-4-6';
  }

  get isConfigured() { return !!this.apiKey; }

  async complete(systemPrompt, userMessage, opts = {}) {
    const _t0 = Date.now();
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': this.apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: opts.maxTokens || 4096,
        system: systemPrompt,
        messages: [{ role: 'user', content: userMessage }],
      }),
      signal: AbortSignal.timeout(opts.timeout || 60000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => '');
      // A non-2xx is generally not billed, but report it so a failing-and-
      // retrying web tier is visible on the ledger rather than silent.
      reportSpend(this.model, null, Date.now() - _t0, false,
                  `HTTP ${res.status}`);
      throw new Error(`Anthropic API ${res.status}: ${err.substring(0, 200)}`);
    }

    const data = await res.json();
    const text = data.content?.[0]?.text || '';
    const usage = {
      inputTokens: data.usage?.input_tokens || 0,
      outputTokens: data.usage?.output_tokens || 0,
    };
    const model = data.model || this.model;

    // R-F2885 — not awaited: the reply must not wait on the cost ledger.
    reportSpend(model, usage, Date.now() - _t0, true, '');

    return { text, usage, model };
  }
}
