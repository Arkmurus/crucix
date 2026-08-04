// A SKIPPED layer must not render a score, and a HARD_STOP must not coexist
// with a CLEAN screen of the same list.
//
// Both defects were MEASURED on delivered DD run dd_29368fbb8b3d (2026-08-03).

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { ddReportSections } from '../lib/reports/pdf_generator.mjs';

describe('a skipped layer reports no score', () => {
  // dd_schema.CommercialCoherenceSection declares coherence_score = 1.0 and
  // tier = "GREEN", so "never ran" and "ran and found nothing wrong" are the
  // same object. The delivered PDF printed:
  //     COMMERCIAL COHERENCE / SKIPPED / Coherence Score 1 / Tier GREEN
  const skippedLayer = {
    meta: { status: 'skipped' },
    coherence_score: 1.0,
    tier: 'GREEN',
    findings: [],
  };

  it('withholds the defaulted scalars', () => {
    const secs = ddReportSections({ commercial_coherence: skippedLayer });
    const cc = secs.find((s) => /coherence/i.test(s.title));
    assert.ok(cc, 'the layer should still appear — it is skipped, not hidden');
    assert.deepEqual(cc.facts, [], (
      'a skipped layer rendered its dataclass defaults as measurements; '
      + 'coherence_score=1.0/GREEN is the BEST possible result, so a check that '
      + 'never ran read as a perfect one'
    ));
  });

  it('still shows the layer AS skipped — nothing is hidden', () => {
    const secs = ddReportSections({ commercial_coherence: skippedLayer });
    const cc = secs.find((s) => /coherence/i.test(s.title));
    assert.match(String(cc.status), /skip/i,
      'suppressing the score must not suppress the fact that it was skipped');
  });

  it('a COMPLETED layer still reports its score', () => {
    const secs = ddReportSections({
      commercial_coherence: {
        meta: { status: 'completed' },
        coherence_score: 0.4,
        tier: 'HIGH',
        findings: [],
      },
    });
    const cc = secs.find((s) => /coherence/i.test(s.title));
    const flat = JSON.stringify(cc.facts);
    assert.match(flat, /HIGH|0\.4/, (
      'guard against over-correction — a real measurement must survive'
    ));
  });
});
