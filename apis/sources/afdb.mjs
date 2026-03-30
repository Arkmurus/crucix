// apis/sources/afdb.mjs
// African Development Bank — Project Pipeline Intelligence
// Covers infrastructure investment across Lusophone Africa and broader ECOWAS region
// Free API: https://projectsapi.afdb.org
// No API key required

const AFDB_API = 'https://projectsapi.afdb.org/ords/analytics/mbfs/projects';

// Countries of primary interest (ISO2 codes)
const PRIORITY_COUNTRIES = {
  'GW': 'Guinea-Bissau',
  'AO': 'Angola',
  'MZ': 'Mozambique',
  'CV': 'Cape Verde',
  'ST': 'São Tomé and Príncipe',
  'GN': 'Guinea',
  'SN': 'Senegal',
  'ML': 'Mali',
  'BF': 'Burkina Faso',
  'NE': 'Niger',
  'CI': "Côte d'Ivoire",
  'GH': 'Ghana',
  'NG': 'Nigeria',
  'SD': 'Sudan',
  'ET': 'Ethiopia',
  'SO': 'Somalia',
  'CD': 'Congo (DRC)',
};

const CRITICAL_SECTORS = [
  'transport', 'energy', 'water', 'infrastructure', 'governance',
  'security', 'agriculture', 'finance', 'social', 'health',
];

export async function briefing() {
  console.log('[AfDB] Fetching project pipeline...');
  const results = { updates: [], signals: [], stats: {}, error: null };

  try {
    const res = await fetch(AFDB_API, {
      headers: {
        'User-Agent': 'CrucixIntelligence/1.0',
        'Accept':     'application/json',
      },
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) throw new Error(`AfDB API ${res.status}`);
    const data  = await res.json();
    const items = data.items || data || [];

    let lusophoneCount = 0;
    let totalValue     = 0;

    for (const proj of items) {
      const country  = (proj.country || proj.COUNTRY || '').toUpperCase().substring(0, 2);
      const name     = proj.project_name || proj.PROJECT_NAME || proj.name || '';
      const sector   = (proj.sector || proj.SECTOR || '').toLowerCase();
      const status   = proj.status || proj.STATUS || '';
      const value    = parseFloat(proj.ua_amount || proj.UA_AMOUNT || proj.amount || 0);
      const approved = proj.approval_date || proj.APPROVAL_DATE || '';
      const countryName = PRIORITY_COUNTRIES[country] || proj.country_name || country;

      if (!PRIORITY_COUNTRIES[country]) continue;

      const isLusophone = ['GW','AO','MZ','CV','ST'].includes(country);
      if (isLusophone) lusophoneCount++;
      if (value > 0) totalValue += value;

      const priority = isLusophone ? 'high' :
                       value > 100 ? 'medium' : 'normal';

      results.updates.push({
        title:   `[AfDB] ${countryName}: ${name}`,
        source:  'AfDB',
        country: countryName,
        sector,
        status,
        value_mua: value, // Millions of Units of Account
        approved,
        url:     `https://www.afdb.org/en/projects-and-operations`,
        type:    'afdb_project',
        priority,
      });

      if (isLusophone || value > 200) {
        results.signals.push({
          text:     `AfDB ${countryName}: ${name} — $${value.toFixed(0)}M UA (${sector})`,
          source:   'AfDB',
          priority,
        });
      }
    }

    results.stats = {
      totalProjects:    results.updates.length,
      lusophoneProjects: lusophoneCount,
      totalValueMUA:    Math.round(totalValue),
      fetchedAt:        new Date().toISOString(),
    };

    console.log(`[AfDB] ${results.updates.length} projects · ${lusophoneCount} Lusophone · $${Math.round(totalValue)}M UA`);
  } catch (err) {
    results.error = err.message;
    console.error('[AfDB] Error:', err.message);
  }

  return results;
}

// CLI test
if (process.argv[1]?.endsWith('afdb.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
