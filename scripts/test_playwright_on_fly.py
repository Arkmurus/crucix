"""Test Playwright form fill on the Fly server."""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

async def main():
    from intel.scraper.playwright_engine import fetch as pw_fetch
    
    # Test 1: Can Playwright load a registration page?
    print("Test 1: Loading govtribe.com/signup...")
    try:
        r = await pw_fetch("https://govtribe.com/signup", timeout=30.0, wait_for="networkidle")
        print(f"  ok={r.ok}, blocked={r.blocked}, text_len={len(r.text) if r.text else 0}")
        if r.text:
            # Check for common form elements
            if 'email' in r.text.lower()[:2000]:
                print("  Form has email field: YES")
            if 'password' in r.text.lower()[:2000]:
                print("  Form has password field: YES")
    except Exception as e:
        print(f"  FAILED: {e}")
    
    # Test 2: Can Playwright load a simpler page?
    print("\nTest 2: Loading httpbin.org...")
    try:
        r = await pw_fetch("https://httpbin.org/html", timeout=15.0, wait_for="networkidle")
        print(f"  ok={r.ok}, blocked={r.blocked}, text_len={len(r.text) if r.text else 0}")
    except Exception as e:
        print(f"  FAILED: {e}")
    
    # Test 3: Check if playwright browser is available
    print("\nTest 3: Checking Playwright browser...")
    try:
        import playwright
        print(f"  playwright version: {playwright.__version__}")
    except Exception as e:
        print(f"  playwright import failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
