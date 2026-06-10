"""Verify the committed changes are bulletproof."""
import sys
import os
import py_compile

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
    for name in runners:
        fn = getattr(dd_layer_extensions, name, None)
        if callable(fn):
            print(f'  OK dd_layer_extensions.{name}')
        else:
            print(f'  MISS dd_layer_extensions.{name}')
            errors.append(name)
except Exception as e:
    print(f'  FAIL import: {e}')
    errors.append('import')

# 3. HTML structure
print("\n=== HTML ===")
with open('public/dd-reports.html', encoding='utf-8') as f:
    html = f.read()
opens = html.count('<script')
closes = html.count('</script>')
if opens == closes:
    print(f'  OK Script tags: {opens} open, {closes} close')
else:
    print(f'  FAIL Script tags: {opens} open, {closes} close')
    errors.append('script_tags')

features = [
    ('dd-full-btn', 'Full DD button'),
    ('R-F1487', 'Full DD handler'),
    ('dd-save-btn', 'Save to Report button'),
    ('dd/save-tool-result', 'Save endpoint call'),
]
for marker, name in features:
    if marker in html:
        print(f'  OK {name}')
    else:
        print(f'  MISS {name}')
        errors.append(name)

# 4. Test results
print("\n=== TESTS ===")
import subprocess
test_files = [
    'aria_service/tests/test_cap_vault_auto_populate_on_startup.py',
    'aria_service/tests/test_dd_extensions_rf584_587.py',
    'aria_service/tests/test_rf1140_dd_trigger_pipeline.py',
]
for tf in test_files:
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', tf, '-q', '--tb=line'],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        # Extract pass count
        for line in result.stdout.split('\n'):
            if 'passed' in line:
                print(f'  OK {tf}: {line.strip()}')
                break
    else:
        print(f'  FAIL {tf}: exit code {result.returncode}')
        errors.append(tf)

# 5. Summary
print("\n=== SUMMARY ===")
if errors:
    print(f'  ISSUES FOUND: {len(errors)}')
    for e in errors:
        print(f'    - {e}')
else:
    print('  ALL CHECKS PASSED - commit is bulletproof')
