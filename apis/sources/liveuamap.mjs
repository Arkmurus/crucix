// apis/sources/liveuamap.mjs
// Liveuamap — Real-time geolocated conflict events
// Covers: Ukraine, Middle East, Iran/Israel, Syria, Africa, Asia + 30 regions
// Paid API — requires LIVEUAMAP_API_KEY in environment
// Reference: https://liveuamap.com/promo/api
// Replaces GDELT for real-time conflict intelligence

const BASE_URL = 'https://api.liveuamap.com';
const API_KEY = process.env.LIVEUAMAP_API_KEY;

// Regions most relevant to Arkmurus 80-country operations
const REGIONS = [
  { id: 'middleeast',    label: 'Middle East',        priority: 'critical' },
  { id: 'israel',        label: 'Israel/Palestine',   priority: 'critical' },
  { id: 'iran',          label: 'Iran',               priority: 'critical' },
  { id: 'ukraine',       label: 'Ukraine',            priority: 'critical' },
  { id: 'russia',        label: 'Russia',             priority: 'high'     },
  { id: 'africa',        label: 'Africa',             priority: 'high'     },
  { id: 'westafrica',    label: 'West Africa',        priority: 'high'     },
  { id: 'syria',         label: 'Syria',              priority: 'high'     },
  { id: 'asia',          label: 'Asia',               priority: 'medium'   },
  { id: 'latinamerica',  label: 'Latin America',      priority: 'medium'   },
];

// Keywords that elevate event priority for Arkmurus
const CRITICAL_KEYWORDS = [
  'airstrike', 'missile', 'explosion', 'attack', 'killed', 'troops',
  'invasion', 'offensive', 'strike', 'drone', 'nuclear', 'chemical',
  'coup', 'siege', 'casualties', 'naval', 'submarine', 'ballistic',
  'intercepted', 'launched', 'destroyed', 'captured', 'ambush',
];

export async function briefing() {
  const results = {
    updates:  [],
    signals:  [],
    alerts:   [],
    regions:  {},
    stats:    {},
    error:    null,
  };

  if (!API_KEY) {
    results.error = 'LIVEUAMAP_API_KEY not set';
    console.warn('[Liveuamap] API key not configured');
    return results;
  }

  const fetchPromises = REGIONS.map(region => fetchRegion(region));
  const settled = await Promise.allSettled(fetchPromises);

  for (let i = 0; i < settled.length; i++) {
    const res = settled[i];
    const region = REGIONS[i];
    if (res.status !== 'fulfilled' || !res.value) continue;

    const events = res.value;
    results.regions[region.label] = events.length;

    for (const event of events) {
      const text = (event.name || '').toLowerCase();
      const isCritical = CRITICAL_KEYWORDS.some(k => text.includes(k));
      const priority = isCritical ? 'critical' : region.priority;

      const update = {
        title:     event.name || 'Unknown event',
        source:    'Liveuamap',
        region:    region.label,
        lat:       event.lat,
        lng:       event.lng,
        timestamp: event.time ? new Date(event.time * 1000).toISOString() : new Date().toISOString(),
        url:       event.link || `https://liveuamap.com`,
        priority,
        type:      'conflict_event',
      };

      results.updates.push(update);

      if (priority === 'critical' || priority === 'high') {
        results.signals.push({
          text:     `[${region.label.toUpperCase()}] ${event.name}`,
          source:   'Liveuamap',
          region:   region.label,
          lat:      event.lat,
          lng:      event.lng,
          url:      event.link || '',
          priority,
          type:     'conflict_signal',
        });
      }

      if (priority === 'critical') {
        results.alerts.push({
          text:     `⚔️ CONFLICT [${region.label}]: ${event.name}`,
          source:   'Liveuamap',
          priority: 'critical',
          url:      event.link || '',
        });
      }
    }
  }

  // Sort by priority
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  results.updates.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));
  results.signals.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));

  results.stats = {
    totalEvents:    results.updates.length,
    criticalEvents: results.alerts.length,
    highPriority:   results.signals.filter(s => s.priority === 'high').length,
    regionsActive:  Object.keys(results.regions).filter(r => results.regions[r] > 0).length,
    fetchedAt:      new Date().toISOString(),
  };

  console.log(`[Liveuamap] ${results.updates.length} events · ${results.alerts.length} critical · ${results.stats.regionsActive} regions`);
  return results;
}

async function fetchRegion(region) {
  try {
    const params = new URLSearchParams({
      token: API_KEY,
      limit: '20',
    });

    const res = await fetch(`${BASE_URL}/v1/events/${region.id}?${params}`, {
      headers: {
        'User-Agent': 'CrucixIntelligence/1.0 (Arkmurus Group)',
        'Accept':     'application/json',
      },
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // API returns array of events or { events: [...] }
    const events = Array.isArray(data) ? data : (data.events || data.data || []);
    return events.slice(0, 15);
  } catch (err) {
    console.warn(`[Liveuamap] ${region.label} failed: ${err.message}`);
    return [];
  }
}

// Search specific region or keyword
export async function searchEvents(query, region = 'all') {
  if (!API_KEY) return { results: [], error: 'No API key' };
  try {
    const params = new URLSearchParams({ token: API_KEY, limit: '20', q: query });
    const res = await fetch(`${BASE_URL}/v1/search?${params}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0', 'Accept': 'application/json' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return { results: [], error: `API ${res.status}` };
    const data = await res.json();
    const events = Array.isArray(data) ? data : (data.events || []);
    return {
      query,
      results: events.slice(0, 10).map(e => ({
        name:      e.name,
        region:    e.region || '',
        lat:       e.lat,
        lng:       e.lng,
        timestamp: e.time ? new Date(e.time * 1000).toISOString() : '',
        url:       e.link || '',
      })),
    };
  } catch (err) {
    return { query, results: [], error: err.message };
  }
}
