"""
End-to-end scraping test: login + scrape car listings.

Usage:
    $env:SAHIBINDEN_USER="your_email"
    $env:SAHIBINDEN_PASS="your_password"
    $env:CAPSOLVER_API_KEY="your_key"
    C:\Python312\python.exe test_scrape.py
"""

import os
import sys
import json
import time
import random

sys.path.insert(0, os.path.dirname(__file__))

import config
from scrapling.fetchers import StealthyFetcher
from cookie_utils import save_cookies, load_cookies
from captcha import solve_turnstile_if_present


def login_no_proxy():
    """Login without requiring a proxy (for local testing)."""
    user = os.getenv("SAHIBINDEN_USER", "")
    pw = os.getenv("SAHIBINDEN_PASS", "")

    if not user or not pw:
        print("[Login] Missing credentials")
        return False

    result = {"success": False, "cookies": []}

    def _login_action(page):
        # Dismiss cookie banner
        try:
            btn = page.query_selector("#onetrust-accept-btn-handler")
            if btn:
                btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # Wait for form
        try:
            page.wait_for_selector("#username", timeout=15000)
        except Exception:
            print("[Login] Login form not found")
            return

        print("[Login] Form found, entering credentials...")

        # Type credentials
        page.wait_for_timeout(500 + int(random.random() * 1000))
        page.click("#username")
        page.wait_for_timeout(300)
        page.type("#username", user, delay=50 + int(random.random() * 100))

        page.wait_for_timeout(500 + int(random.random() * 500))
        page.click("#password")
        page.wait_for_timeout(300)
        page.type("#password", pw, delay=50 + int(random.random() * 100))

        # Solve Turnstile
        page.wait_for_timeout(3000)
        has_turnstile = page.evaluate("""() => {
            return !!(document.querySelector('#cloudflareTurnStileSiteKey')
                   || document.querySelector('#turnStileWidget'));
        }""")
        if has_turnstile:
            print("[Login] Solving Turnstile...")
            solve_turnstile_if_present(page)

        # Submit via form.submit()
        page.wait_for_timeout(1000 + int(random.random() * 1000))
        print("[Login] Submitting...")
        page.evaluate("""() => {
            const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                      || document.querySelector('form');
            if (form) { form.onsubmit = null; form.submit(); }
        }""")

        page.wait_for_timeout(8000)

        # Check result
        current_url = (page.url or "").lower()
        print(f"[Login] Post-login URL: {current_url}")

        if "login" not in current_url and "giris" not in current_url:
            try:
                result["cookies"] = page.context.cookies()
                result["success"] = True
                print(f"[Login] Got {len(result['cookies'])} cookies")
            except Exception as e:
                print(f"[Login] Cookie extraction failed: {e}")

    try:
        StealthyFetcher.fetch(
            url="https://www.sahibinden.com/giris",
            headless=True,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
            network_idle=True,
            timeout=60000,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            page_action=_login_action,
        )

        if result["success"] and result["cookies"]:
            save_cookies(result["cookies"], config.SESSION_COOKIE_FILE)
            print(f"[Login] Cookies saved to {config.SESSION_COOKIE_FILE}")
            return True

    except Exception as e:
        print(f"[Login] Error: {e}")
        import traceback
        traceback.print_exc()

    return False


def scrape_test(url):
    """Scrape a URL using saved cookies."""
    cookies = load_cookies(config.SESSION_COOKIE_FILE)
    if not cookies:
        print("[Scrape] No cookies!")
        return None

    scrape_data = {"needs_login": False, "html": ""}

    def _scrape_action(page):
        # Inject cookies
        try:
            page.context.add_cookies(cookies)
        except Exception as e:
            print(f"[Scrape] Cookie injection failed: {e}")

        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

        # Check if redirected to login
        cur = (page.url or "").lower()
        if "login" in cur or "giris" in cur:
            scrape_data["needs_login"] = True
            print("[Scrape] Redirected to login - cookies expired!")
            return

        # Check for rate limit
        content = (page.content() or "").lower()
        if "destek kodu:" in content:
            print("[Scrape] Rate limited!")
            scrape_data["needs_login"] = True
            return

        print(f"[Scrape] Page loaded: {page.url}")

    response = StealthyFetcher.fetch(
        url=url,
        headless=True,
        solve_cloudflare=True,
        block_webrtc=True,
        hide_canvas=True,
        network_idle=True,
        timeout=60000,
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        page_action=_scrape_action,
    )

    if scrape_data["needs_login"]:
        return None

    return response


def main():
    print("=" * 60)
    print("End-to-End Scraping Test")
    print("=" * 60)

    # Step 1: Login
    print("\n--- Step 1: Login ---")
    cookies = load_cookies(config.SESSION_COOKIE_FILE)
    if cookies:
        age_min = (time.time() - os.path.getmtime(config.SESSION_COOKIE_FILE)) / 60
        print(f"  Found {len(cookies)} cookies ({age_min:.0f} min old)")
        if age_min < 60 * config.COOKIE_REFRESH_HOURS:
            print("  Cookies are fresh, skipping login")
        else:
            print("  Cookies expired, re-logging in...")
            if not login_no_proxy():
                print("  Login FAILED!")
                return 1
    else:
        print("  No cookies, logging in...")
        if not login_no_proxy():
            print("  Login FAILED!")
            return 1

    # Step 2: Scrape
    print("\n--- Step 2: Scrape car listings ---")
    test_url = "https://www.sahibinden.com/otomobil?sorting=date_desc&pagingSize=20"
    print(f"  URL: {test_url}")

    response = scrape_test(test_url)
    if response is None:
        print("  Scrape returned None (needs login or rate limited)")
        return 1

    # Step 3: Parse results
    print("\n--- Step 3: Parse listings ---")
    posts = response.css('tr.searchResultsItem')
    print(f"  Found {len(posts)} listing rows")

    if not posts:
        print("  WARNING: No listings found!")
        # Save diagnostic
        os.makedirs("diag", exist_ok=True)
        html = str(response.html_content) if hasattr(response, 'html_content') else str(response.body)
        with open("diag/scrape_result.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("  Saved HTML to diag/scrape_result.html for inspection")
        return 1

    parsed = 0
    for i, post in enumerate(posts[:10]):
        try:
            post_id = post.attrib.get('data-id', '')
            post_class = post.attrib.get('class', '')
            if 'nativeAd' in post_class:
                continue

            title_els = post.css('a.classifiedTitle')
            if not title_els:
                continue
            title = (title_els[0].text or '').strip()
            if not title:
                continue

            price_els = post.css('.searchResultsPriceValue span, td.searchResultsPriceValue div span')
            price = (price_els[0].text or '').strip() if price_els else 'N/A'

            location_els = post.css('.searchResultsLocationValue')
            location = (location_els[0].text or '').strip().replace('\n', ' ') if location_els else 'N/A'

            model_els = post.css('.searchResultsTagAttributeValue, .searchResultsAttributeValue')
            model = (model_els[0].text or '').strip() if model_els else ''

            print(f"  {parsed+1}. [{post_id}] {title[:50]}")
            print(f"     Price: {price} | Model: {model} | Location: {location}")
            parsed += 1
        except Exception as e:
            print(f"  Error parsing post {i}: {e}")

    print(f"\n  Parsed {parsed} listings successfully")
    print("\n  SCRAPING TEST PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
