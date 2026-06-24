"""Generate base64-encoded Python to run on the fly server."""
import base64

code = (
    'import asyncio,json,os,sys;'
    'sys.path.insert(0,"/app");'
    'os.environ["ARIA_EMAIL_OUTBOUND_ENABLED"]="1";'
    'from aria_service.intel.portal_registry import email_portal_requirements_to_operator;'
    'r=asyncio.run(email_portal_requirements_to_operator());'
    'print(json.dumps(r,indent=2,default=str))'
)
print(base64.b64encode(code.encode()).decode())
