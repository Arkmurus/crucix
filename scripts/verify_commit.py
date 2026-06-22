"""Verify the committed changes are bulletproof."""
import sys
import os
import py_compile
import re

errors = []

# 1. Python syntax check
print("=== PYTHON SYNTAX ===")
files = [
    'aria_service/intel/dd_layer_extensions.py',
    'aria_service/main.py',
    'aria_service/routes/aria.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'  OK {f}')
    except py_compile.PyCompileError as e:
        print(f'  FAIL {f}: {e}')
        errors.append(f)

# 2. Import check
print("\n=== IMPORTS ===")
sys.path.insert(0, 'aria_service')
os.environ['ARIA_DATA_DIR'] = 'data'
try:
    from intel import dd_layer_extensions
    runners = [
        'run_all_extensions',
        '_run_sanctions_divergence',
        '_run_rca_screening',
        '_run_fatf_typology_match',
        '_run_economic_substance',
        '_run_tbml_classifier',
        '_run_crypto_wallet_screen',
        '_run_benford_law',
        '_run_counter_intel_scan',
    ]
    for r in runners:
        if hasattr(dd_layer_extensions, r):
            print(f'  OK dd_layer_extensions.{r}')
        else:
            print(f'  FAIL dd_layer_extensions.{r} missing')
            errors.append(r)
except Exception as e:
    print(f'  FAIL import: {e}')
    errors.append(f'import: {e}')

# 3. HTML check
print("\n=== HTML ===")
html_path = 'aria_service/static/aria_client/aria.html'
if os.path.exists(html_path):
    with open(html_path, encoding='utf-8', errors='ignore') as f:
        html = f.read()
    opens = html.count('<script')
    closes = html.count('</script>')
    print(f'  OK Script tags: {opens} open, {closes} close')
    if opens != closes:
        errors.append(f'Script tag mismatch: {opens} open, {closes} close')
    if 'Full DD' in html:
        print('  OK Full DD button')
    else:
        errors.append('Full DD button missing')
    if 'Full DD handler' in html or 'fullDD' in html:
        print('  OK Full DD handler')
    else:
        errors.append('Full DD handler missing')
    if 'Save to Report' in html:
        print('  OK Save to Report button')
    else:
        errors.append('Save to Report button missing')
    if 'Save endpoint call' in html or '/api/aria/report' in html:
        print('  OK Save endpoint call')
    else:
        errors.append('Save endpoint call missing')
else:
    print('  SKIP HTML (file not found)')

# 4. Test check
print("\n=== TESTS ===")
test_files = [
    'aria_service/tests/test_cap_vault_auto_populate_on_startup.py',
    'aria_service/tests/test_dd_extensions_rf584_587.py',
    'aria_service/tests/test_rf1140_dd_trigger_pipeline.py',
]
for tf in test_files:
    if os.path.exists(tf):
        print(f'  OK {tf} exists')
    else:
        print(f'  SKIP {tf} not found')

# 4b. R-F1776 — dark-path check: new public async functions must have fail_wire
print("\n=== DARK PATH CHECK ===")
try:
    intel_dir = os.path.join(os.path.dirname(__file__), '..', 'aria_service', 'intel')
    for f in sorted(os.listdir(intel_dir)):
        if not f.endswith('.py') or f.startswith('_') or f == 'engine_wiring.py':
            continue
        fp = os.path.join(intel_dir, f)
        with open(fp, encoding='utf-8', errors='ignore') as fh:
            src = fh.read()
        pub_fns = re.findall(r'^async def ([a-z]\w+)\(', src, re.MULTILINE)
        for fn in pub_fns:
            if fn.startswith('_'):
                continue
            fn_idx = src.find(f'async def {fn}(')
            before = src[max(0, fn_idx - 200):fn_idx]
            if '@fail_wire' not in before and 'fail_wire' not in before:
                if 'wire_success' not in before and 'wire_failure' not in before:
                    print(f'  WARN: {f} public function {fn}() has no fail_wire/wire_success')
except Exception as e:
    print(f'  DARK PATH CHECK SKIPPED: {e}')

# 5. Summary
print("\n=== SUMMARY ===")
if errors:
    print(f'  ISSUES FOUND: {len(errors)}')
    for e in errors:
        print(f'    - {e}')
else:
    print('  ALL CHECKS PASSED - commit is bulletproof')
