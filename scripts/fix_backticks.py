"""Fix escaped backticks in the replaced file."""
with open('services/wa-listener/aria_wa_listener.mjs', encoding='utf-8') as f:
    content = f.read()

# Replace escaped backticks with real backticks
content = content.replace('\\`', '`')

with open('services/wa-listener/aria_wa_listener.mjs', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed escaped backticks')
