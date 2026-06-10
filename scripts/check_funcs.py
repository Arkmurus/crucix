"""Verify all function calls in dd_layer_extensions exist."""
import ast

checks = {
    'sanctions': ['screen_with_aliases', 'fuzzy_screen'],
    'rca_screening': ['screen_with_relatives'],
    'fatf_typologies': ['match_typologies'],
    'crypto_sanctions': ['screen_wallet'],
    'forensic_benford': ['benford_test'],
    'counter_intelligence': ['scan_entity'],
    'economic_substance': ['score_substance'],
    'tbml_detection': ['classify_anomaly'],
}
all_ok = True
for mod_name, funcs in checks.items():
    with open(f'aria_service/intel/{mod_name}.py', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    existing = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for func in funcs:
        if func in existing:
            print(f'  OK {mod_name}.{func}')
        else:
            public = sorted([f for f in existing if not f.startswith('_')])
            print(f'  MISS {mod_name}.{func} -- available: {public[:10]}')
            all_ok = False

if all_ok:
    print('\nAll function calls verified!')
else:
    print('\nSome functions missing!')
