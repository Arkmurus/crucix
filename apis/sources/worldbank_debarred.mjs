// ============================================================
// Crucix Source Module: World Bank Debarred Firms & Individuals
// Source: World Bank Group Integrity Vice Presidency
// Feed:   https://www.worldbank.org/en/projects-operations/procurement/debarred-firms
// Auth:   None required — public official feed
// Update: Every 3 hours (per World Bank website)
// Covers: IBRD/IDA debarments + cross-debarments from ADB,
//         AFDB, EBRD, IADB (Mutual Enforcement Agreement 2010)
// Verified: worldbank.org/en/projects-operations/procurement/debarred-firms
// ============================================================

export const id = 'worldbank_debarred';
export const name = 'World Bank Debarred Firms';

// World Bank publishes a JSON-accessible API via their data portal
// The debarment list page is HTML — we use the OpenSanctions bulk
// CSV which mirrors the official list and is updated daily
// Source verified: opensanctions.org/datasets/worldbank_debarred/
const FEED_URL = 'https://data.opensanctions.org/datasets/latest/worldbank_debarred/targets.simple.csv';

export async function fetch(env) {
  try {
    const res = await globalThis.fetch(FEED_URL, {
      headers: { 'User-Agent': 'Crucix-OSINT/1.0 (compliance monitoring)' }
    });

    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status} from World Bank debarment feed` };
    }

    const text = await res.text();
    const lines = text.split('\n').filter(l => l.trim().length > 0);
    const total = Math.max(0, lines.length - 1);

    // Parse for recently added entries (last 14 days)
    const cutoff = new Date(Date.now() - 14 * 24 * 60 * 60 * 1000);
    const recent = [];
    const countries = {};

    for (const line of lines.slice(1)) {
      // Simple CSV split — handles basic cases
      const cols = line.split(',');
      const name = cols[1]?.replace(/"/g, '').trim();
      const country = cols[3]?.replace(/"/g, '').trim();
      const firstSeen = cols[cols.length - 2]?.replace(/"/g, '').trim();

      if (country) {
        countries[country] = (countries[country] || 0) + 1;
      }

      if (firstSeen) {
        try {
          const seenDate = new Date(firstSeen);
          if (seenDate >= cutoff && name) {
            recent.push(name);
          }
        } catch (_) { /* skip unparseable dates */ }
      }
    }

    const topCountries = Object.entries(countries)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([c, n]) => `${c} (${n})`)
      .join(', ');

    const signals = [];

    if (recent.length > 0) {
      signals.push({
        severity: 'PRIORITY',
        text: `World Bank: ${recent.length} new debarment(s) in last 14 days — ${recent.slice(0, 3).join(', ')}${recent.length > 3 ? '...' : ''}`
      });
    } else {
      signals.push({
        severity: 'ROUTINE',
        text: `World Bank Debarment List: ${total.toLocaleString()} ineligible firms/individuals. No new entries in 14 days.`
      });
    }

    return {
      ok: true,
      summary: `World Bank debarred: ${total.toLocaleString()} entities. Top countries: ${topCountries}. New (14d): ${recent.length}.`,
      signals
    };

  } catch (err) {
    return { ok: false, error: err.message };
  }
}
