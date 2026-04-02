// lib/intel/correlate.mjs
// Cross-signal correlation engine
// Detects when multiple independent sources point to the same region/entity
// in the same sweep — convergence = higher-priority alert

import { scoreSignal } from './dedup.mjs';

// Geographic regions and their associated keywords
const REGIONS = {
  'Middle East':         ['iran', 'iraq', 'syria', 'israel', 'gaza', 'lebanon', 'saudi', 'riyadh', 'yemen', 'gulf', 'hormuz', 'persian', 'uae', 'dubai', 'abu dhabi', 'jordan', 'amman'],
  'Eastern Europe':      ['ukraine', 'russia', 'nato', 'kyiv', 'moscow', 'belarus', 'moldova', 'crimea', 'donbas', 'poland', 'romania', 'warsaw', 'bucharest', 'bulgaria', 'sofia'],
  'East Asia':           ['china', 'taiwan', 'beijing', 'south china sea', 'pla', 'dprk', 'north korea', 'korea', 'japan', 'philippines', 'indonesia', 'vietnam', 'manila', 'jakarta'],
  'South Asia':          ['india', 'pakistan', 'afghanistan', 'kashmir', 'bangladesh', 'myanmar', 'dhaka'],
  'Lusophone Africa':    ['angola', 'mozambique', 'guinea-bissau', 'cabo verde', 'cape verde', 'são tomé', 'sao tome', 'lusophone', 'faap', 'fam', 'fadm'],
  'West Africa':         ['sahel', 'nigeria', 'ghana', 'senegal', 'mali', 'niger', 'burkina', 'côte d\'ivoire', 'ivory coast', 'cameroon', 'ecowas', 'abuja', 'lagos', 'accra', 'dakar'],
  'East / Central Africa':['sudan', 'ethiopia', 'somalia', 'kenya', 'uganda', 'tanzania', 'rwanda', 'congo', 'drc', 'nairobi', 'addis ababa', 'amisom', 'aussom', 'chad', 'central africa'],
  'Latin America':       ['venezuela', 'colombia', 'brazil', 'mexico', 'peru', 'cartel', 'cuba', 'haiti', 'nicaragua', 'bogota', 'brasilia', 'lima'],
  'Cyber / Global':      ['cyber', 'hack', 'ransomware', 'critical infrastructure', 'election', 'disinformation', 'espionage', 'data breach'],
  'Energy / Maritime':   ['oil', 'gas', 'pipeline', 'tanker', 'strait', 'suez', 'bab el-mandeb', 'malacca', 'shipping', 'lng', 'brent', 'opec', 'indian ocean'],
  'Defence Procurement': ['tender', 'rfp', 'contract award', 'procurement', 'arms deal', 'modernisation', 'modernization', 'defence budget', 'military spending', 'acquisition', 'fms', 'foreign military'],
};

// Source categories for convergence detection
const SOURCE_CATEGORIES = {
  osint:      signal => signal.channel || signal.type === 'telegram',
  news:       signal => signal.type === 'rss' || signal.source,
  sanctions:  signal => signal.type === 'sanctions_entity',
  financial:  signal => signal.type === 'fred' || signal.type === 'energy' || signal.type === 'central_bank',
  conflict:   signal => signal.type === 'acled' || signal.type === 'gdelt_article',
  trade:      signal => signal.type === 'trade_flow',
};

// Detect which region a signal relates to
function detectRegion(signal) {
  const text = [signal.text, signal.title, signal.headline, signal.summary, signal.content]
    .filter(Boolean).join(' ').toLowerCase();

  const matches = [];
  for (const [region, keywords] of Object.entries(REGIONS)) {
    if (keywords.some(kw => text.includes(kw))) {
      matches.push(region);
    }
  }
  return matches;
}

// Detect source category of a signal
function detectCategory(signal) {
  for (const [cat, test] of Object.entries(SOURCE_CATEGORIES)) {
    if (test(signal)) return cat;
  }
  return 'other';
}

// Main correlation function
export function correlate(data) {
  const correlations = {};

  // Collect all signals from all sources
  const allSignals = [
    ...(data.tg?.urgent || []).map(s => ({ ...s, _src: 'osint' })),
    ...(data.newsFeed   || []).map(s => ({ ...s, _src: 'news' })),
    ...(data.news       || []).map(s => ({ ...s, _src: 'news' })),
    ...(data.sanctions?.updates || []).map(s => ({ ...s, _src: 'sanctions' })),
    ...(data.gdelt?.updates || []).map(s => ({ ...s, _src: 'conflict' })),
    ...(data.unsc?.updates || []).map(s => ({ ...s, _src: 'diplomatic' })),
    ...(data.centralBanks?.updates || []).map(s => ({ ...s, _src: 'financial' })),
    ...(data.thinkTanks?.updates || []).map(s => ({ ...s, _src: 'analytical' })),
    ...(data.tradeFlows?.flows || []).map(s => ({ ...s, _src: 'trade' })),
    ...(data.acled?.deadliestEvents || []).map(s => ({ ...s, _src: 'conflict' })),
    // Defence & procurement sources
    ...(data.defenseNews?.updates || []).map(s => ({ ...s, _src: 'defence_news' })),
    ...(data.sipriArms?.updates   || []).map(s => ({ ...s, _src: 'arms_transfer' })),
    ...(data.procurementTenders?.updates || []).map(s => ({ ...s, _src: 'procurement' })),
    ...(data.exportControlIntel?.alerts  || []).map(s => ({ ...s, _src: 'export_control' })),
    // Explorer intelligence
    ...((data.explorerFindings?.findings?.insights || data.explorerFindings?.insights || [])
        .map(s => ({ title: s.title, text: s.summary, url: s.sourceUrl, _src: 'explorer' }))),
  ];

  // Group signals by region
  for (const signal of allSignals) {
    const regions = detectRegion(signal);
    const scored  = scoreSignal(signal);

    for (const region of regions) {
      if (!correlations[region]) {
        correlations[region] = {
          region,
          signals:    [],
          sources:    new Set(),
          totalScore: 0,
          severity:   'low',
        };
      }
      correlations[region].signals.push({ ...signal, ...scored });
      correlations[region].sources.add(signal._src || detectCategory(signal));
      correlations[region].totalScore += scored.score;
    }
  }

  // Convert Sets to Arrays and calculate final severity
  const results = Object.values(correlations).map(c => {
    const sourceCount = c.sources.size;
    const sigCount    = c.signals.length;

    let severity = 'low';
    if (c.totalScore >= 30 || sourceCount >= 4) severity = 'critical';
    else if (c.totalScore >= 15 || sourceCount >= 3) severity = 'high';
    else if (c.totalScore >= 5  || sourceCount >= 2) severity = 'medium';

    return {
      region:      c.region,
      severity,
      sourceCount: sourceCount,
      sources:     Array.from(c.sources),
      signalCount: sigCount,
      totalScore:  c.totalScore,
      topSignals:  c.signals
        .sort((a, b) => b.score - a.score)
        .slice(0, 5)
        .map(s => ({
          text:   (s.text || s.title || s.headline || s.content || s.summary || '').substring(0, 400),
          source: s._src,
          url:    s.url || s.link || null,
          score:  s.score,
        })),
      timestamp:   new Date().toISOString()
    };
  });

  // Sort by total score descending
  return results
    .filter(r => r.signalCount >= 2)   // Only regions with 2+ signals
    .sort((a, b) => b.totalScore - a.totalScore);
}

// Format correlations for Telegram
export function formatCorrelationsForTelegram(correlations) {
  if (!correlations || correlations.length === 0) return null;

  const critical = correlations.filter(c => c.severity === 'critical');
  const high     = correlations.filter(c => c.severity === 'high');
  const show     = [...critical, ...high].slice(0, 4);

  if (show.length === 0) return null;

  let msg = `*CONVERGENCE ALERT*\n`;
  msg += `_Multiple sources pointing to same regions_\n\n`;

  for (const c of show) {
    const sev = c.severity === 'critical' ? '🔴' : '🟡';
    msg += `${sev} *${c.region}*\n`;
    msg += `Sources: ${c.sources.join(', ')} (${c.sourceCount} independent)\n`;
    msg += `Signals: ${c.signalCount} | Score: ${c.totalScore}\n`;
    if (c.topSignals[0]) {
      msg += `Top: ${c.topSignals[0].text.substring(0, 100)}\n`;
    }
    msg += `\n`;
  }

  return msg;
}
