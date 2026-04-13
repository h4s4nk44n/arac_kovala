"""
CapSolver-based captcha solvers for sahibinden login.

Handles:
1. Cloudflare Turnstile (embedded on login form) - solved via CapSolver API
2. Google reCAPTCHA v2 (rare, ~<25% after login submit) - solved via CapSolver API

Note: Page-load Turnstile is handled automatically by Scrapling's solve_cloudflare=True.
This module handles the embedded Turnstile that appears on the login form itself.
"""

import re

import capsolver

import config


# ---------------------------------------------------------------------------
# Turnstile solver
# ---------------------------------------------------------------------------

def solve_turnstile_if_present(page) -> bool:
    """
    Solve embedded Cloudflare Turnstile on sahibinden's login form.

    Extracts sitekey from #cloudflareTurnStileSiteKey hidden input,
    solves via CapSolver API, injects token into form fields.

    Args:
        page: Playwright Page object (from page_action callback)

    Returns:
        True if Turnstile was found and solved, False otherwise.
    """
    api_key = config.CAPSOLVER_API_KEY
    if not api_key:
        print("[CapSolver] No CAPSOLVER_API_KEY set, skipping Turnstile solving")
        return False

    sitekey = _extract_turnstile_sitekey(page)
    if not sitekey:
        print("[CapSolver] No Turnstile sitekey found on page")
        return False

    page_url = page.url
    print(f"[CapSolver] Turnstile sitekey: {sitekey[:20]}...")

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

        # Inject token into all Turnstile-related fields
        page.evaluate("""(token) => {
            // 1. Cloudflare standard hidden input
            document.querySelectorAll(
                'input[name="cf-turnstile-response"]'
            ).forEach(el => { el.value = token; });

            // 2. Any cf-chl-widget input
            document.querySelectorAll('input[id*="cf-chl-widget"]').forEach(el => {
                el.value = token;
            });

            // 3. Sahibinden's custom token field
            const customToken = document.querySelector('#cloudflareTurnStileToken');
            if (customToken) customToken.value = token;
        }""", token)

        print("[CapSolver] Turnstile token injected")
        return True

    except Exception as e:
        print(f"[CapSolver] Error solving Turnstile: {e}")
        return False


def _extract_turnstile_sitekey(page) -> str | None:
    """Extract Cloudflare Turnstile sitekey from sahibinden's login page."""

    # Method 1: Sahibinden stores it in hidden input #cloudflareTurnStileSiteKey
    try:
        sitekey = page.evaluate("""() => {
            const el = document.querySelector('#cloudflareTurnStileSiteKey');
            return el ? el.value : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 2: data-sitekey attribute
    try:
        sitekey = page.evaluate("""() => {
            const el = document.querySelector(
                '.cf-turnstile[data-sitekey], [data-sitekey]'
            );
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 3: Search page HTML
    try:
        html = page.content()
        match = re.search(
            r'(?:data-sitekey|SiteKey)["\s]*(?:value)?[=:]\s*["\']([0-9a-zA-Z_x-]{20,})["\']',
            html
        )
        if match:
            return match.group(1)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# reCAPTCHA v2 solver
# ---------------------------------------------------------------------------

def solve_recaptcha_v2_if_present(page) -> bool:
    """
    Check if a reCAPTCHA v2 challenge is present on the page.
    If found, solve it via CapSolver API and inject the token.

    Args:
        page: Playwright Page object (from page_action callback)

    Returns:
        True if reCAPTCHA was found and solved, False otherwise.
    """
    api_key = config.CAPSOLVER_API_KEY
    if not api_key:
        print("[CapSolver] No CAPSOLVER_API_KEY set, skipping reCAPTCHA solving")
        return False

    sitekey = _extract_recaptcha_sitekey(page)
    if not sitekey:
        return False

    page_url = page.url
    print(f"[CapSolver] reCAPTCHA v2 detected (sitekey: {sitekey[:20]}...)")

    try:
        capsolver.api_key = api_key
        solution = capsolver.solve({
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        })

        token = solution.get("gRecaptchaResponse")
        if not token:
            print("[CapSolver] No solution token received")
            return False

        print(f"[CapSolver] Solution received ({len(token)} chars)")

        # Inject the token into the page
        page.evaluate("""(token) => {
            const textareas = document.querySelectorAll(
                'textarea[name="g-recaptcha-response"], #g-recaptcha-response'
            );
            textareas.forEach(ta => {
                ta.value = token;
                ta.dispatchEvent(new Event('change', {bubbles: true}));
            });

            // Try calling the callback if available
            if (typeof window.___grecaptcha_cfg !== 'undefined') {
                const clients = window.___grecaptcha_cfg.clients;
                if (clients) {
                    Object.keys(clients).forEach(key => {
                        const client = clients[key];
                        const findCallback = (obj, depth) => {
                            if (depth > 5 || !obj) return;
                            Object.keys(obj).forEach(k => {
                                if (typeof obj[k] === 'function') {
                                    try { obj[k](token); } catch(e) {}
                                } else if (typeof obj[k] === 'object') {
                                    findCallback(obj[k], depth + 1);
                                }
                            });
                        };
                        findCallback(client, 0);
                    });
                }
            }
        }""", token)

        page.wait_for_timeout(3000)
        print("[CapSolver] reCAPTCHA token injected successfully")
        return True

    except Exception as e:
        print(f"[CapSolver] Error solving reCAPTCHA: {e}")
        return False


def _extract_recaptcha_sitekey(page) -> str | None:
    """Extract reCAPTCHA v2 sitekey from the page."""

    # Method 1: data-sitekey attribute on div
    try:
        sitekey = page.evaluate("""() => {
            const el = document.querySelector('.g-recaptcha[data-sitekey], div[data-sitekey]');
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 2: reCAPTCHA iframe src parameter
    try:
        sitekey = page.evaluate("""() => {
            const iframe = document.querySelector(
                'iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha/api2"]'
            );
            if (!iframe) return null;
            const src = iframe.getAttribute('src') || '';
            const match = src.match(/[?&]k=([^&]+)/);
            return match ? match[1] : null;
        }""")
        if sitekey:
            return sitekey
    except Exception:
        pass

    # Method 3: Search page source with regex
    try:
        html = page.content()
        match = re.search(r'data-sitekey=["\']([^"\']+)["\']', html)
        if match:
            return match.group(1)
        match = re.search(r'grecaptcha\.render\([^,]*,\s*\{[^}]*sitekey\s*:\s*["\']([^"\']+)', html)
        if match:
            return match.group(1)
    except Exception:
        pass

    return None
