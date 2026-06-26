// R-F1965 — node:test for the §25 delivery-outcome classifier. Run: node --test
import test from 'node:test';
import assert from 'node:assert';
import { classifyDeliveryOutcome, isDegraded, degradedDetail } from './deliveryOutcome.mjs';

test('a real answer is delivered_real_answer', () => {
  assert.equal(classifyDeliveryOutcome({ response: 'Here is the analysis…' }), 'delivered_real_answer');
  assert.equal(isDegraded({ response: 'ok' }), false);
});

test('degraded (LLM unavailable) → error, NOT delivered_real_answer (the bug)', () => {
  // The shape aria_engine returns when all providers are exhausted.
  const degraded = { response: '⚠️ ARIA degraded mode — Cannot reason…', degraded: true,
                     degradation_reason: 'all_providers_exhausted' };
  assert.equal(classifyDeliveryOutcome(degraded), 'error');
  assert.equal(isDegraded(degraded), true);
});

test('llm_failure timeout → timeout_fallback (today\'s aria_llm outage shape)', () => {
  const failed = { response: '⚠️ The LLM request took longer…', llm_failure: true,
                   llm_error_kind: 'LLM_TIMEOUT' };
  assert.equal(classifyDeliveryOutcome(failed), 'timeout_fallback');
});

test('llm_failure billing/other → error', () => {
  assert.equal(classifyDeliveryOutcome({ llm_failure: true, llm_error_kind: 'LLM_BILLING' }), 'error');
  assert.equal(classifyDeliveryOutcome({ llm_failure: true, llm_error_kind: 'LLM_OTHER' }), 'error');
});

test('degradedDetail surfaces the reason only when degraded', () => {
  assert.equal(degradedDetail({ response: 'ok' }), '');
  assert.equal(degradedDetail({ degraded: true, degradation_reason: 'no_llm' }), 'no_llm');
});
