// R-F355 (2026-05-12) — BD Brain JSON repair Fix 6 + Fix 7 tests.
//
// Live failure (seenode log 06:41:48 2026-05-12):
//   [BD] JSON repair failed after 5 strategies. Input preview: { "weeklyPriority": ...
//   [BD Brain] LLM reasoning failed (non-fatal): JSON parse failed after 5 repair strategies:
//     Expected ',' or '}' after property value in JSON at position 13664 (line 1 column 13665)
//
// The pre-R-F355 5-strategy chain (clean / Fix 1-5) couldn't recover the
// LLM's response when it contained unescaped embedded quotes inside a
// string value. Fix 6 adds an unescaped-quote-aware walker; Fix 7 adds
// position-based truncation as last-resort.

import test from 'node:test';
import assert from 'node:assert/strict';
import { _parseLlmJson } from '../lib/self/bd_intelligence.mjs';


test('R-F355 Fix 6: parses LLM JSON with unescaped embedded quotes inside string value', () => {
  // Real-world LLM pattern: emits `"foo "bar" baz"` without escaping
  // the inner quotes. Pre-Fix-6 the parser saw the inner `"` as a
  // string terminator and choked.
  const malformed = '{"action": "Initiate outreach via "trusted intermediary" channel", "owner": "Antonio"}';
  // Pre-Fix-6: JSON.parse throws "Expected ',' or '}' after property value"
  assert.throws(() => JSON.parse(malformed));
  // Post-Fix-6: should recover by escaping the embedded quotes
  const parsed = _parseLlmJson(malformed);
  assert.ok(parsed, 'must return an object');
  assert.ok(parsed.action, 'action field must survive');
  assert.equal(parsed.owner, 'Antonio');
  // The action value should include the embedded-quote content
  assert.match(parsed.action, /trusted intermediary/);
});


test('R-F355 Fix 6: multiple embedded quotes recovered cleanly', () => {
  const malformed = '{"summary": "Kenya is a major "non-NATO" ally hosting "Camp Simba" base", "rank": "high"}';
  const parsed = _parseLlmJson(malformed);
  assert.equal(parsed.rank, 'high');
  assert.match(parsed.summary, /non-NATO/);
  assert.match(parsed.summary, /Camp Simba/);
});


test('R-F355 Fix 7: position-based truncation rescues partial JSON when later content is malformed', () => {
  // Construct a case where Fix 1-6 can't salvage everything but Fix 7
  // can slice at the last clean comma and preserve the prefix.
  // Pattern: valid fields, then a broken value with an unbalanced bracket.
  const broken = '{"good_field": "valid", "next": [1, 2, 3, broken_token_no_quotes_here}';
  let parsed;
  try {
    parsed = _parseLlmJson(broken);
  } catch (e) {
    // Acceptable to throw if truly unrecoverable — Fix 7 is best-effort,
    // not guaranteed recovery. The test pins that EITHER Fix 7 saves the
    // prefix OR we throw with the new 7-strategy error message (not 5).
    assert.match(e.message, /7 repair strategies/,
      'must indicate Fix 7 was attempted (not just first 5)');
    return;
  }
  // If we did recover, good_field must be preserved
  assert.equal(parsed.good_field, 'valid');
});


test('R-F355: existing test fixtures still parse (regression check)', () => {
  // Common cases that Fix 1-5 already handled — must keep working.
  const cleanJson = '{"a": 1, "b": "two"}';
  assert.equal(_parseLlmJson(cleanJson).a, 1);

  const trailingComma = '{"a": 1, "b": "two",}';
  assert.equal(_parseLlmJson(trailingComma).b, 'two');

  const markdownFence = '```json\n{"a": 1}\n```';
  assert.equal(_parseLlmJson(markdownFence).a, 1);

  const unquotedKey = '{a: 1, b: "two"}';
  assert.equal(_parseLlmJson(unquotedKey).a, 1);

  const truncated = '{"a": 1, "b": "two';  // Fix 4 stack repair
  const parsed = _parseLlmJson(truncated);
  assert.equal(parsed.a, 1);
  // b may be the truncated value with an added closing quote
  assert.match(parsed.b, /two/);
});


test('R-F355 capability: reconstructed BD failure shape from 06:41:48 log', () => {
  // Reconstruct the actual failure shape from the seenode log. The LLM
  // emitted a "weeklyPriority.action" value containing parens + semicolons
  // + an embedded quote somewhere around position 13664. Pre-Fix-6 the
  // parser failed; post-Fix-6 it should recover or at minimum extract
  // the surrounding object structure (Fix 7).
  const reconstructed = `{
    "weeklyPriority": {
      "action": "Initiate direct outreach to Kenya Defence Forces procurement directorate via trusted intermediary for integrated border security and UAV programme, leveraging Paramount Group relationship as endorsed "OEM partner" to pre-empt Turkish inroads and frame proposal benefiting from UK (Kenya is a major non-NATO ally and hosts Camp Simba); emphasise risk of Turkish dependency and lack of technology transfer",
      "owner": "Antonio",
      "deadline": "2026-05-19"
    },
    "rationale": "Time-sensitive geopolitical window post-AfDB summit",
    "confidence": 0.78
  }`;
  // Confirm vanilla JSON.parse fails (embedded "OEM partner" quote)
  assert.throws(() => JSON.parse(reconstructed));
  // Post-Fix-6 should recover the full object
  const parsed = _parseLlmJson(reconstructed);
  assert.ok(parsed.weeklyPriority, 'weeklyPriority must survive recovery');
  assert.equal(parsed.weeklyPriority.owner, 'Antonio');
  assert.equal(parsed.confidence, 0.78);
  assert.match(parsed.weeklyPriority.action, /Paramount Group/);
});
