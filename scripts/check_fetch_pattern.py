"""Check the exact fetch pattern."""
with open('services/wa-listener/aria_wa_listener.mjs', encoding='utf-8') as f:
    content = f.read()
idx = content.find('fetch(')
if idx >= 0:
    snippet = content[idx:idx+120]
    print(repr(snippet))
