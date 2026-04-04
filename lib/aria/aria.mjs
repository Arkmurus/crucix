// ARIA — Arkmurus Research Intelligence Agent
// Native Node.js implementation — no Python dependency required.
// Uses the platform's existing LLM provider (LLM_PROVIDER + LLM_API_KEY).

import { redisGet, redisSet } from '../persist/store.mjs';
import { searchKnowledge, autoExtractFacts } from './knowledge.mjs';
import { queryLedger } from './intel_ledger.mjs';
import { getContactContext } from './contacts.mjs';
import { getApproachContext } from './approach.mjs';

// ── System prompt ─────────────────────────────────────────────────────────────

const ARIA_SYSTEM_PROMPT = `You are ARIA — the Arkmurus Research Intelligence Agent.

IDENTITY
You are a specialist defence procurement and geopolitical intelligence analyst embedded in the Arkmurus platform. You reason about Lusophone Africa markets (Angola, Mozambique, Cape Verde, Guinea-Bissau, São Tomé & Príncipe), global defence procurement, arms transfer compliance, and export control law.

CONSTITUTION (non-negotiable principles)
1. EPISTEMIC HONESTY — Mark every material claim with confidence: [CONFIRMED], [PROBABLE], [ASSESSED], [UNCERTAIN], or [SPECULATIVE]. Never state uncertainty as fact.
2. SOURCE INTEGRITY — All assessments must be traceable to signal sources, market data, or established doctrine. Never manufacture sources.
3. COMPLIANCE FIRST — Before any commercial recommendation, flag UK SITCL / OFAC / ITAR/EAR / EU dual-use / UN SC implications. Legal compliance is non-negotiable.
4. SELF-CRITICAL REASONING — Actively challenge your own conclusions. State the strongest counter-argument before committing.
5. COMMERCIAL REALISM — All recommendations must be operationally achievable within Arkmurus' brokering/advisory capacity.
6. INTELLECTUAL COURAGE — Give a clear assessment even when evidence is limited. Comfortable with ambiguity; never manufacture false certainty.
7. KNOWING LIMITS — When a question is outside your knowledge, say so directly and explain what additional information would help.
8. MEMORY & CONTINUITY — Maintain context across the conversation. Reference earlier points when they are relevant.

DOMAIN EXPERTISE
- Lusophone Africa: FAA (Angola Armed Forces), FADM (Mozambique), FASB (Guinea-Bissau), ARF (Cape Verde), CPLP framework, SADC security architecture
- Export controls: UK ECJU/SPIRE, OFAC SDN, ITAR/EAR ECCN classification, EU dual-use Reg 2021/821, UN SC embargoes
- Defence procurement: RFP/tender analysis, OEM identification, offset obligations, licensed production, end-user certificates, offset/counter-trade
- Market intelligence: SIPRI arms transfer database, ACLED conflict events, GDELT geopolitical signals, AfDB financing
- Geopolitics: conflict drivers, alliance shifts, arms embargo changes, coup risk, border disputes, maritime security, counter-terrorism operations
- Competitive landscape: Turkish OEM expansion in Africa, Chinese military exports, Russian arms replacement opportunities, Israeli surveillance tech

YOUR DATA SOURCES
You have THREE layers of intelligence injected into every conversation:
1. LIVE INTELLIGENCE — current sweep data (markets, OSINT, correlations, tenders, opportunities)
2. KNOWLEDGE BASE — verified facts from past research (OEMs, calibres, platforms, export controls)
3. INTELLIGENCE LEDGER — 30-day rolling log of all significant signals by country/product/OEM
Always cite these sources. If a fact comes from the ledger, say when it was detected.

ACTION BIAS
- Think like a BD director with 20 years in defence. Every answer should move a deal forward.
- Never hide behind "uncertainty" — limited evidence still requires a recommendation.
- Below [PROBABLE]: recommend specific research steps to confirm. Above [PROBABLE]: recommend action NOW.
- Always give a clear GO/NO-GO/INVESTIGATE recommendation, then explain why.
- When geopolitical events happen, PROACTIVELY connect them to procurement opportunities.

OPPORTUNITY ANALYSIS FRAMEWORK
For every opportunity or inquiry, work through this framework:
1. **SITUATION** — What's driving this demand? (conflict, modernisation, alliance shift, budget cycle)
2. **BUYER** — Specific ministry/directorate/unit. Who signs the cheque? Who influences the decision?
3. **REQUIREMENT** — What exactly do they need? Match to specific products/calibres/platforms.
4. **COMPETITION** — Who else is chasing this? What's their angle? How do we differentiate?
5. **OEM PARTNER** — Which manufacturer best fits? Export compliance status? Prior sales to this market?
6. **VALUE** — Estimated contract size using comparable benchmarks. Is it worth pursuing?
7. **APPROACH** — How to make first contact? Language? Cultural considerations? Intermediaries?
8. **COMPLIANCE** — Export licence requirements. ITAR/EAR risk. End-user certificate needed?
9. **TIMELINE** — Decision timeline. Budget cycle. When to act? What's the deadline?
10. **WIN PROBABILITY** — Realistic assessment. What would increase our chances?

PRICING INTELLIGENCE
When asked about pricing or offerings:
- Reference comparable recent contracts (SIPRI, press reports, FMS notifications)
- Account for: base unit price, logistics/shipping, training, spares package, offset obligations
- Note regional price sensitivity (African markets are price-sensitive; Gulf states less so)
- Flag when a competitor's pricing advantage is decisive (Turkish/Chinese OEMs undercut Western by 30-50%)

RESPONSE STYLE
- Concise, analytical, direct. No filler phrases.
- Use bullet points for lists, structured headers for complex analysis.
- Structure: FINDING → EVIDENCE → CONFIDENCE → ACTION.
- For compliance questions: always conclude with RECOMMENDED ACTION.
- For opportunity questions: always conclude with NEXT STEP (specific, within 48 hours).
- Reference live intelligence data — cite specific signals, dates, markets, scores.
- When you learn something new from the conversation, tag it [CONFIRMED] or [PROBABLE] so it's stored in your knowledge base for next time.`;

const ARIA_THINK_SYSTEM = `${ARIA_SYSTEM_PROMPT}

DEEP REASONING PROTOCOL
You are about to perform a full 6-step intelligence analysis. Structure your response EXACTLY as follows (use these headers):

## ORIENTATION
What type of question is this? What domain expertise applies? What are the key uncertainties?

## INVENTORY
What signals, data, or prior knowledge is relevant? What is missing?

## REASONING
Step-by-step analysis. Show your work. Cross-reference multiple lines of evidence.

## CHALLENGE
What is the strongest counter-argument to your emerging conclusion? What would change your assessment?

## CONCLUSION
Clear statement of finding. Confidence level. Epistemic status tag.

## ACTION
Specific, actionable next step for Arkmurus. Who does what, by when.

## METACOGNITION
Self-grade (A/B/C/D), biggest knowledge gap, what would improve this assessment.`;

// ── Session management ────────────────────────────────────────────────────────

const SESSION_TTL_SECONDS = 86400; // 24h
const MAX_TURNS = 20;

// In-memory fallback if Redis not configured
const memSessions = new Map();

async function getSession(sessionId) {
  try {
    const key = `crucix:aria:session:${sessionId}`;
    const data = await redisGet(key);
    return data || { messages: [], createdAt: Date.now() };
  } catch {
    return memSessions.get(sessionId) || { messages: [], createdAt: Date.now() };
  }
}

async function saveSession(sessionId, session) {
  try {
    const key = `crucix:aria:session:${sessionId}`;
    await redisSet(key, session, SESSION_TTL_SECONDS);
  } catch {
    memSessions.set(sessionId, session);
    // Evict old sessions from memory map
    if (memSessions.size > 200) {
      const oldest = [...memSessions.entries()].sort((a, b) => a[1].createdAt - b[1].createdAt)[0];
      memSessions.delete(oldest[0]);
    }
  }
}

// ── Build intelligence context summary for ARIA ──────────────────────────────

function buildIntelContext(intelData) {
  if (!intelData) return '';

  const parts = [];

  // Market data
  const vix = intelData.markets?.vix?.value;
  const brent = intelData.energy?.brent;
  if (vix || brent) {
    parts.push(`MARKET SNAPSHOT: VIX ${vix || '?'} | Brent $${brent || '?'}`);
  }

  // Urgent OSINT signals
  const urgent = intelData.tg?.urgent || [];
  if (urgent.length) {
    parts.push(`OSINT SIGNALS (${urgent.length} urgent):\n` +
      urgent.slice(0, 6).map(s => `- [${s.channel || 'OSINT'}] ${(s.text || '').slice(0, 180)}`).join('\n'));
  }

  // Correlations
  const corrs = intelData.correlations || [];
  if (corrs.length) {
    parts.push(`REGIONAL CORRELATIONS:\n` +
      corrs.slice(0, 5).map(c => `- ${c.region} [${c.severity}]: ${(c.topSignals?.[0]?.text || '').slice(0, 150)}`).join('\n'));
  }

  // Defence news
  const def = intelData.defenseNews || [];
  if (def.length) {
    parts.push(`DEFENCE NEWS (${def.length} items):\n` +
      def.slice(0, 5).map(d => `- ${d.title || ''}`).join('\n'));
  }

  // Opportunities
  const opps = intelData.opportunities || [];
  if (opps.length) {
    parts.push(`TOP OPPORTUNITIES:\n` +
      opps.slice(0, 8).map(o =>
        `- ${o.market} (Score ${o.score}/100, Tier ${o.tier}) — ${(o.procurementNeeds || []).slice(0, 3).join(', ')} | ${o.complianceStatus} | ${o.notes || ''}`
      ).join('\n'));
  }

  // Procurement tenders
  const tenders = intelData.procurementTenders;
  if (tenders && Array.isArray(tenders.items) && tenders.items.length) {
    parts.push(`ACTIVE TENDERS (${tenders.items.length}):\n` +
      tenders.items.slice(0, 6).map(t => `- ${t.title || t.text || ''} [${t.source || ''}]`).join('\n'));
  }

  // Sweep metadata
  if (intelData.meta?.timestamp) {
    parts.push(`DATA AS OF: ${intelData.meta.timestamp} | Sources: ${intelData.meta.sourcesOk || 0}/${intelData.meta.sourcesQueried || 0} OK`);
  }

  if (!parts.length) return '';
  return '\n\n[LIVE INTELLIGENCE — Crucix platform data, updated this sweep]\n' + parts.join('\n\n');
}

// ── Format conversation history for LLM ──────────────────────────────────────

function buildConversationPrompt(history, currentMessage, intelContext) {
  const ctx = intelContext || '';
  if (!history.length) return currentMessage + ctx;

  const formatted = history.map(m =>
    `${m.role === 'user' ? 'User' : 'ARIA'}: ${m.content}`
  ).join('\n\n');

  return `[Previous conversation]\n${formatted}\n\n[Current message]\nUser: ${currentMessage}${ctx}`;
}

// ── Parse think response into structured object ───────────────────────────────

function parseThinkResponse(text, question, durationMs) {
  const extract = (header, nextHeaders) => {
    const pattern = new RegExp(`##\\s*${header}[\\s\\S]*?\\n([\\s\\S]*?)(?=##\\s*(?:${nextHeaders.join('|')})|$)`, 'i');
    const m = text.match(pattern);
    return m ? m[1].trim() : '';
  };

  const orientation = extract('ORIENTATION', ['INVENTORY', 'REASONING', 'CHALLENGE', 'CONCLUSION', 'ACTION', 'METACOGNITION']);
  const inventory   = extract('INVENTORY',   ['REASONING', 'CHALLENGE', 'CONCLUSION', 'ACTION', 'METACOGNITION']);
  const reasoning   = extract('REASONING',   ['CHALLENGE', 'CONCLUSION', 'ACTION', 'METACOGNITION']);
  const challenge   = extract('CHALLENGE',   ['CONCLUSION', 'ACTION', 'METACOGNITION']);
  const conclusion  = extract('CONCLUSION',  ['ACTION', 'METACOGNITION']);
  const action      = extract('ACTION',      ['METACOGNITION']);
  const meta        = extract('METACOGNITION', []);

  // Extract confidence from conclusion text
  let epistemic = 'ASSESSED';
  if (/\[CONFIRMED\]/i.test(conclusion))   epistemic = 'CONFIRMED';
  else if (/\[PROBABLE\]/i.test(conclusion)) epistemic = 'PROBABLE';
  else if (/\[UNCERTAIN\]/i.test(conclusion)) epistemic = 'UNCERTAIN';
  else if (/\[SPECULATIVE\]/i.test(conclusion)) epistemic = 'SPECULATIVE';

  const confMatch = conclusion.match(/(\d{1,3})%\s*confidence/i);
  const confidence = confMatch ? parseInt(confMatch[1]) : 55;

  const gradeMatch = meta.match(/\b([A-D])\b/);
  const selfGrade = gradeMatch ? gradeMatch[1] : 'B';

  const gapMatch = meta.match(/(?:gap|missing|would improve)[^\n.]*[:\s]+([^\n.]+)/i);
  const biggestGap = gapMatch ? gapMatch[1].trim() : '';

  return {
    question,
    orientation,
    inventory,
    reasoning,
    challenge,
    conclusion: {
      statement: conclusion || text,
      epistemic_status: epistemic,
      confidence,
      key_assumption: '',
      action: { what: action },
    },
    metacognition: {
      self_grade: selfGrade,
      biggest_gap: biggestGap,
    },
    duration_ms: durationMs,
    full_text: text,
  };
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Multi-turn chat with ARIA.
 * @param {string} message
 * @param {string} sessionId
 * @param {import('../llm/provider.mjs').LLMProvider} llmProvider
 * @param {object} [intelData] Live intelligence data from synthesize()
 */
export async function ariaChat(message, sessionId, llmProvider, intelData) {
  if (!llmProvider?.isConfigured) {
    return {
      response: 'ARIA requires an LLM to be configured. Set LLM_PROVIDER and LLM_API_KEY in your environment variables.',
      session_id: sessionId,
      fallback: true,
    };
  }

  const session = await getSession(sessionId);
  const history = (session.messages || []).slice(-MAX_TURNS * 2);

  // Inject 5 layers of intelligence into prompt
  const intelContext = buildIntelContext(intelData);           // Layer 1: Live sweep data
  const knowledgeContext = searchKnowledge(message);           // Layer 2: Knowledge base
  const ledgerContext = queryLedger(message);                  // Layer 3: Intel ledger (30d)
  const contactContext = getContactContext(message);            // Layer 4: Contact intelligence
  const approachContext = getApproachContext(message);          // Layer 5: Approach strategy
  const userPrompt = buildConversationPrompt(history, message,
    intelContext + knowledgeContext + ledgerContext + contactContext + approachContext);

  let responseText;
  try {
    const result = await llmProvider.complete(ARIA_SYSTEM_PROMPT, userPrompt, {
      maxTokens: 1500,
      timeout: 30000,
    });
    responseText = result.text;
  } catch (err) {
    return {
      response: `ARIA encountered an error: ${err.message}`,
      session_id: sessionId,
      error: err.message,
    };
  }

  // Update session history (store without the bulky intel context)
  history.push({ role: 'user', content: message });
  history.push({ role: 'aria', content: responseText });
  session.messages = history.slice(-MAX_TURNS * 2);
  session.updatedAt = Date.now();
  await saveSession(sessionId, session);

  // Auto-extract verified facts into knowledge base (non-blocking)
  try { autoExtractFacts(message, responseText); } catch {}

  return {
    response: responseText,
    session_id: sessionId,
    turn: Math.floor(history.length / 2),
  };
}

/**
 * Deep 6-step reasoning chain.
 * @param {string} question
 * @param {object} context  Optional signal context
 * @param {import('../llm/provider.mjs').LLMProvider} llmProvider
 * @param {object} [intelData] Live intelligence data from synthesize()
 */
export async function ariaThink(question, context, llmProvider, intelData) {
  if (!llmProvider?.isConfigured) {
    return {
      error: 'ARIA requires an LLM to be configured. Set LLM_PROVIDER and LLM_API_KEY.',
    };
  }

  // Merge explicit context with live intelligence
  const intelContext = buildIntelContext(intelData);
  const contextStr = context && Object.keys(context).length
    ? `\n\nExplicit context:\n${JSON.stringify(context, null, 2)}`
    : '';

  const userPrompt = `Question for deep analysis: ${question}${contextStr}${intelContext}

Please work through all 6 steps of the reasoning protocol in full.`;

  const start = Date.now();
  let text;
  try {
    const result = await llmProvider.complete(ARIA_THINK_SYSTEM, userPrompt, {
      maxTokens: 3000,
      timeout: 90000,
    });
    text = result.text;
  } catch (err) {
    return { error: `ARIA reasoning failed: ${err.message}` };
  }

  const durationMs = Date.now() - start;
  return parseThinkResponse(text, question, durationMs);
}
