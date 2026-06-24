"""Find all fetch calls using BRAIN_URL."""
import re
with open('services/wa-listener/aria_wa_listener.mjs', encoding='utf-8') as f:
    content = f.read()
for m in re.finditer(r'fetch\(`\$\{BRAIN_URL\}([^`]+)`', content):
    path = m.group(1)
    print(f'fetch(`${{BRAIN_URL}}{path}`)')
