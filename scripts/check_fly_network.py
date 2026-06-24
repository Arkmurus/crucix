"""Check Fly network configuration for cross-app connectivity."""
import json
import subprocess
import sys

def run_flyctl(args):
    result = subprocess.run(['flyctl'] + args, capture_output=True, text=True, timeout=30)
    out = result.stdout.lstrip('\ufeff')
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        print(f'STDOUT: {out[:500]}')
        print(f'STDERR: {result.stderr[:500]}')
        return None

# Check WA app
wa = run_flyctl(['status', '-a', 'aria-wa', '--json'])
if wa and 'Machines' in wa:
    m = wa['Machines'][0]
    print(f'WA: region={m.get("region")}, private_ip={m.get("private_ip")}, state={m.get("state")}')

# Check intel app
intel = run_flyctl(['status', '-a', 'aria-intel', '--json'])
if intel and 'Machines' in intel:
    m = intel['Machines'][0]
    print(f'Intel: region={m.get("region")}, private_ip={m.get("private_ip")}, state={m.get("state")}')

# Check org
print(f'WA org: {wa.get("Organization", {}).get("slug") if wa else "N/A"}')
print(f'Intel org: {intel.get("Organization", {}).get("slug") if intel else "N/A"}')
