"""Replace all fetch(BRAIN_URL) calls with brainFetch."""
with open('services/wa-listener/aria_wa_listener.mjs', encoding='utf-8') as f:
    content = f.read()

# Replace all fetch(`${BRAIN_URL}...`) with brainFetch(`...`)
import re
content = re.sub(
    r'fetch\(`\$\{BRAIN_URL\}([^`]+)`\)',
    r'brainFetch(`\1`)',
    content
)

with open('services/wa-listener/aria_wa_listener.mjs', 'w', encoding='utf-8') as f:
    f.write(content)

# Verify
count = content.count('brainFetch(')
old_count = content.count('fetch(`${BRAIN_URL}')
print(f'Replaced {count} calls with brainFetch')
print(f'Remaining old-style calls: {old_count}')
