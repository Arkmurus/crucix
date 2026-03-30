// apis/sources/polymarket.mjs
// Polymarket Prediction Markets — leading geopolitical indicators
// Gamma API: completely free, no authentication required
// Reference: https://gamma-api.polymarket.com
// Price = probability (0.65 = 65% chance of YES outcome)

const GAMMA_API = 'https://gamma-api.polymarket.com';

// Keywords that identify geopolitically relevant markets for Arkmurus
const GEO_KEYWORDS = [
  'war', 'ceasefire', 'invasion', 'attack', 'strike', 'conflict',
  'nuclear', 'missile', 'sanctions', 'regime', 'coup', 'election',
  'iran', 'russia', 'ukraine', 'israel', 'china', 'taiwan', 'korea',
  'nato', 'un ', 'arms', 'military', 'troops', 'treaty', 'peace',
  'oil', 'crude', 'opec', 'embargo', 'pipeline',
  'africa', 'guinea', 'nigeria', 'sahel', 'sudan', 'somalia',
  'president', 'prime minister', 'government', 'resign', 'impeach',
  'terrorist', 'explosion', 'assassination', 'refugee',
];

// Signal thresholds
const HIGH_CERTAINTY   = 0.80; // >80% = near-certain
const LOW_CERTAINTY    = 0.20; // <20% = near-certain NO
const SHIFT_THRESHOLD  = 0.10; // 10% move = significant
const MIN_VOLUME       = 10000; // Minimum $10k volume to be credible

export async function briefing() {
  const results = {
    updates:  [],
    signals:  [],
    markets:  [],
    alerts:   [],
    stats:    {},
    error:    null,
  };

  try {
    // Fetch top geopolitical markets by 24hr volume
    const params = new URLSearchParams({
      active:    'true',
      closed:    'false',
      order:     'volume24hr',
      ascending: 'false',
      limit:     '100',
    });

    const res = await fetch(`${GAMMA_API}/markets?${params}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0 (Arkmurus Group)' },
      signal: AbortSignal.timeout(20000),
    });

    if (!res.ok) throw new Error(`Polymarket Gamma API ${res.status}`);
    const allMarkets = await res.json();

    // Filter to geopolitically relevant markets
    const geoMarkets = allMarkets.filter(m => {
      const q = (m.question || '').toLowerCase();
      return GEO_KEYWORDS.some(kw => q.includes(kw));
    });

    // Parse and score each market
    for (const m of geoMarkets.slice(0, 50)) {
      const question     = m.question || 'Unknown';
      const outcomes     = m.outcomes || ['Yes', 'No'];
      let _op = m.outcomePrices || ['0.5','0.5']; if (typeof _op === 'string') { try { _op = JSON.parse(_op); } catch(e) { _op = ['0.5','0.5']; } } if (!Array.isArray(_op)) _op = ['0.5','0.5']; const prices = _op.map(Number);
      const yesProb      = prices[0] || 0.5;
      const noProb       = prices[1] || (1 - yesProb);
      const volume24h    = Number(m.volume24hr || 0);
      const volumeTotal  = Number(m.volume || 0);
      const endDate      = m.endDate || null;
      const slug         = m.slug || '';
      const url          = `https://polymarket.com/event/${slug}`;

      if (volumeTotal < MIN_VOLUME) continue;

      const market = {
        question,
        yesProb:     Math.round(yesProb * 100),
        noProb:      Math.round(noProb * 100),
        volume24h:   Math.round(volume24h),
        volumeTotal: Math.round(volumeTotal),
        endDate,
        url,
        type:        'polymarket_market',
        signal:      classifySignal(question, yesProb, volume24h),
      };

      results.markets.push(market);

      // Build update for dashboard
      const probStr = `YES: ${market.yesProb}%  NO: ${market.noProb}%`;
      results.updates.push({
        title:  question,
        source: 'Polymarket',
        url,
        content: `${probStr}  |  24h vol: $${formatVolume(volume24h)}  |  Total: $${formatVolume(volumeTotal)}`,
        type:   'prediction_market',
        signal: market.signal,
      });

      // Generate signals for near-certain or high-volume markets
      if (volume24h > 50000 || yesProb >= HIGH_CERTAINTY || yesProb <= LOW_CERTAINTY) {
        const certainty = yesProb >= HIGH_CERTAINTY ? 'NEAR CERTAIN YES' :
                          yesProb <= LOW_CERTAINTY  ? 'NEAR CERTAIN NO'  : 'HIGH ACTIVITY';
        results.signals.push({
          text:     `[${certainty}] ${question} — ${market.yesProb}% YES — $${formatVolume(volume24h)} 24h vol`,
          source:   'Polymarket',
          url,
          priority: volume24h > 100000 ? 'critical' : 'high',
          type:     'prediction_signal',
        });
      }
    }

    // Generate Arkmurus-specific alerts
    const criticalMarkets = results.markets.filter(m =>
      m.signal === 'critical' || m.volume24h > 100000
    );

    for (const m of criticalMarkets.slice(0, 5)) {
      results.alerts.push({
        text:     `POLYMARKET ALERT: ${m.question} — ${m.yesProb}% YES`,
        priority: 'high',
        source:   'Polymarket',
        url:      m.url,
      });
    }

    results.stats = {
      totalMarkets:    results.markets.length,
      criticalSignals: results.signals.filter(s => s.priority === 'critical').length,
      highSignals:     results.signals.filter(s => s.priority === 'high').length,
      totalVolume24h:  results.markets.reduce((s, m) => s + m.volume24h, 0),
      fetchedAt:       new Date().toISOString(),
    };

    console.log(`[Polymarket] ${results.markets.length} geo markets · ${results.signals.length} signals · $${formatVolume(results.stats.totalVolume24h)} 24h vol`);

  } catch (err) {
    results.error = err.message;
    console.error('[Polymarket] Error:', err.message);
  }

  return results;
}

function classifySignal(question, yesProb, volume24h) {
  const q = question.toLowerCase();
  const isWar       = ['war', 'invasion', 'strike', 'attack', 'nuclear', 'missile'].some(k => q.includes(k));
  const isRegime    = ['coup', 'regime', 'resign', 'assassin', 'impeach'].some(k => q.includes(k));
  const isHighStake = ['iran', 'russia', 'ukraine', 'taiwan', 'china', 'nuclear'].some(k => q.includes(k));

  if ((isWar || isRegime) && volume24h > 50000)   return 'critical';
  if (isHighStake && volume24h > 20000)            return 'high';
  if (yesProb >= HIGH_CERTAINTY || yesProb <= LOW_CERTAINTY) return 'high';
  if (volume24h > 30000)                           return 'medium';
  return 'low';
}

function formatVolume(v) {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

// Search specific topic on Polymarket
export async function searchPolymarket(query) {
  try {
    const params = new URLSearchParams({
      active: 'true',
      closed: 'false',
      order:  'volume24hr',
      ascending: 'false',
      limit:  '20',
    });
    const res = await fetch(`${GAMMA_API}/markets?${params}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return { results: [], error: `API ${res.status}` };
    const markets = await res.json();
    const q = query.toLowerCase();
    const matches = markets.filter(m => (m.question || '').toLowerCase().includes(q));
    return {
      query,
      results: matches.slice(0, 10).map(m => ({
        question:    m.question,
        yesProb:     Math.round(Number(m.outcomePrices?.[0] || 0.5) * 100),
        volume24h:   Math.round(Number(m.volume24hr || 0)),
        volumeTotal: Math.round(Number(m.volume || 0)),
        url:         `https://polymarket.com/event/${m.slug}`,
      })),
    };
  } catch (err) {
    return { query, results: [], error: err.message };
  }
}
