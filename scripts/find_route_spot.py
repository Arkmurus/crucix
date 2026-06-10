"""Find a good spot to add the save-pipeline-result endpoint."""
with open('aria_service/routes/aria.py', encoding='utf-8') as f:
    lines = f.readlines()

# Find the citation audit route end
for i, line in enumerate(lines):
    if 'async def citations_verify_ep' in line:
        # Find the end of this function (next @router or def at column 0)
        for j in range(i+1, min(i+30, len(lines))):
            if lines[j].strip().startswith('@router') or (lines[j].strip().startswith('async def') and j > i+1):
                print(f'Citation audit ends at line {j+1}: {lines[j].rstrip()}')
                print(f'Insert point: after line {j}')
                break
        break
