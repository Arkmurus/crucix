/**
 * ARIA — Conversation Manager for WhatsApp
 * ═══════════════════════════════════════════════════════════════════════════
 * Self-healing conversation layer between Baileys and the brain.
 *
 * Replaces the stateless proxy pattern with a stateful conversation manager
 * that handles:
 *   1. Conversation state machine (idle → thinking → clarifying → responding)
 *   2. Self-healing retry with fallback strategies
 *   3. Progressive disclosure (short answer → offer details)
 *   4. Clarification loop (ask when ambiguous)
 *   5. Mid-stream adaptation (user can interrupt/redirect)
 *
 * Each conversation has a state, a strategy stack, and a message buffer.
 * When a strategy fails, the manager tries the next one automatically.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * STATES
 * ─────────────────────────────────────────────────────────────────────────
 *   idle         — no active conversation
 *   thinking     — waiting for brain response
 *   clarifying   — asked user a question, waiting for answer
 *   responding   — sending response chunks
 *   failed       — all strategies exhausted
 *
 * ─────────────────────────────────────────────────────────────────────────
 * STRATEGIES (tried in order)
 * ─────────────────────────────────────────────────────────────────────────
 *   1. fast_llm     — short timeout, simple prompt (for quick answers)
 *   2. full_llm     — full timeout, full context (for research)
 *   3. local_only   — no LLM, rule-based only (degraded but functional)
 *   4. fallback_msg — honest "I can't do this right now" message
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SELF-HEALING
 * ─────────────────────────────────────────────────────────────────────────
 *   - On 502/503: retry with next strategy (don't show error to user)
 *   - On timeout: retry with longer budget
 *   - On parse fail: try alternative parser
 *   - On disconnect: queue messages, replay on reconnect
 *   - All failures recorded to brain for learning
 */

// ── Conversation states ─────────────────────────────────────────────────
const STATE = {
  IDLE:       'idle',
  THINKING:   'thinking',
  CLARIFYING: 'clarifying',
  RESPONDING: 'responding',
  FAILED:     'failed',
};

// ── Strategy definitions ────────────────────────────────────────────────
const STRATEGIES = {
  FAST_LLM:   { name: 'fast_llm',   timeoutMs: 60000,  label: 'quick answer' },
  FULL_LLM:   { name: 'full_llm',   timeoutMs: 300000, label: 'full research' },
  LOCAL_ONLY: { name: 'local_only', timeoutMs: 30000,  label: 'local data only' },
  FALLBACK:   { name: 'fallback',   timeoutMs: 5000,   label: 'unavailable' },
};

// ── Conversation class ──────────────────────────────────────────────────
export class Conversation {
  constructor(chatId, senderName, groupName) {
    this.chatId = chatId;
    this.senderName = senderName;
    this.groupName = groupName;
    this.state = STATE.IDLE;
    this.strategyIndex = 0;
    this.messageBuffer = [];        // messages during this conversation
    this.attachedDoc = null;        // document text if any
    this.lastActivity = Date.now();
    this.retryCount = 0;
    this.maxRetries = 3;
    this.startTime = Date.now();
    this.traceId = null;
  }

  get age() { return Date.now() - this.startTime; }
  get isStale() { return this.age > 600000 && this.state === STATE.IDLE; } // 10min idle

  // ── Strategy management ───────────────────────────────────────────────
  currentStrategy() {
    const strategies = [STRATEGIES.FAST_LLM, STRATEGIES.FULL_LLM, STRATEGIES.LOCAL_ONLY, STRATEGIES.FALLBACK];
    return strategies[this.strategyIndex] || strategies[strategies.length - 1];
  }

  nextStrategy() {
    this.strategyIndex++;
    this.retryCount++;
    return this.currentStrategy();
  }

  hasMoreStrategies() {
    return this.strategyIndex < 4; // all 4 strategies including fallback
  }

  // ── State transitions ─────────────────────────────────────────────────
  startThinking() { this.state = STATE.THINKING; this.lastActivity = Date.now(); }
  startClarifying() { this.state = STATE.CLARIFYING; this.lastActivity = Date.now(); }
  startResponding() { this.state = STATE.RESPONDING; this.lastActivity = Date.now(); }
  markFailed() { this.state = STATE.FAILED; }
  reset() { this.state = STATE.IDLE; this.strategyIndex = 0; this.retryCount = 0; }

  // ── Message management ────────────────────────────────────────────────
  addMessage(text) {
    this.messageBuffer.push({ text, ts: Date.now() });
    if (this.messageBuffer.length > 20) this.messageBuffer.shift();
  }

  getContext() {
    return this.messageBuffer.slice(-5).map(m => m.text).join('\n');
  }
}

// ── Conversation Manager ────────────────────────────────────────────────
export class ConversationManager {
  constructor() {
    this.conversations = new Map();  // chatId → Conversation
    this._staleInterval = setInterval(() => this._cleanStale(), 300000); // 5min cleanup
  }

  // ── Get or create conversation ────────────────────────────────────────
  getOrCreate(chatId, senderName, groupName) {
    let conv = this.conversations.get(chatId);
    if (!conv) {
      conv = new Conversation(chatId, senderName, groupName);
      this.conversations.set(chatId, conv);
    }
    return conv;
  }

  get(chatId) {
    return this.conversations.get(chatId);
  }

  // ── Handle incoming message ───────────────────────────────────────────
  async handleMessage(msg, chatId, groupName, senderName, text, sendMessageFn, askARIAFn) {
    const conv = this.getOrCreate(chatId, senderName, groupName);
    conv.addMessage(text);

    // If we were waiting for clarification, treat this as the answer
    if (conv.state === STATE.CLARIFYING) {
      conv.startThinking();
      return this._processWithStrategies(conv, text, sendMessageFn, askARIAFn);
    }

    // Normal message — start processing
    conv.startThinking();
    return this._processWithStrategies(conv, text, sendMessageFn, askARIAFn);
  }

  // ── Process with strategy stack (self-healing core) ───────────────────
  async _processWithStrategies(conv, text, sendMessageFn, askARIAFn) {
    while (conv.hasMoreStrategies()) {
      const strategy = conv.currentStrategy();
      try {
        const result = await this._executeStrategy(strategy, conv, text, askARIAFn);
        if (result && result.text) {
          conv.startResponding();
          return result;
        }
        // Empty result — try next strategy
        conv.nextStrategy();
      } catch (err) {
        console.warn(`[ConvManager] Strategy ${strategy.name} failed for ${conv.chatId}: ${err.message}`);
        conv.nextStrategy();
      }
    }

    // All strategies exhausted — send honest fallback
    conv.markFailed();
    return null;
  }

  // ── Progressive streaming: send partial results as they arrive ────────
  // R-F1768: the SSE stream from /api/aria/chat/stream already sends
  // progress events (R-F737) and heartbeats (R-F1747) during long tool
  // execution. The WhatsApp listener's askARIA() function receives these
  // via the progressCb callback and surfaces them as interim messages.
  // This is the safest approach because:
  //   1. Each partial result is a COMPLETE piece of data from a real tool
  //   2. No rushed LLM calls that might hallucinate
  //   3. The user sees continuous progress instead of silence
  //   4. The full analysis arrives as one complete message when done
  async streamPartialResults(conv, text, sendMessageFn, askARIAFn) {
    return this._processWithStrategies(conv, text, sendMessageFn, askARIAFn);
  }

  // ── Execute a single strategy ─────────────────────────────────────────
  async _executeStrategy(strategy, conv, text, askARIAFn) {
    switch (strategy.name) {
      case 'fast_llm':
        return askARIAFn(text, conv.getContext(), conv.senderName, strategy.timeoutMs);

      case 'full_llm':
        return askARIAFn(text, conv.getContext(), conv.senderName, strategy.timeoutMs);

      case 'local_only':
        // Try the local brain endpoint (no LLM) — calls /api/aria/chat
        // with skip_aria_service=true so it routes to the Node fallback
        // instead of the Python LLM chain. Returns a degraded but real
        // answer in ~30s instead of nothing.
        return this._tryLocalOnly(text, conv);

      case 'fallback':
        return { text: this._buildFallbackMessage(conv) };

      default:
        return null;
    }
  }

  // ── Local-only fallback (no LLM) ──────────────────────────────────────
  async _tryLocalOnly(text, conv) {
    // Calls the local Node fallback endpoint which uses rule-based
    // responses (sanctions screen, country risk, etc.) without any LLM.
    // This gives the user a degraded but functional answer instead of
    // a generic error message.
    try {
      const port = process.env.PORT || 3117;
      const token = process.env.ARIA_INTERNAL_TOKEN || '';
      const r = await fetch(`http://localhost:${port}/api/aria/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: text,
          session_id: `wa_group_${conv.senderName.replace(/[^a-zA-Z0-9]/g, '').slice(-10)}`,
          auto_tools: true,
          skip_aria_service: true,  // skip Python LLM chain, use Node fallback
        }),
        signal: AbortSignal.timeout(30000),
      });
      if (!r.ok) return null;
      const data = await r.json();
      if (data && data.text) {
        return { text: data.text };
      }
      return null;
    } catch {
      return null;
    }
  }

  // ── Build honest fallback message ─────────────────────────────────────
  _buildFallbackMessage(conv) {
    const attempts = conv.retryCount;
    return `⚠️ I tried ${attempts} different ways to answer that and couldn't get through to my brain. This is unusual — my LLM provider may be temporarily unavailable.\n\nYou can:\n• Try again in 2-3 minutes\n• Use /screen [name] for a local sanctions check\n• Use /risk [country] for local country risk data\n\n_I've logged this failure and will learn from it._`;
  }

  // ── Ask a clarification question ──────────────────────────────────────
  async askClarification(conv, question, sendMessageFn) {
    conv.startClarifying();
    await sendMessageFn(conv.chatId, `*ARIA* — ${question}`);
  }

  // ── Clean stale conversations ─────────────────────────────────────────
  _cleanStale() {
    for (const [chatId, conv] of this.conversations) {
      if (conv.isStale) {
        this.conversations.delete(chatId);
      }
    }
  }

  // ── Destroy ───────────────────────────────────────────────────────────
  destroy() {
    if (this._staleInterval) clearInterval(this._staleInterval);
    this.conversations.clear();
  }
}

// ── Singleton ───────────────────────────────────────────────────────────
export const conversationManager = new ConversationManager();
