// ============================================================
// Crucix Source Module: FCDO UK Sanctions List
// Source: UK Foreign, Commonwealth & Development Office
// Feed:   https://assets.publishing.service.gov.uk (CSV format)
// Auth:   None required — public official feed
// Update: Daily (published by FCDO GOV.UK)
// Note:   As of 28 January 2026 this is the ONLY official UK
//         sanctions list. The OFSI Consolidated List is closed.
// Verified: gov.uk/government/publications/the-uk-sanctions-list
// ============================================================

export const id = 'fcdo';
export const name = 'FCDO UK Sanctions';

// Official static URL for the UK Sanctions List in CSV format
// Published by FCDO on GOV.UK — no API key required
const FEED_URL = 'https://assets.publishing.service.gov.uk/media/uk-sanctions-list.csv';

export async function fetch(env) {
  try {
    const res = await globalThis.fetch(FEED_URL, {
      headers: { 'User-Agent': 'Crucix-OSINT/1.0 (compliance monitoring)' }
    });

    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status} from FCDO feed` };
    }

    const text = await res.text();
    const lines = text.split('\n').filter(l => l.trim().length > 0);

    // First line is header
    const total = Math.max(0, lines.length - 1);

    // Parse recent additions — look for lines with recent dates (last 7 days)
    const today = new Date();
    const cutoff = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

    const recentlyAdded = [];
    const regimeCounts = {};

    for (const line of lines.slice(1)) {
      const cols = line.split(',');
      // CSV columns: LastUpdated, UniqueID, OFSIGroupID, UNRef, Name6(surname/entity), Name1...
      // Regime name is typically col index 16
      const lastUpdated = cols[0]?.replace(/"/g, '').trim();
      const name6 = cols[4]?.replace(/"/g, '').trim();  // surname or entity name
      const name1 = cols[5]?.replace(/"/g, '').trim();  // first name
      const regime = cols[16]?.replace(/"/g, '').trim() || 'Unknown';

      // Count by regime
      if (regime) {
        regimeCounts[regime] = (regimeCounts[regime] || 0) + 1;
      }

      // Check if recently updated
      if (lastUpdated) {
        const [day, month, year] = lastUpdated.split('/');
        if (day && month && year) {
          const updatedDate = new Date(`${year}-${month}-${day}`);
          if (updatedDate >= cutoff) {
            const fullName = [name1, name6].filter(Boolean).join(' ');
            if (fullName) recentlyAdded.push(fullName);
          }
        }
      }
    }

    // Top regimes by count
    const topRegimes = Object.entries(regimeCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([r, c]) => `${r.substring(0, 40)} (${c})`)
      .join('; ');

    const signals = [];

    if (recentlyAdded.length > 0) {
      signals.push({
        severity: 'PRIORITY',
        text: `FCDO UK Sanctions: ${recentlyAdded.length} designation(s) updated in last 7 days — ${recentlyAdded.slice(0, 3).join(', ')}${recentlyAdded.length > 3 ? '...' : ''}`
      });
    } else {
      signals.push({
        severity: 'ROUTINE',
        text: `FCDO UK Sanctions List: ${total.toLocaleString()} active designations. No changes in last 7 days.`
      });
    }

    return {
      ok: true,
      summary: `UK Sanctions List: ${total.toLocaleString()} designations. Top regimes: ${topRegimes}. Recent changes: ${recentlyAdded.length}.`,
      signals
    };

  } catch (err) {
    return { ok: false, error: err.message };
  }
}
