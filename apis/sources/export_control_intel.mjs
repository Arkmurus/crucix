// apis/sources/export_control_intel.mjs
// Export Control & Compliance Intelligence — Arkmurus SITCL/ECJU compliance edge
// Covers: BIS Entity List, CAATSA, EU dual-use, ITAR/EAR changes,
//         denied party updates, end-user certificate alerts
// Free — no API keys required
// Critical for Arkmurus brokering obligations under Export Control Order 2008

const SOURCES = [
  // BIS (Bureau of Industry and Security) — Entity List & news
  {
    name:   'Federal Register Export',
    url:    'https://www.federalregister.gov/api/v1/articles.rss?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau',
    type:   'rss',
    weight: 'high',
  },
  // US Federal Register — export control rules
  {
    name:   'Federal Register Export',
    url:    'https://www.federalregister.gov/api/v1/articles.rss?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&conditions%5Bterm%5D=export+control',
    type:   'rss',
    weight: 'high',
  },
  // UK GOV export controls news
  {
    name:   'UK Export Controls GOV',
    url:    'https://www.gov.uk/government/organisations/export-control-joint-unit.atom',
    type:   'atom',
    weight: 'critical',
  },
  // EU sanctions / dual-use updates
  {
    name:   'EU Sanctions Updates',
    url:    'https://eur-lex.europa.eu/search.html?type=advanced&qid=1&RSS=true&lang=en&andText0=dual+use+export+control',
    type:   'rss',
    weight: 'high',
  },
  // OFAC recent actions
  {
    name:   'Treasury Press Releases',
    url:    'https://home.treasury.gov/news/press-releases.xml',
    type:   'rss',
    weight: 'critical',
  },
  // Sanctions news from GOV.UK
  {
    name:   'UK FCDO Sanctions',
    url:    'https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom',
    type:   'atom',
    weight: 'critical',
  },
];

// Compliance-critical keywords for Arkmurus
const COMPLIANCE_KEYWORDS = [
  // Licensing
  'export licence', 'export license', 'sitcl', 'siel', 'oiel', 'ogel',
  'itar', 'ear ', 'ccl ', 'eccn', 'dual-use', 'dual use', 'end-user',
  'end user certificate', 'euc', 'denied party', 'debarred',
  // Enforcement
  'violation', 'penalty', 'fine', 'prosecution', 'enforcement', 'seized',
  'contraband', 'smuggling', 'diversion', 'unauthorized transfer',
  // Entity list / sanctions
  'entity list', 'designated', 'sanctioned', 'designation', 'listed',
  'blocked person', 'sdn list', 'specially designated',
  // Categories relevant to Arkmurus
  'military end use', 'military end user', 'arms', 'ammunition', 'weapons',
  'explosives', 'tnt', 'c4', 'propellant', 'munitions', 'firearms',
  'night vision', 'body armour', 'surveillance', 'counter-drone',
  'radar', 'sonar', 'military vehicle', 'armoured',
  // Countries of concern
  'russia', 'iran', 'north korea', 'dprk', 'china', 'belarus',
  'myanmar', 'mali', 'burkina', 'niger', 'sudan', 'venezuela',
  // Organisations
  'ecju', 'bis ', 'ofac', 'hmrc', 'hmtc', 'dsca', 'ddtc', 'state department',
  'caatsa', 'aeca', 'itar', 'sipri', 'wassenaar',
];

const CRITICAL_COMPLIANCE_KEYWORDS = [
  'entity list addition', 'new designation', 'sanctions imposed',
  'license revoked', 'enforcement action', 'indictment', 'arrest',
  'export violation', 'caatsa', 'arms embargo', 'debarred',
];

export async function briefing() {
  const results = {
    updates:         [],
    signals:         [],
    alerts:          [],
    entityListChanges: [],
    sanctionChanges: [],
    licenseAlerts:   [],
    stats:           {},
    error:           null,
  };

  const settled = await Promise.allSettled(
    SOURCES.map(src => fetchSource(src))
  );

  for (let i = 0; i < settled.length; i++) {
    const res = settled[i];
    const src = SOURCES[i];
    if (res.status !== 'fulfilled' || !res.value) continue;

    for (const item of res.value) {
      const text = `${item.title} ${item.description || ''}`.toLowerCase();
      const isRelevant = COMPLIANCE_KEYWORDS.some(k => text.includes(k));
      if (!isRelevant) continue;

      const isCritical = CRITICAL_COMPLIANCE_KEYWORDS.some(k => text.includes(k));
      const priority = isCritical ? 'critical' : src.weight;

      const update = {
        title:    item.title,
        source:   src.name,
        url:      item.link || '',
        date:     item.pubDate || new Date().toISOString(),
        priority,
        type:     'export_control_intel',
        category: categorise(text),
      };

      results.updates.push(update);

      // Categorise into specific buckets
      if (text.includes('entity list') || text.includes('designated') || text.includes('sdn')) {
        results.entityListChanges.push(update);
      }
      if (text.includes('sanction') || text.includes('ofac') || text.includes('hmtc')) {
        results.sanctionChanges.push(update);
      }
      if (text.includes('licence') || text.includes('license') || text.includes('ecju') || text.includes('siel')) {
        results.licenseAlerts.push(update);
      }

      // Generate signals for high/critical items
      if (priority === 'critical' || priority === 'high') {
        results.signals.push({
          text:     `[COMPLIANCE: ${src.name}] ${item.title}`,
          source:   src.name,
          url:      item.link || '',
          priority,
          type:     'compliance_signal',
          category: update.category,
        });
      }

      // Alerts for critical compliance changes
      if (priority === 'critical') {
        results.alerts.push({
          text:     `COMPLIANCE ALERT [${update.category}]: ${item.title}`,
          source:   src.name,
          priority: 'critical',
          url:      item.link || '',
        });
      }
    }
  }

  // Sort by priority
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  results.updates.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));
  results.signals.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));

  results.stats = {
    totalUpdates:      results.updates.length,
    entityListChanges: results.entityListChanges.length,
    sanctionChanges:   results.sanctionChanges.length,
    licenseAlerts:     results.licenseAlerts.length,
    criticalAlerts:    results.alerts.length,
    fetchedAt:         new Date().toISOString(),
  };

  console.log(`[ExportControlIntel] ${results.updates.length} updates · ${results.alerts.length} critical · ${results.entityListChanges.length} entity list changes`);
  return results;
}

function categorise(text) {
  if (text.includes('entity list') || text.includes('designated'))  return 'Entity List';
  if (text.includes('sanction'))                                      return 'Sanctions';
  if (text.includes('licence') || text.includes('license'))          return 'Licensing';
  if (text.includes('enforcement') || text.includes('violation'))    return 'Enforcement';
  if (text.includes('arms') || text.includes('weapons'))             return 'Arms Controls';
  if (text.includes('dual-use') || text.includes('dual use'))        return 'Dual-Use';
  return 'General';
}

async function fetchSource(src) {
  try {
    const res = await fetch(src.url, {
      headers: {
        'User-Agent': 'CrucixIntelligence/1.0 (Arkmurus Group)',
        'Accept':     'application/atom+xml, application/rss+xml, application/xml, text/xml',
      },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const xml = await res.text();
    return src.type === 'atom' ? parseAtom(xml) : parseRSS(xml);
  } catch (err) {
    console.warn(`[ExportControlIntel] ${src.name} failed: ${err.message}`);
    return [];
  }
}

function parseRSS(xml) {
  const items = [];
  const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    items.push({
      title:       extractTag(block, 'title'),
      link:        extractTag(block, 'link'),
      description: extractTag(block, 'description'),
      pubDate:     extractTag(block, 'pubDate'),
    });
  }
  return items.slice(0, 20);
}

function parseAtom(xml) {
  const items = [];
  const entryRegex = /<entry>([\s\S]*?)<\/entry>/gi;
  let match;
  while ((match = entryRegex.exec(xml)) !== null) {
    const block = match[1];
    const linkMatch = block.match(/href="([^"]+)"/);
    items.push({
      title:       extractTag(block, 'title'),
      link:        linkMatch ? linkMatch[1] : '',
      description: extractTag(block, 'summary') || extractTag(block, 'content'),
      pubDate:     extractTag(block, 'updated') || extractTag(block, 'published'),
    });
  }
  return items.slice(0, 20);
}

function extractTag(xml, tag) {
  const re = new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]></${tag}>|<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, 'i');
  const m = xml.match(re);
  if (!m) return '';
  return (m[1] || m[2] || '').replace(/<[^>]+>/g, '').trim();
}
