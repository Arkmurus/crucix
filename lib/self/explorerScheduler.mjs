/**
 * CRUCIX — Explorer Auto-Scheduler
 * GAP 8 FIX: Automatically runs web explorer on ARIA's top curiosity threads.
 * Results feed back to ARIA memory and mark threads as resolved.
 *
 * Wire into server.mjs after brain is online:
 *   import { startExplorerScheduler } from './lib/self/explorerScheduler.mjs';
 *   startExplorerScheduler(redis, telegramNotify);
 */

const BRAIN_URL          = process.env.BRAIN_SERVICE_URL || 'http://localhost:5001';
const RUN_INTERVAL_MS    = 3 * 60 * 60 * 1000;   // every 3 hours
const MAX_THREADS_PER_RUN = 3;                     // top 3 unresolved curiosity threads
const EXPLORER_ENDPOINT  = '/api/explorer/run';    // your existing explorer route

// ── Scheduler ─────────────────────────────────────────────────────────────────

export function startExplorerScheduler(app, redis, notifyFn = null) {
  console.log('[ExplorerScheduler] Starting — runs every 3h');

  const run = async () => {
    try {
      await runCuriosityExploration(redis, notifyFn);
    } catch (e) {
      console.error('[ExplorerScheduler] Run failed:', e.message);
    }
  };

  // First run after 5-minute warm-up (let the sweep complete first)
  setTimeout(run, 5 * 60 * 1000);
  setInterval(run, RUN_INTERVAL_MS);
}

// ── Main Exploration Run ──────────────────────────────────────────────────────

async function runCuriosityExploration(redis, notifyFn) {
  // ── 1. Fetch ARIA's open curiosity threads ────────────────────────────────
  let threads = [];
  try {
    const resp = await fetch(`${BRAIN_URL}/api/aria/curiosity`, { signal: AbortSignal.timeout(10000) });
    if (!resp.ok) throw new Error(`Brain returned ${resp.status}`);
    const data = await resp.json();
    threads    = (data.open_threads || []).filter(t => !t.resolved);
  } catch (e) {
    console.warn('[ExplorerScheduler] Could not fetch curiosity threads:', e.message);
    return;
  }

  if (threads.length === 0) {
    console.log('[ExplorerScheduler] No open curiosity threads — skipping run');
    return;
  }

  const topThreads = threads.slice(0, MAX_THREADS_PER_RUN);
  console.log(`[ExplorerScheduler] Investigating ${topThreads.length} curiosity threads`);

  const results = [];

  for (const thread of topThreads) {
    const question = thread.question;
    console.log(`[ExplorerScheduler] Exploring: "${question.slice(0, 80)}"`);

    try {
      // ── 2. Generate search queries for this curiosity thread ───────────────
      const queries = await generateQueriesForThread(thread);

      // ── 3. Run web explorer for each query ────────────────────────────────
      const findings = await runExplorerQueries(queries, question);

      if (findings.length === 0) {
        results.push({ question, status: 'no_findings', queries });
        continue;
      }

      // ── 4. Synthesise findings into an answer ──────────────────────────────
      const synthesised = await synthesiseFindings(question, findings);

      // ── 5. Resolve the curiosity thread in ARIA ────────────────────────────
      await resolveThread(thread.question, synthesised);

      // ── 6. Store findings to ARIA's intelligence memory ────────────────────
      await storeToARIAMemory(question, synthesised, findings);

      results.push({
        question,
        status:   'resolved',
        queries,
        findings: findings.length,
        answer:   synthesised.slice(0, 200),
      });

      console.log(`[ExplorerScheduler] Resolved: "${question.slice(0, 60)}"`);

    } catch (e) {
      console.error(`[ExplorerScheduler] Thread exploration failed: ${e.message}`);
      results.push({ question, status: 'error', error: e.message });
    }

    // Avoid hammering APIs
    await sleep(2000);
  }

  // ── 7. Notify summary ──────────────────────────────────────────────────────
  const resolved = results.filter(r => r.status === 'resolved');
  if (resolved.length > 0 && notifyFn) {
    const lines = resolved.map(r => `• _"${r.question.slice(0, 100)}"_\n  → ${r.answer}`).join('\n\n');
    await notifyFn(
      `🧠 *ARIA CURIOSITY RESOLVED*\n\n` +
      `ARIA investigated ${topThreads.length} open intelligence questions.\n` +
      `${resolved.length} resolved:\n\n${lines}`
    ).catch((e) => { console.warn('[ExplorerScheduler]', e.message); });
  }

  return results;
}

// ── Query Generation ──────────────────────────────────────────────────────────

async function generateQueriesForThread(thread) {
  // Ask ARIA's brain to generate targeted search queries for this question
  try {
    const resp = await fetch(`${BRAIN_URL}/api/aria/think`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        question: `Generate 4 targeted web search queries to answer this intelligence question: "${thread.question}". Focus on procurement, defence, and geopolitical sources. Return as a JSON array of query strings.`,
        fast:     true,
        context:  { thread_raised_at: thread.raised_at, purpose: 'generate_search_queries' },
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (resp.ok) {
      const thought = await resp.json();
      // Try to extract queries from the conclusion
      const conclusion = thought?.conclusion;
      if (Array.isArray(conclusion))           return conclusion.slice(0, 4);
      if (conclusion?.queries)                 return conclusion.queries.slice(0, 4);
      if (typeof conclusion?.statement === 'string') {
        // Fallback: extract quoted strings as queries
        const matches = conclusion.statement.match(/"([^"]+)"/g);
        if (matches) return matches.slice(0, 4).map(m => m.replace(/"/g, ''));
      }
    }
  } catch (e) {
    console.warn('[ExplorerScheduler] Query generation via brain failed:', e.message);
  }

  // Fallback: build queries directly from the question
  return buildFallbackQueries(thread.question);
}

function buildFallbackQueries(question) {
  // Extract key terms and build queries
  const cleanQ  = question.replace(/[?!,]/g, ' ').replace(/\s+/g, ' ').trim();
  const words   = cleanQ.split(' ').filter(w => w.length > 4);
  const keyWords = words.slice(0, 6).join(' ');

  return [
    `${keyWords} defence procurement ${new Date().getFullYear()} ${new Date().getFullYear() + 1}`,
    `${keyWords} Lusophone Africa`,
    `${keyWords} ministry of defence`,
    cleanQ.slice(0, 100),
  ];
}

// ── Explorer Query Runner ─────────────────────────────────────────────────────

async function runExplorerQueries(queries, threadContext) {
  const findings = [];

  for (const query of queries.slice(0, 4)) {
    try {
      // Call your existing web explorer endpoint
      const resp = await fetch(`http://localhost:${process.env.PORT || 3000}${EXPLORER_ENDPOINT}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'x-internal': 'explorer-scheduler' },
        body:    JSON.stringify({
          query,
          context:    threadContext,
          maxResults: 5,
          sources:    ['web', 'news', 'academic'],
        }),
        signal: AbortSignal.timeout(45000),
      });

      if (!resp.ok) {
        console.warn(`[ExplorerScheduler] Explorer returned ${resp.status} for query: "${query.slice(0, 50)}"`);
        continue;
      }

      const data = await resp.json();
      const results = data.findings || data.results || [];
      findings.push(...results.slice(0, 3).map(r => ({
        query,
        title:   r.title   || r.name   || '',
        content: r.content || r.snippet || r.summary || '',
        url:     r.url     || r.link    || '',
        source:  r.source  || 'web',
      })));

    } catch (e) {
      console.warn(`[ExplorerScheduler] Explorer query failed: "${query.slice(0, 50)}" — ${e.message}`);
    }

    await sleep(1000);
  }

  return findings;
}

// ── Finding Synthesis ─────────────────────────────────────────────────────────

async function synthesiseFindings(question, findings) {
  if (findings.length === 0) return 'No findings from web exploration.';

  const findingsSummary = findings
    .slice(0, 8)
    .map(f => `Source: ${f.source}\nTitle: ${f.title}\nContent: ${f.content?.slice(0, 400)}`)
    .join('\n---\n');

  try {
    const resp = await fetch(`${BRAIN_URL}/api/aria/think`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        question: `Based on these web exploration findings, answer this intelligence question: "${question}"\n\nFINDINGS:\n${findingsSummary}`,
        fast:     true,
        context:  { purpose: 'synthesise_findings', finding_count: findings.length },
      }),
      signal: AbortSignal.timeout(60000),
    });

    if (resp.ok) {
      const thought  = await resp.json();
      const c        = thought?.conclusion || {};
      return typeof c === 'string' ? c : (c.statement || c.key_finding || JSON.stringify(c)).slice(0, 1000);
    }
  } catch (e) {
    console.warn('[ExplorerScheduler] Synthesis via ARIA failed:', e.message);
  }

  // Fallback: simple concatenation
  return findings.slice(0, 3).map(f => f.content?.slice(0, 200)).filter(Boolean).join('\n\n');
}

// ── ARIA Integration ──────────────────────────────────────────────────────────

async function resolveThread(question, answer) {
  try {
    await fetch(`${BRAIN_URL}/api/aria/curiosity/resolve`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ question, answer: answer.slice(0, 800) }),
      signal:  AbortSignal.timeout(10000),
    });
  } catch (e) {
    console.warn('[ExplorerScheduler] Thread resolution failed:', e.message);
  }
}

async function storeToARIAMemory(question, answer, findings) {
  try {
    await fetch(`${BRAIN_URL}/api/brain/signal`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        title:   `Explorer finding: ${question.slice(0, 80)}`,
        content: answer,
        source:  'web_explorer_curiosity',
        market:  extractMarketFromText(question + ' ' + answer),
        keywords: ['explorer', 'curiosity', 'auto-research'],
        finding_urls: findings.slice(0, 3).map(f => f.url).filter(Boolean),
      }),
      signal:  AbortSignal.timeout(10000),
    });
  } catch (e) {
    console.warn('[ExplorerScheduler] Memory storage failed:', e.message);
  }
}

// ── ACLED-Triggered Reactive Exploration ─────────────────────────────────────
// Called from your ACLED source when fatalities exceed threshold

export async function triggerReactiveExploration(acledEvent, redis, notifyFn) {
  const { country, event_type, fatalities, date } = acledEvent;
  if (!country || fatalities < 10) return;   // threshold: 10+ fatalities

  console.log(`[ExplorerScheduler] ACLED trigger: ${country} (${fatalities} fatalities) — reactive sweep`);

  const urgentQueries = [
    `${country} defence procurement ${new Date().getFullYear()} ${new Date().getFullYear() + 1}`,
    `${country} military spending budget`,
    `${country} ministry of defence tender`,
    `${country} ${event_type} security response procurement`,
  ];

  try {
    // Push reactive queries directly to ARIA brain queue
    await fetch(`${BRAIN_URL}/api/aria/think`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        question: `ACLED alert: ${fatalities} fatalities recorded in ${country} (${event_type}, ${date}). What are the immediate defence procurement implications for Arkmurus? What capability gaps does this create?`,
        fast:     false,
        context:  { trigger: 'acled', country, event_type, fatalities, date },
      }),
      signal:  AbortSignal.timeout(90000),
    });

    if (notifyFn) {
      await notifyFn(
        `🔴 *ACLED TRIGGER — REACTIVE INTEL SWEEP*\n\n` +
        `*Country:* ${country}\n` +
        `*Event:* ${event_type}\n` +
        `*Fatalities:* ${fatalities}\n` +
        `*Date:* ${date}\n\n` +
        `_ARIA is analysing procurement implications. Check /brief for updated intelligence._`
      ).catch((e) => { console.warn('[ExplorerScheduler]', e.message); });
    }
  } catch (e) {
    console.error('[ExplorerScheduler] Reactive exploration failed:', e.message);
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

const MARKET_KEYWORDS = {
  'Angola':        ['angola', 'luanda', 'faa', 'angolaense'],
  'Mozambique':    ['mozambique', 'maputo', 'fadm', 'cabo delgado'],
  'Guinea-Bissau': ['guinea-bissau', 'guinea bissau', 'bissau', 'fasb'],
  'Nigeria':       ['nigeria', 'abuja', 'lagos', 'nigerian'],
  'Kenya':         ['kenya', 'nairobi', 'kenyan'],
};

function extractMarketFromText(text) {
  const lower = text.toLowerCase();
  for (const [market, keywords] of Object.entries(MARKET_KEYWORDS)) {
    if (keywords.some(k => lower.includes(k))) return market;
  }
  return 'global';
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
