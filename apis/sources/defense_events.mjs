// apis/sources/defense_events.mjs
// European & global defence exhibitions, conferences, and export-control events
// Provides advance notice for team attendance and pipeline planning — Arkmurus operations

import '../utils/env.mjs';

function daysUntil(dateStr) {
  const target = new Date(dateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  target.setHours(0, 0, 0, 0);
  return Math.round((target - today) / (1000 * 60 * 60 * 24));
}

// ── Master Event Calendar ────────────────────────────────────────────────────
// priority: high = must attend | medium = consider | low = monitor
// Update dates annually. Sources: official event websites + Jane's/Defense News.
const EVENTS = [

  // ── 2026 ──────────────────────────────────────────────────────────────────

  {
    name: 'DSA 2026',
    fullName: 'Defence Services Asia',
    location: 'Kuala Lumpur Convention Centre, Malaysia',
    country: 'MY',
    region: 'Asia',
    startDate: '2026-04-27',
    endDate: '2026-04-30',
    url: 'https://www.dsaexhibition.com',
    category: 'expo',
    frequency: 'biennial',
    priority: 'low',
    focus: ['maritime', 'land', 'air', 'Asia Pacific procurement'],
    notes: 'Major Southeast Asian defence expo. Good for tracking Asian buyer networks.',
  },

  {
    name: 'HEMUS 2026',
    fullName: 'International Defence, Security & Aviation Technology Exhibition',
    location: 'Plovdiv, Bulgaria',
    country: 'BG',
    region: 'Europe',
    startDate: '2026-05-27',
    endDate: '2026-05-30',
    url: 'https://hemus.eu',
    category: 'expo',
    frequency: 'biennial',
    priority: 'low',
    focus: ['land systems', 'air systems', 'Balkan procurement'],
    notes: 'Regional Balkan defence expo. Eastern European supplier and buyer contacts.',
  },

  {
    name: 'DVD 2026',
    fullName: 'Defence Vehicle Dynamics',
    location: 'Millbrook Proving Ground, Bedfordshire, UK',
    country: 'GB',
    region: 'Europe',
    startDate: '2026-06-09',
    endDate: '2026-06-11',
    url: 'https://www.dvdshow.co.uk',
    category: 'expo',
    frequency: 'biennial',
    priority: 'medium',
    focus: ['ground vehicles', 'armoured vehicles', 'mobility systems'],
    notes: 'UK-led land vehicle demonstration. Live mobility trials on proving ground.',
  },

  {
    name: 'Eurosatory 2026',
    fullName: 'International Land & Airland Defence and Security Exhibition',
    location: 'Paris-Nord Villepinte, France',
    country: 'FR',
    region: 'Europe',
    startDate: '2026-06-15',
    endDate: '2026-06-19',
    url: 'https://www.eurosatory.com',
    category: 'expo',
    frequency: 'biennial',
    priority: 'high',
    focus: ['land systems', 'armoured vehicles', 'munitions', 'C4ISR', 'security'],
    notes: 'Premier European land defence expo — ~60,000 visitors, 1,700+ exhibitors. Critical for Lusophone Africa buyer introductions. French DGA and European procurement leads present.',
  },

  {
    name: 'BIS Update Conference 2026',
    fullName: 'US Bureau of Industry & Security Annual Update Conference',
    location: 'Washington DC, USA',
    country: 'US',
    region: 'Americas',
    startDate: '2026-06-22',
    endDate: '2026-06-24',
    url: 'https://www.bis.doc.gov',
    category: 'conference',
    frequency: 'annual',
    priority: 'high',
    focus: ['EAR compliance', 'ITAR', 'US export controls', 'sanctions', 'deemed export'],
    notes: 'Essential for US export control compliance updates — rule changes, enforcement priorities, licensing trends. Critical for Arkmurus deal structuring.',
  },

  {
    name: 'MSPO 2026',
    fullName: 'International Defence Industry Exhibition',
    location: 'Kielce, Poland',
    country: 'PL',
    region: 'Europe',
    startDate: '2026-09-01',
    endDate: '2026-09-04',
    url: 'https://mspo.eu',
    category: 'expo',
    frequency: 'annual',
    priority: 'medium',
    focus: ['land systems', 'air defence', 'cybersecurity', 'NATO interoperability'],
    notes: 'Largest Central European defence expo. Polish MoD signs contracts live on floor. Strong Eastern European procurement activity.',
  },

  {
    name: 'Africa Aerospace & Defence 2026',
    fullName: 'AAD — Africa Aerospace and Defence Exhibition',
    location: 'Waterkloof AFB, Pretoria, South Africa',
    country: 'ZA',
    region: 'Africa',
    startDate: '2026-09-16',
    endDate: '2026-09-20',
    url: 'https://www.aadexpo.co.za',
    category: 'expo',
    frequency: 'biennial',
    priority: 'high',
    focus: ['African markets', 'SADC procurement', 'aerospace', 'land systems', 'naval'],
    notes: 'Premier sub-Saharan African defence expo. Angola, Mozambique, and SADC buyers attend. Critical for Arkmurus Lusophone Africa pipeline — SADCBRIG and regional MoD procurement leads.',
  },

  {
    name: 'SIPRI Arms Transfers Workshop 2026',
    fullName: 'SIPRI Workshop on Arms Transfers and Export Controls',
    location: 'Stockholm, Sweden',
    country: 'SE',
    region: 'Europe',
    startDate: '2026-09-10',
    endDate: '2026-09-11',
    url: 'https://www.sipri.org/events',
    category: 'conference',
    frequency: 'annual',
    priority: 'medium',
    focus: ['arms transfer monitoring', 'export control policy', 'transparency reporting'],
    notes: 'SIPRI research event. Useful for market intelligence and tracking global arms flow trends.',
  },

  {
    name: 'NATO Industry Advisory Forum 2026',
    fullName: 'NIAG Annual Conference',
    location: 'Brussels, Belgium',
    country: 'BE',
    region: 'Europe',
    startDate: '2026-10-06',
    endDate: '2026-10-07',
    url: 'https://www.nato.int/cps/en/natolive/events.htm',
    category: 'conference',
    frequency: 'annual',
    priority: 'medium',
    focus: ['NATO procurement', 'defence industry policy', 'interoperability standards'],
    notes: 'Key NATO-industry engagement. Good for tracking Alliance procurement priorities and standardisation agreements.',
  },

  {
    name: 'Euronaval 2026',
    fullName: 'World Naval Defence & Maritime Exhibition',
    location: 'Paris-Nord Villepinte, France',
    country: 'FR',
    region: 'Europe',
    startDate: '2026-10-19',
    endDate: '2026-10-23',
    url: 'https://www.euronaval.fr',
    category: 'expo',
    frequency: 'biennial',
    priority: 'medium',
    focus: ['naval systems', 'maritime security', 'patrol vessels', 'submarines', 'coastguard'],
    notes: 'Key maritime defence event. Relevant for African coastal security programmes — Angola and Mozambique have active naval procurement.',
  },

  {
    name: 'EU Export Control Forum 2026',
    fullName: 'European Commission Annual Export Control Forum',
    location: 'Brussels, Belgium',
    country: 'BE',
    region: 'Europe',
    startDate: '2026-11-10',
    endDate: '2026-11-11',
    url: 'https://trade.ec.europa.eu',
    category: 'conference',
    frequency: 'annual',
    priority: 'high',
    focus: ['EU dual-use regulation', 'sanctions compliance', 'Common Position 944', 'end-user controls'],
    notes: 'Critical for Arkmurus compliance — EU regulation updates, sanctions enforcement trends, Lusophone Africa export licence implications.',
  },

  // ── 2027 ──────────────────────────────────────────────────────────────────

  {
    name: 'Enforce Tac 2027',
    fullName: 'International Trade Fair for Tactical Equipment',
    location: 'Nuremberg, Germany',
    country: 'DE',
    region: 'Europe',
    startDate: '2027-02-24',
    endDate: '2027-02-26',
    url: 'https://www.enforce-tac.de',
    category: 'expo',
    frequency: 'annual',
    priority: 'low',
    focus: ['special forces', 'law enforcement', 'tactical equipment', 'light weapons'],
    notes: 'Annual tactical equipment show co-located with IWA OutdoorClassics.',
  },

  {
    name: 'IDET 2027',
    fullName: 'International Defence & Security Technologies Fair',
    location: 'Brno Exhibition Centre, Czech Republic',
    country: 'CZ',
    region: 'Europe',
    startDate: '2027-05-19',
    endDate: '2027-05-21',
    url: 'https://www.idet.cz',
    category: 'expo',
    frequency: 'biennial',
    priority: 'low',
    focus: ['land systems', 'cybersecurity', 'Central European procurement'],
    notes: 'Central European defence and security expo.',
  },

  {
    name: 'DSEI 2027',
    fullName: 'Defence & Security Equipment International',
    location: 'ExCeL London, UK',
    country: 'GB',
    region: 'Europe',
    startDate: '2027-09-14',
    endDate: '2027-09-17',
    url: 'https://www.dsei.co.uk',
    category: 'expo',
    frequency: 'biennial',
    priority: 'high',
    focus: ['land', 'naval', 'air', 'cyber', 'special forces', 'security'],
    notes: "World's leading full-spectrum defence & security event. 35,000+ industry professionals. Critical for Arkmurus brokering pipeline — prime contractor and end-user introductions.",
  },

  {
    name: 'IDEX 2027',
    fullName: 'International Defence Exhibition & Conference',
    location: 'Abu Dhabi National Exhibition Centre, UAE',
    country: 'AE',
    region: 'Middle East',
    startDate: '2027-02-22',
    endDate: '2027-02-26',
    url: 'https://www.idexuae.ae',
    category: 'expo',
    frequency: 'biennial',
    priority: 'high',
    focus: ['land', 'naval', 'air', 'Gulf procurement', 'African buyer participation'],
    notes: 'World largest naval & land defence expo outside Europe. African delegations attend — useful for Lusophone Africa pipeline alongside Gulf procurement.',
  },

  {
    name: 'Milipol 2027',
    fullName: 'World Exhibition on Internal State Security',
    location: 'Paris-Nord Villepinte, France',
    country: 'FR',
    region: 'Europe',
    startDate: '2027-11-17',
    endDate: '2027-11-20',
    url: 'https://www.milipol.com',
    category: 'expo',
    frequency: 'biennial',
    priority: 'medium',
    focus: ['homeland security', 'law enforcement', 'border control', 'surveillance'],
    notes: 'Biennial internal security expo in Paris.',
  },
];

export async function briefing() {
  const now = new Date();

  // Tag each event with days-until and filter to 18-month window
  const tagged = EVENTS.map(e => ({ ...e, daysUntil: daysUntil(e.startDate) }))
    .filter(e => e.daysUntil >= -3 && e.daysUntil <= 548) // -3 days (ongoing) to 18 months ahead
    .sort((a, b) => a.daysUntil - b.daysUntil);

  const upcoming = tagged.filter(e => e.daysUntil >= 0);
  const ongoing  = tagged.filter(e => e.daysUntil < 0);

  // Generate signals and alerts
  const signals = [];
  const alerts  = [];

  for (const e of upcoming) {
    if (e.priority === 'high' && e.daysUntil <= 7) {
      alerts.push(`${e.name} starts in ${e.daysUntil} day${e.daysUntil === 1 ? '' : 's'} — ${e.location}`);
    } else if (e.daysUntil <= 14) {
      signals.push(`${e.name} in ${e.daysUntil} days (${e.location}) — ${e.focus.slice(0, 2).join(', ')}`);
    } else if (e.daysUntil <= 30) {
      signals.push(`${e.name} in ${e.daysUntil} days — ${e.location} [${e.priority}]`);
    } else if (e.daysUntil <= 60 && e.priority === 'high') {
      signals.push(`${e.name} — ${Math.round(e.daysUntil / 7)} weeks away (${e.location})`);
    }
  }

  // Upcoming count by region
  const byRegion = {};
  for (const e of upcoming) {
    byRegion[e.region] = (byRegion[e.region] || 0) + 1;
  }

  // Next high-priority event
  const nextHigh = upcoming.find(e => e.priority === 'high');
  const nextEvent = upcoming[0] || null;

  return {
    source: 'DefenseEvents',
    timestamp: now.toISOString(),
    upcoming,
    ongoing,
    signals,
    alerts,
    totalUpcoming: upcoming.length,
    nextEvent,
    nextHighPriority: nextHigh || null,
    byRegion,
    updates: upcoming.map(e => ({
      title: `${e.name} — ${e.location}`,
      content: `${e.startDate} to ${e.endDate} · ${e.daysUntil} days · ${e.priority} priority · ${e.focus.join(', ')}`,
      url: e.url,
      type: e.category,
      seenAt: now.toISOString(),
    })),
  };
}

// CLI runner
if (process.argv[1]?.endsWith('defense_events.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
