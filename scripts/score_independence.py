"""ARIA Independence Score: 0 to 100 — verified against LIVE system."""
import json
import os
import subprocess
import urllib.request

# Get live health data
h = json.loads(urllib.request.urlopen("https://aria-intel.fly.dev/health", timeout=15).read())

score = 0
total = 0
results = []

# === LAYER 1: LLM PROVIDERS (6 points) ===
providers = h.get("llm_chain", {}).get("active_providers", [])
chain_order = h.get("llm_chain", {}).get("chain_order", [])
serving = h.get("llm_chain", {}).get("serving_provider", "")

total += 1
if "ollama" in providers:
    score += 1
    results.append(("ollama_configured", True, "Ollama is in the active chain"))
else:
    results.append(("ollama_configured", False, "No free LLM provider configured"))

total += 1
if chain_order and chain_order[0] == "ollama":
    score += 1
    results.append(("free_is_primary", True, f"Chain starts with ollama: {' -> '.join(chain_order)}"))
else:
    results.append(("free_is_primary", False, f"Chain starts with {chain_order[0] if chain_order else 'none'}"))

total += 1
if serving == "ollama":
    score += 1
    results.append(("free_is_serving", True, "Ollama is the serving provider"))
else:
    results.append(("free_is_serving", False, f"{serving} is serving"))

total += 1
if "deepseek" in providers:
    score += 1
    results.append(("paid_fallback_available", True, "DeepSeek available as emergency fallback"))
else:
    results.append(("paid_fallback_available", False, "No paid fallback"))

total += 1
stats = h.get("llm_fallback_stats", {})
cooling = [k for k, v in stats.items() if v.get("status") == "cooling"]
if not cooling:
    score += 1
    results.append(("no_billing_exhausted", True, "No providers in cooldown"))
else:
    results.append(("no_billing_exhausted", False, f"Providers in cooldown: {cooling}"))

total += 1
if len(providers) >= 2:
    score += 1
    results.append(("multiple_fallbacks", True, f"{len(providers)} providers in chain"))
else:
    results.append(("multiple_fallbacks", False, f"Only {len(providers)} provider"))

# === LAYER 2: AUTONOMY (6 points) ===
auto = h.get("autonomous", {})

total += 1
if auto.get("enabled"):
    score += 1
    results.append(("autonomous_enabled", True, "Self-coding loop active"))
else:
    results.append(("autonomous_enabled", False, "Autonomous mode disabled"))

total += 1
if auto.get("autonomy_level", 0) >= 3:
    score += 1
    results.append(("autonomy_level_3", True, f"Level {auto.get('autonomy_level')}"))
else:
    results.append(("autonomy_level_3", False, f"Level {auto.get('autonomy_level')}"))

total += 1
if auto.get("running"):
    score += 1
    results.append(("coder_running", True, "Coder is running"))
else:
    results.append(("coder_running", False, "Coder not running"))

total += 1
if auto.get("tasks_loaded", 0) > 0:
    score += 1
    results.append(("tasks_loaded", True, f"{auto.get('tasks_loaded')} tasks loaded"))
else:
    results.append(("tasks_loaded", False, "No tasks loaded"))

total += 1
if os.path.exists("aria_service/autonomous/sovereign_llm.py"):
    score += 1
    results.append(("sovereign_llm", True, "SovereignLLM module exists"))
else:
    results.append(("sovereign_llm", False, "SovereignLLM module missing"))

total += 1
if os.path.exists("aria_service/intel/autonomous_coder.py"):
    score += 1
    results.append(("autonomous_coder", True, "AutonomousCoder fallback exists"))
else:
    results.append(("autonomous_coder", False, "AutonomousCoder fallback missing"))

# === LAYER 3: COST CONTROLS (3 points) ===
total += 1
r = subprocess.run(["flyctl", "secrets", "list", "-a", "aria-intel"], capture_output=True, timeout=30)
secrets_text = r.stdout.decode("utf-8", errors="replace")
if "ARIA_MONTHLY_CAP_USD" in secrets_text:
    score += 1
    results.append(("cost_cap_set", True, "Monthly cap configured"))
else:
    results.append(("cost_cap_set", False, "No monthly cost cap"))

total += 1
if os.path.exists("aria_service/llm/prompt_budget.py"):
    score += 1
    results.append(("prompt_budget", True, "Prompt budget module exists"))
else:
    results.append(("prompt_budget", False, "Prompt budget module missing"))

total += 1
if os.path.exists("aria_service/llm/fallback.py"):
    with open("aria_service/llm/fallback.py", "r", encoding="utf-8", errors="replace") as f:
        fb = f.read()
    if "HARD_COOLDOWN" in fb or "cooldown" in fb.lower():
        score += 1
        results.append(("billing_cooldown", True, "Billing cooldown active"))
    else:
        results.append(("billing_cooldown", False, "No billing cooldown"))
else:
    results.append(("billing_cooldown", False, "Fallback module missing"))

# === LAYER 4: STATE BACKEND (1 point) ===
total += 1
sb = h.get("state_backend", {})
if sb.get("backend") == "sqlite" and sb.get("reachable"):
    score += 1
    results.append(("free_state_backend", True, "SQLite - no paid persistence"))
else:
    results.append(("free_state_backend", False, f"{sb.get('backend')} - may have costs"))

# === LAYER 5: CODING ENGINE (3 points) ===
total += 1
if os.path.exists("aria_service/intel/self_coding_os.py"):
    score += 1
    results.append(("self_coding_os", True, "AST composition engine exists"))
else:
    results.append(("self_coding_os", False, "SelfCodingOS missing"))

total += 1
if os.path.exists("aria_service/autonomous/gap_detector.py"):
    score += 1
    results.append(("gap_detector", True, "Gap detection pipeline exists"))
else:
    results.append(("gap_detector", False, "Gap detector missing"))

total += 1
if os.path.exists("aria_service/intel/engine_wiring.py"):
    score += 1
    results.append(("brain_wiring", True, "Brain wiring module exists"))
else:
    results.append(("brain_wiring", False, "Brain wiring missing"))

# === LAYER 6: PUBLIC ACCESS (2 points) ===
total += 1
try:
    r2 = urllib.request.urlopen("https://aria-intel.fly.dev/", timeout=10)
    if r2.status == 200:
        score += 1
        results.append(("demo_page", True, "Demo page at /"))
    else:
        results.append(("demo_page", False, f"Demo page returned {r2.status}"))
except Exception:
    results.append(("demo_page", False, "Demo page unreachable"))

total += 1
try:
    r3 = urllib.request.urlopen("https://aria-intel.fly.dev/health/live", timeout=10)
    if r3.status == 200:
        score += 1
        results.append(("health_endpoint", True, "Health endpoint at /health/live"))
    else:
        results.append(("health_endpoint", False, f"Health endpoint returned {r3.status}"))
except Exception:
    results.append(("health_endpoint", False, "Health endpoint unreachable"))

# === CALCULATE ===
pct = round(score / total * 100)

print("=" * 65)
print("  ARIA INDEPENDENCE SCORE: 0 to 100")
print("=" * 65)
print()

layers = {
    "LLM PROVIDERS": results[0:6],
    "AUTONOMY": results[6:12],
    "COST CONTROLS": results[12:15],
    "STATE BACKEND": results[15:16],
    "CODING ENGINE": results[16:19],
    "PUBLIC ACCESS": results[19:21],
}

for layer_name, layer_results in layers.items():
    layer_score = sum(1 for _, passed, _ in layer_results if passed)
    layer_total = len(layer_results)
    print(f"  [{layer_score}/{layer_total}] {layer_name}")
    for name, passed, detail in layer_results:
        icon = "OK" if passed else "  "
        print(f"     [{icon}] {name}: {detail}")
    print()

print("=" * 65)
print(f"  FINAL SCORE: {score}/{total} = {pct}%")
print()

if pct >= 90:
    print("  LEVEL: FULL INDEPENDENCE")
    print("  ARIA can code, learn, and improve with $0 LLM cost.")
elif pct >= 75:
    print("  LEVEL: HIGH INDEPENDENCE")
    print("  ARIA is mostly self-sufficient. One or two gaps remain.")
elif pct >= 50:
    print("  LEVEL: PARTIAL INDEPENDENCE")
    print("  ARIA has some self-sufficiency but still depends on paid services.")
else:
    print("  LEVEL: DEPENDENT")
    print("  ARIA relies heavily on external paid services.")

print()
print("  COST PROFILE:")
if serving == "ollama":
    print("    Primary: Ollama (FREE)")
else:
    print(f"    Primary: {serving} (PAID)")
print("    Fallback: deepseek (PAID - emergency only)")
print("    Monthly target: $0-5 (only when Ollama is unavailable)")
print("=" * 65)
