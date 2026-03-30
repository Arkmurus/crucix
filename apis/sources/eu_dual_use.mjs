// apis/sources/eu_dual_use.mjs
// EU Dual-Use Export Controls & CAATSA Designation Feeds
// Sources: EUR-Lex (EU Official Journal), Federal Register (US CAATSA/sanctions)
// Free RSS — no API keys required

// Use rss2json proxy to bypass Render IP blocks on EU/US government sites
const RSS2JSON = 'https://api.rss2json.com/v1/api.json?rss_url=';

const SOURCES = [
  // EU Official Journal — dual-use and sanctions regulations
  {
    url:    'https://eur-lex.europa.eu/EN/display-all-rss.html',
    label:  'EUR-Lex OJ',
    region: 'EU',
    weight: 'high',
  },
  // Federal Register — US CAATSA, EAR, ITAR rule changes
  {
    url:    'https://www.federalregister.gov/api/v1/articles.rss?conditions[agencies][]=bureau-of-industry-and-security&conditions[type][]=RULE',
    label:  'Federal Register (BIS)',
    region: 'US',
    weight: 'high',
  },
  {
    url:    'https://www.federalregister.gov/api/v1/articles.rss?conditions[agencies][]=office-of-foreign-assets-control&conditions[type][]=RULE,NOTICE',
    label:  'Federal Register (OFAC)',
    region: 'US',
    weight: 'critical',
  },
  // ECJU (UK Export Control) via GOV.UK
  {
    url:    'https://www.gov.uk/search/policy-papers-and-consultations.atom?keywords=export+control&organisations%5B%5D=export-control-joint-unit',
    label:  'ECJU (UK)',
    region: 'UK',
    weight: 'high',
  },
  // NATO Supply & Procurement
  {
    url:    'https://www.nato.int/cps/en/natolive/news.htm?RSS=y',
    label:  'NATO',
    region: 'NATO',
    weight: 'medium',
  },
];

// Keywords that flag dual-use / CAATSA relevance
const DUAL_USE_KEYWORDS = [
  'dual-use', 'export control', 'export licence', 'export license',
  'entity list', 'denied party', 'debarment', 'caatsa', 'itar', 'ear',
  'sanctions', 'designation', 'end-user', 'end user',
  'weapons proliferation', 'wmd', 'missile', 'nuclear', 'chemical',
  'arms embargo', 'military end use', 'catch-all',
];

// ── Fetch with rss2json proxy fallback ────────────────────────────────────────
async function fetchFeed(src) {
  // Try direct first
  try {
    const res = await fetch(src.url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
      signal: AbortSignal.timeout(8000),
    });
    if (res.ok) {
      const items = parseRSS(await res.text());
      if (items.length > 0) return items;
    }
  } catch {}

  // Proxy fallback
  try {
    const res = await fetch(RSS2JSON + encodeURIComponent(src.url), {
      signal: AbortSignal.timeout(10000),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'ok' && data.items?.length > 0) {
        return data.items.slice(0, 10).map(i => ({
          title:   i.title || '',
          url:     i.link  || '',
          summary: (i.description || i.content || '').replace(/<[^>]+>/g, '').substring(0, 250),
          pubDate: i.pubDate || new Date().toISOString(),
        }));
      }
    }
  } catch {}

  return [];
}

// ── Main briefing ─────────────────────────────────────────────────────────────
export async function briefing() {
  console.log('[EU Dual-Use] Fetching export control and CAATSA feeds...');
  const results = { updates: [], signals: [], alerts: [], error: null };

  const feedResults = await Promise.allSettled(
    SOURCES.map(src => fetchFeed(src).then(items => ({ src, items })))
  );

  for (const res of feedResults) {
    if (res.status !== 'fulfilled') continue;
    const { src, items } = res.value;

    for (const item of items) {
      const text = (item.title + ' ' + item.summary).toLowerCase();
      const isRelevant = DUAL_USE_KEYWORDS.some(kw => text.includes(kw));
      if (!isRelevant) continue;

      const isCritical = ['sanctions', 'designation', 'caatsa', 'entity list', 'arms embargo']
        .some(kw => text.includes(kw));
      const priority = isCritical || src.weight === 'critical' ? 'critical' :
                       src.weight === 'high' ? 'high' : 'medium';

      results.updates.push({
        title:   item.title,
        source:  src.label,
        region:  src.region,
        url:     item.url,
        summary: item.summary,
        pubDate: item.pubDate,
        type:    'dual_use_regulation',
        priority,
      });

      if (priority === 'critical' || priority === 'high') {
        results.signals.push({
          text:     `[${src.label}] ${item.title}`,
          source:   src.label,
          priority,
          type:     'export_control_signal',
        });
      }

      if (priority === 'critical') {
        results.alerts.push({
          text:     `EXPORT CONTROL ALERT [${src.region}]: ${item.title}`,
          source:   src.label,
          priority: 'critical',
        });
      }
    }
  }

  console.log(`[EU Dual-Use] ${results.updates.length} updates · ${results.alerts.length} critical alerts`);
  return results;
}

// ── RSS parser ─────────────────────────────────────────────────────────────────
function parseRSS(xml) {
  const items = [];
  const re    = /<(?:item|entry)[^>]*>([\s\S]*?)<\/(?:item|entry)>/gi;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const b     = m[1];
    const title = extractTag(b, 'title');
    if (!title) continue;
    items.push({
      title,
      url:     extractTag(b, 'link') || extractTag(b, 'guid'),
      summary: extractTag(b, 'summary') || extractTag(b, 'description'),
      pubDate: extractTag(b, 'published') || extractTag(b, 'pubDate') || extractTag(b, 'updated'),
    });
  }
  return items.slice(0, 10);
}

function extractTag(xml, tag) {
  const m = xml.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
          || xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'))
          || xml.match(new RegExp(`<${tag}[^>]*/?>([^<]*)`, 'i'));
  return m ? m[1].replace(/<[^>]+>/g,'').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').trim() : '';
}

// CLI test
if (process.argv[1]?.endsWith('eu_dual_use.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
