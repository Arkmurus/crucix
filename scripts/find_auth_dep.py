"""Find the auth dependency in routes."""
with open('aria_service/routes/aria.py', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if '_router_auth_dep' in line:
        print(f'Line {i+1}: {line.rstrip()}')
        for j in range(i, min(i+10, len(lines))):
            print(f'  {j+1}: {lines[j].rstrip()}')
        break
