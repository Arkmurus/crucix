"""Verify the fix."""
import sys
sys.path.insert(0, 'aria_service')

import importlib

# Clear any cached modules
for mod in list(sys.modules.keys()):
    if 'aria_service' in mod:
        del sys.modules[mod]

mod = importlib.import_module('aria_service.intel.confidence_footer')
print("wire_success in dir:", 'wire_success' in dir(mod))

if 'wire_success' in dir(mod):
    print("SUCCESS: wire_success is now accessible")
    result = mod.build_footer(
        response_text='Test response with enough chars to trigger footer X' * 3,
        verification=None,
        tools_used=['test_tool'],
    )
    print("build_footer succeeded:", repr(result[:100]))
else:
    print("FAILED: wire_success still not accessible")
    for name in sorted(dir(mod)):
        if not name.startswith('_'):
            print(f"  {name}")
