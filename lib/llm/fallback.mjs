// lib/llm/fallback.mjs
// Node-side LLM Fallback Chain — mirrors aria_service/llm/fallback.py
//
// Tries providers in priority order. If the primary fails (rate limit, 500,
// auth error), automatically switches to the next available provider.
//
// 2026-04-12: created to fix the gap where the Node/seenode deployment had
// no fallback when Claude hit rate limits — ARIA just returned an error.

import { LLMProvider } from './provider.mjs';

const HARD_COOLDOWN_MS = 30 * 60 * 1000;  // 30 min — auth/billing failures
const SOFT_COOLDOWN_MS = 60 * 1000;       // 60s — rate limits / transient

export class FallbackProvider extends LLMProvider {
  constructor(providers) {
    super({});
    this.providers = providers.filter(p => p?.isConfigured);
    this.name = 'fallback';
    this.model = this.providers[0]?.model || 'unknown';
    this._stats = new Map();

    for (const p of this.providers) {
      this._stats.set(p.name, {
        calls: 0, failures: 0, consecutiveFailures: 0,
        cooldownUntil: 0, lastKind: '',
      });
    }

    if (this.providers.length > 0) {
      console.log(`[LLM Fallback] Chain: ${this.providers.map(p => p.name).join(' → ')}`);
    } else {
      console.warn('[LLM Fallback] No providers configured!');
    }
  }

  get isConfigured() {
    return this.providers.length > 0;
  }

  _shouldSkip(stats) {
    return stats.cooldownUntil > Date.now();
  }

  _classifyError(err) {
    const msg = (err.message || '').toLowerCase();
    const status = parseInt(msg.match(/\b(4\d{2}|5\d{2})\b/)?.[1] || '0');

    if (status === 429 || msg.includes('rate limit')) return 'rate_limit';
    if (status === 401 || status === 403 || msg.includes('auth')) return 'auth';
    if (status === 402 || msg.includes('billing') || msg.includes('insufficient')) return 'billing';
    if (status === 504 || msg.includes('gateway timeout')) return 'timeout';
    if (status >= 500) return 'server';
    if (msg.includes('timeout') || msg.includes('abort')) return 'timeout';
    return 'other';
  }

  _recordSuccess(stats) {
    if (stats.failures > 0 || stats.cooldownUntil > 0) {
      console.log(`[LLM Fallback] Provider recovered — resetting failure stats`);
    }
    stats.failures = 0;
    stats.consecutiveFailures = 0;
    stats.cooldownUntil = 0;
    stats.lastKind = '';
  }

  _recordFailure(providerName, stats, err) {
    stats.failures++;
    stats.consecutiveFailures++;
    const kind = this._classifyError(err);
    stats.lastKind = kind;

    if (kind === 'auth' || kind === 'billing') {
      stats.cooldownUntil = Date.now() + HARD_COOLDOWN_MS;
      console.error(`[LLM Fallback] ${providerName} HARD cooldown (${kind}) for 30m: ${err.message?.slice(0, 200)}`);
    } else if (kind === 'rate_limit') {
      stats.cooldownUntil = Date.now() + SOFT_COOLDOWN_MS;
      console.warn(`[LLM Fallback] ${providerName} rate-limited, soft cooldown 60s`);
    } else if (stats.consecutiveFailures >= 2) {
      stats.cooldownUntil = Date.now() + SOFT_COOLDOWN_MS;
      console.warn(`[LLM Fallback] ${providerName} soft cooldown 60s after ${stats.consecutiveFailures} failures`);
    } else {
      console.warn(`[LLM Fallback] ${providerName} failed (${stats.consecutiveFailures}): ${err.message?.slice(0, 200)} — trying next`);
    }
  }

  async complete(systemPrompt, userMessage, opts = {}) {
    let lastError = null;
    let attempted = false;
    const tStart = Date.now();
    const budget = opts.timeout || 90000;

    for (const provider of this.providers) {
      const stats = this._stats.get(provider.name);
      if (this._shouldSkip(stats)) continue;

      const elapsed = Date.now() - tStart;
      const remaining = budget - elapsed;
      if (remaining < 15000) {
        console.warn(`[LLM Fallback] Budget exhausted (${remaining}ms remaining); skipping ${provider.name}`);
        break;
      }

      attempted = true;
      try {
        stats.calls++;
        const result = await provider.complete(systemPrompt, userMessage, {
          ...opts,
          timeout: Math.min(budget, remaining),
        });
        this._recordSuccess(stats);
        result._routed = `fallback:${provider.name}`;
        return result;
      } catch (err) {
        this._recordFailure(provider.name, stats, err);
        lastError = err;
      }
    }

    if (!attempted) {
      // All providers in cooldown — give the soonest recovery time
      let soonest = Infinity;
      for (const [, s] of this._stats) {
        if (s.cooldownUntil > 0) soonest = Math.min(soonest, s.cooldownUntil);
      }
      const waitSec = Math.max(1, Math.ceil((soonest - Date.now()) / 1000));
      throw new Error(`All LLM providers in cooldown. Retry in ~${waitSec}s.`);
    }

    throw lastError || new Error('All LLM providers failed — try again in a minute');
  }

  getStats() {
    const out = {};
    for (const [name, s] of this._stats) {
      out[name] = {
        calls: s.calls,
        failures: s.failures,
        status: s.cooldownUntil > Date.now() ? 'cooling_down' : 'active',
        lastKind: s.lastKind,
      };
    }
    return out;
  }
}
