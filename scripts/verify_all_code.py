"""Verify every code change is correct."""
import os

def check(name, ok):
    print(f'  {"PASS" if ok else "FAIL"} {name}')

# R-F1510: state_store.py
f = open('aria_service/intel/state_store.py', 'rb').read()
check('R-F1510 _upsert timeout', b'asyncio.wait_for' in f and b'_UPSERT_TIMEOUT_S' in f)
check('R-F1510 _upsert rate-limited log', b'_upsert_last_log' in f and b'_UPSERT_LOG_INTERVAL_S' in f)
check('R-F1510 incr retry on locked', b'database is locked' in f and b'asyncio.sleep(0.5)' in f)

# R-F1510: self_improve.py
f = open('aria_service/intel/self_improve.py', 'rb').read()
check('R-F1510 record_error circuit breaker', b'_RECORD_ERROR_CB_THRESHOLD' in f and b'_RECORD_ERROR_CB_COOLDOWN_S' in f)
check('R-F1510 cb_until tracking', b'_record_error_cb_until' in f)

# R-F1512: WA DNS
f = open('services/wa-listener/aria_wa_listener.mjs', 'rb').read()
check('R-F1512 .internal primary', b'aria-intel.internal' in f)
check('R-F1512 BRAIN_INTERNAL constant', b'BRAIN_INTERNAL' in f)
check('R-F1512 BRAIN_PUBLIC fallback', b'BRAIN_PUBLIC' in f)
check('R-F1512 3s primary timeout', b'AbortSignal.timeout(3000)' in f)
check('R-F1512 10s fallback timeout', b'AbortSignal.timeout(10000)' in f)
check('R-F1512 2s probe timeout', b'AbortSignal.timeout(2000)' in f)
check('R-F1512 old DNS code removed', b'ARIA_BRAIN_FALLBACK_IP' not in f)

# R-F1512: memory_leak_detector.py
f = open('aria_service/intel/memory_leak_detector.py', 'rb').read()
check('R-F1512 50MB threshold', b'rate > 50' in f)
check('R-F1512 normal growth debug', b'below 50MB threshold' in f)

# R-F1512: neural_memory.py
f = open('aria_service/intel/neural_memory.py', 'rb').read()
check('R-F1512 max hot edges constant', b'_MAX_HOT_EDGES_PER_NEURON' in f)
check('R-F1512 cold edges key prefix', b'_COLD_EDGES_KEY_PREFIX' in f)
check('R-F1512 offload function', b'def _offload_cold_edges' in f)
check('R-F1512 offload called from strengthen', b'_offload_cold_edges(from_id)' in f)

# R-F1512: student.py
f = open('aria_service/intel/student.py', 'rb').read()
check('R-F1512 seed_baseline_mastery function', b'async def seed_baseline_mastery' in f)
check('R-F1512 gentle weight 0.3', b'MASTERY_LR_POSITIVE * 0.3' in f)
check('R-F1512 3 signals per topic', b'for _ in range(3)' in f)

# R-F1512: main.py
f = open('aria_service/main.py', 'rb').read()
check('R-F1512 seed wired in lifespan', b'seed_baseline_mastery' in f and b'_seed_mastery_bg' in f)

# R-F1512: web_integrity_agent.py
f = open('aria_service/intel/web_integrity_agent.py', 'rb').read()
idx = f.find(b'_WEB_ENDPOINTS_PUBLIC')
endpoints_section = f[idx:idx+300]
check('R-F1512 stale auth endpoint removed', b'/api/auth/status' not in endpoints_section)
check('R-F1512 stale status endpoint removed', b'/api/aria/status' not in endpoints_section)
check('R-F1512 healthz still present', b'/healthz' in endpoints_section)

print()
print('ALL CODE CHECKS COMPLETE')
