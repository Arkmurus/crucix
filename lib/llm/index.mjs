// LLM Factory — creates the configured provider or returns null

import { AnthropicProvider } from "./anthropic.mjs";
import { OpenAIProvider } from "./openai.mjs";
import { OpenRouterProvider } from "./openrouter.mjs";
import { GeminiProvider } from "./gemini.mjs";
import { CodexProvider } from "./codex.mjs";
import { MiniMaxProvider } from "./minimax.mjs";
import { MistralProvider } from "./mistral.mjs";
import { OllamaProvider } from "./ollama.mjs";
import { HybridProvider } from "./hybrid.mjs";
import { FallbackProvider } from "./fallback.mjs";

export { LLMProvider } from "./provider.mjs";
export { AnthropicProvider } from "./anthropic.mjs";
export { OpenAIProvider } from "./openai.mjs";
export { OpenRouterProvider } from "./openrouter.mjs";
export { GeminiProvider } from "./gemini.mjs";
export { CodexProvider } from "./codex.mjs";
export { MiniMaxProvider } from "./minimax.mjs";
export { MistralProvider } from "./mistral.mjs";
export { OllamaProvider } from "./ollama.mjs";
export { HybridProvider } from "./hybrid.mjs";
export { FallbackProvider } from "./fallback.mjs";

/**
 * Create an LLM provider based on config.
 * Automatically wraps in a FallbackProvider when additional API keys are
 * available in env vars (DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY).
 *
 * @param {{ provider: string|null, apiKey: string|null, model: string|null, baseUrl: string|null }} llmConfig
 * @returns {LLMProvider|null}
 */
export function createLLMProvider(llmConfig) {
  if (!llmConfig?.provider) return null;

  const { provider, apiKey, model, baseUrl } = llmConfig;
  let primary;

  switch (provider.toLowerCase()) {
    case "anthropic":
      primary = new AnthropicProvider({ apiKey, model });
      break;
    case "openai":
      primary = new OpenAIProvider({ apiKey, model, baseUrl });
      break;
    case "deepseek":
      primary = new OpenAIProvider({ apiKey, model: model || 'deepseek-chat', baseUrl: baseUrl || 'https://api.deepseek.com/v1' });
      primary.name = 'deepseek';
      break;
    case "openrouter":
      primary = new OpenRouterProvider({ apiKey, model });
      break;
    case "gemini":
      primary = new GeminiProvider({ apiKey, model });
      break;
    case "codex":
      primary = new CodexProvider({ model });
      break;
    case "minimax":
      primary = new MiniMaxProvider({ apiKey, model });
      break;
    case "mistral":
      primary = new MistralProvider({ apiKey, model });
      break;
    case "ollama":
      primary = new OllamaProvider({ model, baseUrl });
      break;
    case "hybrid":
      // Uses DeepSeek for complex tasks + Ollama for simple tasks
      primary = new HybridProvider({ apiKey, model, baseUrl });
      break;
    default:
      console.warn(
        `[LLM] Unknown provider "${provider}". LLM features disabled.`,
      );
      return null;
  }

  if (!primary?.isConfigured) return primary;

  // Build fallback chain from env vars — auto-detect secondary providers
  // BUG-FIX 2026-04-12: Node side had no fallback when Claude hit rate limits.
  const fallbacks = [];
  const pName = provider.toLowerCase();

  if (pName !== 'deepseek' && process.env.DEEPSEEK_API_KEY) {
    const ds = new OpenAIProvider({
      apiKey: process.env.DEEPSEEK_API_KEY,
      model: 'deepseek-chat',
      baseUrl: 'https://api.deepseek.com/v1',
    });
    ds.name = 'deepseek';
    fallbacks.push(ds);
  }
  if (pName !== 'anthropic' && process.env.ANTHROPIC_API_KEY && process.env.ANTHROPIC_API_KEY !== apiKey) {
    fallbacks.push(new AnthropicProvider({
      apiKey: process.env.ANTHROPIC_API_KEY,
      model: 'claude-sonnet-4-6',
    }));
  }
  if (pName !== 'openai' && process.env.OPENAI_API_KEY) {
    fallbacks.push(new OpenAIProvider({
      apiKey: process.env.OPENAI_API_KEY,
      model: 'gpt-4o-mini',
      baseUrl: 'https://api.openai.com/v1',
    }));
  }
  // Last resort: local Ollama (if configured / running)
  if (pName !== 'ollama' && pName !== 'hybrid' && (process.env.OLLAMA_URL || process.env.OLLAMA_BASE_URL)) {
    fallbacks.push(new OllamaProvider({
      model: process.env.OLLAMA_MODEL || 'deepseek-r1:7b',
      baseUrl: process.env.OLLAMA_URL || process.env.OLLAMA_BASE_URL,
    }));
  }

  if (fallbacks.length === 0) return primary;

  return new FallbackProvider([primary, ...fallbacks]);
}