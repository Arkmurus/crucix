// lib/intel/source_taxonomy.mjs
// Declarative source classification — one place that maps every sweep-side
// `_src` tag to (class, appropriate_uses).
//
// Why this exists:
// Before 2026-04-25 the correlator distinguished sources only by reliability
// history (success/fail rate). All sources were treated as equally
// authoritative on every topic, so a Dutch shipbuilder's competitor-activity
// feed (Damen) could elevate a Middle East geopolitical correlation to
// CRITICAL on a single Strait-of-Hormuz mention. The first fix was a
// hardcoded source-tag list + keyword list inside correlate.mjs (commit
// af9b50f). That worked but was a heuristic, not a structure — adding new
// sources or topic classes meant editing two lists in the correlator.
//
// This module replaces those hardcoded lists with a single declarative
// table. Each source declares:
//   class:           what KIND of source it is
//   appropriate_uses: which correlation types it should feed at full weight
//
// When a signal's source-class is NOT listed as appropriate for a given
// correlation use-case, the correlator dampens its score contribution
// (currently 0.5×). The signal still appears in topSignals — the LLM gets
// the data with a source-class caveat — but it can't single-handedly
// elevate cluster severity outside its competence.
//
// Adding a new source: append to `SOURCE_CLASSES` below. No code change
// elsewhere is needed.

// ── Source classes ────────────────────────────────────────────────────────
// Defines the universe of source kinds. `appropriate_uses` is the set of
// correlation use-cases this class is authoritative on; outside those it
// gets dampened.
export const SOURCE_CLASSES = {
  primary_geopolitical: {
    description: 'First-hand reporting of geopolitical events from primary news / OSINT / diplomatic / conflict-tracker sources',
    appropriate_uses: new Set([
      'geopolitical_correlation',  // Middle East / Eastern Europe / etc.
      'threat_correlation',         // cyber + maritime threats
      'policy_correlation',         // sanctions + UNSC
    ]),
  },
  commercial_defence: {
    description: 'Defence-industry news, OEM activity feeds, arms-transfer trackers, procurement portals — authoritative on market/competitor activity, NOT on raw geopolitics',
    appropriate_uses: new Set([
      'commercial_correlation',     // Defence Procurement, OEM moves
    ]),
  },
  analytical: {
    description: 'Think-tank reports, central-bank releases — authoritative on policy interpretation and macro analysis',
    appropriate_uses: new Set([
      'geopolitical_correlation',
      'policy_correlation',
      'commercial_correlation',
    ]),
  },
  trade_finance: {
    description: 'Trade flow data, commodity prices, financial market signals',
    appropriate_uses: new Set([
      'threat_correlation',         // energy/maritime price shocks
      'policy_correlation',
    ]),
  },
  osint_aggregator: {
    description: 'Mixed-quality OSINT (Telegram channels, news aggregators) — gets reliability gating elsewhere',
    appropriate_uses: new Set([
      'geopolitical_correlation',
      'threat_correlation',
    ]),
  },
  exploration: {
    description: 'Spider/explorer outputs — appropriate to inform any correlation but never as sole basis',
    appropriate_uses: new Set([
      'geopolitical_correlation',
      'commercial_correlation',
      'policy_correlation',
      'threat_correlation',
    ]),
  },
};

// ── Source-tag → class table ──────────────────────────────────────────────
// Maps the `_src` tag set in correlate.mjs:91-110 to a class above.
// Unmapped sources default to 'osint_aggregator' (mixed-quality assumption).
export const SOURCE_TAG_TO_CLASS = {
  // Primary geopolitical
  osint:           'primary_geopolitical',
  conflict:        'primary_geopolitical',
  diplomatic:      'primary_geopolitical',
  sanctions:       'primary_geopolitical',
  news:            'primary_geopolitical',

  // Commercial defence
  defence_news:    'commercial_defence',
  arms_transfer:   'commercial_defence',
  procurement:     'commercial_defence',
  export_control:  'commercial_defence',

  // Analytical
  analytical:      'analytical',
  financial:       'analytical',

  // Trade / finance
  trade:           'trade_finance',

  // Exploration
  explorer:        'exploration',
};

// ── Region → correlation-use-case map ─────────────────────────────────────
// Each correlation region has a primary "use-case" type. A signal's source-
// class either is or isn't in this region's use-case `appropriate_uses` set.
// If not — dampen.
//
// Geographic regions are geopolitical_correlation use-cases by default. The
// thematic regions (Cyber/Global, Defence Procurement, Energy/Maritime) map
// to their specific use-cases.
export const REGION_USE_CASE = {
  'Middle East':           'geopolitical_correlation',
  'Eastern Europe':        'geopolitical_correlation',
  'East Asia':             'geopolitical_correlation',
  'South Asia':            'geopolitical_correlation',
  'Lusophone Africa':      'geopolitical_correlation',
  'West Africa':           'geopolitical_correlation',
  'East / Central Africa': 'geopolitical_correlation',
  'Latin America':         'geopolitical_correlation',
  'Cyber / Global':        'threat_correlation',
  'Energy / Maritime':     'threat_correlation',
  'Defence Procurement':   'commercial_correlation',
};

// ── Public API ─────────────────────────────────────────────────────────────

/** Look up the class string for a `_src` tag. */
export function classifySource(srcTag) {
  if (!srcTag) return 'osint_aggregator';
  return SOURCE_TAG_TO_CLASS[srcTag] || 'osint_aggregator';
}

/**
 * Source-quality multiplier for a given (signal source, correlation region) pair.
 *
 * Returns 1.0 when the source class is appropriate for the region's
 * correlation use-case (full weight), 0.5 when not (dampened — the signal
 * still appears in context but can't single-handedly elevate severity).
 *
 * Replaces the hardcoded keyword-based dampener from af9b50f. Now driven
 * entirely by the declarative table above; new sources/regions just need
 * to be added to the maps.
 */
export function sourceQualityMultiplierForRegion(srcTag, region) {
  const cls = classifySource(srcTag);
  const useCase = REGION_USE_CASE[region];
  if (!useCase) return 1; // Unknown region — pass through (don't penalise)
  const classDef = SOURCE_CLASSES[cls];
  if (!classDef) return 1; // Unknown class — pass through
  return classDef.appropriate_uses.has(useCase) ? 1 : 0.5;
}

/**
 * Human-readable caveat that the correlator embeds in topSignals when a
 * signal was dampened. The LLM sees this inline so it doesn't elevate a
 * dampened signal's correlation to CRITICAL editorial classification.
 */
export function dampenedSourceLabel(srcTag, region) {
  const cls = classifySource(srcTag);
  const useCase = REGION_USE_CASE[region] || 'general';
  return `${srcTag} (${cls} source — appropriate for ${
    Array.from(SOURCE_CLASSES[cls]?.appropriate_uses || []).join(', ') || 'general use'
  }; this region's use-case is ${useCase}, so treat as adjacent commentary, not primary reporting)`;
}
