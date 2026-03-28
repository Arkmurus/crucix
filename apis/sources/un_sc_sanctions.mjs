// ============================================================
// Crucix Source Module: UN Security Council Consolidated Sanctions
// Source: United Nations Security Council
// Feed:   https://scsanctions.un.org/resources/xml/en/consolidated.xml
// Auth:   None required — official UN public feed
// Update: Ad hoc (updated when Security Council makes changes)
// Covers: All UN SC sanctions regimes including Al-Qaida/ISIL,
//         Iran, DPRK, Russia, Libya, Yemen, Sudan, DRC, Mali, etc.
// Verified: main.un.org/securitycouncil/en/content/un-sc-consolidated-list
// ============================================================

export const id = 'un_sc_sanctions';
export const name = 'UN SC Consolidated Sanctions';

// Official UN XML feed — no authentication required
// Verified URL from main.un.org Security Council documentation
const FEED_URL = 'https://scsanctions.un.org/resources/xml/en/consolidated.xml';

export async function fetch(env) {
  try {
    const res = await globalThis.fetch(FEED_URL, {
      headers: { 'User-Agent': 'Crucix-OSINT/1.0 (compliance monitoring)' }
    });

    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status} from UN SC sanctions feed` };
    }

    const xml = await res.text();

    // Count individuals and entities using XML tag counting
    // (avoids need for a full XML parser dependency)
    const individualMatches = xml.match(/<INDIVIDUAL>/g) || [];
    const entityMatches = xml.match(/<ENTITY>/g) || [];
    const totalIndividuals = individualMatches.length;
    const totalEntities = entityMatches.length;
    const total = totalIndividuals + totalEntities;

    // Extract generation date from XML header
    // Format: <CONSOLIDATED_LIST dateGenerated="YYYY-MM-DDTHH:MM:SS">
    const dateMatch = xml.match(/dateGenerated="([^"]+)"/);
    const listDate = dateMatch ? dateMatch[1].split('T')[0] : 'unknown';

    // Extract regime names to identify most-represented regimes
    const regimeMatches = xml.match(/<UN_LIST_TYPE>([^<]+)<\/UN_LIST_TYPE>/g) || [];
    const regimeCounts = {};
    for (const m of regimeMatches) {
      const regime = m.replace(/<\/?UN_LIST_TYPE>/g, '').trim();
      regimeCounts[regime] = (regimeCounts[regime] || 0) + 1;
    }

    const topRegimes = Object.entries(regimeCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([r, c]) => `${r} (${c})`)
      .join('; ');

    // Check if list was updated in last 7 days
    const signals = [];
    let isRecent = false;

    if (listDate && listDate !== 'unknown') {
      const generated = new Date(listDate);
      const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
      isRecent = generated >= sevenDaysAgo;
    }

    if (isRecent) {
      signals.push({
        severity: 'PRIORITY',
        text: `UN SC Sanctions list updated ${listDate}: ${totalIndividuals} individuals, ${totalEntities} entities across all regimes.`
      });
    } else {
      signals.push({
        severity: 'ROUTINE',
        text: `UN SC Sanctions: ${total} designations (${totalIndividuals} individuals, ${totalEntities} entities). List date: ${listDate}.`
      });
    }

    return {
      ok: true,
      summary: `UN SC Consolidated List: ${total} designations as of ${listDate}. Top regimes: ${topRegimes}.`,
      signals
    };

  } catch (err) {
    return { ok: false, error: err.message };
  }
}
