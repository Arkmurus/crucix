// lib/self/web_explorer.mjs
// Autonomous internet sweep — discovers new data sources, reads trends, generates ideas
// Uses existing search infrastructure (lib/search/engine.mjs) + LLM synthesis
// Runs on-demand (/explore) and daily (06:00 UTC + 14:00 UTC via cron)

import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { saveExplorerFindings, logSelfUpdate } from './learning_store.mjs';

// ICU-free Europe/London timestamp (BST/GMT auto-switching)
function londonTs(date = new Date(), seconds = false) {
  function ukOffset(d) {
    const y = d.getUTCFullYear();
    const lsm = new Date(Date.UTC(y, 2, 31, 1, 0, 0)); while (lsm.getUTCDay() !== 0) lsm.setUTCDate(lsm.getUTCDate() - 1);
    const lso = new Date(Date.UTC(y, 9, 31, 1, 0, 0)); while (lso.getUTCDay() !== 0) lso.setUTCDate(lso.getUTCDate() - 1);
    return (d >= lsm && d < lso) ? 1 : 0;
  }
  const p = n => String(n).padStart(2, '0');
  const local = new Date(date.getTime() + ukOffset(date) * 3600000);
  const base = `${local.getUTCFullYear()}-${p(local.getUTCMonth()+1)}-${p(local.getUTCDate())} ${p(local.getUTCHours())}:${p(local.getUTCMinutes())}`;
  return seconds ? `${base}:${p(local.getUTCSeconds())}` : base;
}

// Search queries — global market scope, export-control aware, no sanctioned countries
const EXPLORATION_QUERIES = [
  // Lusophone Africa — core markets
  'Angola Ministry of Defense tender procurement 2026',
  'Mozambique armed forces equipment tender RFP 2026',
  'Guinea-Bissau military procurement ECOWAS grant 2026',
  'Angola FAA Forças Armadas Angolanas contract award',
  'Mozambique FADM equipment contract signed 2026',
  // West & Central Africa
  'Nigeria Army modernisation procurement contract 2026',
  'Ghana Armed Forces equipment tender 2026',
  'Kenya Defence Forces procurement contract 2026',
  'Senegal military equipment purchase 2026',
  'Côte d\'Ivoire defense procurement modernisation 2026',
  // East Africa
  'Ethiopia military procurement contract 2026',
  'Rwanda Defence Force equipment tender 2026',
  'Uganda People\'s Defence Force procurement 2026',
  'Tanzania People\'s Defence Force contract 2026',
  // Southeast Asia (non-sanctioned)
  'Philippines military procurement contract 2026',
  'Vietnam defense modernisation equipment tender 2026',
  'Indonesia military procurement contract award 2026',
  'Bangladesh Army procurement tender 2026',
  // Latin America
  'Brazil Army military procurement tender 2026',
  'Colombia defense procurement contract 2026',
  'Peru military equipment purchase contract 2026',
  // Middle East (non-sanctioned, export-control permissible)
  'Saudi Arabia defense procurement contract 2026',
  'UAE military equipment tender contract 2026',
  'Jordan Armed Forces procurement 2026',
  // European markets
  'Poland military procurement contract award 2026',
  'Romania defense modernisation NATO contract 2026',
  'Greece Hellenic Army procurement tender 2026',
  'Bulgaria defense equipment purchase contract 2026',
  // OEM & brokering intelligence
  'defense broker intermediary arms export license approved 2026',
  'European arms manufacturer Africa partnership 2026',
  'SIPRI arms transfers global 2025 2026 new data',
  // Finance triggers
  'AfDB World Bank security sector defense equipment loan 2026',
  'EU peace facility African Union equipment funding 2026',
  // Geopolitical drivers — conflict creates procurement
  'military escalation conflict procurement opportunity 2026',
  'border dispute military buildup equipment 2026',
  'counter-terrorism military operation equipment deployed 2026',
  'peacekeeping force deployment equipment requirement ATMIS AMISOM 2026',
  'coup military government defense spending 2026',
  // Competitive intelligence — who is winning deals
  'defense contract awarded Africa Middle East 2026',
  'Turkey defense export Africa drone delivery 2026',
  'China military export Africa arms deal 2026',
  'Russia arms export Africa replacement alternative 2026',
  'Israel defense deal Africa surveillance border 2026',
  // Product-specific opportunities
  'armoured vehicle procurement Africa tender 2026',
  'UAV drone procurement military Africa 2026',
  'ammunition supply contract military Africa 2026',
  'patrol vessel coast guard procurement Africa 2026',
  'military helicopter procurement Africa 2026',
  'border security system procurement tender 2026',
  'air defense system procurement Africa Middle East 2026',
  // Defence budget & spending signals
  'defense budget increase Africa 2026 2027',
  'military spending growth emerging markets 2026',
  'FMS foreign military sales notification Africa 2026',
  'defense cooperation agreement signed Africa 2026',
  // Exhibitions & conferences (networking opportunities)
  'defense exhibition Africa 2026 exhibitor',
  'IDEX DSEI Africa Aerospace Defence AAD ShieldAfrica 2026',
];

// ── Brain-directed dynamic queries (Unit 2) ─────────────────────────────────
// Reads the brain's nextSweepFocus + knowledgeGaps from learning store,
// converts them to search queries, and appends to static queries.
export function buildDynamicQueries() {
  const queries = [...EXPLORATION_QUERIES];
  try {
    const learningPath = join(process.cwd(), 'runs', 'learning', 'bd_learning.json');
    if (!existsSync(learningPath)) return queries;
    const L = JSON.parse(readFileSync(learningPath, 'utf8'));
    const directives = L.explorerDirectives;
    if (!directives) return queries;

    const dynamic = [];
    // Convert nextSweepFocus to a search query
    if (directives.nextSweepFocus) {
      const focus = directives.nextSweepFocus.trim();
      if (focus.length > 10) {
        dynamic.push(focus + ' 2026 procurement');
        dynamic.push(focus + ' defence tender');
      }
    }
    // Convert knowledge gaps to queries
    for (const gap of (directives.knowledgeGaps || []).slice(0, 5)) {
      const q = (gap.question || gap.howToFind || '').trim();
      if (q.length > 10) dynamic.push(q.substring(0, 120));
    }

    if (dynamic.length > 0) {
      console.log(`[Explorer] Added ${dynamic.length} brain-directed queries`);
      queries.push(...dynamic.slice(0, 10));
    }
    return queries;
  } catch {
    return queries;
  }
}

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

  const context = rawResults.slice(0, 50).map(r =>
    `Q: ${r.query}\nTitle: ${r.title}\nSnippet: ${r.snippet?.substring(0, 250)}\nURL: ${r.url}`
  ).join('\n\n---\n');

  const systemPrompt = `You are the senior business development analyst for Arkmurus, a defense brokering firm with a global mandate. Core markets are Lusophone Africa (Angola, Mozambique, Guinea-Bissau, Cape Verde, Brazil) and West Africa, but the firm pursues opportunities worldwide in any non-embargoed, export-control permissible market including Southeast Asia, East Africa, Latin America, and the Middle East (Saudi Arabia, UAE, Jordan). Sanctioned states (Russia, Belarus, Iran, North Korea, Syria, Myanmar, Sudan) are strictly excluded.

You have just received live web search results from an autonomous intelligence sweep. Your mission is to identify REAL, ACTIONABLE business opportunities — contracts we can bid on, relationships to establish, and deals to broker.

Extract intelligence in three categories:

1. INSIGHTS (4-8): Hard procurement/contract signals — budget approvals, tender publications, contract awards, military exercise announcements, ministerial visits, bilateral defense agreements. For each: cite the specific source URL, name the decision-maker or institution if visible, estimate deal size if possible.

2. SALES IDEAS (3-5): Specific, named opportunities for Arkmurus to pursue THIS QUARTER. Each must name: the buyer (ministry/unit), the product category needed, the likely OEM supplier to partner with, the estimated contract value, and the exact first action Arkmurus should take (e.g. "Contact Col. X at Angola MoD via…", "Submit EOI to tender reference Y").

3. NEW SOURCES (2-4): Government portals, procurement databases, tender feeds, or APIs that publish defense procurement data for our target markets. Must have a real, working URL.

Output ONLY valid JSON (no markdown):
{
  "insights": [
    { "title": "...", "summary": "...", "relevance": "HIGH|MEDIUM|LOW", "region": "...", "sourceUrl": "...", "dealSize": "...", "timeline": "..." }
  ],
  "salesIdeas": [
    { "title": "...", "market": "...", "buyer": "...", "productCategory": "...", "suggestedOEM": "...", "estimatedValue": "...", "rationale": "...", "urgency": "HIGH|MEDIUM|LOW", "nextStep": "..." }
  ],
  "newSources": [
    { "name": "...", "url": "...", "type": "api|rss|scrape", "why": "...", "moduleName": "snake_case_name" }
  ]
}`;

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

// ── Rules-Based Fallback Synthesis (no LLM required) ─────────────────────────
// Scores raw search results against procurement/defense keywords and groups them
// into structured insights. Weaker than LLM but always works.
const HIGH_KW   = ['tender', 'contract award', 'procurement', 'rfp', 'rfq', 'bid', 'budget approved', 'signed', 'deal', 'billion', 'million'];
const MEDIUM_KW = ['military', 'defence', 'defense', 'armed forces', 'ministry of defense', 'upgrade', 'acquisition', 'modernisation', 'modernization'];
const REGION_KW = ['angola', 'mozambique', 'nigeria', 'kenya', 'ghana', 'senegal', 'philippines', 'indonesia', 'brazil', 'poland', 'ukraine', 'saudi', 'uae', 'jordan', 'ethiopia', 'cameroon', 'rwanda', 'colombia'];

function _rulesSynthesis(rawResults) {
  const insights   = [];
  const salesIdeas = [];
  const seen       = new Set();

  for (const r of rawResults) {
    const text  = ((r.title || '') + ' ' + (r.snippet || '')).toLowerCase();
    const title = r.title || '';
    if (!title || seen.has(title.substring(0, 60))) continue;

    const highHits   = HIGH_KW.filter(k => text.includes(k)).length;
    const mediumHits = MEDIUM_KW.filter(k => text.includes(k)).length;
    const region     = REGION_KW.find(k => text.includes(k)) || null;

    if (highHits >= 1 || (mediumHits >= 2 && region)) {
      const relevance = highHits >= 2 ? 'HIGH' : highHits === 1 ? 'MEDIUM' : 'LOW';
      seen.add(title.substring(0, 60));
      insights.push({
        title:      title.substring(0, 120),
        summary:    (r.snippet || title).substring(0, 300),
        relevance,
        region:     region ? region.charAt(0).toUpperCase() + region.slice(1) : 'Global',
        sourceUrl:  r.url || '',
        dealSize:   null,
        timeline:   null,
      });

      if (highHits >= 1 && region && insights.length <= 3) {
        salesIdeas.push({
          title:          `Pursue: ${title.substring(0, 80)}`,
          market:         region.charAt(0).toUpperCase() + region.slice(1),
          buyer:          'Ministry of Defense / Armed Forces',
          productCategory: HIGH_KW.find(k => text.includes(k)) || 'defense equipment',
          suggestedOEM:   'Match via /oem command',
          estimatedValue: 'TBC',
          rationale:      (r.snippet || '').substring(0, 200),
          urgency:        highHits >= 2 ? 'HIGH' : 'MEDIUM',
          nextStep:       `Review source: ${(r.url || '').substring(0, 80)}`,
        });
      }
    }
  }

  return {
    insights:   insights.slice(0, 8),
    salesIdeas: salesIdeas.slice(0, 5),
    newSources: [],
  };
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function runExploration(llmProvider = null, opts = {}) {
  const braveKey = process.env.BRAVE_API_KEY;
  // Build query set: static + brain-directed + evolved (genetic algorithm)
  let queries = opts.queries || buildDynamicQueries();
  try {
    const { getEvolvedQueries } = await import('../aria/query_evolution.mjs');
    const evolved = getEvolvedQueries();
    if (evolved.length > 0) {
      queries = [...queries, ...evolved];
      console.log(`[WebExplorer] +${evolved.length} genetically evolved queries added`);
    }
  } catch {}

  console.log(`[WebExplorer] Starting exploration — ${queries.length} queries, Brave: ${braveKey ? 'yes' : 'no'}`);

  const queryResults = await runQueries(queries, braveKey);
  const rawResults   = deduplicateResults(queryResults);

  console.log(`[WebExplorer] Found ${rawResults.length} unique results from ${queryResults.length} queries`);

  let synthesis = null;
  if (llmProvider?.isConfigured) {
    synthesis = await synthesizeFindings(llmProvider, rawResults, []);
  }

  // Fallback: rules-based synthesis when LLM not configured
  // Extracts procurement signals by keyword matching against raw results
  if (!synthesis && rawResults.length > 0) {
    synthesis = _rulesSynthesis(rawResults);
  }

  const findings = {
    runAt: new Date().toISOString(),
    queriesRun: queries.length,
    resultsFound: rawResults.length,
    rawResults: rawResults.slice(0, 50),
    insights: synthesis?.insights || [],
    newSources: synthesis?.newSources || [],
    salesIdeas: synthesis?.salesIdeas || [],
    synthesisMethod: llmProvider?.isConfigured ? 'llm' : 'rules',
  };

  saveExplorerFindings(findings);
  logSelfUpdate('web_exploration', {
    queries: queries.length,
    results: rawResults.length,
    insights: findings.insights.length,
    newSources: findings.newSources.length,
    salesIdeas: findings.salesIdeas.length,
  });

  // Auto-generate + stage source modules for newly discovered sources (if LLM available)
  if (llmProvider?.isConfigured && findings.newSources?.length > 0) {
    const { generateSourceModule, stageModule } = await import('./code_generator.mjs');
    for (const src of findings.newSources.slice(0, 3)) {
      if (!src.url || !src.name) continue;
      const moduleName = src.name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
      if (!moduleName || moduleName.length < 3) continue;
      try {
        console.log(`[WebExplorer] Auto-generating module for: ${src.name} (${src.url})`);
        const gen = await generateSourceModule(
          llmProvider,
          `${src.name} — ${src.description || 'defense/security procurement feed'} — Primary URL: ${src.url}`,
          moduleName
        );
        if (gen.success) {
          await stageModule(moduleName, gen.code, { type: 'new', description: `Auto-discovered by web explorer: ${src.name}`, confidence: 0.65 });
          logSelfUpdate('auto_stage_from_explorer', { moduleName, sourceName: src.name, url: src.url });
          console.log(`[WebExplorer] Auto-staged module: ${moduleName}`);
        }
      } catch (err) {
        console.warn(`[WebExplorer] Auto-generate ${src.name} failed (non-fatal):`, err.message);
      }
    }
  }

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

  const ts = findings.runAt ? londonTs(new Date(findings.runAt), false) : 'unknown';
  let msg = `🌐 *INTELLIGENCE EXPLORATION*\n_${ts} London · ${findings.queriesRun || 0} queries · ${findings.resultsFound || 0} results_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

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
