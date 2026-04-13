"""
Smoke tests for the SeleniumBase → Scrapling migration.
Run with: python test_scrapling.py

Tests (no credentials or captcha service needed):
1. Import check - all modules import without errors
2. StealthyFetcher basic fetch - can open a page and parse HTML
3. page_action callback - Playwright Page object works inside callback
4. Cookie save/load - cookie persistence works correctly
5. Element parsing - Scrapling Adaptor API works for CSS selectors
6. Proxy URL format - proxy_utils returns correct format
7. Sahibinden homepage - can fetch sahibinden.com without getting blocked
"""

import os
import sys
import json
import time
import tempfile

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def test_imports():
    """Test 1: All modules import without errors."""
    print("\n--- Test 1: Import check ---")
    errors = []

    try:
        from scrapling.fetchers import StealthyFetcher
        print(f"  {PASS} scrapling.fetchers.StealthyFetcher")
    except Exception as e:
        errors.append(f"StealthyFetcher: {e}")
        print(f"  {FAIL} scrapling.fetchers.StealthyFetcher: {e}")

    try:
        import capsolver
        print(f"  {PASS} capsolver")
    except Exception as e:
        errors.append(f"capsolver: {e}")
        print(f"  {FAIL} capsolver: {e}")

    # Test our own modules
    for mod_name in ['config', 'proxy_utils', 'cookie_utils', 'image_utils', 'captcha', 'scraper']:
        try:
            __import__(mod_name)
            print(f"  {PASS} {mod_name}")
        except Exception as e:
            errors.append(f"{mod_name}: {e}")
            print(f"  {FAIL} {mod_name}: {e}")

    return len(errors) == 0


def test_basic_fetch():
    """Test 2: StealthyFetcher can fetch a simple page."""
    print("\n--- Test 2: Basic StealthyFetcher fetch ---")
    try:
        from scrapling.fetchers import StealthyFetcher

        page = StealthyFetcher.fetch(
            url="https://httpbin.org/html",
            headless=True,
            timeout=30000,
            network_idle=True,
        )

        # Check we got HTML back
        html = str(page.html_content) if hasattr(page, 'html_content') else str(page.body)
        assert len(html) > 100, f"HTML too short: {len(html)} chars"
        print(f"  {PASS} Fetched page ({len(html)} chars)")

        # Check element parsing works
        h1_elements = page.css('h1')
        assert len(h1_elements) > 0, "No <h1> elements found"
        h1_text = h1_elements[0].text
        print(f"  {PASS} CSS selector works: h1 = '{h1_text}'")

        return True
    except Exception as e:
        print(f"  {FAIL} {e}")
        import traceback
        traceback.print_exc()
        return False


def test_page_action_callback():
    """Test 3: page_action callback receives Playwright Page object."""
    print("\n--- Test 3: page_action callback ---")
    try:
        from scrapling.fetchers import StealthyFetcher

        callback_result = {"called": False, "url": None, "title": None, "has_context": False}

        def _test_action(page):
            callback_result["called"] = True
            callback_result["url"] = page.url
            callback_result["title"] = page.title()
            callback_result["has_context"] = hasattr(page, 'context') and page.context is not None

        StealthyFetcher.fetch(
            url="https://httpbin.org/html",
            headless=True,
            timeout=30000,
            page_action=_test_action,
        )

        assert callback_result["called"], "page_action was never called"
        print(f"  {PASS} page_action was called")

        assert callback_result["url"], "page.url is empty"
        print(f"  {PASS} page.url = {callback_result['url']}")

        assert callback_result["has_context"], "page.context is not accessible"
        print(f"  {PASS} page.context is accessible (needed for cookies)")

        return True
    except Exception as e:
        print(f"  {FAIL} {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cookie_save_load():
    """Test 4: Cookie save/load roundtrip."""
    print("\n--- Test 4: Cookie save/load ---")
    try:
        from cookie_utils import save_cookies, load_cookies

        # Simulate Playwright cookie format
        test_cookies = [
            {
                "name": "session_id",
                "value": "abc123",
                "domain": ".example.com",
                "path": "/",
                "expires": time.time() + 86400,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            },
            {
                "name": "pref",
                "value": "dark_mode",
                "domain": ".example.com",
                "path": "/",
                "expires": -1,
                "secure": False,
            },
        ]

        # Save to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            tmp_path = f.name

        save_cookies(test_cookies, tmp_path)
        print(f"  {PASS} Cookies saved to {tmp_path}")

        # Load back
        loaded = load_cookies(tmp_path)
        assert len(loaded) == 2, f"Expected 2 cookies, got {len(loaded)}"
        assert loaded[0]["name"] == "session_id"
        assert loaded[0]["value"] == "abc123"
        assert loaded[0]["domain"] == ".example.com"
        assert loaded[0]["secure"] == True
        print(f"  {PASS} Cookies loaded back correctly ({len(loaded)} cookies)")

        # Test backward compatibility with SeleniumBase format (expiry vs expires)
        legacy_cookies = [
            {
                "name": "old_cookie",
                "value": "legacy_val",
                "domain": ".sahibinden.com",
                "path": "/",
                "expiry": 1999999999,  # SeleniumBase uses 'expiry'
                "secure": False,
            }
        ]
        save_cookies(legacy_cookies, tmp_path)
        loaded_legacy = load_cookies(tmp_path)
        assert len(loaded_legacy) == 1
        assert loaded_legacy[0].get("expires") == 1999999999.0
        print(f"  {PASS} Legacy SeleniumBase cookie format (expiry->expires) handled")

        # Cleanup
        os.unlink(tmp_path)
        return True
    except Exception as e:
        print(f"  {FAIL} {e}")
        import traceback
        traceback.print_exc()
        return False


def test_proxy_url_format():
    """Test 5: proxy_utils returns correct URL format."""
    print("\n--- Test 5: Proxy URL format ---")
    try:
        from proxy_utils import _get_proxy_url

        # This will return None if no proxy env vars are set
        result = _get_proxy_url(rotate_session=False)

        if result is None:
            print(f"  {SKIP} No proxy configured (IPROYAL_PROXY not set) — format check skipped")
            print(f"  {PASS} Function runs without error")
            return True

        assert result.startswith("http://"), f"Proxy URL should start with http://, got: {result}"
        print(f"  {PASS} Proxy URL format correct: {result[:30]}...")
        return True
    except Exception as e:
        print(f"  {FAIL} {e}")
        return False


def test_sahibinden_fetch():
    """Test 6: Can fetch sahibinden.com homepage without being blocked."""
    print("\n--- Test 6: Sahibinden homepage fetch (anti-detection test) ---")
    try:
        from scrapling.fetchers import StealthyFetcher

        page_info = {"url": None, "blocked": False}

        def _check_action(page):
            page_info["url"] = page.url
            content = page.content().lower()
            # Check for Cloudflare block page indicators
            if "just a moment" in content or "attention required" in content:
                page_info["blocked"] = True

        page = StealthyFetcher.fetch(
            url="https://www.sahibinden.com/",
            headless=True,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
            network_idle=True,
            timeout=60000,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            page_action=_check_action,
        )

        html = str(page.html_content) if hasattr(page, 'html_content') else str(page.body)

        if page_info["blocked"]:
            print(f"  {FAIL} Cloudflare blocked the request")
            return False

        # Check for sahibinden content
        has_content = len(html) > 1000
        has_sahibinden = "sahibinden" in html.lower()

        if has_content and has_sahibinden:
            print(f"  {PASS} Sahibinden homepage loaded ({len(html)} chars)")
            print(f"  {PASS} No Cloudflare block detected")
            print(f"  {PASS} Final URL: {page_info['url']}")
            return True
        else:
            print(f"  {FAIL} Page loaded but content looks wrong ({len(html)} chars)")
            return False
    except Exception as e:
        print(f"  {FAIL} {e}")
        import traceback
        traceback.print_exc()
        return False


def test_element_parsing():
    """Test 7: Scrapling Adaptor API matches what scraper.py expects."""
    print("\n--- Test 7: Element parsing (Adaptor API) ---")
    try:
        from scrapling.fetchers import StealthyFetcher

        # Fetch a page with known structure
        page = StealthyFetcher.fetch(
            url="https://httpbin.org/html",
            headless=True,
            timeout=30000,
        )

        # Test .css() selector
        paragraphs = page.css('p')
        assert len(paragraphs) > 0, "No <p> elements found"
        print(f"  {PASS} page.css('p') returned {len(paragraphs)} elements")

        # Test .text property
        text = paragraphs[0].text
        assert text and len(text) > 0, "Element .text is empty"
        print(f"  {PASS} element.text works: '{text[:50]}...'")

        # Test .attrib access
        links = page.css('a')
        if links:
            href = links[0].attrib.get('href', '')
            print(f"  {PASS} element.attrib.get('href') = '{href}'")
        else:
            print(f"  {SKIP} No <a> elements to test attrib on")

        # Test nested CSS
        body = page.css('body')
        if body:
            nested_p = body[0].css('p')
            assert len(nested_p) > 0, "Nested CSS selector failed"
            print(f"  {PASS} Nested element.css('p') works")

        return True
    except Exception as e:
        print(f"  {FAIL} {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Scrapling Migration Smoke Tests")
    print("=" * 60)

    results = {}

    results["imports"] = test_imports()
    results["basic_fetch"] = test_basic_fetch()
    results["page_action"] = test_page_action_callback()
    results["cookie_save_load"] = test_cookie_save_load()
    results["proxy_format"] = test_proxy_url_format()
    results["element_parsing"] = test_element_parsing()
    results["sahibinden_fetch"] = test_sahibinden_fetch()

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, ok in results.items():
        status = PASS if ok else FAIL
        print(f"  {status} {name}")

    print(f"\n{passed}/{total} tests passed")

    if passed < total:
        sys.exit(1)
