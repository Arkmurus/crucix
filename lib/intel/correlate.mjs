// lib/intel/correlate.mjs
// Cross-signal correlation engine
// Detects when multiple independent sources point to the same region/entity
// in the same sweep — convergence = higher-priority alert

import { scoreSignal } from './dedup.mjs';
import { getSourceHistory } from '../self/learning_store.mjs';
import {
  sourceQualityMultiplierForRegion,
  dampenedSourceLabel,
} from './source_taxonomy.mjs';
// Side-effect import: populates the signal-source registry. The
// correlator then iterates the registry to flatten the sweep payload
// into tagged signals, instead of hardcoding (data field → _src tag)
// pairs inline. Adding a new source = one entry in
// source_registry_bootstrap.mjs, not two edits across two files.
import './source_registry_bootstrap.mjs';
import { listSignalSources } from './source_registry.mjs';

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

// Build a source reliability gate from sweep history.
// Sources with enough history (<5 sweeps = too new to judge) and reliability
// below 40% get their score contribution halved; below 20% are excluded.
function buildReliabilityGate() {
  const gate = {};
  for (const src of getSourceHistory()) {
    const total = (src.totalOk || 0) + (src.totalFail || 0);
    if (total < 5) continue; // not enough data yet
    gate[src.name.toLowerCase()] = src.reliability ?? 100;
  }
  return gate;
}

function reliabilityMultiplier(signalSource, gate) {
  if (!signalSource || Object.keys(gate).length === 0) return 1;
  const key = signalSource.toLowerCase();
  // Try exact match first, then partial match
  let reliability = gate[key];
  if (reliability === undefined) {
    for (const [name, rel] of Object.entries(gate)) {
      if (key.includes(name) || name.includes(key)) { reliability = rel; break; }
    }
  }
  if (reliability === undefined) return 1; // unknown source — pass through
  if (reliability < 20) return 0;          // completely unreliable — exclude
  if (reliability < 40) return 0.5;        // degraded — halve contribution
  return 1;
}

// 2026-04-25: source-quality gate for geopolitical claims from commercial
// defence feeds. Distinct from reliabilityMultiplier (which gates by
// fail-history). This gate addresses the Damen-feed elevation issue: a
// shipbuilder's competitor-activity feed shouldn't drive Middle East
// correlation severity to CRITICAL on Strait of Hormuz commentary just
// because it scrapes geopolitical content adjacent to its own market view.
//
// Rule: when a signal originates from a commercial defence-industry feed
// (defence_news, arms_transfer, procurement, export_control) AND the
// content is a geopolitical claim (Hormuz / blockade / ceasefire / etc.,
// not the OEM's own market activity), halve its score contribution. The
// signal is still ingested and visible to the LLM — but it can't
// single-handedly elevate a regional correlation to CRITICAL tier.
// 2026-04-25 refactor: source-quality gating moved to declarative
// `source_taxonomy.mjs`. The two static lists that lived here (commercial
// defence source-tags + geopolitical content keywords) became hard to
// maintain — every new source / new region required two list edits.
// Now `sourceQualityMultiplierForRegion(srcTag, region)` consults a
// per-class table that declares which correlation use-cases each source
// class is authoritative on. Outside that competence, dampen.

// Main correlation function
export function correlate(data) {
  const correlations = {};
  const reliabilityGate = buildReliabilityGate();

  // Collect all signals from registered sources. Each registered source
  // declares (tag, getSignalsFromData, optional mapper); we flatten the
  // sweep payload through them and attach the `_src` tag uniformly.
  const allSignals = [];
  for (const src of listSignalSources()) {
    const items = src.getSignalsFromData(data);
    if (!items || items.length === 0) continue;
    for (const raw of items) {
      const shaped = src.mapper ? src.mapper(raw) : raw;
      allSignals.push({ ...shaped, _src: src.tag });
    }
  }

  // Group signals by region
  for (const signal of allSignals) {
    const regions = detectRegion(signal);
    const scored  = scoreSignal(signal);

    // Reliability gate: downweight or exclude signals from degraded sources
    const srcName = signal.source || signal._src || signal.channel || '';
    const rMult   = reliabilityMultiplier(srcName, reliabilityGate);
    if (rMult === 0) continue; // exclude completely unreliable source
    const baseScore = scored.score;

    for (const region of regions) {
      // Source-quality gate per (source-class × region-use-case). A
      // defence_news signal contributes at full weight to the
      // "Defence Procurement" correlation, but only at half weight to
      // "Middle East" — its source class is `commercial_defence`, which
      // is appropriate for `commercial_correlation` but not
      // `geopolitical_correlation`. Replaces the keyword-list heuristic
      // (af9b50f) with a declarative taxonomy lookup (2026-04-25).
      const qMult = sourceQualityMultiplierForRegion(signal._src || '', region);
      const combinedMult = rMult * qMult;
      const adjustedScore = combinedMult < 1
        ? Math.round(baseScore * combinedMult)
        : baseScore;
      const dampened = qMult < 1;

      if (!correlations[region]) {
        correlations[region] = {
          region,
          signals:    [],
          sources:    new Set(),
          totalScore: 0,
          severity:   'low',
        };
      }
      correlations[region].signals.push({
        ...signal, ...scored,
        _dampened: dampened,
        _dampened_for_region: dampened ? region : null,
      });
      correlations[region].sources.add(signal._src || detectCategory(signal));
      correlations[region].totalScore += adjustedScore;
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
        .map(s => {
          const primary = s.text || s.title || s.headline || '';
          const secondary = s.content || s.description || s.summary || '';
          // Combine title + description for news-type signals to avoid showing only the headline
          const combined = primary && secondary && !primary.includes(secondary.substring(0, 40))
            ? `${primary}\n\n${secondary}`
            : primary || secondary;
          // Dampened-source caveat tag — LLM sees the source class explicitly
          // so it doesn't elevate out-of-competence claims to CRITICAL
          // editorial classification. Now driven by the source-taxonomy
          // module (2026-04-25 refactor of af9b50f).
          const sourceLabel = s._dampened
            ? dampenedSourceLabel(s._src || 'unknown', c.region)
            : s._src;
          return {
            text:   combined.substring(0, 2000),
            source: sourceLabel,
            url:    s.url || s.link || null,
            score:  s.score,
            _dampened: !!s._dampened,
          };
        }),
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
