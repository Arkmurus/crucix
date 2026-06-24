"""Find the layer-5c endpoint in routes."""
with open('aria_service/routes/aria.py', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'dd_layer_5c_stats_ep' in line:
        print(f'Line {i+1}: {line.rstrip()}')
        for j in range(i-3, min(i+2, len(lines))):
            print(f'  {j+1}: {lines[j].rstrip()}')
        break
