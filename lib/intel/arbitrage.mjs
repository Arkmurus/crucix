// lib/intel/arbitrage.mjs
// Polymarket Arbitrage Signal Engine
//
// Detects divergence between prediction market probabilities and OSINT severity.
// When Polymarket says "30% chance of escalation" but our correlation engine
// scores the same region as CRITICAL — that gap is the advisory edge.

// Map Polymarket question keywords → correlation region names
const REGION_MAP = [
  { region: 'Middle East',       keywords: ['iran', 'israel', 'gaza', 'lebanon', 'saudi', 'hormuz', 'houthi', 'persian', 'hamas', 'hezbollah'] },
  { region: 'Eastern Europe',    keywords: ['ukraine', 'russia', 'nato', 'kyiv', 'moscow', 'zelensky', 'putin', 'donbas', 'crimea', 'belarus'] },
  { region: 'East Asia',         keywords: ['china', 'taiwan', 'beijing', 'south china sea', 'dprk', 'north korea', 'xi jinping', 'pla', 'korea'] },
  { region: 'Energy / Maritime', keywords: ['oil', 'crude', 'opec', 'pipeline', 'tanker', 'shipping', 'gas', 'lng', 'brent', 'wti'] },
  { region: 'Latin America',     keywords: ['venezuela', 'colombia', 'mexico', 'cartel', 'cuba', 'nicaragua', 'maduro', 'latin'] },
  { region: 'Africa',            keywords: ['sahel', 'niger', 'mali', 'sudan', 'ethiopia', 'somalia', 'nigeria', 'africa', 'coup'] },
  { region: 'South Asia',        keywords: ['india', 'pakistan', 'afghanistan', 'kashmir', 'bangladesh'] },
  { region: 'Cyber / Global',    keywords: ['cyber', 'hack', 'ransomware', 'election', 'disinformation', 'infrastructure'] },
];

// OSINT severity → numeric score
const OSINT_SCORE = { critical: 3, high: 2, medium: 1, low: 0 };

// Polymarket YES probability → numeric score
function marketScore(yesProb) {
  if (yesProb >= 75) return 3; // market highly confident in YES
  if (yesProb >= 55) return 2;
  if (yesProb >= 40) return 1;
  return 0;                    // market confident this won't happen
}

// Detect which region(s) a market question relates to
function detectRegions(question) {
  const q = question.toLowerCase();
  return REGION_MAP.filter(r => r.keywords.some(kw => q.includes(kw))).map(r => r.region);
}

// Main: compare Polymarket odds against OSINT correlation severity
export function detectArbitrage(polymarketData, correlations) {
  const signals = [];
  if (!polymarketData?.markets?.length || !correlations?.length) return signals;

  // Build a severity lookup by region
  const osintByRegion = {};
  for (const c of correlations) {
    osintByRegion[c.region] = { severity: c.severity, score: c.totalScore, sourceCount: c.sourceCount };
  }

  for (const market of polymarketData.markets) {
    if (market.volumeTotal < 25000) continue; // ignore thin markets
    const regions = detectRegions(market.question);
    if (regions.length === 0) continue;

    for (const region of regions) {
      const osint = osintByRegion[region];
      if (!osint) continue;

      const mScore = marketScore(market.yesProb);
      const oScore = OSINT_SCORE[osint.severity] ?? 0;
      const gap    = oScore - mScore; // positive = OSINT higher than market

      // OSINT sees danger the market hasn't priced in
      if (gap >= 2) {
        signals.push({
          type:        'underpriced_risk',
          region,
          question:    market.question,
          yesProb:     market.yesProb,
          osintScore:  osint.severity,
          gap,
          volume:      market.volumeTotal,
          url:         market.url,
          text:        `${region}: Market at ${market.yesProb}% YES but OSINT signals ${osint.severity.toUpperCase()} (${osint.sourceCount} sources). Possible underpriced risk.`,
          priority:    gap >= 3 ? 'critical' : 'high',
        });
      }

      // Market pricing in danger OSINT hasn't detected yet
      if (mScore - oScore >= 2 && market.volume24h > 50000) {
        signals.push({
          type:        'market_leading_osint',
          region,
          question:    market.question,
          yesProb:     market.yesProb,
          osintScore:  osint.severity,
          gap:         mScore - oScore,
          volume:      market.volumeTotal,
          url:         market.url,
          text:        `${region}: Market at ${market.yesProb}% YES on high volume ($${(market.volume24h / 1000).toFixed(0)}K/24h) while OSINT is only ${osint.severity}. Market may be leading indicator.`,
          priority:    'high',
        });
      }
    }
  }

  // Sort by gap size (largest divergence first)
  return signals.sort((a, b) => b.gap - a.gap);
}

// Format for Telegram
export function formatArbitrageForTelegram(signals) {
  if (!signals?.length) return null;

  let msg = `📊 *PREDICTION ARBITRAGE*\n`;
  msg += `_Market odds diverge from OSINT signals_\n\n`;

  for (const s of signals.slice(0, 5)) {
    const emoji = s.type === 'underpriced_risk' ? '🔴' : '🟡';
    const label = s.type === 'underpriced_risk' ? 'UNDERPRICED RISK' : 'MARKET LEADING';
    msg += `${emoji} *${label}* — ${s.region}\n`;
    msg += `Market: ${s.yesProb}% YES | OSINT: ${s.osintScore.toUpperCase()}\n`;
    msg += `Q: _${s.question.substring(0, 100)}_\n`;
    msg += `Vol: $${(s.volume / 1000).toFixed(0)}K total\n\n`;
  }

  return msg;
}
