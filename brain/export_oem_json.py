"""
Export OEM Database v2 to JSON — consumed by Node.js oem_db.mjs

Run: python brain/export_oem_json.py
Output: brain/oem_data_v2.json

The Node.js app loads this JSON at startup, giving it access to the
richer Python OEM data (export regimes, product lines, revenue, contracts)
without running Python in production.

When ARIA migrates to Python, she imports oem_database_v2.py directly.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from oem_database_v2 import OEMDatabase
import json

def export():
    db = OEMDatabase()
    output_path = os.path.join(os.path.dirname(__file__), 'oem_data_v2.json')

    data = {
        "version": "2.0",
        "generated": __import__('datetime').datetime.now().isoformat(),
        "stats": db.stats(),
        "oems": json.loads(db.to_json()),
        "monitoring_sources": __import__('oem_database_v2').OEM_MONITORING_SOURCES,
    }

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Exported {db.count()} OEMs to {output_path}")
    print(f"Stats: {json.dumps(db.stats(), indent=2)}")

if __name__ == "__main__":
    export()
