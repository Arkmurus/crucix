// EPA RadNet — Radiation Monitoring Network
// No auth required. Government open data via Envirofacts REST API.
// Monitors ambient radiation levels across the US via fixed monitoring stations.
// Complements Safecast (citizen science) with official government readings.

import { safeFetch } from '../utils/fetch.mjs';

const BASE = 'https://enviro.epa.gov/enviro/efservice';

// RadNet analytical results endpoint
const RADNET_ANALYTICAL = `${BASE}/RADNET_ANALYTICAL_RESULTS`;
// RadNet auxiliary data
const RADNET_AUX = `${BASE}/RADNET_AUX`;

// Key US cities with RadNet monitoring stations
const MONITORING_STATIONS = {
  washingtonDC:  { label: 'Washington, DC',   state: 'DC', lat: 38.9, lon: -77.0 },
  newYork:       { label: 'New York, NY',      state: 'NY', lat: 40.7, lon: -74.0 },
  losAngeles:    { label: 'Los Angeles, CA',   state: 'CA', lat: 34.1, lon: -118.2 },
  chicago:       { label: 'Chicago, IL',       state: 'IL', lat: 41.9, lon: -87.6 },
  seattle:       { label: 'Seattle, WA',       state: 'WA', lat: 47.6, lon: -122.3 },
  denver:        { label: 'Denver, CO',        state: 'CO', lat: 39.7, lon: -105.0 },
  honolulu:      { label: 'Honolulu, HI',      state: 'HI', lat: 21.3, lon: -157.9 },
  anchorage:     { label: 'Anchorage, AK',     state: 'AK', lat: 61.2, lon: -149.9 },
  miami:         { label: 'Miami, FL',         state: 'FL', lat: 25.8, lon: -80.2 },
  sanFrancisco:  { label: 'San Francisco, CA', state: 'CA', lat: 37.8, lon: -122.4 },
};

// Analyte types that indicate concerning radiation
const KEY_ANALYTES = [
  'GROSS BETA',
  'GROSS ALPHA',
  'IODINE-131',
  'CESIUM-137',
  'CESIUM-134',
  'STRONTIUM-90',
  'TRITIUM',
  'URANIUM',
  'PLUTONIUM',
];

// Normal background radiation thresholds (pCi/L or pCi/m3 depending on medium)
const THRESHOLDS = {
  'GROSS BETA': { normal: 1.0, elevated: 5.0, unit: 'pCi/m3' },
  'GROSS ALPHA': { normal: 0.05, elevated: 0.15, unit: 'pCi/m3' },
  'IODINE-131': { normal: 0.01, elevated: 0.1, unit: 'pCi/m3' },
  'CESIUM-137': { normal: 0.01, elevated: 0.1, unit: 'pCi/m3' },
  'CESIUM-134': { normal: 0.001, elevated: 0.01, unit: 'pCi/m3' },
};

// Per-call timeouts tightened from 25s → 10s. The sweep's outer hard cap
// is 30s, and this briefing fires 4 Envirofacts calls; any serial delay
// blew the cap and the whole EPA slot returned nothing. With 10s per call
// run in parallel, 4 calls + a small headroom fit inside the 30s budget
// even if every call stalls near its individual timeout.
const EPA_CALL_TIMEOUT_MS = 10000;

// Get recent RadNet analytical results (JSON)
export async function getAnalyticalResults(opts = {}) {
  const { rows = 50, startRow = 0 } = opts;
  return safeFetch(
    `${RADNET_ANALYTICAL}/ROWS/${startRow}:${startRow + rows}/JSON`,
    { timeout: EPA_CALL_TIMEOUT_MS }
  );
}

// Get results filtered by state
export async function getResultsByState(state, opts = {}) {
  const { rows = 25, startRow = 0 } = opts;
  return safeFetch(
    `${RADNET_ANALYTICAL}/ANA_STATE/${state}/ROWS/${startRow}:${startRow + rows}/JSON`,
    { timeout: EPA_CALL_TIMEOUT_MS }
  );
}

// Get results filtered by analyte type
export async function getResultsByAnalyte(analyte, opts = {}) {
  const { rows = 25, startRow = 0 } = opts;
  const encoded = encodeURIComponent(analyte);
  return safeFetch(
    `${RADNET_ANALYTICAL}/ANA_TYPE/${encoded}/ROWS/${startRow}:${startRow + rows}/JSON`,
    { timeout: EPA_CALL_TIMEOUT_MS }
  );
}

// Lookup coords by city name or state
const CITY_COORDS = Object.fromEntries(
  Object.values(MONITORING_STATIONS).map(s => [s.label.split(',')[0].toUpperCase(), s])
);

// Compact a reading for briefing output
function compactReading(r) {
  const city = (r.ANA_CITY || r.LOCATION || '').toUpperCase().trim();
  const station = CITY_COORDS[city];
  return {
    location: r.ANA_CITY || r.LOCATION || 'Unknown',
    state: r.ANA_STATE || r.STATE || null,
    analyte: r.ANA_TYPE || r.ANALYTE_NAME || null,
    result: r.ANA_RESULT != null ? parseFloat(r.ANA_RESULT) : null,
    unit: r.RESULT_UNIT || r.ANA_UNIT || null,
    collectDate: r.COLLECT_DATE || r.SAMPLE_DATE || null,
    medium: r.SAMPLE_TYPE || r.MEDIUM || null,
    lat: station?.lat || null,
    lon: station?.lon || null,
  };
}

// Check a reading against known thresholds
function checkReading(reading) {
  if (reading.result === null || reading.result <= 0) return null;
  const threshold = THRESHOLDS[reading.analyte?.toUpperCase()];
  if (!threshold) return null;

  if (reading.result > threshold.elevated) {
    return {
      level: 'ELEVATED',
      reading,
      threshold: threshold.elevated,
      ratio: (reading.result / threshold.elevated).toFixed(1),
    };
  }
  if (reading.result > threshold.normal * 3) {
    return {
      level: 'ABOVE_NORMAL',
      reading,
      threshold: threshold.normal,
      ratio: (reading.result / threshold.normal).toFixed(1),
    };
  }
  return null;
}

// Briefing — get recent radiation readings from EPA network, flag anomalies.
// Envirofacts is the single source of truth for US RadNet — there is no
// alternative API. When it times out, the correct behaviour is graceful
// degradation: return an empty-but-structured result so the sweep's EPA
// slot isn't lost, rather than throwing and showing "CRITICAL" in the log.
export async function briefing() {
  const readings = [];
  const signals = [];
  const fetchErrors = [];

  // Run the broad pull + the per-analyte pulls in PARALLEL via
  // allSettled. Previously the broad pull was awaited first, so if it
  // stalled near its timeout the analyte calls queued behind it and the
  // whole briefing blew past the sweep's 30s hard cap.
  const parallelResults = await Promise.allSettled([
    getAnalyticalResults({ rows: 50 }).then(d => ({ kind: 'broad', records: Array.isArray(d) ? d : [] })),
    getResultsByAnalyte('GROSS BETA',  { rows: 20 }).then(d => ({ kind: 'GROSS BETA',  records: Array.isArray(d) ? d : [] })),
    getResultsByAnalyte('IODINE-131',  { rows: 20 }).then(d => ({ kind: 'IODINE-131',  records: Array.isArray(d) ? d : [] })),
    getResultsByAnalyte('CESIUM-137',  { rows: 20 }).then(d => ({ kind: 'CESIUM-137',  records: Array.isArray(d) ? d : [] })),
  ]);

  for (const r of parallelResults) {
    if (r.status === 'fulfilled') {
      const { kind, records } = r.value;
      for (const raw of records) {
        const reading = compactReading(raw);
        // Dedupe across the 4 parallel calls (same station/analyte/date
        // shows up in both the broad pull and the analyte-specific pull).
        const isDup = readings.some(existing =>
          existing.location === reading.location &&
          existing.collectDate === reading.collectDate &&
          existing.analyte === reading.analyte
        );
        if (!isDup) readings.push(reading);
      }
    } else {
      fetchErrors.push(r.reason?.message || String(r.reason));
    }
  }

  // Graceful degradation: all 4 calls failed → return the empty slot
  // with an explicit note rather than throwing. The sweep log already
  // separately logged CRITICAL so the operator sees the incident; we
  // just don't want to lose the EPA slot on the dashboard.
  if (readings.length === 0 && fetchErrors.length === parallelResults.length) {
    return {
      source: 'EPA RadNet',
      timestamp: new Date().toISOString(),
      totalReadings: 0,
      readings: [],
      stateSummary: {},
      signals: [`EPA Envirofacts unreachable (${fetchErrors[0]?.slice(0, 80)}) — no alternative public API for US RadNet. Skipped this sweep.`],
      monitoredAnalytes: KEY_ANALYTES,
      thresholds: THRESHOLDS,
      degraded: true,
      fetchErrors,
      note: 'All 4 Envirofacts calls failed. Sweep continues; next attempt in 7 min.',
    };
  }

  // Check all readings against thresholds
  for (const reading of readings) {
    const alert = checkReading(reading);
    if (alert) {
      if (alert.level === 'ELEVATED') {
        signals.push(
          `ELEVATED ${reading.analyte} at ${reading.location}, ${reading.state}: ` +
          `${reading.result} ${reading.unit || ''} (${alert.ratio}x threshold) [${reading.collectDate}]`
        );
      } else {
        signals.push(
          `ABOVE NORMAL ${reading.analyte} at ${reading.location}, ${reading.state}: ` +
          `${reading.result} ${reading.unit || ''} (${alert.ratio}x normal) [${reading.collectDate}]`
        );
      }
    }
  }

  // Summarize by state
  const byState = {};
  for (const r of readings) {
    const st = r.state || 'UNK';
    if (!byState[st]) byState[st] = { count: 0, analytes: new Set() };
    byState[st].count++;
    if (r.analyte) byState[st].analytes.add(r.analyte);
  }

  // Convert sets to arrays for JSON
  const stateSummary = Object.fromEntries(
    Object.entries(byState).map(([st, info]) => [
      st,
      { count: info.count, analytes: [...info.analytes] },
    ])
  );

  return {
    source: 'EPA RadNet',
    timestamp: new Date().toISOString(),
    totalReadings: readings.length,
    readings: readings.slice(0, 50), // cap for briefing size
    stateSummary,
    signals: signals.length > 0
      ? signals
      : ['All EPA RadNet readings within normal background levels'],
    monitoredAnalytes: KEY_ANALYTES,
    thresholds: THRESHOLDS,
    note: 'RadNet data may lag by hours to days. Near-real-time gamma data updates more frequently.',
  };
}

// Run standalone
if (process.argv[1]?.endsWith('epa.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
