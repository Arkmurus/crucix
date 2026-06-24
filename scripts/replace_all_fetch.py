"""Replace all fetch(BRAIN_URL) with brainFetch."""
with open('services/wa-listener/aria_wa_listener.mjs', encoding='utf-8') as f:
    content = f.read()

# Replace all fetch(`${BRAIN_URL}...`) with brainFetch(`...`)
# The pattern is: fetch(`${BRAIN_URL}SOMEPATH`, OPTIONS)
import re
content = re.sub(
    r'fetch\(`\$\{BRAIN_URL\}(/health/live|/api/aria/[^`]+)`\s*,\s*\{',
    r'brainFetch(\`\1\`, {',
    content
)

# Also handle the ones without options object
content = re.sub(
    r'fetch\(`\$\{BRAIN_URL\}(/health/live|/api/aria/[^`]+)`\)',
    r'brainFetch(\`\1\`)',
    content
)

# Handle the ${path} variants
content = re.sub(
    r'fetch\(`\$\{BRAIN_URL\}\$\{path\}`\s*,\s*\{',
    r'brainFetch(path, {',
    content
)

with open('services/wa-listener/aria_wa_listener.mjs', 'w', encoding='utf-8') as f:
    f.write(content)

# Count remaining
remaining = content.count('fetch(`${BRAIN_URL}')
print(f'Remaining old-style calls: {remaining}')
if remaining == 0:
    print('All calls replaced!')
else:
    # Show remaining
    for i, line in enumerate(content.split('\n')):
        if 'fetch(`${BRAIN_URL}' in line:
            print(f'  Line {i+1}: {line.strip()[:100]}')
