// lib/intel/source_registry.mjs
// Single registration point for sweep-side signal sources.
//
// Why this exists:
// Before this module existed, adding a new source meant editing two
// central files in lockstep:
//   1. lib/intel/correlate.mjs — append a `_src`-tagged spread to the
//      `allSignals` flatten list, mapping a data field to a tag.
//   2. lib/intel/source_taxonomy.mjs — append the new tag to
//      SOURCE_TAG_TO_CLASS so the source-quality gate knew its class.
//
// Each new source had to coordinate two centrally-maintained tables.
// That's the architectural smell the 2026-04-25 TODO flagged: "register
// at ingest, not retroactively". This module collapses both edits into
// one `registerSignalSource()` call. The correlator iterates the
// registry at correlate-time; source_taxonomy.classifySource consults
// it before its hardcoded fallback.
//
// New source flow:
//   import { registerSignalSource } from './source_registry.mjs';
//   registerSignalSource({
//     tag: 'my_new_source',
//     sourceClass: 'primary_geopolitical',
//     getSignalsFromData: (data) => data?.myNewSource?.updates || [],
//   });
//
// That's it — the correlator picks up the new source on next sweep, and
// the source-quality gate knows its class.

const REGISTRY = [];

/**
 * Register a signal source with the correlator and the source-quality
 * taxonomy. Idempotent on `tag` — re-registration overwrites.
 *
 * @param {object} spec
 * @param {string} spec.tag             The `_src` tag (e.g. 'defence_news').
 * @param {string} spec.sourceClass     Key in source_taxonomy SOURCE_CLASSES.
 * @param {function} spec.getSignalsFromData
 *   (data) => Array<signal>. Pulls this source's signals out of the
 *   sweep payload. Must return [] when the source's data field is absent.
 * @param {string} [spec.description]   Optional human-readable note.
 * @param {function} [spec.mapper]      Optional per-signal transform applied
 *   AFTER getSignalsFromData but BEFORE the `_src` tag is attached. Used by
 *   sources whose raw shape needs reshaping (e.g. explorer findings).
 */
export function registerSignalSource(spec) {
  if (!spec || !spec.tag) throw new Error('registerSignalSource: tag is required');
  if (!spec.sourceClass) throw new Error(`registerSignalSource(${spec.tag}): sourceClass is required`);
  if (typeof spec.getSignalsFromData !== 'function') {
    throw new Error(`registerSignalSource(${spec.tag}): getSignalsFromData must be a function`);
  }
  const idx = REGISTRY.findIndex(r => r.tag === spec.tag);
  const entry = {
    tag: spec.tag,
    sourceClass: spec.sourceClass,
    description: spec.description || '',
    getSignalsFromData: spec.getSignalsFromData,
    mapper: spec.mapper || null,
  };
  if (idx >= 0) REGISTRY[idx] = entry;
  else REGISTRY.push(entry);
}

/** All registered signal sources, in registration order. */
export function listSignalSources() {
  return REGISTRY.slice();
}

/** Look up a source by tag. Returns undefined for unknown tags. */
export function getSignalSource(tag) {
  return REGISTRY.find(r => r.tag === tag);
}

/** Class string for a tag, or undefined if not registered. */
export function getSourceClassForTag(tag) {
  return REGISTRY.find(r => r.tag === tag)?.sourceClass;
}

/** Test-only: clear the registry. Production code never calls this. */
export function _resetRegistryForTests() {
  REGISTRY.length = 0;
}
