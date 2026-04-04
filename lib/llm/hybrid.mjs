// lib/llm/hybrid.mjs
// Hybrid LLM Provider — routes tasks to different models strategically
//
// DeepSeek: complex reasoning, brain assessments, strategic analysis (best quality)
// ARIA Local (Ollama): fast queries, knowledge lookups, simple Q&A (lowest cost, fastest)
//
// Config:
//   LLM_PROVIDER=hybrid
//   LLM_API_KEY=deepseek-api-key
//   OLLAMA_URL=http://localhost:11434  (or remote GPU server)
//   OLLAMA_MODEL=aria-v1              (your fine-tuned model)
//
// Routing logic:
//   - Brain assessments → DeepSeek (needs best reasoning)
//   - Think mode (6-step analysis) → DeepSeek
//   - ARIA chat (simple Q&A) → Ollama first, DeepSeek fallback
//   - Ideas generation → DeepSeek
//   - Everything else → Ollama first, DeepSeek fallback

import { LLMProvider } from './provider.mjs';
import { OllamaProvider } from './ollama.mjs';
import { OpenAIProvider } from './openai.mjs';

export class HybridProvider extends LLMProvider {
  constructor(config) {
    super(config);
    this.name = 'hybrid';

    // Primary: DeepSeek (best quality, costs money)
    this.primary = new OpenAIProvider({
      apiKey: config.apiKey,
      model: config.model || 'deepseek-chat',
      baseUrl: config.baseUrl || 'https://api.deepseek.com/v1',
    });

    // Secondary: Ollama (local/self-hosted, free, fast)
    this.secondary = new OllamaProvider({
      model: config.ollamaModel || process.env.OLLAMA_MODEL || 'llama3.1:8b',
      baseUrl: config.ollamaUrl || process.env.OLLAMA_URL || 'http://localhost:11434',
    });

    // Track if Ollama is reachable
    this._ollamaAvailable = null;
    this._lastOllamaCheck = 0;
  }

  get isConfigured() {
    return this.primary.isConfigured;
  }

  /**
   * Check if Ollama is reachable (cached for 5 minutes).
   */
  async _isOllamaReady() {
    const now = Date.now();
    if (this._ollamaAvailable !== null && now - this._lastOllamaCheck < 300000) {
      return this._ollamaAvailable;
    }
    try {
      const r = await fetch(this.secondary.baseUrl + '/api/tags', {
        signal: AbortSignal.timeout(3000),
      });
      this._ollamaAvailable = r.ok;
    } catch {
      this._ollamaAvailable = false;
    }
    this._lastOllamaCheck = now;
    return this._ollamaAvailable;
  }

  /**
   * Route to appropriate model based on task complexity.
   */
  async complete(systemPrompt, userMessage, opts = {}) {
    const useDeepSeek = this._shouldUseDeepSeek(systemPrompt, userMessage, opts);

    if (useDeepSeek) {
      // Complex task → DeepSeek (best quality)
      try {
        const result = await this.primary.complete(systemPrompt, userMessage, opts);
        return { ...result, _routed: 'deepseek' };
      } catch (err) {
        console.warn('[Hybrid] DeepSeek failed, trying Ollama fallback:', err.message);
        // Fall through to Ollama
      }
    }

    // Simple task or DeepSeek failed → try Ollama first
    const ollamaReady = await this._isOllamaReady();
    if (ollamaReady) {
      try {
        const result = await this.secondary.complete(systemPrompt, userMessage, opts);
        return { ...result, _routed: 'ollama' };
      } catch (err) {
        console.warn('[Hybrid] Ollama failed:', err.message);
      }
    }

    // Ollama not available or failed → DeepSeek as final fallback
    const result = await this.primary.complete(systemPrompt, userMessage, opts);
    return { ...result, _routed: 'deepseek-fallback' };
  }

  /**
   * Determine if this task needs DeepSeek (complex) or can use Ollama (simple).
   */
  _shouldUseDeepSeek(systemPrompt, userMessage, opts) {
    const sys = (systemPrompt || '').toLowerCase();
    const msg = (userMessage || '').toLowerCase();

    // Brain assessment — always DeepSeek (needs best reasoning + JSON output)
    if (sys.includes('autonomous bd intelligence brain') || sys.includes('crucix')) return true;

    // Think mode — always DeepSeek (6-step deep analysis)
    if (sys.includes('deep reasoning protocol') || sys.includes('6-step')) return true;

    // Ideas generation — always DeepSeek
    if (msg.includes('generate') && (msg.includes('ideas') || msg.includes('strategy'))) return true;

    // Long response needed — DeepSeek handles better
    if (opts.maxTokens > 2000) return true;

    // Complex queries (compliance, multi-market, financial)
    if (msg.includes('compliance') || msg.includes('itar') || msg.includes('export control')) return true;
    if (msg.includes('compare') || msg.includes('versus') || msg.includes('which market')) return true;

    // Everything else → Ollama (fast, free)
    return false;
  }
}
