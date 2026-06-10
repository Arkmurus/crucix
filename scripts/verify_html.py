"""Verify the dd-reports.html changes are well-formed."""
with open('public/dd-reports.html', encoding='utf-8') as f:
    content = f.read()

opens = content.count('<script')
closes = content.count('</script>')
print(f'Script tags: {opens} open, {closes} close')

checks = {
    'Full DD button': 'dd-full-btn' in content,
    'Full DD handler': 'R-F1487' in content,
    'Save to Report button': 'dd-save-btn' in content,
    'Save endpoint': 'dd/save-tool-result' in content,
    'Pipeline tools section': 'dd-pipeline-grid' in content,
    'TOOLS array': 'const TOOLS' in content,
}
for label, found in checks.items():
    status = "OK" if found else "MISSING"
    print(f'  {label}: {status}')

# Check the new route exists
with open('aria_service/routes/aria.py', encoding='utf-8') as f:
    routes = f.read()
route_checks = {
    'Save tool result endpoint': 'dd/save-tool-result' in routes,
    'R-F1486 marker': 'R-F1486' in routes,
    'R-F1484 auto-seed': 'R-F1484' in routes,
}
for label, found in route_checks.items():
    status = "OK" if found else "MISSING"
    print(f'  Route {label}: {status}')

# Check the extension layers
with open('aria_service/intel/dd_layer_extensions.py', encoding='utf-8') as f:
    ext = f.read()
ext_checks = {
    'R-F1485 marker': 'R-F1485' in ext,
    'Sanctions divergence runner': '_run_sanctions_divergence' in ext,
    'RCA screening runner': '_run_rca_screening' in ext,
    'FATF typology runner': '_run_fatf_typology_match' in ext,
    'Economic substance runner': '_run_economic_substance' in ext,
    'TBML classifier runner': '_run_tbml_classifier' in ext,
    'Crypto wallet runner': '_run_crypto_wallet_screen' in ext,
    'Benford law runner': '_run_benford_law' in ext,
    'Counter-intel runner': '_run_counter_intel_scan' in ext,
}
for label, found in ext_checks.items():
    status = "OK" if found else "MISSING"
    print(f'  Ext {label}: {status}')
