// lib/intel/source_registry_bootstrap.mjs
// One-time registration of all current sweep-side signal sources.
//
// Importing this module triggers side-effect registrations into
// source_registry.mjs. The correlator imports this once at module load
// to populate the registry before the first correlate() call.
//
// Why a bootstrap file (rather than registering inside each
// apis/sources/*.mjs module): those modules don't currently know about
// `_src` tags or the correlator data-field paths — those are
// composer-level concerns assigned in server.mjs / sweep orchestration.
// Centralising registration here keeps the composer-side knowledge in
// one place, while still removing the "two tables in lockstep" smell
// that prompted the refactor: adding a new source = ONE entry here.
//
// Adding a new source:
//   registerSignalSource({
//     tag: 'my_new_source',
//     sourceClass: 'primary_geopolitical',  // see source_taxonomy.mjs SOURCE_CLASSES
//     getSignalsFromData: (data) => data?.myNewSource?.updates || [],
//     description: 'What this source feeds and where it comes from',
//   });

import { registerSignalSource } from './source_registry.mjs';

// ── Primary geopolitical ─────────────────────────────────────────────────
registerSignalSource({
  tag: 'osint',
  sourceClass: 'primary_geopolitical',
  description: 'Telegram urgent OSINT channels',
  getSignalsFromData: (data) => data?.tg?.urgent || [],
});

registerSignalSource({
  tag: 'news',
  sourceClass: 'primary_geopolitical',
  description: 'newsFeed + news primary reporting',
  // Both data.newsFeed and data.news flow into the same `news` tag —
  // emit them together so the registry stays one-entry-per-tag.
  getSignalsFromData: (data) => [
    ...(data?.newsFeed || []),
    ...(data?.news || []),
  ],
});

registerSignalSource({
  tag: 'sanctions',
  sourceClass: 'primary_geopolitical',
  description: 'OFAC / UNSC / FCDO sanctions updates',
  getSignalsFromData: (data) => data?.sanctions?.updates || [],
});

registerSignalSource({
  tag: 'conflict',
  sourceClass: 'primary_geopolitical',
  description: 'GDELT + ACLED conflict-event signals',
  getSignalsFromData: (data) => [
    ...(data?.gdelt?.updates || []),
    ...(data?.acled?.deadliestEvents || []),
  ],
});

registerSignalSource({
  tag: 'diplomatic',
  sourceClass: 'primary_geopolitical',
  description: 'UN Security Council resolutions / press releases',
  getSignalsFromData: (data) => data?.unsc?.updates || [],
});

// ── Analytical ───────────────────────────────────────────────────────────
registerSignalSource({
  tag: 'financial',
  sourceClass: 'analytical',
  description: 'Central bank releases / monetary policy decisions',
  getSignalsFromData: (data) => data?.centralBanks?.updates || [],
});

registerSignalSource({
  tag: 'analytical',
  sourceClass: 'analytical',
  description: 'Think-tank reports and policy interpretation',
  getSignalsFromData: (data) => data?.thinkTanks?.updates || [],
});

// ── Trade / finance ──────────────────────────────────────────────────────
registerSignalSource({
  tag: 'trade',
  sourceClass: 'trade_finance',
  description: 'Trade flow data, commodity prices',
  getSignalsFromData: (data) => data?.tradeFlows?.flows || [],
});

// ── Commercial defence ───────────────────────────────────────────────────
registerSignalSource({
  tag: 'defence_news',
  sourceClass: 'commercial_defence',
  description: 'Defence-industry news (Janes, DefenseNews, Damen feeds, etc.)',
  getSignalsFromData: (data) => data?.defenseNews?.updates || [],
});

registerSignalSource({
  tag: 'arms_transfer',
  sourceClass: 'commercial_defence',
  description: 'SIPRI arms-transfer database updates',
  getSignalsFromData: (data) => data?.sipriArms?.updates || [],
});

registerSignalSource({
  tag: 'procurement',
  sourceClass: 'commercial_defence',
  description: 'Defence procurement portal feeds',
  getSignalsFromData: (data) => data?.procurementTenders?.updates || [],
});

registerSignalSource({
  tag: 'export_control',
  sourceClass: 'commercial_defence',
  description: 'Export-control alerts and dual-use list changes',
  getSignalsFromData: (data) => data?.exportControlIntel?.alerts || [],
});

// ── Exploration ──────────────────────────────────────────────────────────
registerSignalSource({
  tag: 'explorer',
  sourceClass: 'exploration',
  description: 'Spider/explorer findings — general-purpose, never sole basis',
  // Explorer findings need reshaping into the standard signal shape
  // before being tagged. Mapper runs before _src attachment.
  getSignalsFromData: (data) =>
    data?.explorerFindings?.findings?.insights ||
    data?.explorerFindings?.insights ||
    [],
  mapper: (s) => ({
    title: s.title,
    text: s.summary,
    url: s.sourceUrl,
  }),
});
