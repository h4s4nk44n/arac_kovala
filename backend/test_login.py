"""
Login test for Scrapling + CapSolver Turnstile solving.

Usage:
    $env:SAHIBINDEN_USER="your_email"
    $env:SAHIBINDEN_PASS="your_password"
    $env:CAPSOLVER_API_KEY="your_key"
    python test_login.py
"""

import os
import sys
import re
import random
import time

import capsolver


def _extract_turnstile_sitekey(page) -> str | None:
    """Extract Cloudflare Turnstile sitekey from sahibinden's login page."""

    # Method 1: Sahibinden stores it in a hidden input #cloudflareTurnStileSiteKey
    try:
        sitekey = page.evaluate("""() => {
            const el = document.querySelector('#cloudflareTurnStileSiteKey');
            return el ? el.value : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 2: data-sitekey attribute on any element
    try:
        sitekey = page.evaluate("""() => {
            const el = document.querySelector(
                '.cf-turnstile[data-sitekey], div[data-sitekey], [data-sitekey]'
            );
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 3: Search page HTML for sitekey patterns
    try:
        html = page.content()
        match = re.search(r'(?:data-sitekey|SiteKey)["\s]*(?:value)?[=:]\s*["\']([0-9a-zA-Z_x-]{20,})["\']', html)
        if match:
            return match.group(1)
    except Exception:
        pass

    return None


def _solve_turnstile_via_capsolver(page, api_key: str) -> bool:
    """Solve embedded Turnstile captcha using CapSolver API."""

    sitekey = _extract_turnstile_sitekey(page)
    if not sitekey:
        print("[CapSolver] Could not extract Turnstile sitekey")
        return False

    page_url = page.url
    print(f"[CapSolver] Turnstile sitekey: {sitekey[:20]}...")
    print(f"[CapSolver] Solving via CapSolver API...")

    try:
        capsolver.api_key = api_key
        solution = capsolver.solve({
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        })

        token = solution.get("token")
        if not token:
            print("[CapSolver] No Turnstile token received")
            return False

        print(f"[CapSolver] Turnstile token received ({len(token)} chars)")

        # First, debug: dump the turnStileWidget attributes
        widget_info = page.evaluate("""() => {
            const w = document.querySelector('#turnStileWidget');
            if (!w) return {found: false};
            const attrs = {};
            for (const a of w.attributes) attrs[a.name] = a.value;
            return {found: true, attrs: attrs, outerHTML: w.outerHTML.substring(0, 500)};
        }""")
        print(f"[Debug] turnStileWidget: {widget_info}")

        # Inject token into all relevant fields
        inject_result = page.evaluate("""(token) => {
            const log = [];

            // 1. cf-turnstile-response (Cloudflare standard)
            document.querySelectorAll(
                'input[name="cf-turnstile-response"]'
            ).forEach(el => {
                el.value = token;
                log.push('cf-turnstile-response SET');
            });

            // 2. Any input with id containing cf-chl-widget and _response
            document.querySelectorAll('input[id*="cf-chl-widget"]').forEach(el => {
                el.value = token;
                log.push('cf-chl-widget input SET: ' + el.id);
            });

            // 3. Sahibinden's custom token field
            const customToken = document.querySelector('#cloudflareTurnStileToken');
            if (customToken) {
                customToken.value = token;
                log.push('cloudflareTurnStileToken SET');
            }

            // 4. Find and call the turnstile callback
            const widget = document.querySelector('#turnStileWidget');
            if (widget) {
                const cbName = widget.getAttribute('data-callback');
                log.push('data-callback attr: ' + cbName);
                if (cbName && typeof window[cbName] === 'function') {
                    try {
                        window[cbName](token);
                        log.push('Callback ' + cbName + ' CALLED');
                    } catch(e) {
                        log.push('Callback error: ' + e.message);
                    }
                }
            }

            // 5. Try to find ANY global function that looks like a turnstile callback
            for (const key of Object.keys(window)) {
                if (key.toLowerCase().includes('turnstile') || key.toLowerCase().includes('cfcb')) {
                    if (typeof window[key] === 'function') {
                        log.push('Found window.' + key + ' (function)');
                        try {
                            window[key](token);
                            log.push('Called window.' + key);
                        } catch(e) {
                            log.push('Error calling ' + key + ': ' + e.message);
                        }
                    }
                }
            }

            // 6. Check final state of all token fields
            const cfResp = document.querySelector('input[name="cf-turnstile-response"]');
            const customResp = document.querySelector('#cloudflareTurnStileToken');
            log.push('cf-turnstile-response value: ' + (cfResp ? cfResp.value.substring(0, 30) + '...' : 'NOT FOUND'));
            log.push('cloudflareTurnStileToken value: ' + (customResp ? customResp.value.substring(0, 30) + '...' : 'NOT FOUND'));

            return log;
        }""", token)
        for line in inject_result:
            print(f"[Debug] {line}")

        page.wait_for_timeout(1000)

        # Submit the form bypassing client-side Turnstile validation
        # The button click gets blocked by JS because the widget looks unchecked,
        # so we submit the form directly via JS
        print("[CapSolver] Token injected, submitting form via JS (bypassing client-side check)...")
        submit_result = page.evaluate("""() => {
            // Try 1: Find the login form and submit it directly
            const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                      || document.querySelector('form[action*="login"]')
                      || document.querySelector('form[action*="giris"]')
                      || document.querySelector('#loginForm');
            if (form) {
                // Remove any onsubmit handlers that might block
                form.onsubmit = null;
                form.submit();
                return 'form.submit() called';
            }

            // Try 2: Find any form on the page
            const forms = document.querySelectorAll('form');
            for (const f of forms) {
                if (f.querySelector('#username') || f.querySelector('#password')) {
                    f.onsubmit = null;
                    f.submit();
                    return 'found form with credentials, submitted';
                }
            }

            return 'no form found';
        }""")
        print(f"[Debug] Submit result: {submit_result}")
        page.wait_for_timeout(8000)

        return True

    except Exception as e:
        print(f"[CapSolver] Error solving Turnstile: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    user = os.getenv("SAHIBINDEN_USER", "")
    pw = os.getenv("SAHIBINDEN_PASS", "")
    capsolver_key = os.getenv("CAPSOLVER_API_KEY", "")

    if not user or not pw:
        print("ERROR: Set SAHIBINDEN_USER and SAHIBINDEN_PASS env vars first")
        sys.exit(1)

    if not capsolver_key:
        print("WARNING: No CAPSOLVER_API_KEY set - Turnstile solving will be skipped")
        print("  $env:CAPSOLVER_API_KEY=\"your_key\"")
        print()

    print(f"[Test] Logging in as: {user}")
    print(f"[Test] CapSolver: {'configured' if capsolver_key else 'NOT configured'}")
    print(f"[Test] Browser will be VISIBLE so you can watch")
    print()

    from scrapling.fetchers import StealthyFetcher

    result = {"success": False, "url": "", "recaptcha": False, "twofa": False, "error": None}

    def _login_action(page):
        """Interactive login flow."""

        # Dismiss cookie banner
        try:
            banner_btn = page.query_selector("#onetrust-accept-btn-handler")
            if banner_btn:
                banner_btn.click()
                page.wait_for_timeout(1000)
                print("[Test] Cookie banner dismissed")
        except Exception:
            pass

        # Wait for login form
        try:
            page.wait_for_selector("#username", timeout=15000)
        except Exception:
            print("[Test] FAIL - Login form not found")
            _save_screenshot(page, "no_form")
            result["error"] = "Login form not found"
            return

        print("[Test] Login form found! Entering credentials...")

        # Type username
        page.wait_for_timeout(500 + int(random.random() * 1000))
        page.click("#username")
        page.wait_for_timeout(300)
        page.type("#username", user, delay=50 + int(random.random() * 100))
        print("[Test] Username entered")

        page.wait_for_timeout(500 + int(random.random() * 500))

        # Type password
        page.click("#password")
        page.wait_for_timeout(300)
        page.type("#password", pw, delay=50 + int(random.random() * 100))
        print("[Test] Password entered")

        # Wait for Turnstile widget to render (it may appear before or after submit)
        page.wait_for_timeout(3000)

        # Solve Turnstile via CapSolver BEFORE submitting
        if capsolver_key:
            # Check if Turnstile is on the page (check for the sitekey input, not just iframe)
            has_turnstile = page.evaluate("""() => {
                return !!(document.querySelector('#cloudflareTurnStileSiteKey')
                       || document.querySelector('#turnStileWidget')
                       || document.querySelector('iframe[src*="challenges.cloudflare.com"]'));
            }""")
            if has_turnstile:
                print("[Test] Turnstile detected on form, solving via CapSolver...")
                _solve_turnstile_via_capsolver(page, capsolver_key)
            else:
                print("[Test] No Turnstile on form")

        # Now submit the form via form.submit() to bypass client-side JS validation
        page.wait_for_timeout(1000 + int(random.random() * 1000))
        print("[Test] Submitting form...")
        page.evaluate("""() => {
            const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                      || document.querySelector('form');
            if (form) {
                form.onsubmit = null;
                form.submit();
            }
        }""")

        # Wait for redirect / server response
        page.wait_for_timeout(8000)

        # If still on login page, Turnstile may have appeared AFTER submit — try again
        current = (page.url or "").lower()
        if "giris" in current or "login" in current:
            turnstile_after = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if turnstile_after and capsolver_key:
                print("[Test] Still on login page, Turnstile found AFTER submit, solving again...")
                _solve_turnstile_via_capsolver(page, capsolver_key)
                # Re-submit
                page.evaluate("""() => {
                    const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                              || document.querySelector('form');
                    if (form) { form.onsubmit = null; form.submit(); }
                }""")
                page.wait_for_timeout(8000)

        # Check for reCAPTCHA v2
        try:
            recaptcha_iframe = page.query_selector(
                'iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha/api2"]'
            )
            if recaptcha_iframe:
                result["recaptcha"] = True
                print("[Test] reCAPTCHA v2 detected (normal ~25% of the time)")
        except Exception:
            pass

        page.wait_for_timeout(3000)

        # Check final URL
        current_url = (page.url or "").lower()
        result["url"] = current_url
        print(f"[Test] Final URL: {current_url}")

        if "iki-asamali" in current_url or "twofactor" in current_url:
            result["twofa"] = True
            print("[Test] 2FA page detected")

        if "login" not in current_url and "giris" not in current_url:
            result["success"] = True

        # Save cookies if login succeeded
        if result["success"]:
            try:
                cookies = page.context.cookies()
                print(f"[Test] Got {len(cookies)} cookies from session")
            except Exception:
                pass

        _save_screenshot(page, "result")

    def _save_screenshot(page, name):
        os.makedirs("diag", exist_ok=True)
        path = f"diag/test_login_{name}.png"
        try:
            page.screenshot(path=path)
            print(f"[Test] Screenshot saved: {path}")
        except Exception as e:
            print(f"[Test] Screenshot failed: {e}")

    try:
        print("[Test] Opening sahibinden.com/giris ...")
        print()

        StealthyFetcher.fetch(
            url="https://www.sahibinden.com/giris",
            headless=False,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
            network_idle=True,
            timeout=60000,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            page_action=_login_action,
        )
    except Exception as e:
        result["error"] = str(e)
        import traceback
        traceback.print_exc()

    # Print results
    print()
    print("=" * 50)
    print("RESULTS")
    print("=" * 50)

    if result["success"]:
        print("  LOGIN SUCCEEDED!")
        print(f"  Final URL: {result['url']}")
    elif result["recaptcha"]:
        print("  reCAPTCHA v2 appeared (normal ~25%)")
        print("  CapSolver handles this in production")
    elif result["twofa"]:
        print("  2FA page appeared")
    elif result["error"]:
        print(f"  ERROR: {result['error']}")
    else:
        print("  LOGIN FAILED - still on login page")
        print(f"  Final URL: {result['url']}")
        print("  Check diag/test_login_result.png")

    print()
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
