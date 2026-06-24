"""R-F1510: Trigger the portal requirements email to the operator."""
import asyncio
import json
import os
import sys

# Ensure we can import from the project
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ARIA_EMAIL_OUTBOUND_ENABLED", "1")

from aria_service.intel.portal_registry import email_portal_requirements_to_operator


async def main():
    result = await email_portal_requirements_to_operator()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
