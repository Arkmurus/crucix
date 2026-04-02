// lib/self/web_explorer.mjs
// Autonomous internet sweep — discovers new data sources, reads trends, generates ideas
// Uses existing search infrastructure (lib/search/engine.mjs) + LLM synthesis
// Runs on-demand (/explore) and weekly (Sunday 04:00 UTC via cron)

import { saveExplorerFindings, logSelfUpdate } from './learning_store.mjs';

// Search queries covering Arkmurus's core intelligence domains
const EXPLORATION_QUERIES = [
  // Lusophone Africa defense procurement
  'Angola defense procurement contract 2026',
  'Mozambique military equipment tender',
  'Guinea-Bissau armed forces funding ECOWAS',
  // Global defense market trends
  'Africa defense spending increase 2026 budget',
  'West Africa military modernisation programme',
  'CPLP defense cooperation agreement',
  // Arms trade intelligence
  'European artillery ammunition exports Africa',
  'NATO ally arms export license Africa 2026',
  'defense industrial base Lusophone cooperation',
  // Open government / new data sources
  'African Union peace security budget API',
  'Angola government procurement portal defense',
  'SIPRI arms transfer database update 2026',
  // Geopolitical triggers
  'Mozambique Cabo Delgado insurgency 2026 update',
  'Angola SADC peacekeeping deployment 2026',
];

// Known data sources to periodically re-verify are alive
const SOURCE_VERIFICATION_URLS = [
  'https://api.reliefweb.int/v1/reports?appname=crucix&limit=1',
  'https://acleddata.com/api/acled/read/?key=test&limit=1',
  'https://api.gdeltproject.org/api/v2/summary/summary?d=web&t=summary',
];

async function safeFetch(url, opts = {}) {
  try {
    const res = await fetch(url, {
      signal: AbortSignal.timeout(opts.timeout || 12000),
      headers: { 'User-Agent': 'Crucix-Explorer/2.0', ...opts.headers },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return opts.json ? await res.json() : await res.text();
  } catch {
    return null;
  }
}

async function searchBrave(query, apiKey) {
  if (!apiKey) return [];
  const url = `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=5&safesearch=off`;
  const data = await safeFetch(url, {
    json: true,
    headers: { 'Accept': 'application/json', 'X-Subscription-Token': apiKey },
  });
  return (data?.web?.results || []).map(r => ({
    title: r.title,
    url: r.url,
    snippet: r.description || '',
  }));
}

async function searchSearXNG(query) {
  const instances = [
    'https://searx.be',
    'https://searxng.world',
    'https://paulgo.io',
  ];
  for (const base of instances) {
    const url = `${base}/search?q=${encodeURIComponent(query)}&format=json&categories=general,news`;
    const data = await safeFetch(url, { json: true, timeout: 8000 });
    if (data?.results?.length > 0) {
      return data.results.slice(0, 5).map(r => ({
        title: r.title,
        url: r.url,
        snippet: r.content || '',
      }));
    }
  }
  return [];
}

async function fetchGoogleNewsRSS(query) {
  const url = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en-US&gl=US&ceid=US:en`;
  const xml = await safeFetch(url, { timeout: 8000 });
  if (!xml) return [];

  const items = [];
  const itemRx = /<item>([\s\S]*?)<\/item>/gi;
  let m;
  while ((m = itemRx.exec(xml)) !== null) {
    const block = m[1];
    const get = tag => {
      const tm = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
               || block.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
      return tm ? tm[1].trim().replace(/<[^>]+>/g, '') : '';
    };
    const title = get('title');
    if (!title || title.length < 10) continue;
    items.push({ title, url: get('link') || get('guid'), snippet: get('description').substring(0, 200) });
  }
  return items.slice(0, 5);
}

async function runQueries(queries, braveKey) {
  const allResults = [];

  // Run searches in parallel batches of 3 to avoid hammering APIs
  for (let i = 0; i < queries.length; i += 3) {
    const batch = queries.slice(i, i + 3);
    const batchResults = await Promise.allSettled(
      batch.map(async q => {
        let results = [];
        if (braveKey) {
          results = await searchBrave(q, braveKey);
        }
        if (results.length === 0) {
          results = await searchSearXNG(q);
        }
        if (results.length === 0) {
          results = await fetchGoogleNewsRSS(q);
        }
        return { query: q, results: results.slice(0, 4) };
      })
    );
    for (const r of batchResults) {
      if (r.status === 'fulfilled') allResults.push(r.value);
    }
  }

  return allResults;
}

// Deduplicate results by URL across queries
function deduplicateResults(queryResults) {
  const seen = new Set();
  const deduped = [];
  for (const { query, results } of queryResults) {
    for (const r of results) {
      if (!seen.has(r.url) && r.title && r.url) {
        seen.add(r.url);
        deduped.push({ ...r, query });
      }
    }
  }
  return deduped;
}

// LLM synthesis: turn raw search results into actionable intelligence + source suggestions
async function synthesizeFindings(llmProvider, rawResults, existingSources) {
  if (!llmProvider?.isConfigured || rawResults.length === 0) return null;

  const context = rawResults.slice(0, 30).map(r =>
    `Q: ${r.query}\nTitle: ${r.title}\nSnippet: ${r.snippet?.substring(0, 150)}\nURL: ${r.url}`
  ).join('\n\n---\n');

  const systemPrompt = `You are the intelligence analyst for Arkmurus, a defense brokering firm focused on Lusophone Africa (Angola, Mozambique, Guinea-Bissau) and West Africa.

You have just received web search results from an autonomous intelligence sweep. Your job is to:
1. Extract 3-6 actionable intelligence INSIGHTS from these results
2. Identify 2-4 NEW DATA SOURCES worth adding (APIs, databases, government portals, RSS feeds)
3. Generate 2-3 SALES IDEAS for Arkmurus based on what you read

For insights: focus on procurement signals, conflict developments, budget announcements, policy changes.
For new sources: must have a real URL, machine-readable format preferred (API/JSON/RSS).
For sales ideas: specific, actionable, cite the source that triggered the idea.

Output JSON:
{
  "insights": [
    { "title": "...", "summary": "...", "relevance": "HIGH|MEDIUM|LOW", "region": "...", "sourceUrl": "..." }
  ],
  "newSources": [
    { "name": "...", "url": "...", "type": "api|rss|scrape", "why": "...", "moduleName": "snake_case_name" }
  ],
  "salesIdeas": [
    { "title": "...", "market": "...", "rationale": "...", "urgency": "HIGH|MEDIUM|LOW", "nextStep": "..." }
  ]
}

Output ONLY valid JSON. No markdown.`;

  try {
    const result = await llmProvider.complete(systemPrompt, context, { maxTokens: 3000, timeout: 90000 });
    let text = (result.text || '').trim();
    if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
    return JSON.parse(text);
  } catch (err) {
    console.error('[WebExplorer] LLM synthesis failed:', err.message);
    return null;
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function runExploration(llmProvider = null, opts = {}) {
  const braveKey = process.env.BRAVE_API_KEY;
  const queries = opts.queries || EXPLORATION_QUERIES;

  console.log(`[WebExplorer] Starting exploration — ${queries.length} queries, Brave: ${braveKey ? 'yes' : 'no'}`);

  const queryResults = await runQueries(queries, braveKey);
  const rawResults   = deduplicateResults(queryResults);

  console.log(`[WebExplorer] Found ${rawResults.length} unique results from ${queryResults.length} queries`);

  let synthesis = null;
  if (llmProvider?.isConfigured) {
    synthesis = await synthesizeFindings(llmProvider, rawResults, []);
  }

  const findings = {
    runAt: new Date().toISOString(),
    queriesRun: queries.length,
    resultsFound: rawResults.length,
    rawResults: rawResults.slice(0, 50),
    insights: synthesis?.insights || [],
    newSources: synthesis?.newSources || [],
    salesIdeas: synthesis?.salesIdeas || [],
  };

  saveExplorerFindings(findings);
  logSelfUpdate('web_exploration', {
    queries: queries.length,
    results: rawResults.length,
    insights: findings.insights.length,
    newSources: findings.newSources.length,
    salesIdeas: findings.salesIdeas.length,
  });

  console.log(`[WebExplorer] Complete — ${findings.insights.length} insights, ${findings.newSources.length} new sources, ${findings.salesIdeas.length} sales ideas`);
  return findings;
}

// Quick targeted exploration for a specific topic (used by /explore <topic> command)
export async function exploreQuery(llmProvider, query) {
  const braveKey = process.env.BRAVE_API_KEY;

  let results = [];
  if (braveKey) results = await searchBrave(query, braveKey);
  if (results.length === 0) results = await searchSearXNG(query);
  if (results.length === 0) results = await fetchGoogleNewsRSS(query);

  if (results.length === 0) return { error: 'No results found', query };

  if (!llmProvider?.isConfigured) {
    return {
      query,
      results: results.slice(0, 5),
      summary: 'LLM not configured — raw results only',
    };
  }

  const context = results.map(r => `Title: ${r.title}\nSnippet: ${r.snippet?.substring(0, 200)}\nURL: ${r.url}`).join('\n\n---\n');
  const systemPrompt = `You are an analyst for Arkmurus (defense brokering, Lusophone Africa focus).
Analyze these search results for the query: "${query}"
Provide: 2-3 sentence intelligence summary, key facts, and 1 actionable recommendation for Arkmurus.
Be specific and cite sources. Keep response under 400 words.`;

  try {
    const result = await llmProvider.complete(systemPrompt, context, { maxTokens: 800, timeout: 30000 });
    return { query, results: results.slice(0, 5), analysis: result.text };
  } catch {
    return { query, results: results.slice(0, 5), analysis: 'LLM analysis failed — raw results above' };
  }
}

export function formatExplorerFindingsForTelegram(findings) {
  if (!findings || (!findings.insights?.length && !findings.salesIdeas?.length)) {
    return '🌐 *INTELLIGENCE EXPLORATION*\n\nNo significant findings in latest exploration.\nUse /explore <topic> for a targeted search.';
  }

  const ts = findings.runAt ? new Date(findings.runAt).toISOString().slice(0, 16).replace('T', ' ') : 'unknown';
  let msg = `🌐 *INTELLIGENCE EXPLORATION*\n_${ts} UTC · ${findings.queriesRun || 0} queries · ${findings.resultsFound || 0} results_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  if (findings.insights?.length > 0) {
    msg += `*📡 KEY INSIGHTS (${findings.insights.length})*\n`;
    for (const insight of findings.insights.slice(0, 4)) {
      const badge = insight.relevance === 'HIGH' ? '🔴' : insight.relevance === 'MEDIUM' ? '🟠' : '🟡';
      msg += `${badge} *${insight.title?.substring(0, 70)}*\n`;
      if (insight.summary) msg += `${insight.summary.substring(0, 180)}\n`;
      msg += '\n';
    }
  }

  if (findings.salesIdeas?.length > 0) {
    msg += `*💼 SALES IDEAS (${findings.salesIdeas.length})*\n`;
    for (const idea of findings.salesIdeas.slice(0, 3)) {
      const urgBadge = idea.urgency === 'HIGH' ? '🔴' : idea.urgency === 'MEDIUM' ? '🟠' : '🟡';
      msg += `${urgBadge} *${idea.title?.substring(0, 70)}*\n`;
      if (idea.market) msg += `  Market: ${idea.market}\n`;
      if (idea.nextStep) msg += `  Next: _${idea.nextStep.substring(0, 100)}_\n`;
      msg += '\n';
    }
  }

  if (findings.newSources?.length > 0) {
    msg += `*🔗 NEW SOURCES TO ADD (${findings.newSources.length})*\n`;
    for (const src of findings.newSources.slice(0, 3)) {
      msg += `▸ *${src.name}* (${src.type}) — ${src.why?.substring(0, 80)}\n`;
      msg += `  /update add ${src.moduleName} to deploy\n`;
    }
    msg += '\n';
  }

  msg += `_/explore <topic> for targeted search · Auto-runs Sundays 04:00 UTC_`;
  return msg;
}
