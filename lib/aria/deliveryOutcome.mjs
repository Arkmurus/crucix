// R-F1965 — §25 delivery-outcome classification (shared by WA + web surfaces).
//
// ROOT CAUSE this fixes: when the LLM chain is unavailable, the brain returns a
// DEGRADED non-answer — aria_chat returns {response, degraded:true,
// degradation_reason} (aria_engine.py) or {response:"⚠️…", llm_failure:true,
// llm_error_kind} (routes/aria.py chat_ep) — as a normal HTTP 200. The surfaces
// extracted only `.response` and recorded the §25 outcome as
// `delivered_real_answer`, so a non-answer (e.g. today's 8-min aria_llm timeout →
// "Cannot reason without my brain… local storage" dump) was logged as a SUCCESS.
// ARIA was blind to her own failure. These helpers read the flag the brain
// already sets so the surface records the TRUTH (timeout_fallback / error).

export function isDegraded(result) {
  return !!(result && (result.degraded || result.llm_failure));
}

// Map a brain chat result to a §25 outcome:
//   delivered_real_answer | timeout_fallback | error
export function classifyDeliveryOutcome(result) {
  const r = result || {};
  if (r.degraded || r.llm_failure) {
    const kind = String(r.llm_error_kind || r.degradation_reason || '').toLowerCase();
    return (kind.includes('timeout') || kind.includes('abort')) ? 'timeout_fallback' : 'error';
  }
  return 'delivered_real_answer';
}

// Short human detail for the outcome ledger when degraded (else '').
export function degradedDetail(result) {
  const r = result || {};
  if (!isDegraded(r)) return '';
  return String(r.llm_error_kind || r.degradation_reason || 'degraded').slice(0, 200);
}
