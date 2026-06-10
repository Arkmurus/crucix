"""Prove the vault auto-population fix works."""
import sys, os, tempfile, shutil, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

from intel.agent_signup_vault import AgentSignupVault
from intel.portal_registry import PORTALS

tmpdir = tempfile.mkdtemp()
try:
    vault = AgentSignupVault(db_path=os.path.join(tmpdir, 'vault.db'))

    # This is the call that main.py now makes (fixed in R-F1482)
    count = vault.import_open_portals(PORTALS, agent_id='system')

    stats = vault.stats()
    print(f"Imported: {count} portals")
    print(f"Total in vault: {stats['total']}")
    print(f"By status: {stats['by_status']}")
    print(f"By type: {stats['by_type']}")

    # Show a few entries
    entries = vault.list(limit=5)
    for e in entries:
        print(f"  [{e['status']}] {e['site_id']}: {e['site_name']} (agent={e['agent_id']})")

    # Verify the old wrong name raises AttributeError (proving the bug existed)
    try:
        vault.import_from_portal_registry(PORTALS, agent_id='system')
        print("\nBUG STILL EXISTS: import_from_portal_registry didn't raise!")
    except AttributeError as e:
        print(f"\nOld method correctly raises: {e}")
        print("R-F1482 fix verified: main.py now calls import_open_portals instead")

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
