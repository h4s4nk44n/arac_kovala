from seleniumbase import SB
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS
import threading
import time
import uuid
import json
import os
import re
import random
from urllib.parse import urlparse, urlsplit
from datetime import datetime, timezone
import mimetypes
import sys
import secrets
import requests
from twocaptcha import TwoCaptcha



PUSH_TOKENS = set()

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')
HTML_SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'html_snapshots')

# Ensure diagnostic directories exist
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(HTML_SNAPSHOTS_DIR, exist_ok=True)

DATA_DIR = os.getenv('RAILWAY_VOLUME_MOUNT_PATH', os.path.dirname(__file__))
print(f"Using data directory: {DATA_DIR}")
os.makedirs(DATA_DIR, exist_ok=True) # Ensure the directory exists

KNOWN_IDS_FILE = os.path.join(DATA_DIR, 'known_ids.json')
PUSH_TOKENS_FILE = os.path.join(DATA_DIR, 'push_tokens.json')
FILTERS_FILE = os.path.join(DATA_DIR, 'filters.json')
POSTS_FILE = os.path.join(DATA_DIR, 'posts.json')
KNOWN_IDS_FILE = os.path.join(DATA_DIR, 'known_ids.json')
IMAGES_DIR = os.path.join(DATA_DIR, 'images')
os.makedirs(IMAGES_DIR, exist_ok=True)


# ---- Env / flags ----
def _env_true(name: str, default="0"):
    return os.getenv(name, default) in ("1", "true", "True", "YES", "yes")

ALLOW_LOGIN = _env_true("ALLOW_LOGIN", "1")  # you said it must log in when needed
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "1"))  # per process lifetime
LOGIN_COOLDOWN_SEC = int(os.getenv("LOGIN_COOLDOWN_SEC", "5"))  # 5 sec cooldown
# Persist session cookies on the data volume by default
SESSION_COOKIE_FILE = os.getenv("SESSION_COOKIE_FILE", os.path.join(DATA_DIR, "session_cookies.json"))

# CAPTCHA handling
CAPTCHA_MAX_RELOAD_CLICKS = int(os.getenv("CAPTCHA_MAX_RELOAD_CLICKS", "20"))
LOGIN_RECLICK_RETRIES = int(os.getenv("LOGIN_RECLICK_RETRIES", "2"))
LOGIN_RECLICK_WAIT_SEC = int(os.getenv("LOGIN_RECLICK_WAIT_SEC", "3"))


class NeedsLogin(Exception):
    pass


def _parse_netscape_cookies_txt(text: str, domain_filter: str = "sahibinden.com"):
    """Parse cookies.txt; omit 'domain' for host-only cookies to avoid domain mismatch."""
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, flag, path, secure, expiry, name, value = parts
        if domain_filter and (domain_filter not in domain):
            continue

        c = {
            "name": name,
            "value": value,
            "path": path or "/",
            "secure": (secure.upper() == "TRUE"),
        }
        if domain.startswith("."):
            c["domain"] = domain  # shared cookie ok
        try:
            exp = int(expiry)
            if exp > 0:
                c["expiry"] = exp
        except Exception:
            pass
        c["_host_hint"] = domain.lstrip(".")  # for routing
        cookies.append(c)
    return cookies

def _add_cookies_for_host(sb, host_url: str, cookies: list):
    """Navigate to host_url, then add cookies. Host-only cookies only on exact host."""
    try:
        sb.get(host_url)
        time.sleep(0.5)
    except Exception as e:
        print("navigate failed:", host_url, e)
        return
    current_host = urlsplit(host_url).hostname or ""
    for c in cookies:
        cookie = {k: v for k, v in c.items() if not k.startswith("_")}
        host_hint = c.get("_host_hint", "")
        is_host_only = ("domain" not in cookie)
        if is_host_only and host_hint and host_hint!= current_host:
            continue
        try:
            sb.add_cookie(cookie)
        except Exception as e:
            print("cookie add failed:", cookie.get("name"), e)


def _prepare_cookies(cookies_raw):
    """Normalize a list of cookie dicts into the format sb.add_cookie expects.
    Adds a helper _host_hint for host-only routing.
    """
    prepared = []
    for cookie in cookies_raw or []:
        try:
            clean_cookie = {"name": cookie.get("name"), "value": cookie.get("value")}
            if cookie.get("path"): clean_cookie["path"] = cookie["path"]
            if "secure" in cookie: clean_cookie["secure"] = bool(cookie.get("secure"))
            if "expiry" in cookie and isinstance(cookie["expiry"], (int, float)):
                clean_cookie["expiry"] = int(cookie["expiry"])
            elif "expirationDate" in cookie:
                exp = cookie.get("expirationDate")
                if isinstance(exp, (int, float)):
                    clean_cookie["expiry"] = int(exp)
            domain_val = cookie.get("domain")
            host_hint = ""
            if isinstance(domain_val, str) and domain_val:
                clean_cookie["domain"] = domain_val
                host_hint = domain_val.lstrip(".")
            clean_cookie["_host_hint"] = host_hint
            prepared.append(clean_cookie)
        except Exception:
            continue
    return prepared

def _prime_anon_cookies(sb):
    """
    This function is intentionally left blank.
    Session handling is now managed by scrape_sahibinden.
    """
    print("Bypassing legacy cookie priming. Session will be handled by the scraper.")
    pass

def _get_chrome_profile_dir() -> str:
    """
    Returns a persistent Chrome profile directory to increase realism across runs.
    Uses CHROME_PROFILE_DIR if provided; otherwise stores under DATA_DIR.
    Each session gets a unique subdirectory to avoid lock conflicts.
    """
    dir_from_env = os.getenv("CHROME_PROFILE_DIR")
    if not dir_from_env or not dir_from_env.strip():
        # If not set, return None to use temp profile
        return None
    
    base_profile = dir_from_env.strip()
    try:
        os.makedirs(base_profile, exist_ok=True)
        # Use a session-unique subdirectory to avoid locks
        import threading
        thread_id = threading.get_ident()
        profile_dir = os.path.join(base_profile, f"session_{thread_id}")
        os.makedirs(profile_dir, exist_ok=True)
        return profile_dir
    except Exception:
        return None

def _humanize_session(sb, moves: int = 10, dwell_time_sec=None):
    """
    Emit simple human-like signals: mouse moves and incremental scrolling.
    This avoids suspiciously static sessions.
    Args:
        sb: SeleniumBase instance
        moves: Number of random mouse moves
        dwell_time_sec: Total time to spend on page (defaults to 3-8 seconds for realistic human reading)
    """
    # Initial pause (user reading/looking at page)
    sb.sleep(0.5 + random.random() * 1.0)
    
    try:
        w = sb.execute_script("return window.innerWidth || 1200") or 1200
        h = sb.execute_script("return window.innerHeight || 800") or 800
    except Exception:
        w, h = 1200, 800

    # Random mouse path using JS events to avoid ActionChains brittleness with iframes
    try:
        x, y = random.randint(20, int(0.3 * w)), random.randint(60, int(0.5 * h))
        for _ in range(max(3, moves)):
            x = max(1, min(w - 5, x + random.randint(-80, 120)))
            y = max(1, min(h - 5, y + random.randint(-50, 90)))
            sb.execute_script(
                "window.dispatchEvent(new MouseEvent('mousemove', {bubbles:true,clientX:arguments[0],clientY:arguments[1]}));",
                x, y,
            )
            sb.sleep(0.05 + random.random() * 0.25)
    except Exception:
        pass
    
    # Random scroll to simulate reading
    try:
        scroll_amount = random.randint(100, 400)
        sb.execute_script(f"window.scrollBy({{top: {scroll_amount}, behavior: 'smooth'}});")
        sb.sleep(0.3 + random.random() * 0.5)
        sb.execute_script(f"window.scrollBy({{top: -{scroll_amount // 2}, behavior: 'smooth'}});")
    except Exception:
        pass
    
    # Additional dwell time (human reading page content)
    if dwell_time_sec is None:
        dwell_time_sec = 3.0 + random.random() * 5.0  # 3-8 seconds
    
    sb.sleep(dwell_time_sec)

def _get_chrome_args() -> list:
    """
    Chrome flags for maximum stealth and container stability.
    Allows extra args via EXTRA_CHROME_ARGS (comma-separated).
    """
    base = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",  # CRITICAL: Hide automation
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-features=IsolateOrigins,site-per-process",  # Reduce detection surface
        "--disable-site-isolation-trials",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
        "--password-store=basic",
        "--use-mock-keychain",
        "--lang=tr-TR",
        "--window-size=1920,1080",
        # Realistic user agent will be set via CDP
    ]
    
    # Headless mode if requested (new headless mode is more stealthy)
    if _env_true("HEADLESS", "0"):
        base.append("--headless=new")
    
    # Add user-provided extra args
    extra = (os.getenv("EXTRA_CHROME_ARGS") or "").strip()
    if extra:
        for token in [t.strip() for t in extra.split(",") if t.strip()]:
            if token and token not in base:
                base.append(token)
    
    print(f"Chrome args: {' '.join(base[:5])}... ({len(base)} total)")
    return base

def _is_headless() -> bool:
    return _env_true("HEADLESS", "0")

def _verify_chrome_binary():
    """Verify Chrome binary exists and is executable at startup."""
    import subprocess
    chrome_bin = os.getenv("CHROME_BIN") or os.getenv("SB_CHROME_BINARY") or "/usr/bin/google-chrome"
    
    # Check memory
    try:
        with open('/proc/meminfo', 'r') as f:
            mem_info = f.read()
            for line in mem_info.split('\n'):
                if 'MemAvailable:' in line:
                    mem_kb = int(line.split()[1])
                    mem_mb = mem_kb // 1024
                    print(f"Available memory: {mem_mb} MB")
                    if mem_mb < 300:
                        print("⚠ WARNING: Low memory! Chrome may fail to start. Recommend >= 512MB")
                    break
    except Exception:
        pass
    
    if os.path.exists(chrome_bin) and os.access(chrome_bin, os.X_OK):
        print(f"✓ Chrome binary found and executable: {chrome_bin}")
        try:
            result = subprocess.run([chrome_bin, "--version"], capture_output=True, text=True, timeout=5)
            print(f"✓ Chrome version: {result.stdout.strip()}")
            
            # Test minimal headless launch
            print("Testing Chrome minimal launch...")
            test_result = subprocess.run(
                [chrome_bin, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", 
                 "--disable-gpu", "--no-first-run", "--dump-dom", "about:blank"],
                capture_output=True, text=True, timeout=10
            )
            if test_result.returncode == 0:
                print("✓ Chrome minimal launch successful")
            else:
                print(f"⚠ Chrome launch test failed with code {test_result.returncode}")
                if test_result.stderr:
                    print(f"  stderr: {test_result.stderr[:500]}")
            return True
        except subprocess.TimeoutExpired:
            print(f"⚠ Chrome test timed out (may indicate resource starvation)")
            return False
        except Exception as e:
            print(f"⚠ Chrome test failed: {e}")
            return False
    else:
        print(f"✗ Chrome binary not found or not executable: {chrome_bin}")
        return False

    # Gentle scrolling down and up a bit
    try:
        total = 0
        for _ in range(6):
            dy = random.randint(80, 240)
            sb.execute_script("window.scrollBy(0, arguments[0]);", dy)
            total += dy
            sb.sleep(0.25 + random.random() * 0.5)
        for _ in range(2):
            dy = random.randint(60, 180)
            sb.execute_script("window.scrollBy(0, arguments[0]);", -dy)
            sb.sleep(0.25 + random.random() * 0.5)
    except Exception:
        pass

def _realistic_user_agent() -> str:
    """
    Return a modern desktop Chrome UA matching the actual Chrome version.
    Can be overridden via BROWSER_UA env.
    """
    ua_env = os.getenv("BROWSER_UA")
    if ua_env:
        return ua_env.strip()
    # Match the actual Chrome stable version (check google-chrome --version)
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

def _apply_stealth(sb):
    """
    Apply comprehensive stealth tweaks to make the browser indistinguishable from a real user.
    - Turkish locale and timezone
    - Hide all automation signals
    - Realistic navigator properties
    - WebGL/Canvas fingerprint protection
    """
    try:
        ua = _realistic_user_agent()
        
        # Enable network interception
        try:
            sb.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        
        # Set realistic headers
        try:
            sb.driver.execute_cdp_cmd(
                "Network.setExtraHTTPHeaders",
                {"headers": {
                    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "max-age=0",
                    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                }},
            )
        except Exception:
            pass
        
        # Override user agent
        try:
            sb.driver.execute_cdp_cmd(
                "Emulation.setUserAgentOverride",
                {
                    "userAgent": ua,
                    "acceptLanguage": "tr-TR,tr",
                    "platform": "Win32",
                    "userAgentMetadata": {
                        "brands": [
                            {"brand": "Google Chrome", "version": "131"},
                            {"brand": "Chromium", "version": "131"},
                            {"brand": "Not_A Brand", "version": "24"}
                        ],
                        "fullVersion": "131.0.0.0",
                        "platform": "Windows",
                        "platformVersion": "10.0.0",
                        "architecture": "x86",
                        "model": "",
                        "mobile": False,
                    }
                },
            )
        except Exception:
            pass

        # Set Turkish timezone
        try:
            sb.driver.execute_cdp_cmd(
                "Emulation.setTimezoneOverride", {"timezoneId": "Europe/Istanbul"}
            )
        except Exception:
            pass
        
        # Set realistic geolocation (Istanbul)
        try:
            sb.driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
                "latitude": 41.0082,
                "longitude": 28.9784,
                "accuracy": 100
            })
        except Exception:
            pass

        # CRITICAL: Comprehensive anti-detection script
        stealth_js = """
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            delete navigator.__proto__.webdriver;
            
            // Set realistic navigator properties
            Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
            Object.defineProperty(navigator, 'language', {get: () => 'tr-TR'});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
            
            // Fake realistic plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [
                        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                        {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}
                    ];
                    plugins.__proto__ = PluginArray.prototype;
                    return plugins;
                }
            });
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    originalQuery(parameters)
            );
            
            // WebGL vendor/renderer spoofing (realistic Intel GPU)
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) { return 'Intel Inc.'; } // UNMASKED_VENDOR_WEBGL
                if (parameter === 37446) { return 'Intel Iris OpenGL Engine'; } // UNMASKED_RENDERER_WEBGL
                return getParameter.apply(this, arguments);
            };
            
            // Canvas fingerprint noise (subtle randomization)
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                const context = this.getContext('2d');
                if (context) {
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        // Add minimal noise (undetectable to human eye)
                        imageData.data[i] += Math.floor(Math.random() * 2);
                        imageData.data[i+1] += Math.floor(Math.random() * 2);
                        imageData.data[i+2] += Math.floor(Math.random() * 2);
                    }
                    context.putImageData(imageData, 0, 0);
                }
                return originalToDataURL.apply(this, arguments);
            };
            
            // Hide automation-related properties
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
            
            // Chrome runtime
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // Screen properties (realistic 1080p)
            Object.defineProperty(window.screen, 'width', {get: () => 1920});
            Object.defineProperty(window.screen, 'height', {get: () => 1080});
            Object.defineProperty(window.screen, 'availWidth', {get: () => 1920});
            Object.defineProperty(window.screen, 'availHeight', {get: () => 1040});
            Object.defineProperty(window.screen, 'colorDepth', {get: () => 24});
            Object.defineProperty(window.screen, 'pixelDepth', {get: () => 24});
        """
        
        try:
            sb.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js}
            )
        except Exception:
            pass
            
    except Exception as e:
        print(f"Stealth application warning: {e}")

def _bypass_turnstile_if_present(sb, max_wait_seconds: int = 40) -> bool:
    """
    Detect Cloudflare Turnstile "Tarayıcınızı kontrol ediyoruz..." page and attempt to proceed.
    Returns True if we detected the page and it appears cleared; False otherwise.
    """
    def _is_challenge_page() -> bool:
        try:
            if sb.is_element_present("#btn-continue"):
                return True
        except Exception:
            pass
        try:
            if sb.is_element_present("#turnStileWidget"):
                return True
        except Exception:
            pass
        try:
            html = (sb.get_page_source() or "").lower()
            return ("tarayıcınızı kontrol ediyoruz" in html) or ("turnstile" in html)
        except Exception:
            return False

    if not _is_challenge_page():
        return False

    start = time.time()
    while time.time() - start < max_wait_seconds:
        try:
            # Click the continue button if present
            if sb.is_element_present("#btn-continue"):
                try:
                    sb.cdp.click("#btn-continue")
                except Exception:
                    try:
                        sb.js_click("#btn-continue")
                    except Exception:
                        try:
                            sb.click("#btn-continue")
                        except Exception:
                            pass

            # Give Turnstile a moment to verify in the background
            sb.sleep(1.5)

            # Heuristics: if the message/button disappears or the text no longer present, we passed
            still_button = False
            try:
                still_button = sb.is_element_present("#btn-continue")
            except Exception:
                still_button = False

            try:
                html_now = (sb.get_page_source() or "").lower()
            except Exception:
                html_now = ""

            if (not still_button) and ("tarayıcınızı kontrol ediyoruz" not in html_now):
                return True
        except Exception:
            # ignore and retry within the wait window
            pass

    return False

def _try_uc_gui_click_captcha(sb, max_wait_seconds: int = 60) -> bool:
    """
    Prefer SeleniumBase's GUI captcha clicker when available.
    Attempts to click any visible Cloudflare/Turnstile checkbox using GUI actions.
    Returns True if invoked and the challenge appears cleared; False otherwise.
    """
    # Quick presence probe
    def _challenge_present() -> bool:
        try:
            if sb.is_element_present('#btn-continue'):
                return True
        except Exception:
            pass
        try:
            ifr = sb.find_elements('css selector', 'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], iframe[title*="Cloudflare" i]')
            return bool(ifr)
        except Exception:
            return False

    def _cleared() -> bool:
        try:
            html = (sb.get_page_source() or '').lower()
        except Exception:
            html = ''
        still = _challenge_present()
        return (not still) and ('gerçek kişi olduğunuzu doğrulayın' not in html)

    if not _challenge_present():
        return False

    start = time.time()
    attempts = 0
    while time.time() - start < max_wait_seconds:
        if _cleared():
            return attempts > 0
        try:
            attempts += 1
            # Built-in SeleniumBase helper (GUI-based)
            sb.uc_gui_click_captcha()
        except Exception:
            pass
        # Also try the explicit continue button if present
        try:
            if sb.is_element_present('#btn-continue'):
                try:
                    sb.cdp.click('#btn-continue')
                except Exception:
                    try:
                        sb.js_click('#btn-continue')
                    except Exception:
                        try:
                            sb.click('#btn-continue')
                        except Exception:
                            pass
        except Exception:
            pass

        # Small human-like jitter
        try:
            sb.sleep(1.0 + (random.random() * 0.7))
        except Exception:
            pass
        if _cleared():
            return True
    return False

def _solve_cloudflare_checkbox(sb, max_wait_seconds: int = 60) -> bool:
    """
    Handle Cloudflare "I'm human" checkbox challenges - both iframe and non-iframe versions.
    Returns True if we detected and attempted to solve the challenge (and it cleared),
    False if nothing was detected or could not be solved within the time window.
    """
    def _challenge_present() -> bool:
        try:
            # Check for iframe-based challenges
            iframes = sb.find_elements('css selector',
                'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare" i], iframe[src*="turnstile"]')
            if iframes:
                print(f"[Cloudflare] Detected {len(iframes)} challenge iframe(s)")
                return True
            
            # Check for non-iframe Cloudflare challenges (direct page)
            html = (sb.get_page_source() or '').lower()
            url = (sb.get_current_url() or '').lower()
            
            # Debug: Print what we're seeing
            print(f"[Cloudflare Debug] URL: {url[:100]}...")
            
            # Cloudflare challenge indicators (multiple patterns for reliability)
            indicators = [
                ("URL contains challenges.cloudflare", "challenges.cloudflare.com" in url),
                ("URL contains /cdn-cgi/", "/cdn-cgi/" in url),
                ("HTML contains 'verify you are human'", "verify you are human" in html),
                ("HTML contains Turkish verification text", any(x in html for x in [
                    "gerçek kişi", "gercek kisi", "doğrulayın", "dogrula"
                ])),
                ("HTML contains 'cloudflare'", "cloudflare" in html),
                ("HTML contains challenge page markers", any(x in html for x in [
                    "challenge-platform", "cf-challenge", "ray id:", "checking your browser"
                ])),
                ("Page title contains Cloudflare", any(x in html for x in [
                    "<title>just a moment", "<title>bir an"
                ])),
            ]
            
            for description, condition in indicators:
                if condition:
                    print(f"[Cloudflare] ✓ Challenge detected: {description}")
                    return True
                    
            return False
        except Exception as e:
            print(f"[Cloudflare] Detection error: {e}")
            return False

    def _cleared() -> bool:
        try:
            # Heuristic: no challenge iframe visible and page text not showing the prompt
            has_iframe = False
            try:
                iframes = sb.find_elements('css selector',
                    'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare" i], iframe[src*="turnstile"]')
                has_iframe = bool(iframes)
            except Exception:
                pass
            
            html = (sb.get_page_source() or '').lower()
            url = (sb.get_current_url() or '').lower()
            
            # Challenge is cleared if:
            # - No iframe present
            # - URL doesn't contain challenges.cloudflare.com or /cdn-cgi/
            # - Page doesn't show verification prompts
            text_present = any([
                "gerçek kişi" in html,
                "gercek kisi" in html,
                "verify you are human" in html,
                "checking your browser" in html,
                ("cloudflare" in html and "challenge" in html),
                "challenges.cloudflare.com" in url,
                "/cdn-cgi/" in url,
                "ray id:" in html,
            ])
            
            cleared = (not has_iframe) and (not text_present)
            if cleared:
                print("[Cloudflare] Challenge appears to be cleared")
            return cleared
        except Exception as e:
            print(f"[Cloudflare] Clear detection error: {e}")
            return False

    if not _challenge_present():
        print("[Cloudflare] No challenge detected")
        return False
    
    print("[Cloudflare] Challenge detected! Attempting to solve...")

    start = time.time()
    while time.time() - start < max_wait_seconds:
        try:
            if _cleared():
                print("[Cloudflare] ✓ Challenge cleared!")
                return True

            # METHOD 1: Try iframe-based challenges
            frames = sb.find_elements('css selector',
                'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare" i], iframe[src*="turnstile"]')
            if frames:
                print(f"[Cloudflare] Found {len(frames)} challenge iframe(s), attempting to solve...")
                for fr in frames:
                    try:
                        sb.driver.switch_to.frame(fr)
                        for sel in (
                            'input[type="checkbox"]',
                            'div[role="checkbox"]',
                            'label[for]',
                            'button[type="submit"]',
                            '#challenge-stage input[type="checkbox"]',
                        ):
                            try:
                                if sb.is_element_present(sel):
                                    print(f"[Cloudflare] Clicking iframe element: {sel}")
                                    try:
                                        sb.cdp.click(sel)
                                    except Exception:
                                        try:
                                            sb.js_click(sel)
                                        except Exception:
                                            try:
                                                sb.click(sel)
                                            except Exception:
                                                pass
                                    sb.sleep(2)
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        print(f"[Cloudflare] Iframe solving error: {e}")
                    finally:
                        try:
                            sb.driver.switch_to.default_content()
                        except Exception:
                            pass
            
            # METHOD 2: Try non-iframe challenges (direct page buttons/checkboxes)
            # These appear when Cloudflare shows challenge directly on the page (not in iframe)
            else:
                print("[Cloudflare] No iframe detected, looking for direct page elements...")
                # Common Cloudflare challenge selectors (non-iframe)
                selectors = [
                    'input[type="checkbox"][id*="challenge"]',
                    'input[type="checkbox"][name*="cf"]',
                    'button[type="submit"]',
                    'input[type="button"][value*="Verify"]',
                    'input[type="button"][value*="Doğrula"]',
                    'label[for*="challenge"]',
                    'div[role="button"]',
                    'div.challenge-button',
                    'button.challenge-button',
                    # Very broad fallback - any visible checkbox or button
                    'input[type="checkbox"]:not([style*="display: none"])',
                    'button:not([style*="display: none"])',
                ]
                
                for sel in selectors:
                    try:
                        if sb.is_element_visible(sel):
                            print(f"[Cloudflare] Found visible element: {sel}, attempting UC click...")
                            # Use UC mode's special click methods for anti-detection
                            try:
                                sb.cdp.click(sel)
                                print(f"[Cloudflare] ✓ CDP click successful on {sel}")
                            except Exception as e1:
                                print(f"[Cloudflare] CDP click failed: {e1}, trying JS click...")
                                try:
                                    sb.js_click(sel)
                                    print(f"[Cloudflare] ✓ JS click successful on {sel}")
                                except Exception as e2:
                                    print(f"[Cloudflare] JS click failed: {e2}, trying regular click...")
                                    try:
                                        sb.click(sel)
                                        print(f"[Cloudflare] ✓ Regular click successful on {sel}")
                                    except Exception as e3:
                                        print(f"[Cloudflare] All click methods failed: {e3}")
                                        continue
                            
                            sb.sleep(3)  # Wait for challenge to process
                            
                            # Check if cleared after this click
                            if _cleared():
                                print("[Cloudflare] ✓ Challenge cleared after click!")
                                return True
                            else:
                                print("[Cloudflare] Challenge still present after click, continuing...")
                    except Exception as e:
                        continue

            # Small delay and re-check
            sb.sleep(2)
            if _cleared():
                print("[Cloudflare] ✓ Challenge cleared!")
                return True
        except Exception as e:
            print(f"[Cloudflare] Solving loop error: {e}")

    print(f"[Cloudflare] ✗ Challenge NOT cleared after {max_wait_seconds}s timeout")
    return False

def _solve_cloudflare_turnstile_with_2captcha(sb, max_wait_seconds: int = 120, proxy_string: str = None) -> bool:
    """
    Solve Cloudflare Turnstile CAPTCHA using 2Captcha API service.
    Requires TWOCAPTCHA_API_KEY environment variable.
    
    Based on 2Captcha docs: https://2captcha.com/in.php
    
    Args:
        sb: SeleniumBase driver instance
        max_wait_seconds: Maximum time to wait for solution (default 120s)
        proxy_string: Proxy string in format "user:pass@host:port" (CRITICAL for IP matching)
    
    Returns True if CAPTCHA is solved, False otherwise.
    """
    api_key = os.getenv("TWOCAPTCHA_API_KEY", "").strip()
    if not api_key:
        print("[2Captcha-Turnstile] ⚠ TWOCAPTCHA_API_KEY not set, skipping 2Captcha solver")
        return False
    
    print("[2Captcha-Turnstile] Looking for Cloudflare Turnstile challenge...")
    
    # Initialize 2Captcha solver
    solver = TwoCaptcha(api_key)
    
    try:
        # Switch to default content
        sb.driver.switch_to.default_content()
        
        # CRITICAL: Wait for Turnstile JavaScript to load and render the widget
        # The challenge page loads a script that then renders the widget dynamically
        print("[2Captcha-Turnstile] Waiting for Turnstile widget to load...")
        sb.sleep(3)  # Give JS time to execute
        
        # Wait for either the Turnstile script or the response input to appear
        max_wait = 10
        for i in range(max_wait):
            try:
                has_script = sb.execute_script("""
                    return document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]') !== null;
                """)
                has_input = sb.execute_script("""
                    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
                """)
                
                if has_script or has_input:
                    print(f"[2Captcha-Turnstile] ✓ Turnstile elements detected (script: {has_script}, input: {has_input})")
                    break
                    
                if i < max_wait - 1:
                    sb.sleep(1)
            except Exception:
                pass
        
        # Save page source for debugging
        try:
            ts_debug = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            debug_html = os.path.join(SCREENSHOTS_DIR, f"page_source_2captcha_{ts_debug}.html")
            with open(debug_html, 'w', encoding='utf-8') as f:
                f.write(sb.get_page_source())
            print(f"[2Captcha-Turnstile] Saved page source: {debug_html}")
        except Exception as e:
            print(f"[2Captcha-Turnstile] Could not save page source: {e}")
        
        # Find the Cloudflare Turnstile sitekey using JavaScript (more reliable)
        sitekey = None
        
        print("[2Captcha-Turnstile] Searching for Turnstile sitekey...")
        
        # Method 1: Extract from Turnstile API script tag (most reliable for JS challenges)
        try:
            script_js = """
                // Look for Turnstile API script
                let scripts = document.querySelectorAll('script[src*="challenges.cloudflare.com/turnstile"]');
                if (scripts.length > 0) {
                    let src = scripts[0].getAttribute('src');
                    // URL format: https://challenges.cloudflare.com/turnstile/v0/b/SITEKEY/api.js
                    return src;
                }
                return null;
            """
            script_src = sb.execute_script(script_js)
            if script_src:
                print(f"[2Captcha-Turnstile] Found Turnstile script: {script_src}")
                # Extract sitekey from URL like: /turnstile/v0/b/c88755b0cddc/api.js
                import re
                match = re.search(r'/turnstile/v0/[^/]+/([a-zA-Z0-9_-]+)/', script_src)
                if match:
                    sitekey = match.group(1)
                    print(f"[2Captcha-Turnstile] ✓ Extracted sitekey from script URL: {sitekey}")
        except Exception as e:
            print(f"[2Captcha-Turnstile] Script tag extraction failed: {e}")
        
        # Method 2: Use JavaScript to find data-sitekey attribute
        if not sitekey:
            try:
                sitekey_js = """
                    // Look for div with data-sitekey attribute
                    let divs = document.querySelectorAll('div[data-sitekey]');
                    if (divs.length > 0) {
                        return divs[0].getAttribute('data-sitekey');
                    }
                    
                    // Look for cf-turnstile class
                    divs = document.querySelectorAll('.cf-turnstile');
                    if (divs.length > 0) {
                        return divs[0].getAttribute('data-sitekey');
                    }
                    
                    // Look in all divs with class containing 'turnstile'
                    divs = document.querySelectorAll('div[class*="turnstile"]');
                    for (let div of divs) {
                        let sk = div.getAttribute('data-sitekey');
                        if (sk) return sk;
                    }
                    
                    return null;
                """
                sitekey = sb.execute_script(sitekey_js)
                if sitekey:
                    print(f"[2Captcha-Turnstile] ✓ Found sitekey via JavaScript: {sitekey}")
            except Exception as e:
                print(f"[2Captcha-Turnstile] JavaScript sitekey search failed: {e}")
        
        # Method 2: Extract from iframe src URL
        if not sitekey:
            try:
                iframe_js = """
                    let iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                    if (iframes.length > 0) {
                        return iframes[0].getAttribute('src');
                    }
                    return null;
                """
                iframe_src = sb.execute_script(iframe_js)
                if iframe_src:
                    import re
                    match = re.search(r'[?&]sitekey=([^&]+)', iframe_src)
                    if match:
                        sitekey = match.group(1)
                        print(f"[2Captcha-Turnstile] ✓ Found sitekey in iframe src: {sitekey}")
            except Exception as e:
                print(f"[2Captcha-Turnstile] Iframe extraction failed: {e}")
        
        # Method 3: Search page source with regex
        if not sitekey:
            try:
                page_source = sb.get_page_source()
                import re
                
                # Pattern 1: Turnstile script URL (most common for JS challenges)
                # Example: /turnstile/v0/b/c88755b0cddc/api.js
                match = re.search(r'/turnstile/v0/[^/]+/([a-zA-Z0-9_-]+)/', page_source)
                if match:
                    sitekey = match.group(1)
                    print(f"[2Captcha-Turnstile] ✓ Found sitekey in script URL (page source): {sitekey}")
                else:
                    # Pattern 2: data-sitekey attribute
                    match = re.search(r'data-sitekey=["\']([0-9a-zA-Z_-]{20,})["\']', page_source)
                    if match:
                        sitekey = match.group(1)
                        print(f"[2Captcha-Turnstile] ✓ Found data-sitekey in page source: {sitekey}")
            except Exception as e:
                print(f"[2Captcha-Turnstile] Page source search failed: {e}")
        
        if not sitekey:
            print("[2Captcha-Turnstile] ✗ Could not find Cloudflare Turnstile sitekey")
            return False
        
        # Get current page URL
        page_url = sb.get_current_url()
        print(f"[2Captcha-Turnstile] Page URL: {page_url}")
        print(f"[2Captcha-Turnstile] Sitekey: {sitekey}")
        
        # Prepare solver parameters
        solver_params = {
            'sitekey': sitekey,
            'url': page_url,
        }
        
        # Add proxy if provided (CRITICAL for matching IP)
        if proxy_string:
            # Parse proxy string: username:password@host:port
            try:
                if '@' in proxy_string:
                    auth, server = proxy_string.split('@')
                    user, password = auth.split(':')
                    host, port = server.split(':')
                    
                    solver_params['proxy'] = {
                        'type': 'HTTP',
                        'uri': f"{host}:{port}",
                        'login': user,
                        'password': password,
                    }
                    print(f"[2Captcha-Turnstile] Using proxy: {host}:{port}")
            except Exception as e:
                print(f"[2Captcha-Turnstile] ⚠ Proxy parsing failed: {e}")
        
        # Submit Turnstile to 2Captcha
        print(f"[2Captcha-Turnstile] Submitting to 2Captcha (timeout: {max_wait_seconds}s)...")
        print("[2Captcha-Turnstile] This may take 30-60 seconds...")
        
        try:
            # Use Turnstile method
            result = solver.turnstile(**solver_params)
            token = result.get('code')
            
            if not token:
                print("[2Captcha-Turnstile] ✗ No solution received from 2Captcha")
                return False
            
            print(f"[2Captcha-Turnstile] ✓ Solution received! Token length: {len(token)}")
            
        except Exception as e:
            print(f"[2Captcha-Turnstile] ✗ 2Captcha API error: {e}")
            return False
        
        # Inject the Turnstile token into the page
        # According to 2Captcha docs: inject into input name="cf-turnstile-response"
        print("[2Captcha-Turnstile] Injecting solution into page...")
        
        inject_js = """
            const token = arguments[0];
            console.log('[2Captcha] Injecting token, length:', token.length);
            
            let injected = false;
            let method = 'none';
            
            // Primary method: Find cf-turnstile-response (as per 2Captcha docs)
            let responseInputs = document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
            console.log('[2Captcha] Found', responseInputs.length, 'cf-turnstile-response elements');
            
            if (responseInputs.length > 0) {
                responseInputs.forEach((input, idx) => {
                    input.value = token;
                    // Trigger change event
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    console.log('[2Captcha] Injected into cf-turnstile-response', idx);
                    injected = true;
                    method = 'cf-turnstile-response';
                });
            }
            
            // Fallback: Look for any turnstile-related inputs
            if (!injected) {
                let hiddenInputs = document.querySelectorAll('input[name*="turnstile"], textarea[name*="turnstile"]');
                console.log('[2Captcha] Found', hiddenInputs.length, 'turnstile-related inputs');
                
                if (hiddenInputs.length > 0) {
                    hiddenInputs.forEach((input, idx) => {
                        input.value = token;
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        console.log('[2Captcha] Injected into turnstile input', idx);
                        injected = true;
                        method = 'turnstile-input';
                    });
                }
            }
            
            // Try to find and trigger Turnstile callback
            if (typeof window.turnstile !== 'undefined') {
                console.log('[2Captcha] window.turnstile object exists');
                try {
                    // Some sites have a callback function
                    if (window.turnstile.callback) {
                        window.turnstile.callback(token);
                        console.log('[2Captcha] Called window.turnstile.callback()');
                    }
                } catch (e) {
                    console.log('[2Captcha] Turnstile callback error:', e.message);
                }
            }
            
            return {
                success: injected, 
                method: method,
                inputsFound: responseInputs.length
            };
        """
        
        try:
            result = sb.execute_script(inject_js, token)
            print(f"[2Captcha-Turnstile] Injection result: {result}")
            
            if not result.get('success'):
                print("[2Captcha-Turnstile] ⚠ Warning: No cf-turnstile-response inputs found!")
                print("[2Captcha-Turnstile] The page may not have loaded Turnstile yet, or uses different method")
        except Exception as e:
            print(f"[2Captcha-Turnstile] ⚠ Injection error: {e}")
            # Still continue - sometimes it works anyway
        
        # Wait for Cloudflare to validate
        print("[2Captcha-Turnstile] Waiting for Cloudflare validation (5-10s)...")
        sb.sleep(5.0 + random.random() * 5.0)
        
        # Take screenshot after injection
        try:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot = os.path.join(SCREENSHOTS_DIR, f"2captcha_turnstile_after_{ts}.png")
            sb.save_screenshot(shot)
            print(f"[2Captcha-Turnstile] Screenshot: {shot}")
        except Exception:
            pass
        
        print("[2Captcha-Turnstile] ✓ Solution injected successfully")
        return True
        
    except Exception as e:
        print(f"[2Captcha-Turnstile] Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def _solve_recaptcha_with_2captcha(sb, max_wait_seconds: int = 180, auto_submit: bool = True, proxy_string: str = None) -> bool:
    """
    Use 2Captcha API service (official SDK) to solve reCAPTCHA v2/v3 challenges (including image challenges).
    Requires TWOCAPTCHA_API_KEY environment variable.
    
    Args:
        sb: SeleniumBase driver instance
        max_wait_seconds: Maximum time to wait for solution (default 180s for image challenges)
        auto_submit: Whether to auto-submit the form after injecting solution (default True)
                     Set to False for pre-login CAPTCHAs where credentials need to be entered first
        proxy_string: Proxy string in format "user:pass@host:port" to use for solving (CRITICAL for IP matching)
    
    Returns True if CAPTCHA is solved, False otherwise.
    """
    api_key = os.getenv("TWOCAPTCHA_API_KEY", "").strip()
    if not api_key:
        print("[2Captcha] ⚠ TWOCAPTCHA_API_KEY not set, skipping 2Captcha solver")
        return False
    
    print("[2Captcha] Looking for reCAPTCHA challenge...")
    
    # Initialize 2Captcha solver
    solver = TwoCaptcha(api_key)
    
    try:
        # Switch to default content to search for reCAPTCHA
        sb.driver.switch_to.default_content()
        
        # Find the reCAPTCHA site key from the page
        sitekey = None
        
        # Method 1: Look for data-sitekey attribute
        try:
            recaptcha_elements = sb.find_elements('css selector', 
                '[data-sitekey], .g-recaptcha, [class*="recaptcha"]')
            for elem in recaptcha_elements:
                sk = elem.get_attribute('data-sitekey')
                if sk:
                    sitekey = sk
                    break
        except Exception:
            pass
        
        # Method 2: Extract from iframe src
        if not sitekey:
            try:
                recaptcha_iframes = sb.find_elements('css selector', 
                    'iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"]')
                if recaptcha_iframes:
                    iframe_src = recaptcha_iframes[0].get_attribute('src')
                    # Extract sitekey from URL: ?k=SITEKEY&...
                    import re
                    match = re.search(r'[?&]k=([^&]+)', iframe_src)
                    if match:
                        sitekey = match.group(1)
            except Exception:
                pass
        
        if not sitekey:
            print("[2Captcha] Could not find reCAPTCHA sitekey on page")
            return False
        
        page_url = sb.get_current_url()
        print(f"[2Captcha] ✓ Found sitekey: {sitekey[:20]}...")
        print(f"[2Captcha] Page URL: {page_url}")
        # Save a diagnostic snapshot before submitting to 2Captcha
        try:
            ts_pre = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot_pre = os.path.join(SCREENSHOTS_DIR, f"recaptcha_pre_submit_{ts_pre}.png")
            sb.save_screenshot(shot_pre)
            html_pre_path = os.path.join(HTML_SNAPSHOTS_DIR, f"recaptcha_pre_submit_{ts_pre}.html")
            try:
                with open(html_pre_path, 'w', encoding='utf-8') as f:
                    f.write(sb.get_page_source())
            except Exception:
                try:
                    with open(html_pre_path, 'w', encoding='utf-8') as f:
                        f.write(sb.driver.page_source)
                except Exception:
                    pass
            print(f"[2Captcha] Saved pre-submit diagnostics: {shot_pre}, {html_pre_path}")
        except Exception:
            pass
        
        # Detect if this is an invisible reCAPTCHA
        is_invisible = False
        try:
            # Check if the reCAPTCHA badge is visible (invisible CAPTCHAs show a badge)
            recaptcha_elements = sb.find_elements('css selector', 
                '[data-size="invisible"], .g-recaptcha[data-size="invisible"]')
            is_invisible = len(recaptcha_elements) > 0
            
            # Also check iframe for invisible type
            if not is_invisible:
                recaptcha_iframes = sb.find_elements('css selector', 
                    'iframe[src*="recaptcha"][src*="invisible"]')
                is_invisible = len(recaptcha_iframes) > 0
        except Exception:
            pass
        
        # Prepare solver parameters and submit using official SDK
        print("[2Captcha] Submitting CAPTCHA to 2Captcha service (via official SDK)...")
        
        # CRITICAL: Use the SAME proxy that the browser is using
        # This ensures the 2Captcha worker and browser have the same IP address
        solver_params = {
            'sitekey': sitekey,
            'url': page_url
        }
        
        # Parse and add proxy parameters if provided
        # IMPORTANT: Solve WITHOUT proxy to avoid ERROR_CAPTCHA_UNSOLVABLE
        # When using proxy, 2Captcha workers often fail with poor image quality or network issues
        # The solution token works regardless of where it was solved
        # Our browser is still using the proxy, so the injection happens from correct IP
        print("[2Captcha] Solving WITHOUT proxy (better success rate for image challenges)")
        
        # Submit and wait for solution (SDK handles polling automatically)
        start_time = time.time()
        try:
            print("[2Captcha] Waiting for solution (image challenges may take 60-120 seconds)...")
            result = solver.recaptcha(**solver_params)
            
            elapsed = int(time.time() - start_time)
            captcha_solution = result['code']
            print(f"[2Captcha] ✓ Solution received after {elapsed}s")
            
        except Exception as e:
            elapsed = int(time.time() - start_time)
            print(f"[2Captcha] ❌ Failed to get solution after {elapsed}s: {e}")
            return False
        
        # OLD HTTP API CODE REMOVED - NOW USING SDK ABOVE t
        
        # Inject the solution using a safer method (pass token as argument)
        print("[2Captcha] Injecting solution into page...")
        print(f"[2Captcha] Token length: {len(captcha_solution)} chars, starts with: {captcha_solution[:50]}...")
        
        # CRITICAL FIX: For invisible/enterprise reCAPTCHA (modal iframe), we need to override grecaptcha API
        inject_js = """
            const token = arguments[0];
            console.log('[2Captcha-Inject] Starting injection for invisible reCAPTCHA');
            console.log('[2Captcha-Inject] Token length:', token.length);
            
            let injected = false;
            let method = 'none';
            
            // METHOD 1: Override grecaptcha.enterprise (priority)
            if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
                console.log('[2Captcha-Inject] Found grecaptcha.enterprise');
                try {
                    grecaptcha.enterprise.getResponse = function() { return token; };
                    method = 'enterprise_override';
                    injected = true;
                    console.log('[2Captcha-Inject] ✓ Overrode grecaptcha.enterprise.getResponse');
                } catch (e) {
                    console.error('[2Captcha-Inject] Enterprise override failed:', e);
                }
            }
            
            // METHOD 2: Override standard grecaptcha.getResponse
            if (typeof grecaptcha !== 'undefined') {
                console.log('[2Captcha-Inject] Found grecaptcha');
                try {
                    const originalGetResponse = grecaptcha.getResponse;
                    grecaptcha.getResponse = function(widgetId) {
                        console.log('[2Captcha-Inject] getResponse called, returning token');
                        return token;
                    };
                    if (!injected) {
                        method = 'grecaptcha_override';
                        injected = true;
                    }
                    console.log('[2Captcha-Inject] ✓ Overrode grecaptcha.getResponse');
                } catch (e) {
                    console.error('[2Captcha-Inject] grecaptcha override failed:', e);
                }
            }
            
            // METHOD 3: Manipulate ___grecaptcha_cfg to inject token and execute callbacks
            if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                console.log('[2Captcha-Inject] Found ___grecaptcha_cfg with', Object.keys(___grecaptcha_cfg.clients).length, 'clients');
                
                for (const clientId in ___grecaptcha_cfg.clients) {
                    const client = ___grecaptcha_cfg.clients[clientId];
                    console.log('[2Captcha-Inject] Processing client:', clientId);
                    
                    // Deep search for response fields and callbacks
                    const processObject = (obj, depth = 0, path = '') => {
                        if (depth > 8 || !obj || typeof obj !== 'object') return;
                        
                        for (const key in obj) {
                            const fullPath = path ? path + '.' + key : key;
                            
                            // Inject into response fields
                            if (key === 'response' || key === 'g-recaptcha-response') {
                                console.log('[2Captcha-Inject] Setting response at:', fullPath);
                                obj[key] = token;
                                injected = true;
                                method = 'cfg_injection';
                            }
                            
                            // Execute callbacks
                            if (key === 'callback' && typeof obj[key] === 'function') {
                                console.log('[2Captcha-Inject] Executing callback at:', fullPath);
                                try {
                                    obj[key](token);
                                    method = 'callback_executed';
                                    console.log('[2Captcha-Inject] ✓ Callback executed successfully');
                                } catch (e) {
                                    console.error('[2Captcha-Inject] Callback execution failed:', e);
                                }
                            }
                            
                            // Recurse
                            processObject(obj[key], depth + 1, fullPath);
                        }
                    };
                    
                    processObject(client);
                }
            }
            
            // METHOD 4: Find and populate textareas (fallback for visible reCAPTCHA)
            const textareas = document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea#g-recaptcha-response');
            if (textareas.length > 0) {
                console.log('[2Captcha-Inject] Found', textareas.length, 'textarea(s)');
                textareas.forEach((ta, idx) => {
                    ta.style.display = 'block';
                    ta.value = token;
                    ta.innerHTML = token;
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                    ta.dispatchEvent(new Event('change', { bubbles: true }));
                    console.log('[2Captcha-Inject] ✓ Textarea', idx, 'updated');
                });
                if (!injected) {
                    method = 'textarea_fallback';
                    injected = true;
                }
            } else {
                console.log('[2Captcha-Inject] No textareas found (expected for invisible reCAPTCHA)');
            }
            
            return { 
                success: injected, 
                method: method,
                warning: method === 'callback_executed' ? null : 'no_callback_executed'
            };
        """
        
        try:
            result = sb.execute_script(inject_js, captcha_solution)
            print(f"[2Captcha] Injection result: {result}")
            
            if result and result.get('success'):
                method = result.get('method', 'unknown')
                callback_name = result.get('callback', 'none')
                print(f"[2Captcha] ✓ Solution injected using method: {method} (callback: {callback_name})")
                
                if result.get('warning'):
                    print(f"[2Captcha] ⚠ Warning: {result.get('warning')}")
            else:
                error = result.get('error') if result else 'unknown'
                print(f"[2Captcha] ❌ Injection failed: {error}")
                
        except Exception as e:
            print(f"[2Captcha] ❌ Injection script exception: {e}")
        
        # CRITICAL: Wait for reCAPTCHA to validate the solution (visual checkmark appears)
        # This must happen AFTER injection regardless of success/failure
        print("[2Captcha] Waiting for reCAPTCHA validation (3-5 seconds)...")
        sb.sleep(3.0 + random.random() * 2.0)  # 3-5 seconds for validation
        
        # Save a diagnostic snapshot AFTER injection
        try:
            ts_post = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot_post = os.path.join(SCREENSHOTS_DIR, f"recaptcha_post_inject_{ts_post}.png")
            sb.save_screenshot(shot_post)
            html_post_path = os.path.join(HTML_SNAPSHOTS_DIR, f"recaptcha_post_inject_{ts_post}.html")
            try:
                with open(html_post_path, 'w', encoding='utf-8') as f:
                    f.write(sb.get_page_source())
            except Exception:
                try:
                    with open(html_post_path, 'w', encoding='utf-8') as f:
                        f.write(sb.driver.page_source)
                except Exception:
                    pass
            print(f"[2Captcha] Saved post-inject diagnostics: {shot_post}, {html_post_path}")
        except Exception:
            pass
        
        # Auto-submit the form after CAPTCHA solution (if enabled)
        if auto_submit:
            print("[2Captcha] Auto-submitting form after solution...")
            try:
                # Try to find and click the submit button
                submit_selectors = [
                    "#userLoginSubmitButton",
                    "button[type='submit']",
                    "input[type='submit']",
                    "button.submit",
                    ".submit-button"
                ]
                
                submitted = False
                for sel in submit_selectors:
                    try:
                        if sb.is_element_present(sel):
                            sb.execute_script(f"document.querySelector('{sel}').click();")
                            print(f"[2Captcha] Clicked submit button: {sel}")
                            submitted = True
                            break
                    except Exception:
                        continue
                
                if not submitted:
                    print("[2Captcha] ⚠ No submit button found for auto-submit")
                
                sb.sleep(1.5)
            except Exception as e:
                print(f"[2Captcha] Auto-submit error: {e}")
        else:
            print("[2Captcha] Skipping auto-submit (disabled)")
        
        return True
        
    except Exception as e:
        print(f"[2Captcha] Error: {e}")
        try:
            sb.driver.switch_to.default_content()
        except Exception:
            pass
        return False

def build_brightdata_proxy_string(country_code="tr", session_id=None):
    """
    Returns PROXY_STRING in the format: username:password@host:port
    - Adds -country-<code> and -session-<id> to the Bright Data username
    """
    base_user = os.getenv("BRD_BASE_USER", "")
    password  = os.getenv("BRD_PASSWORD", "")
    host      = os.getenv("BRD_HOST", "")
    port      = os.getenv("BRD_PORT", "")
    if not (base_user and password and host and port):
        return None

    if not session_id:
        # short sticky session token; change to rotate
        session_id = secrets.token_hex(4)

    username = f"{base_user}-country-{country_code}-session-{session_id}"
    return f"{username}:{password}@{host}:{port}"
    
def _get_iproyal_requests_proxies():
    """
    Build a requests proxies dict from IPRoyal env vars if present.
    Expected env:
      - IPROYAL_PROXY: e.g. 'geo.iproyal.com:12321'
      - IPROYAL_PROXY_AUTH: e.g. 'username:password_country-tr_city-istanbul_streaming-1'
    """
    proxy_host_port = (os.getenv("IPROYAL_PROXY") or "").strip()
    proxy_auth = (os.getenv("IPROYAL_PROXY_AUTH") or "").strip()
    if proxy_host_port and proxy_auth:
        proxy_url = f"http://{proxy_auth}@{proxy_host_port}"
        return {"http": proxy_url, "https": proxy_url}
    return None

def _get_selenium_proxy_string(rotate_session=False):
    """
    Return a proxy string suitable for SeleniumBase:
      - Prefer IPRoyal if IPROYAL_* env vars are set: 'username:password@host:port'
      - Otherwise fall back to Bright Data if BRD_* env vars are set.
    Args:
        rotate_session: If True, append a random session ID to force IP rotation (IPRoyal)
    """
    proxy_host_port = (os.getenv("IPROYAL_PROXY") or "").strip()
    proxy_auth = (os.getenv("IPROYAL_PROXY_AUTH") or "").strip()
    
    # Debug: Check if env vars are actually set
    if not proxy_host_port:
        print("[Proxy] ❌ ERROR: IPROYAL_PROXY environment variable is NOT set in Railway!")
        print("[Proxy] Please set: IPROYAL_PROXY=geo.iproyal.com:12321")
    if not proxy_auth:
        print("[Proxy] ❌ ERROR: IPROYAL_PROXY_AUTH environment variable is NOT set in Railway!")
        print("[Proxy] Please set: IPROYAL_PROXY_AUTH=username:password_country-tr_streaming-1")
    
    if proxy_host_port and proxy_auth:
        # CRITICAL: Ensure Turkey country code for sahibinden.com
        # IPRoyal format: username:password_country-tr_city-istanbul_streaming-1_sessionid-xxx
        if "_country-" not in proxy_auth:
            print("[Proxy] ⚠️  WARNING: No country code in proxy auth! Adding _country-tr")
            proxy_auth = f"{proxy_auth}_country-tr"
        
        # Add session rotation for fresh IPs on each login
        if rotate_session and "_sessionid-" not in proxy_auth:
            import uuid
            session_id = str(uuid.uuid4())[:8]  # Short random ID
            proxy_auth = f"{proxy_auth}_sessionid-{session_id}"
            print(f"[Proxy] Rotating session: {session_id}")
        
        proxy_string = f"{proxy_auth}@{proxy_host_port}"
        print(f"[Proxy] Configured proxy: {proxy_host_port}")
        print(f"[Proxy] Auth string includes: {'country-tr' if 'country-tr' in proxy_auth else '⚠️  NO TURKEY CODE'}")
        return proxy_string
    
    return build_brightdata_proxy_string()
    
def login_with_proxy_and_save_cookies_with_retry(target_url: str, max_retries: int = 3) -> bool:
    """
    Wrapper around login_with_proxy_and_save_cookies with exponential backoff retry logic.
    If proxy IP gets rate-limited, rotates to a new session and retries.
    
    IMPORTANT: If cookies exist from a previous 2FA session, we skip login and use those cookies.
    """
    for attempt in range(1, max_retries + 1):
        print(f"[Proxy Login] Attempt {attempt}/{max_retries}")
        
        # Check if we already have cookies from a 2FA session
        if attempt > 1 and os.path.exists(SESSION_COOKIE_FILE):
            print("[Proxy Login] ✓ Found existing cookies from previous attempt (possibly 2FA session)")
            print("[Proxy Login] Skipping login, will use existing cookies for scraping")
            return True  # Use the cookies we already have
        
        success = login_with_proxy_and_save_cookies(target_url)
        if success:
            return True
        
        # If cookies exist after login attempt (even if stuck on 2FA), use them!
        # 2FA cookies ARE valid for browsing, just can't complete verification
        if os.path.exists(SESSION_COOKIE_FILE):
            print("[Proxy Login] ✓ Cookies saved from login attempt (possibly 2FA session)")
            print("[Proxy Login] These cookies are valid for scraping. Proceeding...")
            return True
        
        if attempt < max_retries:
            # Exponential backoff: 30s, 60s, 120s
            wait_time = 30 * (2 ** (attempt - 1))
            print(f"⏳ Proxy login failed. Waiting {wait_time}s before retry with new session...")
            time.sleep(wait_time)
    
    print(f"❌ All {max_retries} proxy login attempts failed")
    return False

def login_with_proxy_and_save_cookies(target_url: str) -> bool:
    """
    Open a fresh browser WITH proxy, perform login, save cookies to SESSION_COOKIE_FILE,
    close the browser, and return whether login succeeded.
    """
    SAHIBINDEN_USER = os.getenv("SAHIBINDEN_USER", "")
    SAHIBINDEN_PASS = os.getenv("SAHIBINDEN_PASS", "")
    # Use session rotation to get a fresh IP for each login attempt
    proxy_string = _get_selenium_proxy_string(rotate_session=True)
    if not proxy_string:
        print("ERROR: IPRoyal (IPROYAL_*) or Bright Data (BRD_*) proxy env vars not set. Cannot perform proxy-backed login.")
        return False

    if not SAHIBINDEN_USER or not SAHIBINDEN_PASS:
        print("ERROR: SAHIBINDEN_USER/SAHIBINDEN_PASS not set. Cannot login.")
        return False
    
    # Debug: Show proxy configuration (mask password)
    print(f"[Proxy] Proxy string format: {proxy_string[:30]}...@{proxy_string.split('@')[-1] if '@' in proxy_string else 'invalid'}")

    def _accept_cookie_banner_if_any_local(sb):
        try:
            if sb.is_element_present("#onetrust-accept-btn-handler"):
                sb.js_click("#onetrust-accept-btn-handler")
                time.sleep(0.5)
        except Exception:
            pass

    try:
        print(f"[Login] Starting proxy login session (uc=True, headless={_is_headless()})")
        print(f"[Proxy] Using proxy: {proxy_string}")
        
        # SeleniumBase accepts proxy in format: username:password@host:port
        # CRITICAL: Use incognito + uc mode for maximum anti-detection (per SeleniumBase docs)
        # "Sometimes you need to add incognito=True with uc=True to maximize your anti-detection abilities"
        with SB(
            uc=True,  # Use undetected mode for login
            incognito=True,  # CRITICAL: Incognito mode prevents browser cache/history detection
            headless=_is_headless(),
            xvfb=True,
            agent=_realistic_user_agent(),
            locale_code="tr-TR",
            window_size="1920,1080",
            user_data_dir=None,  # CRITICAL: Don't use profile in incognito mode
            proxy=proxy_string,  # CRITICAL: Use SeleniumBase's built-in proxy support
            chromium_arg=",".join(_get_chrome_args()),
        ) as sb:
            print(f"[Proxy] ✓ Browser started with proxy extension")
            
            try:
                sb.driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                )
            except Exception:
                pass
            try:
                _apply_stealth(sb)
            except Exception:
                pass
            try:
                # Small pause and some human-like signals at session start
                sb.sleep(0.8 + random.random() * 0.8)
                _humanize_session(sb, 8)
            except Exception:
                pass
            
            # Verify proxy IP (optional diagnostic)
            try:
                print("[Proxy] Checking IP address...")
                sb.get("https://api.ipify.org?format=json")
                sb.sleep(1)
                ip_info = sb.get_page_source()
                print(f"[Proxy] Current IP: {ip_info}")
            except Exception as e:
                print(f"[Proxy] IP check failed: {e}")

            # Load login page
            login_urls = [
                "https://www.sahibinden.com/giris",
            ]
            loaded = False
            attempt_idx = 0
            for login_url in login_urls:
                try:
                    attempt_idx += 1
                    # CRITICAL: Use uc_open_with_reconnect for stealth, then activate CDP Mode for CAPTCHA
                    # Load page with UC mode first (allows detection), then switch to CDP for clicking
                    print(f"Loading login page: {login_url}")
                    sb.uc_open_with_reconnect(login_url, reconnect_time=5)
                    
                    # Wait for Cloudflare challenge widget to render (if present)
                    print("[Login] Waiting for page to settle...")
                    sb.sleep(3 + random.random() * 2)  # Random 3-5s wait
                    
                    # Take screenshot BEFORE attempting CAPTCHA detection
                    try:
                        ts_before = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        shot_before = os.path.join(SCREENSHOTS_DIR, f"login_before_detection_{ts_before}.png")
                        sb.save_screenshot(shot_before)
                        print(f"[Login] Screenshot before detection: {shot_before}")
                    except Exception as e:
                        print(f"[Login] Screenshot error: {e}")
                    
                    # Debug: Check what's actually on the page
                    print("[Login] Debugging page content...")
                    try:
                        page_info = sb.execute_script("""
                            return {
                                title: document.title,
                                url: window.location.href,
                                hasCloudflareChallengeDiv: document.querySelector('.cf-turnstile') !== null,
                                hasCloudflareChallengeIframe: document.querySelector('iframe[src*="challenges.cloudflare.com"]') !== null,
                                hasUsernameField: document.querySelector('#username') !== null,
                                hasPasswordField: document.querySelector('#password') !== null,
                                bodyTextStart: document.body.innerText.substring(0, 200),
                                turnstileDivs: document.querySelectorAll('div[data-sitekey]').length,
                                allIframes: document.querySelectorAll('iframe').length,
                                cfTurnstileDivs: document.querySelectorAll('.cf-turnstile').length
                            };
                        """)
                        print(f"[Login] Page debug info:")
                        for key, value in page_info.items():
                            print(f"[Login]   {key}: {value}")
                    except Exception as e:
                        print(f"[Login] Page info error: {e}")
                    
                    # Check if login form is already visible (no challenge)
                    try:
                        if sb.is_element_present("#username") and sb.is_element_present("#password"):
                            print("[Login] ✓ Login form already visible (no challenge required)")
                            loaded = True
                            break
                    except Exception:
                        pass
                    
                    # Use 2Captcha API to solve Cloudflare Turnstile challenge
                    print("[Login] Handling Cloudflare challenge...")
                    
                    # IMPORTANT: Check what type of Cloudflare challenge this is
                    challenge_type = None
                    try:
                        # Collect comprehensive challenge information
                        challenge_info = sb.execute_script("""
                            return {
                                hasTurnstileScript: document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]') !== null,
                                hasTurnstileResponse: document.querySelector('input[name="cf-turnstile-response"]') !== null,
                                hasManagedChallenge: document.querySelector('script[src*="/cdn-cgi/challenge-platform/"]') !== null,
                                hasChallengeDiv: document.querySelector('#cf-wrapper') !== null || document.querySelector('[id*="challenge"]') !== null,
                                pageTitle: document.title,
                                hasCheckingText: document.body.innerText.includes('Checking') || 
                                                document.body.innerText.includes('güvenliğini gözden') ||
                                                document.body.innerText.includes('insan olduğunuzu'),
                                bodyTextStart: document.body.innerText.substring(0, 300)
                            };
                        """)
                        print(f"[Login] Challenge detection: {challenge_info}")
                        
                        # CRITICAL: Save full challenge page structure for analysis
                        try:
                            ts_debug = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                            
                            # Save full HTML
                            html_file = os.path.join(SCREENSHOTS_DIR, f"cloudflare_challenge_full_{ts_debug}.html")
                            with open(html_file, 'w', encoding='utf-8') as f:
                                f.write(sb.get_page_source())
                            print(f"[Challenge] Saved full HTML: {html_file}")
                            
                            # Extract and save detailed challenge structure
                            challenge_structure = sb.execute_script("""
                                function getElementInfo(el) {
                                    if (!el) return null;
                                    return {
                                        tagName: el.tagName,
                                        id: el.id,
                                        className: el.className,
                                        innerHTML: el.innerHTML.substring(0, 500),
                                        attributes: Array.from(el.attributes).map(a => ({name: a.name, value: a.value}))
                                    };
                                }
                                
                                return {
                                    // All scripts on the page
                                    scripts: Array.from(document.querySelectorAll('script')).map(s => ({
                                        src: s.src,
                                        hasInnerText: s.innerText.length > 0,
                                        innerTextStart: s.innerText.substring(0, 200)
                                    })),
                                    
                                    // All iframes
                                    iframes: Array.from(document.querySelectorAll('iframe')).map(iframe => ({
                                        src: iframe.src,
                                        id: iframe.id,
                                        title: iframe.title,
                                        name: iframe.name
                                    })),
                                    
                                    // All forms
                                    forms: Array.from(document.querySelectorAll('form')).map(form => getElementInfo(form)),
                                    
                                    // All inputs
                                    inputs: Array.from(document.querySelectorAll('input')).map(input => ({
                                        type: input.type,
                                        name: input.name,
                                        id: input.id,
                                        value: input.value ? '***' : '',
                                        className: input.className
                                    })),
                                    
                                    // Cloudflare-specific elements
                                    cloudflareElements: {
                                        cfWrapper: getElementInfo(document.querySelector('#cf-wrapper')),
                                        cfChallenge: getElementInfo(document.querySelector('[id*="challenge"]')),
                                        turnstileDiv: getElementInfo(document.querySelector('.cf-turnstile')),
                                        turnstileResponse: getElementInfo(document.querySelector('input[name="cf-turnstile-response"]')),
                                        challengeForm: getElementInfo(document.querySelector('#challenge-form'))
                                    },
                                    
                                    // Page metadata
                                    metadata: {
                                        title: document.title,
                                        url: window.location.href,
                                        bodyClasses: document.body.className,
                                        bodyId: document.body.id
                                    }
                                };
                            """)
                            
                            # Save structure as JSON
                            import json
                            structure_file = os.path.join(SCREENSHOTS_DIR, f"cloudflare_structure_{ts_debug}.json")
                            with open(structure_file, 'w', encoding='utf-8') as f:
                                json.dump(challenge_structure, f, indent=2, ensure_ascii=False)
                            print(f"[Challenge] Saved structure JSON: {structure_file}")
                            
                            # Print key findings
                            print("[Challenge] ===== STRUCTURE ANALYSIS =====")
                            print(f"[Challenge] Scripts found: {len(challenge_structure.get('scripts', []))}")
                            print(f"[Challenge] Iframes found: {len(challenge_structure.get('iframes', []))}")
                            print(f"[Challenge] Forms found: {len(challenge_structure.get('forms', []))}")
                            print(f"[Challenge] Inputs found: {len(challenge_structure.get('inputs', []))}")
                            
                            # Show Cloudflare-specific scripts
                            cf_scripts = [s for s in challenge_structure.get('scripts', []) if s.get('src') and ('cloudflare' in s.get('src', '').lower() or 'challenge' in s.get('src', '').lower())]
                            print(f"[Challenge] Cloudflare scripts: {len(cf_scripts)}")
                            for idx, script in enumerate(cf_scripts[:5]):  # Show first 5
                                print(f"[Challenge]   Script {idx+1}: {script.get('src', 'N/A')}")
                            
                            # Show iframes
                            if challenge_structure.get('iframes'):
                                print("[Challenge] Iframes:")
                                for idx, iframe in enumerate(challenge_structure.get('iframes', [])):
                                    print(f"[Challenge]   Iframe {idx+1}: src={iframe.get('src', 'N/A')}, title={iframe.get('title', 'N/A')}")
                            
                            # Show Cloudflare elements
                            cf_elems = challenge_structure.get('cloudflareElements', {})
                            print("[Challenge] Cloudflare Elements:")
                            for elem_name, elem_data in cf_elems.items():
                                if elem_data:
                                    print(f"[Challenge]   {elem_name}: Found (tag={elem_data.get('tagName', 'N/A')})")
                                else:
                                    print(f"[Challenge]   {elem_name}: Not found")
                            
                            print("[Challenge] ===== END STRUCTURE ANALYSIS =====")
                            
                            # Take detailed screenshot
                            screenshot_file = os.path.join(SCREENSHOTS_DIR, f"cloudflare_challenge_screenshot_{ts_debug}.png")
                            sb.save_screenshot(screenshot_file)
                            print(f"[Challenge] Screenshot: {screenshot_file}")
                            
                        except Exception as e:
                            print(f"[Challenge] ✗ Failed to save challenge structure: {e}")
                            import traceback
                            traceback.print_exc()
                        
                        # Cloudflare Managed Challenge - use SeleniumBase built-in methods
                        # This is the type sahibinden.com uses (not standard Turnstile widget)
                        if challenge_info.get('hasManagedChallenge') or challenge_info.get('hasCheckingText'):
                            print("[Login] ✓ Detected Cloudflare Managed Challenge")
                            print("[Login] This challenge type requires SeleniumBase UC Mode (2Captcha doesn't support it)")
                            print("[Login] Using uc_gui_handle_captcha() method...")
                            
                            # Wait for challenge to fully render
                            sb.sleep(3)
                            
                            # Take screenshot before
                            try:
                                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                                shot = os.path.join(SCREENSHOTS_DIR, f"managed_challenge_before_{ts}.png")
                                sb.save_screenshot(shot)
                                print(f"[Login] Screenshot: {shot}")
                            except Exception:
                                pass
                            
                            # Use SeleniumBase's built-in method
                            try:
                                print("[Login] Executing uc_gui_handle_captcha()...")
                                sb.uc_gui_handle_captcha()
                                print("[Login] ✓ uc_gui_handle_captcha() completed")
                                
                                # Wait for Cloudflare to validate (needs time!)
                                print("[Login] Waiting for Cloudflare validation (12s)...")
                                sb.sleep(12)
                                
                                # Take screenshot after
                                try:
                                    shot_after = os.path.join(SCREENSHOTS_DIR, f"managed_challenge_after_{ts}.png")
                                    sb.save_screenshot(shot_after)
                                    print(f"[Login] Screenshot after: {shot_after}")
                                except Exception:
                                    pass
                                
                                # Check if login form appeared
                                if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                    print("[Login] ✓ Login form appeared after challenge!")
                                    loaded = True
                                    break
                                else:
                                    print("[Login] Form not visible yet...")
                                    # Check page state
                                    current_url = sb.get_current_url()
                                    page_title = sb.get_title()
                                    print(f"[Login] Current URL: {current_url}")
                                    print(f"[Login] Page title: {page_title}")
                                    
                                    # Wait a bit more
                                    print("[Login] Waiting additional 8 seconds...")
                                    sb.sleep(8)
                                    
                                    if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                        print("[Login] ✓ Login form appeared after extended wait!")
                                        loaded = True
                                        break
                                    else:
                                        print("[Login] ✗ Login form still not visible")
                                        print("[Login] Challenge may have failed or IP is flagged")
                                    
                            except Exception as e:
                                print(f"[Login] ✗ UC Mode error: {e}")
                                import traceback
                                traceback.print_exc()
                        
                        # Standard Turnstile - try 2Captcha
                        elif challenge_info.get('hasTurnstileScript') and challenge_info.get('hasTurnstileResponse'):
                            print("[Login] Detected standard Turnstile challenge")
                            print("[Login] Attempting 2Captcha API...")
                            
                            # Get proxy string for 2Captcha (must match browser IP)
                            captcha_proxy_string = proxy_string if proxy_string else None
                            
                            # Try 2Captcha Turnstile solver
                            if _solve_cloudflare_turnstile_with_2captcha(sb, max_wait_seconds=120, proxy_string=captcha_proxy_string):
                                print("[Login] ✓ 2Captcha solved Cloudflare challenge!")
                                
                                # Wait for page to update
                                sb.sleep(5)
                                
                                # Check if login form appeared
                                if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                    print("[Login] ✓ Login form appeared after 2Captcha!")
                                    loaded = True
                                    break
                            else:
                                print("[Login] 2Captcha failed, trying UC Mode fallback...")
                                try:
                                    sb.uc_gui_handle_captcha()
                                    sb.sleep(10)
                                    if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                        print("[Login] ✓ Login form appeared after UC Mode fallback!")
                                        loaded = True
                                        break
                                except Exception as e:
                                    print(f"[Login] UC Mode fallback error: {e}")
                        
                        else:
                            print("[Login] ⚠ Unknown challenge type, trying UC Mode...")
                            try:
                                sb.uc_gui_handle_captcha()
                                sb.sleep(10)
                                if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                    print("[Login] ✓ Login form appeared!")
                                    loaded = True
                                    break
                            except Exception as e:
                                print(f"[Login] UC Mode error: {e}")
                                
                    except Exception as e:
                        print(f"[Login] Challenge detection error: {e}")
                        # Fallback to UC Mode
                        try:
                            print("[Login] Falling back to UC Mode...")
                            sb.uc_gui_handle_captcha()
                            sb.sleep(10)
                            if sb.is_element_present("#username") and sb.is_element_present("#password"):
                                print("[Login] ✓ Login form appeared!")
                                loaded = True
                                break
                        except Exception as e2:
                            print(f"[Login] UC Mode fallback error: {e2}")
                    
                    # Check if we succeeded
                    if loaded:
                        print("[Login] ✓ CAPTCHA solving successful!")
                        break
                    else:
                        print(f"[Login] ✗ All CAPTCHA solving attempts failed")
                        print("[Login] ℹ️ This likely means:")
                        print("[Login]    - Cloudflare has flagged this proxy IP")
                        print("[Login]    - Need to rotate to a fresh session/IP")
                        print("[Login]    - Consider using a different proxy provider")
                        print("[Login] ℹ️ This likely means the proxy IP is flagged/burned by Cloudflare")
                        print("[Login] ℹ️ Recommendation: Rotate to a new proxy session or use different IP range")
                    
                    # Final check for login form (CDP Mode compatible)
                    try:
                        # Try CDP Mode first (more stealthy)
                        try:
                            form_present = sb.cdp.is_element_present("#username") and sb.cdp.is_element_present("#password")
                        except Exception:
                            # Fallback to regular WebDriver check
                            form_present = sb.is_element_present("#username") and sb.is_element_present("#password")
                        
                        if form_present:
                            print(f"[Login] ✓ Login form detected")
                            loaded = True
                            break
                    except Exception as e:
                        print(f"[Login] Form detection error: {e}")
                    
                    # NOW check for rate-limit page (after captcha attempts)
                    try:
                        # Get page source (CDP Mode compatible)
                        try:
                            page_text = sb.cdp.get_page_source().lower()
                            current_url = sb.cdp.get_current_url().lower()
                        except Exception:
                            page_text = sb.get_page_source().lower()
                            current_url = sb.get_current_url().lower()
                        
                        if "olağandışı bir durum" in page_text or "destek kodu:" in page_text:
                            print(f"⚠ Rate-limit page detected on proxy login attempt {attempt_idx} (URL: {current_url})")
                            ts_rl = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                            shot_rl = os.path.join(SCREENSHOTS_DIR, f"proxy_rate_limit_{ts_rl}.png")
                            sb.save_screenshot(shot_rl)
                            
                            # Save HTML for analysis
                            html_rl = os.path.join(HTML_SNAPSHOTS_DIR, f"proxy_rate_limit_{ts_rl}.html")
                            with open(html_rl, 'w', encoding='utf-8') as f:
                                f.write(sb.get_page_source())
                            
                            print(f"Proxy IP is rate-limited. Saved: {shot_rl}, {html_rl}")
                            # Try next login URL with same rotated session (same IP)
                            continue
                    except Exception as e:
                        print(f"Rate-limit check error: {e}")
                    
                    try:
                        _humanize_session(sb, 6)
                    except Exception:
                        pass
                    _accept_cookie_banner_if_any_local(sb)
                    sb.wait_for_ready_state_complete()
                    
                    # Check if we successfully reached the login form
                    if sb.is_element_present("#username") and sb.is_element_present("#password"):
                        print(f"✓ Login page loaded successfully: {login_url}")
                        loaded = True
                        break
                    else:
                        print(f"Login form not found on {login_url}")
                    sb.sleep(0.8)
                except Exception as e:
                    print("Login page open failed:", e)
                    # Save diagnostics for this failed attempt
                    try:
                        ts_diag = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        shot = os.path.join(SCREENSHOTS_DIR, f"proxy_login_attempt_{attempt_idx}_{ts_diag}.png")
                        sb.save_screenshot(shot)
                        html = ""
                        try:
                            html = sb.get_page_source()
                        except Exception:
                            try:
                                html = sb.driver.page_source
                            except Exception:
                                html = ""
                        if html:
                            html_path = os.path.join(HTML_SNAPSHOTS_DIR, f"proxy_login_attempt_{attempt_idx}_{ts_diag}.html")
                            with open(html_path, 'w', encoding='utf-8') as f:
                                f.write(html)
                            print(f"Saved proxy login diagnostics: {shot} , {html_path}")
                    except Exception:
                        pass
            if not loaded:
                print("Failed to open a login page.")
                # Final diagnostics when no login URL could be opened
                try:
                    ts_fail = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    shotf = os.path.join(SCREENSHOTS_DIR, f"proxy_login_failed_open_{ts_fail}.png")
                    sb.save_screenshot(shotf)
                    htmlf = ""
                    try:
                        htmlf = sb.get_page_source()
                    except Exception:
                        try:
                            htmlf = sb.driver.page_source
                        except Exception:
                            htmlf = ""
                    if htmlf:
                        htmlf_path = os.path.join(HTML_SNAPSHOTS_DIR, f"proxy_login_failed_open_{ts_fail}.html")
                        with open(htmlf_path, 'w', encoding='utf-8') as f:
                            f.write(htmlf)
                        print(f"Saved proxy login final diagnostics: {shotf} , {htmlf_path}")
                except Exception:
                    pass
                return False

            # STEP 1: Enter credentials first (NO CAPTCHA solving yet)
            print("[Login] Entering credentials...")
            sb.sleep(0.5 + random.random())
            
            try:
                # Click username field to focus
                try:
                    sb.cdp.click("#username")
                    sb.sleep(0.3)
                except Exception:
                    try:
                        sb.click("#username")
                        sb.sleep(0.3)
                    except Exception:
                        pass
                
                # Type username character by character (realistic 50-150ms per char)
                username_field = sb.find_element("#username")
                for char in SAHIBINDEN_USER:
                    username_field.send_keys(char)
                    sb.sleep(0.05 + random.random() * 0.1)
                
                sb.sleep(0.5 + random.random() * 0.5)
                
                # Click password field
                try:
                    sb.cdp.click("#password")
                    sb.sleep(0.3)
                except Exception:
                    try:
                        sb.click("#password")
                        sb.sleep(0.3)
                    except Exception:
                        pass
                
                # Type password character by character
                password_field = sb.find_element("#password")
                for char in SAHIBINDEN_PASS:
                    password_field.send_keys(char)
                    sb.sleep(0.05 + random.random() * 0.1)
                
                sb.sleep(1.0 + random.random())  # Pause before CAPTCHA check (human-like)
                
            except Exception as e:
                print(f"[Login] Credential entry failed: {e}")
                # Save diagnostic
                try:
                    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    shot = os.path.join(SCREENSHOTS_DIR, f"login_cred_entry_fail_{ts}.png")
                    sb.save_screenshot(shot)
                except Exception:
                    pass
                return False

            # CRITICAL: Check for CAPTCHA BEFORE submitting the form
            print("[Login] Checking for CAPTCHA before submit...")
            
            # CAPTURE PRE-SUBMIT PAGE STRUCTURE
            try:
                ts_presubmit = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                
                # Save screenshot before CAPTCHA
                screenshot_file = os.path.join(SCREENSHOTS_DIR, f"presubmit_screenshot_{ts_presubmit}.png")
                sb.save_screenshot(screenshot_file)
                print(f"[PreSubmit] Screenshot: {screenshot_file}")
                
                # Capture CAPTCHA structure
                captcha_info = sb.execute_script("""
                    function getElementInfo(el) {
                        if (!el) return null;
                        return {
                            tagName: el.tagName,
                            id: el.id,
                            className: el.className,
                            visible: el.offsetParent !== null,
                            dimensions: {width: el.offsetWidth, height: el.offsetHeight},
                            position: {top: el.offsetTop, left: el.offsetLeft}
                        };
                    }
                    
                    return {
                        // reCAPTCHA elements
                        recaptcha: {
                            iframe: getElementInfo(document.querySelector('iframe[src*="recaptcha"]')),
                            div: getElementInfo(document.querySelector('.g-recaptcha')),
                            checkbox: getElementInfo(document.querySelector('#recaptcha-anchor')),
                            responseField: getElementInfo(document.querySelector('#g-recaptcha-response'))
                        },
                        
                        // Cloudflare Turnstile
                        turnstile: {
                            div: getElementInfo(document.querySelector('.cf-turnstile')),
                            iframe: getElementInfo(document.querySelector('iframe[src*="challenges.cloudflare.com"]')),
                            responseField: getElementInfo(document.querySelector('input[name="cf-turnstile-response"]'))
                        },
                        
                        // hCaptcha
                        hcaptcha: {
                            div: getElementInfo(document.querySelector('.h-captcha')),
                            iframe: getElementInfo(document.querySelector('iframe[src*="hcaptcha"]'))
                        },
                        
                        // Generic CAPTCHA containers
                        generic: {
                            captchaDiv: getElementInfo(document.querySelector('[class*="captcha"]')),
                            captchaContainer: getElementInfo(document.querySelector('#captcha'))
                        },
                        
                        // All iframes
                        allIframes: Array.from(document.querySelectorAll('iframe')).map(iframe => ({
                            src: iframe.src,
                            id: iframe.id,
                            className: iframe.className,
                            visible: iframe.offsetParent !== null,
                            width: iframe.width,
                            height: iframe.height
                        }))
                    };
                """)
                
                print("[PreSubmit] ===== CAPTCHA DETECTION =====")
                
                # Check each CAPTCHA type
                captcha_found = None
                
                if captcha_info.get('recaptcha', {}).get('iframe'):
                    print("[PreSubmit] ✓ reCAPTCHA detected!")
                    captcha_found = 'recaptcha'
                    recaptcha_info = captcha_info['recaptcha']
                    print(f"[PreSubmit]   Iframe visible: {recaptcha_info['iframe'].get('visible', False)}")
                    if recaptcha_info.get('div'):
                        print(f"[PreSubmit]   Div class: {recaptcha_info['div'].get('className', 'N/A')}")
                
                if captcha_info.get('turnstile', {}).get('iframe'):
                    print("[PreSubmit] ✓ Cloudflare Turnstile detected!")
                    captcha_found = 'turnstile'
                
                if captcha_info.get('hcaptcha', {}).get('iframe'):
                    print("[PreSubmit] ✓ hCaptcha detected!")
                    captcha_found = 'hcaptcha'
                
                if not captcha_found and captcha_info.get('allIframes'):
                    print(f"[PreSubmit] Found {len(captcha_info['allIframes'])} iframes:")
                    for idx, iframe in enumerate(captcha_info['allIframes'][:5]):
                        print(f"[PreSubmit]   Iframe {idx+1}: src={iframe.get('src', 'N/A')[:80]}, visible={iframe.get('visible', False)}")
                
                print("[PreSubmit] ===== END DETECTION =====")
                
                # Save structure
                import json
                structure_file = os.path.join(SCREENSHOTS_DIR, f"presubmit_captcha_{ts_presubmit}.json")
                with open(structure_file, 'w', encoding='utf-8') as f:
                    json.dump(captcha_info, f, indent=2, ensure_ascii=False)
                print(f"[PreSubmit] Saved structure: {structure_file}")
                
            except Exception as e:
                print(f"[PreSubmit] ✗ Structure capture failed: {e}")
                import traceback
                traceback.print_exc()
            
            # Try to solve CAPTCHA BEFORE submitting
            captcha_solved = False
            if sb.is_element_present('iframe[src*="recaptcha"]'):
                print("[Login] ✓ reCAPTCHA found - solving before submit...")
                try:
                    if _solve_recaptcha_with_2captcha(sb, max_wait_seconds=180, auto_submit=False, proxy_string=proxy_string):
                        print("[Login] ✓ reCAPTCHA solved successfully!")
                        captcha_solved = True
                        sb.sleep(3)  # Wait for validation
                    else:
                        print("[Login] ⚠ reCAPTCHA solving failed")
                except Exception as e:
                    print(f"[Login] reCAPTCHA error: {e}")
            
            elif sb.is_element_present('.cf-turnstile') or sb.is_element_present('input[name="cf-turnstile-response"]'):
                print("[Login] ✓ Cloudflare Turnstile found - solving before submit...")
                try:
                    if _solve_cloudflare_turnstile_with_2captcha(sb, max_wait_seconds=120, proxy_string=proxy_string):
                        print("[Login] ✓ Turnstile solved successfully!")
                        captcha_solved = True
                        sb.sleep(3)  # Wait for validation
                    else:
                        print("[Login] ⚠ Turnstile solving failed")
                except Exception as e:
                    print(f"[Login] Turnstile error: {e}")
            
            else:
                print("[Login] No CAPTCHA detected before submit")

            # Submit the form
            print("[Login] Submitting login form...")
            submitted = False
            for sel in ("#userLoginSubmitButton", "button[type='submit']", "input[type='submit']"):
                try:
                    if sb.is_element_present(sel):
                        try:
                            sb.cdp.click(sel)
                        except Exception:
                            try:
                                sb.js_click(sel)
                            except Exception:
                                sb.click(sel)
                        submitted = True
                        print(f"[Login] Clicked submit button: {sel}")
                        break
                except Exception:
                    continue
            
            if not submitted:
                print("[Login] No submit button found, trying Enter key...")
                try:
                    password_field.send_keys("\n")
                    submitted = True
                except Exception as e:
                    print(f"[Login] Enter key failed: {e}")
                    return False

            # CRITICAL: Wait for page to change and CAPTCHA to appear after submit
            print("[Login] Waiting for post-login CAPTCHA to appear...")
            sb.sleep(5.0)  # Wait for server to show CAPTCHA challenge
            
            # CAPTURE POST-LOGIN PAGE STRUCTURE
            try:
                ts_postlogin = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                
                # Save full HTML after login submit
                postlogin_html = os.path.join(SCREENSHOTS_DIR, f"postlogin_page_{ts_postlogin}.html")
                with open(postlogin_html, 'w', encoding='utf-8') as f:
                    f.write(sb.get_page_source())
                print(f"[PostLogin] Saved HTML: {postlogin_html}")
                
                # Capture post-login page structure
                postlogin_structure = sb.execute_script("""
                    function getElementInfo(el) {
                        if (!el) return null;
                        return {
                            tagName: el.tagName,
                            id: el.id,
                            className: el.className,
                            innerHTML: el.innerHTML.substring(0, 500),
                            outerHTML: el.outerHTML.substring(0, 800),
                            attributes: Array.from(el.attributes).map(a => ({name: a.name, value: a.value}))
                        };
                    }
                    
                    return {
                        // Page info
                        url: window.location.href,
                        title: document.title,
                        
                        // All iframes
                        iframes: Array.from(document.querySelectorAll('iframe')).map(iframe => ({
                            src: iframe.src,
                            id: iframe.id,
                            title: iframe.title,
                            name: iframe.name,
                            className: iframe.className,
                            width: iframe.width,
                            height: iframe.height
                        })),
                        
                        // CAPTCHA-specific elements
                        captchaElements: {
                            // reCAPTCHA
                            recaptchaIframe: getElementInfo(document.querySelector('iframe[src*="recaptcha"]')),
                            recaptchaDiv: getElementInfo(document.querySelector('.g-recaptcha')),
                            recaptchaResponse: getElementInfo(document.querySelector('#g-recaptcha-response')),
                            recaptchaV3: getElementInfo(document.querySelector('.grecaptcha-badge')),
                            
                            // Cloudflare Turnstile
                            turnstileDiv: getElementInfo(document.querySelector('.cf-turnstile')),
                            turnstileResponse: getElementInfo(document.querySelector('input[name="cf-turnstile-response"]')),
                            
                            // Generic CAPTCHA
                            captchaContainer: getElementInfo(document.querySelector('[class*="captcha"]')),
                            captchaDiv: getElementInfo(document.querySelector('#captcha')),
                            
                            // hCaptcha
                            hcaptchaIframe: getElementInfo(document.querySelector('iframe[src*="hcaptcha"]')),
                            hcaptchaDiv: getElementInfo(document.querySelector('.h-captcha'))
                        },
                        
                        // All scripts
                        scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src),
                        
                        // Forms
                        forms: Array.from(document.querySelectorAll('form')).map(form => ({
                            id: form.id,
                            name: form.name,
                            action: form.action,
                            method: form.method,
                            className: form.className
                        })),
                        
                        // Error messages
                        errorMessages: Array.from(document.querySelectorAll('.error, .alert, [class*="error"], [class*="alert"]')).map(el => ({
                            className: el.className,
                            text: el.innerText.substring(0, 200)
                        })),
                        
                        // Body text for error detection
                        bodyText: document.body.innerText
                    };
                """)
                
                # Save structure as JSON
                import json
                structure_file = os.path.join(SCREENSHOTS_DIR, f"postlogin_structure_{ts_postlogin}.json")
                with open(structure_file, 'w', encoding='utf-8') as f:
                    json.dump(postlogin_structure, f, indent=2, ensure_ascii=False)
                print(f"[PostLogin] Saved structure: {structure_file}")
                
                # Take screenshot
                screenshot_file = os.path.join(SCREENSHOTS_DIR, f"postlogin_screenshot_{ts_postlogin}.png")
                sb.save_screenshot(screenshot_file)
                print(f"[PostLogin] Screenshot: {screenshot_file}")
                
                # Analyze and print findings
                print("[PostLogin] ===== CAPTCHA ANALYSIS =====")
                print(f"[PostLogin] URL: {postlogin_structure.get('url', 'N/A')}")
                print(f"[PostLogin] Title: {postlogin_structure.get('title', 'N/A')}")
                print(f"[PostLogin] Total iframes: {len(postlogin_structure.get('iframes', []))}")
                
                # Show all iframes
                for idx, iframe in enumerate(postlogin_structure.get('iframes', [])):
                    print(f"[PostLogin]   Iframe {idx+1}: src={iframe.get('src', 'N/A')[:100]}, title={iframe.get('title', 'N/A')}")
                
                # Check CAPTCHA elements
                captcha_elems = postlogin_structure.get('captchaElements', {})
                found_captchas = []
                for elem_name, elem_data in captcha_elems.items():
                    if elem_data:
                        print(f"[PostLogin] ✓ Found: {elem_name}")
                        found_captchas.append(elem_name)
                
                if found_captchas:
                    print(f"[PostLogin] CAPTCHA types detected: {', '.join(found_captchas)}")
                else:
                    print("[PostLogin] ⚠ No CAPTCHA elements found in standard locations")
                
                # Check for error messages
                errors = postlogin_structure.get('errorMessages', [])
                if errors:
                    print(f"[PostLogin] Error messages found: {len(errors)}")
                    for err in errors[:3]:  # Show first 3
                        print(f"[PostLogin]   Error: {err.get('text', 'N/A')[:100]}")
                
                # Check body text for CAPTCHA keywords
                body_text = postlogin_structure.get('bodyText', '').lower()
                captcha_keywords = ['captcha', 'recaptcha', 'doğrulama', 'robot', 'güvenlik']
                found_keywords = [kw for kw in captcha_keywords if kw in body_text]
                if found_keywords:
                    print(f"[PostLogin] CAPTCHA keywords in text: {', '.join(found_keywords)}")
                
                print("[PostLogin] ===== END ANALYSIS =====")
                
            except Exception as e:
                print(f"[PostLogin] ✗ Structure capture failed: {e}")
                import traceback
                traceback.print_exc()
            
            # Check if CAPTCHA appeared
            has_turnstile = False
            has_recaptcha = False
            has_other_captcha = False
            
            try:
                # Check for Cloudflare Turnstile (most likely based on logs)
                has_turnstile = sb.execute_script("""
                    return document.querySelector('input[name="cf-turnstile-response"]') !== null ||
                           document.querySelector('.cf-turnstile') !== null ||
                           document.querySelector('iframe[src*="challenges.cloudflare.com"]') !== null;
                """)
                
                # Check for reCAPTCHA
                has_recaptcha = sb.is_element_present("iframe[src*='recaptcha']")
                
                # Check for generic CAPTCHA elements
                has_other_captcha = sb.execute_script("""
                    return document.querySelector('[class*="captcha"]') !== null ||
                           document.querySelector('#captcha') !== null;
                """)
            except Exception as e:
                print(f"[Login] CAPTCHA detection error: {e}")
            
            print(f"[Login] CAPTCHA detection: Turnstile={has_turnstile}, reCAPTCHA={has_recaptcha}, Other={has_other_captcha}")
            
            # If Cloudflare Turnstile detected - use uc_gui_handle_captcha()
            if has_turnstile:
                print("[Login] ✓ Cloudflare Turnstile detected after submit!")
                print("[Login] Using SeleniumBase uc_gui_handle_captcha() to solve...")
                
                try:
                    # Take screenshot before
                    ts_before = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    shot_before = os.path.join(SCREENSHOTS_DIR, f"turnstile_before_{ts_before}.png")
                    sb.save_screenshot(shot_before)
                    print(f"[Login] Screenshot before: {shot_before}")
                    
                    # Use UC Mode to handle Turnstile
                    print("[Login] Executing uc_gui_handle_captcha()...")
                    sb.uc_gui_handle_captcha()
                    print("[Login] ✓ uc_gui_handle_captcha() completed")
                    
                    # Wait for Cloudflare to validate
                    print("[Login] Waiting for Turnstile validation (10s)...")
                    sb.sleep(10)
                    
                    # Take screenshot after
                    shot_after = os.path.join(SCREENSHOTS_DIR, f"turnstile_after_{ts_before}.png")
                    sb.save_screenshot(shot_after)
                    print(f"[Login] Screenshot after: {shot_after}")
                    
                    # Check if redirected
                    current_url = sb.get_current_url().lower()
                    print(f"[Login] Current URL after Turnstile: {current_url}")
                    
                    if "giris" not in current_url and "login" not in current_url:
                        print("[Login] ✓ Turnstile solved! Redirected successfully!")
                    else:
                        print("[Login] Still on login page, may need another attempt")
                        # Try clicking submit again
                        try:
                            if sb.is_element_present("#userLoginSubmitButton"):
                                print("[Login] Clicking submit button again...")
                                sb.click("#userLoginSubmitButton")
                                sb.sleep(8)
                        except Exception:
                            pass
                    
                except Exception as e:
                    print(f"[Login] ✗ Turnstile solving error: {e}")
                    import traceback
                    traceback.print_exc()
            
            # If reCAPTCHA detected - use 2Captcha API
            elif has_recaptcha:
                print("[Login] ✓ reCAPTCHA detected after submit!")
                print("[Login] Using 2Captcha API to solve...")
                
                # Try multiple CAPTCHA solving attempts
                for attempt in range(3):
                    print(f"[Login] reCAPTCHA attempt {attempt + 1}/3")
                    
                    try:
                        if _solve_recaptcha_with_2captcha(sb, max_wait_seconds=180, auto_submit=False, proxy_string=proxy_string):
                            print("[Login] ✓ 2Captcha solution injected")
                            
                            # Wait for validation
                            print("[Login] Waiting for reCAPTCHA validation (5s)...")
                            sb.sleep(5.0)
                            
                            # Click submit button again
                            print("[Login] Clicking submit button again...")
                            for sel in ("#userLoginSubmitButton", "button[type='submit']"):
                                try:
                                    if sb.is_element_present(sel):
                                        sb.click(sel)
                                        print(f"[Login] ✓ Clicked submit: {sel}")
                                        break
                                except Exception:
                                    continue
                            
                            # Wait for redirect
                            sb.sleep(5.0)
                            current_url_check = sb.get_current_url().lower()
                            if "giris" not in current_url_check and "login" not in current_url_check:
                                print(f"[Login] ✓ Login successful! Redirected to: {current_url_check}")
                                break
                            else:
                                print(f"[Login] ⚠ Still on login page, retrying...")
                                
                    except Exception as e:
                        print(f"[Login] 2Captcha error: {e}")
                    
                    sb.sleep(2.0)
            
            # No CAPTCHA detected
            else:
                print("[Login] No CAPTCHA detected, checking if login succeeded...")
                sb.sleep(2)
                current_url = sb.get_current_url().lower()
                if "giris" not in current_url and "login" not in current_url:
                    print("[Login] ✓ Login successful without CAPTCHA!")
                    sb.get(target_url)
                    sb.sleep(2)
                    with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
                        json.dump(sb.get_cookies(), f)
                    print(f"✓ Saved session cookies to {SESSION_COOKIE_FILE}")
                    return True
            
            # Basic validation: still on login?
            def _still_on_login() -> bool:
                try:
                    u = (sb.get_current_url() or "").lower()
                except Exception:
                    u = ""
                # Exclude 2FA pages - they mean login succeeded, just needs verification
                if "iki-asamali-dogrulama" in u or "twofactor" in u:
                    return False
                if ("login" in u) or ("giris" in u):
                    return True
                try:
                    return sb.is_element_present("#username")
                except Exception:
                    return False
            
            # Wait for redirect (up to 20 seconds - increased from 10)
            print("[Login] Checking for redirect...")
            max_wait = 20
            start_wait = time.time()
            redirect_detected = False
            while time.time() - start_wait < max_wait:
                if not _still_on_login():
                    redirect_detected = True
                    print(f"[Login] ✓ Redirect detected after {int(time.time() - start_wait)}s")
                    break
                sb.sleep(0.5)
            
            if not redirect_detected:
                print(f"[Login] ⚠ No redirect after {max_wait}s")
            
            # Check login result and save diagnostics if failed
            current_url_after = ""
            try:
                current_url_after = sb.get_current_url()
                print(f"[Login] URL after submit: {current_url_after}")
            except Exception:
                pass
            
            if _still_on_login():
                # Login failed - save detailed diagnostics
                print("❌ Login did not succeed (still on login page)")
                
                try:
                    page_html = sb.get_page_source()
                    
                    # Check for Turkish error messages
                    error_indicators = {
                        "wrong_credentials": ["hatalı kullanıcı", "şifre hatalı", "geçersiz"],
                        "captcha_required": ["doğrulama", "robot", "captcha", "güvenlik"],
                        "rate_limit": ["çok fazla", "geçici olarak", "sonra tekrar"],
                        "account_locked": ["kilitlendi", "askıya alındı"],
                    }
                    
                    found_errors = []
                    for error_type, patterns in error_indicators.items():
                        for pattern in patterns:
                            if pattern in page_html.lower():
                                found_errors.append(f"{error_type}: {pattern}")
                    
                    if found_errors:
                        print(f"❌ Detected login errors: {', '.join(found_errors)}")
                    
                    # Save screenshot and HTML
                    ts_after = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    shot2 = os.path.join(SCREENSHOTS_DIR, f"login_failed_{ts_after}.png")
                    sb.save_screenshot(shot2)
                    html2_path = os.path.join(HTML_SNAPSHOTS_DIR, f"login_failed_{ts_after}.html")
                    with open(html2_path, 'w', encoding='utf-8') as f:
                        f.write(page_html)
                    
                    print(f"Saved diagnostics: {shot2}, {html2_path}")
                    
                except Exception as e:
                    print(f"Failed to save diagnostics: {e}")
                
                return False
            
            print("✓ Login appears successful (redirected away from login page)")
            
            # Check if we're on the 2FA page
            try:
                current_url_check = sb.get_current_url()
                if "iki-asamali-dogrulama" in current_url_check or "twoFactor" in current_url_check:
                    print("[Login] ⚠ 2FA page detected!")
                    print("[Login] URL: " + current_url_check)
                    print("[Login] ❌ Cannot bypass 2FA automatically.")
                    print("[Login]")
                    print("[Login] 💡 WHY IS 2FA REQUIRED?")
                    print("[Login]    Even with 2FA disabled, sahibinden.com requires it for:")
                    print("[Login]    - Logins from new/foreign IP addresses (security measure)")
                    print("[Login]    - Logins from VPN/proxy servers")
                    print("[Login]")
                    print("[Login] 🔧 SOLUTIONS:")
                    print("[Login]    1. Check proxy location - must be TURKEY (currently showing Netherlands!)")
                    print("[Login]       Set IPROYAL_PROXY_AUTH=username:password_country-tr_streaming-1")
                    print("[Login]")
                    print("[Login]    2. Manual 2FA completion:")
                    print("[Login]       - Complete 2FA verification on sahibinden.com")
                    print("[Login]       - Export cookies using browser extension")
                    print("[Login]       - Upload to /data/session_cookies.json")
                    print("[Login]")
                    print("[Login]    3. Use a Turkey-based VPS instead of proxy (recommended)")
                    print("[Login]       - Deploy on a Turkey server (Digital Ocean Istanbul, etc.)")
                    print("[Login]       - No proxy needed, sahibinden sees Turkish IP")
                    
                    # Don't save 2FA cookies - they won't work
                    return False
                    
            except Exception as e:
                print(f"[Login] Error checking for 2FA: {e}")

            # Navigate to target and save cookies
            try:
                sb.get(target_url)
                sb.sleep(1.5 + random.random() * 1.5)
            except Exception:
                pass
            try:
                _bypass_turnstile_if_present(sb, 20)
            except Exception:
                pass
            try:
                _try_uc_gui_click_captcha(sb, 45)
            except Exception:
                pass
            try:
                _solve_cloudflare_checkbox(sb, 60)
            except Exception:
                pass
            try:
                _humanize_session(sb, 6)
            except Exception:
                pass
            _accept_cookie_banner_if_any_local(sb)
            try:
                with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
                    json.dump(sb.get_cookies(), f)
                print(f"Saved session cookies to {SESSION_COOKIE_FILE} (via proxy login)")
            except Exception as e:
                print("Saving cookies failed:", e)
                return False
            return True
    except Exception as e:
        print("Proxy login session failed:", e)
        return False
    
def send_push_notification(title, body, data={}):
    if not PUSH_TOKENS:
        print("No registered devices to send notifications to.")
        return

    url = 'https://exp.host/--/api/v2/push/send'
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip, deflate',
    }
    
    proxies = _get_iproyal_requests_proxies()

    for token in PUSH_TOKENS:
        payload = {
            'to': token, 'sound': 'default', 'title': title,
            'body': body, 'data': data,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10, proxies=proxies)
            response.raise_for_status()
            print(f"Successfully sent notification to token starting with: {token[:10]}...")
        except requests.exceptions.RequestException as e:
            print(f"Error sending notification: {e}")


def _extract_img_src(element):
    try:
        img = element.find_element('css selector', 'img')
    except Exception:
        return None

    for attr in ('data-src', 'data-original', 'src'):
        try:
            val = img.get_attribute(attr) or ''
        except Exception:
            val = ''
        if val and not val.startswith('data:'):
            if val.startswith('//'):
                return 'https:' + val
            return val
    return None


def _guess_extension_from_response(resp, fallback_url):
    content_type = resp.headers.get('Content-Type') if resp is not None else None
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(';')[0].strip())
        if ext: return ext
    path = urlsplit(fallback_url).path
    base_ext = os.path.splitext(path)[1]
    if base_ext: return base_ext
    return '.jpg'


def _download_image(image_url, post_id):
    if not image_url: return None
    tmp_filename = f"{post_id}.tmp"
    tmp_path = os.path.join(IMAGES_DIR, tmp_filename)

    for name in os.listdir(IMAGES_DIR):
        if name.startswith(f"{post_id}.") and not name.endswith('.tmp'):
            return name

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        proxies = _get_iproyal_requests_proxies()
        resp = requests.get(image_url, headers=headers, timeout=10, proxies=proxies)
        if resp.status_code != 200 or not resp.content: return None
        with open(tmp_path, 'wb') as f: f.write(resp.content)
        ext = _guess_extension_from_response(resp, image_url)
        ext = ext.lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'): ext = '.jpg'
        final_filename = f"{post_id}{ext}"
        final_path = os.path.join(IMAGES_DIR, final_filename)
        try:
            if os.path.exists(final_path): os.remove(final_path)
        except Exception: pass
        os.replace(tmp_path, final_path)
        return final_filename
    except Exception as e:
        try:
            if os.path.exists(tmp_path): os.remove(tmp_path)
        except Exception: pass
        print(f"Image download failed for {image_url}: {e}")
        return None


def scrape_sahibinden(sb, url, known_posts):
    # --- config from env ---
    SESSION_COOKIES_JSON = os.getenv("SESSION_COOKIES_JSON")
    SAHIBINDEN_USER = os.getenv("SAHIBINDEN_USER", "")
    SAHIBINDEN_PASS = os.getenv("SAHIBINDEN_PASS", "")
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "1"))
    LOGIN_COOLDOWN_SEC = int(os.getenv("LOGIN_COOLDOWN_SEC", "5"))
    
    SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    # Ensure HTML snapshots directory exists for debugging login page issues
    try:
        os.makedirs(HTML_SNAPSHOTS_DIR, exist_ok=True)
    except Exception:
        pass

    if not hasattr(sb, "_login_meta"):
        sb._login_meta = {"attempts": 0, "last": 0.0}
    meta = sb._login_meta

    def _is_on_login():
        try:
            u = (sb.get_current_url() or "").lower()
            # Check URL and presence of login form elements
            if "login" in u or "giris" in u:
                print(f"Detected login URL: {u}")
                return True
            # Check for login form elements
            if sb.is_element_present("#username") or sb.is_element_present("input[name='username']"):
                print(f"Detected login form on page: {u}")
                return True
            # Check if redirected to secure login domain
            if "secure" in u and ("sahibinden" in u):
                print(f"Detected secure login domain: {u}")
                return True
            return False
        except Exception as e:
            print(f"Login detection error: {e}")
            return False

    def _accept_cookie_banner_if_any():
        try:
            if sb.is_element_present("#onetrust-accept-btn-handler"):
                sb.js_click("#onetrust-accept-btn-handler")
                time.sleep(0.5)
        except Exception:
            pass

    def _save_current_cookies():
        try:
            import json as _json
            with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
                _json.dump(sb.get_cookies(), f)
            print(f"Saved session cookies to {SESSION_COOKIE_FILE}")
        except Exception as e:
            print("save cookies failed:", e)

    # Prefer persistent session on disk; seed from env only if no file
    cookies_loaded_successfully = False
    if os.path.exists(SESSION_COOKIE_FILE):
        print(f"Attempting to load session from file: {SESSION_COOKIE_FILE}")
        try:
            with open(SESSION_COOKIE_FILE, "r", encoding="utf-8") as f:
                file_cookies = json.load(f)
            prepared = _prepare_cookies(file_cookies)
            sb.get("https://www.sahibinden.com/")
            _accept_cookie_banner_if_any()
            for host in ("https://www.sahibinden.com/", "https://secure.sahibinden.com/", "https://secure2.sahibinden.com/"):
                try:
                    _add_cookies_for_host(sb, host, prepared)
                except Exception as e:
                    print("Cookie priming on host failed:", host, e)
            if prepared:
                cookies_loaded_successfully = True
                print(f"Loaded {len(prepared)} cookies from session file.")
        except Exception as e:
            print("Failed loading cookies from file:", e)

    if (not cookies_loaded_successfully) and SESSION_COOKIES_JSON:
        print("Seeding session from SESSION_COOKIES_JSON (one-time) because no file was usable...")
        try:
            sb.get("https://www.sahibinden.com/")
            _accept_cookie_banner_if_any()
            cookies_raw = json.loads(SESSION_COOKIES_JSON)
            prepared = _prepare_cookies(cookies_raw)
            for host in (
                "https://www.sahibinden.com/",
                "https://secure.sahibinden.com/",
                "https://secure2.sahibinden.com/",
            ):
                try:
                    _add_cookies_for_host(sb, host, prepared)
                except Exception as e:
                    print("Cookie priming on host failed:", host, e)
            if prepared:
                cookies_loaded_successfully = True
                # Persist immediately so future cycles use the file
                try:
                    with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
                        json.dump(sb.get_cookies(), f)
                    print(f"Seeded and saved cookies to {SESSION_COOKIE_FILE}")
                except Exception as e:
                    print("Failed to persist seeded cookies:", e)
        except Exception as e:
            print("Seeding from env cookies failed:", e)

    if not cookies_loaded_successfully:
        print("No usable session cookies found. Login required.")
        raise NeedsLogin("No valid cookies on disk or env seed")

    # Navigate to target using the loaded/seeded session
    try:
        # Use UC mode's uc_open_with_reconnect for stealth
        print("[Scrape] Opening URL with UC mode...")
        sb.uc_open_with_reconnect(url, reconnect_time=3)
        
        # Wait for page to settle
        print("[Scrape] Waiting for page to settle...")
        sb.sleep(2)
        
        # Check if search results are already visible (no challenge)
        if sb.is_element_present("tr.searchResultsItem"):
            print("[Scrape] ✓ Search results already visible (no challenge)")
        else:
            # Use official SeleniumBase UC method to handle any Cloudflare challenge
            print("[Scrape] Handling potential Cloudflare challenge...")
            try:
                # This method handles detection, clicking, AND waiting for validation
                sb.uc_gui_handle_captcha()
                print("[Scrape] ✓ uc_gui_handle_captcha() completed")
                
                # Wait for validation and page update
                print("[Scrape] Waiting for validation...")
                sb.sleep(3)
                
                # Verify results appeared
                if sb.is_element_present("tr.searchResultsItem"):
                    print("[Scrape] ✓ Results appeared after challenge")
                else:
                    # Fallback: try click method
                    print("[Scrape] Trying fallback click method...")
                    sb.uc_gui_click_captcha()
                    sb.sleep(3)
            except Exception as e:
                print(f"[Scrape] Challenge handling error: {e}")
        
        try:
            # Use shorter dwell time for scraping (1-3s) vs login (3-8s)
            _humanize_session(sb, moves=6, dwell_time_sec=1.0 + random.random() * 2.0)
        except Exception:
            pass
        _accept_cookie_banner_if_any()
        time.sleep(1)
    except Exception as e:
        print("Navigation with session failed:", e)

    current_url = ""
    try:
        current_url = sb.get_current_url()
        print(f"Current URL after navigation: {current_url}")
    except Exception:
        pass
    
    # Check for rate-limit/bot-detection page
    try:
        page_text = sb.get_page_source().lower()
        if "olağandışı bir durum" in page_text or "unusual situation" in page_text or "destek kodu:" in page_text:
            print("⚠ Detected rate-limit/bot-detection page. Waiting before retry...")
            # Save diagnostic
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot = os.path.join(SCREENSHOTS_DIR, f"rate_limit_{ts}.png")
            sb.save_screenshot(shot)
            print(f"Saved rate-limit screenshot: {shot}")
            # Wait longer before retry (handled by caller)
            raise NeedsLogin("Rate limited - need fresh session")
    except NeedsLogin:
        raise
    except Exception:
        pass
    
    if _is_on_login():
        print(f"Redirected to login page due to expired/missing cookies. URL: {current_url}")
        raise NeedsLogin("Cookies expired")

    # Save any updated cookies back to disk to keep the latest session
    try:
        _save_current_cookies()
    except Exception:
        pass

    print("Proceeding to scrape data...")
    try:
        sb.wait_for_element_visible("tr.searchResultsItem", timeout=15)
    except Exception as e:
        print(f"Search results not found or page did not load correctly: {e}")
        # Save diagnostic info
        try:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot_path = os.path.join(SCREENSHOTS_DIR, f"no_results_{ts}.png")
            sb.save_screenshot(shot_path)
            html = sb.get_page_source()
            html_path = os.path.join(HTML_SNAPSHOTS_DIR, f"no_results_{ts}.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"Saved diagnostics: {shot_path}, {html_path}")
            print(f"Page title: {sb.get_title()}")
            print(f"Current URL: {sb.get_current_url()}")
        except Exception:
            pass
        return set(), []

    new_posts = []
    seen_new_ids = set()
    post_elements = sb.find_elements('css selector', 'tr.searchResultsItem')
    current_ids = set()
    if not post_elements:
        print("No ad listings found on the page.")
    for post in post_elements:
        post_id = post.get_attribute('data-id')
        if not post_id or 'nativeAd' in post.get_attribute('class'):
            continue
        current_ids.add(post_id)
        if post_id not in known_posts and post_id not in seen_new_ids:
            try:
                title_el = post.find_element('css selector', 'a.classifiedTitle')
                title_text = title_el.text.strip()
                if not title_text or title_text.lower().startswith('www.sahibinden.com'):
                    continue
                model = post.find_element('css selector', '.searchResultsTagAttributeValue,.searchResultsAttributeValue').text.strip()
                price = post.find_element('css selector', '.searchResultsPriceValue span').text.strip()
                href = title_el.get_attribute('href')
                parsed = urlparse(href or '')
                raw_segments = [seg for seg in (parsed.path or '').split('/') if seg]
                brand = ''
                serie = ''
                category_slugs = ['otomobil', 'arazi-suv-pickup']
                cat_idx = -1
                for slug in category_slugs:
                    if slug in raw_segments:
                        cat_idx = raw_segments.index(slug)
                        break
                if cat_idx!= -1:
                    if len(raw_segments) > cat_idx + 1:
                        brand = raw_segments[cat_idx + 1].replace('-', ' ').strip().title()
                    if len(raw_segments) > cat_idx + 2:
                        serie = raw_segments[cat_idx + 2].replace('-', ' ').strip().title()
                else:
                    IGNORE_WORDS = {'ilan', 'vasita', 'otomobil', 'arazi-suv-pickup', 'detay', 'arazi', 'suv', 'pickup'}
                    filtered_segments = [seg for seg in raw_segments if seg not in IGNORE_WORDS]
                    all_words = '-'.join(filtered_segments).split('-')
                    car_info_parts = [w for w in all_words if w]
                    if len(car_info_parts) > 0:
                        brand = car_info_parts[0].replace('-', ' ').strip().title()
                    if len(car_info_parts) > 1:
                        serie = car_info_parts[1].replace('-', ' ').strip().title()
                print(f"brand : {brand}, serie : {serie}")
                def _attr_texts(elem):
                    try:
                        cells = elem.find_elements('css selector', 'td.searchResultsAttributeValue')
                        if not cells:
                            cells = elem.find_elements('css selector', '.searchResultsAttributeValue')
                        return [c.text.strip() for c in cells if c.text]
                    except Exception:
                        return
                attrs = _attr_texts(post)
                year_val, km_val = None, None
                if attrs:
                    try:
                        year_digits = re.sub(r'[^0-9]', '', (attrs[0] if len(attrs) > 0 else ''))
                        year_val = int(year_digits) if len(year_digits) >= 4 else None
                    except Exception:
                        year_val = None
                    try:
                        km_digits = re.sub(r'[^0-9]', '', (attrs[1] if len(attrs) > 1 else ''))
                        km_val = int(km_digits) if km_digits else None
                    except Exception:
                        km_val = None
                if not all([href, brand, price, model, year_val, km_val]):
                    continue
                thumb = _extract_img_src(post)
                saved_name = _download_image(thumb, post_id) if thumb else None
                custom_title = f"{brand} {model} {title_text}"
                new_posts.append({
                    "id": post_id, "brand": brand, "serie": serie, "model": model,
                    "price": price, "url": href, "year": year_val, "km": km_val,
                    "image": saved_name, "title": custom_title,
                })
                print(f"id: {post_id}, brand: {brand}, serie: {serie}, model: {model}, price: {price}, url: {href}, year: {year_val}, km: {km_val}")
                seen_new_ids.add(post_id)
            except Exception as e:
                print(f"Error scraping post with ID {post_id}: {e}")
    if new_posts:
        print(f"Found {len(new_posts)} new posts.")
    else:
        print("Scrape complete. No new posts found on this run.")
    return current_ids, new_posts



# -------------------- Flask App & State --------------------
app = Flask(__name__)
CORS(app)

STATE_LOCK = threading.Lock()
DRIVER = None
SCRAPER_THREAD = None
STOP_EVENT = threading.Event()

FILTERS = {}
POSTS = {}
KNOWN_IDS = {}

def _load_data_from_disk():
    global FILTERS, POSTS, KNOWN_IDS, PUSH_TOKENS
    if os.path.exists(FILTERS_FILE):
        try:
            with open(FILTERS_FILE, 'r', encoding='utf-8') as f:
                items = json.load(f)
                FILTERS = {item['id']: item for item in items}
        except Exception as e: print(f"Failed to read filters.json: {e}")
    
    if os.path.exists(POSTS_FILE):
        try:
            with open(POSTS_FILE, 'r', encoding='utf-8') as f:
                POSTS = json.load(f)
        except Exception as e: print(f"Failed to read posts.json: {e}")

    if os.path.exists(KNOWN_IDS_FILE):
        try:
            with open(KNOWN_IDS_FILE, 'r', encoding='utf-8') as f:
                loaded_ids = json.load(f)
                KNOWN_IDS = {k: set(v) for k, v in loaded_ids.items()}
        except Exception as e: print(f"Failed to read known_ids.json: {e}")

    if os.path.exists(PUSH_TOKENS_FILE):
        try:
            with open(PUSH_TOKENS_FILE, 'r', encoding='utf-8') as f:
                tokens_list = json.load(f)
                PUSH_TOKENS = set(tokens_list)
                print(f"Loaded {len(PUSH_TOKENS)} push tokens from disk.")
        except Exception as e:
            print(f"Failed to read push_tokens.json: {e}")

def _save_data_to_disk():
    with STATE_LOCK:
        try:
            with open(FILTERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(list(FILTERS.values()), f, ensure_ascii=False, indent=2)
            with open(POSTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(POSTS, f, ensure_ascii=False, indent=2)
            savable_ids = {k: list(v) for k, v in KNOWN_IDS.items()}
            with open(KNOWN_IDS_FILE, 'w', encoding='utf-8') as f:
                json.dump(savable_ids, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to write data to disk: {e}")

# --- MODIFIED FOR DOCKER DEPLOYMENT ---

def _scrape_loop(poll_seconds: int = 60):
    print("Scraper loop started.")
    retry_delays = {}  # Track per-filter retry delays for backoff
    
    while not STOP_EVENT.is_set():
        try:
            with STATE_LOCK: 
                items = list(FILTERS.values())
            
            if not items:
                print("No filters configured. Scraper is idle.")
                time.sleep(poll_seconds)
                continue

            for flt in items:
                fid = flt['id']
                url = flt['url']
                
                with STATE_LOCK: 
                    known = KNOWN_IDS.setdefault(fid, set())

                need_login = False
                current_ids, new_posts = set(), []

                # 1) Try scraping WITHOUT proxy using existing cookies
                try:
                    print(f"[Scrape Loop] Starting browser session (uc=True, headless={_is_headless()})")
                    
                    with SB(
                        uc=True,  # Use undetected mode
                        headless=_is_headless(),
                        xvfb=True,
                        agent=_realistic_user_agent(),
                        locale_code="tr-TR",
                        window_size="1920,1080",
                        user_data_dir=_get_chrome_profile_dir(),
                        proxy=None,
                        chromium_arg=",".join(_get_chrome_args()),
                    ) as sb:
                        try:
                            sb.driver.execute_cdp_cmd(
                                "Page.addScriptToEvaluateOnNewDocument",
                                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                            )
                        except Exception:
                            pass
                        
                        try:
                            _apply_stealth(sb)
                        except Exception:
                            pass
                        
                        try:
                            sb.sleep(0.8 + random.random() * 0.8)
                            _humanize_session(sb, 8)
                        except Exception:
                            pass
                        
                        try:
                            current_ids, new_posts = scrape_sahibinden(sb, url, known)
                        except NeedsLogin as e:
                            if "Rate limited" in str(e):
                                print("Rate limit detected. Implementing backoff...")
                                delay = retry_delays.get(fid, 60)
                                delay = min(delay * 2, 600)
                                retry_delays[fid] = delay
                                print(f"Waiting {delay} seconds before next attempt for filter {fid}")
                                time.sleep(delay)
                                continue  # Skip to next filter
                            else:
                                print("Cookie invalid; will refresh via proxy login.")
                                need_login = True
                                retry_delays[fid] = 60  # Reset backoff
                        
                except Exception as e:
                    print(f"Non-proxy browser session failed: {e}")
                    import traceback
                    traceback.print_exc()
                    need_login = True  # Try login on unexpected errors

                # 2) If login needed, perform proxy-backed login and retry scraping
                if need_login:
                    success = login_with_proxy_and_save_cookies_with_retry(url, max_retries=3)
                    if not success:
                        print("Proxy login failed; skipping this filter this cycle.")
                        continue
                    
                    # Retry scraping without proxy using the refreshed cookies
                    try:
                        print(f"[Scrape Loop] Retry after login (uc=True, headless={_is_headless()})")
                        
                        with SB(
                            uc=True,  # Keep uc mode for consistency
                            headless=_is_headless(),
                            xvfb=True,
                            agent=_realistic_user_agent(),
                            locale_code="tr-TR",
                            window_size="1920,1080",
                            user_data_dir=_get_chrome_profile_dir(),
                            proxy=None,
                            chromium_arg=",".join(_get_chrome_args()),
                        ) as sb:
                            try:
                                sb.driver.execute_cdp_cmd(
                                    "Page.addScriptToEvaluateOnNewDocument",
                                    {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                                )
                            except Exception:
                                pass
                            
                            try:
                                _apply_stealth(sb)
                            except Exception:
                                pass
                            
                            try:
                                sb.sleep(0.8 + random.random() * 0.8)
                                _humanize_session(sb, 8)
                            except Exception:
                                pass
                            
                            try:
                                current_ids, new_posts = scrape_sahibinden(sb, url, known)
                            except Exception as e:
                                print("Scrape after login failed:", e)
                                current_ids, new_posts = set(), []
                    
                    except Exception as e:
                        print("Browser session after login failed:", e)
                        current_ids, new_posts = set(), []

                # 3) Persist results (OUTSIDE try/except blocks)
                if new_posts:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    for p in new_posts:
                        p['discovered_at'] = now_iso
                        p['filter_id'] = fid
                        p['filter_name'] = flt.get('name')
                    
                    with STATE_LOCK:
                        current_posts = POSTS.get(fid, [])
                        combined_posts = new_posts + current_posts
                        
                        unique_posts = []
                        seen_ids_in_list = set()
                        for post in combined_posts:
                            if post['id'] not in seen_ids_in_list:
                                unique_posts.append(post)
                                seen_ids_in_list.add(post['id'])
                        
                        sorted_posts = sorted(unique_posts, key=lambda p: p.get('discovered_at', ''), reverse=True)
                        POSTS[fid] = sorted_posts[:10]
                        
                        KNOWN_IDS[fid].update(p['id'] for p in new_posts)

                    print(f"Found {len(new_posts)} new posts for filter '{flt.get('name')}'. Sending notifications...")
                    
                    for post in new_posts:
                        title = post.get('title', 'New Car')
                        body = f"Price: {post.get('price', 'N/A')}"
                        send_push_notification(title, body, data={'url': post.get('url')})
                
                # Always update known IDs
                with STATE_LOCK:
                    KNOWN_IDS[fid].update(current_ids)
                
                _save_data_to_disk()
                print(f"✓ Filter '{flt.get('name')}' processing complete.")

            print(f"Scrape cycle complete. Waiting {poll_seconds} seconds...")
            time.sleep(poll_seconds)
            
        except Exception as e:
            print(f"Loop error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(poll_seconds)

    print("Scraper loop stopped.")

def _start_scraper_thread():
    global SCRAPER_THREAD
    if SCRAPER_THREAD and SCRAPER_THREAD.is_alive(): return
    STOP_EVENT.clear()
    # Use SCRAPE_INTERVAL_SEC env or default to 60s (user confirmed this works fine with cookies)
    interval = int(os.getenv("SCRAPE_INTERVAL_SEC", "60"))
    print(f"Starting scraper with {interval}s interval between cycles")
    SCRAPER_THREAD = threading.Thread(target=_scrape_loop, args=(interval,), daemon=True)
    SCRAPER_THREAD.start()

# -------------------- API Endpoints --------------------
@app.get('/health')
def health(): return jsonify({"status": "ok"})

@app.get('/')
def root(): return jsonify({
    "message": "Sahibinden tracker API", "status": "ok",
})

@app.get('/filters')
def list_filters():
    with STATE_LOCK: return jsonify(list(FILTERS.values()))

@app.post('/filters')
def create_filter():
    data = request.get_json(force=True, silent=True) or {}
    name, url = data.get('name') or 'Filtre', data.get('url')
    if not url: return jsonify({"error": "url is required"}), 400
    fid = str(uuid.uuid4())
    item = {'id': fid, 'name': name, 'url': url, 'created_at': datetime.now(timezone.utc).isoformat()}
    with STATE_LOCK:
        FILTERS[fid] = item
        KNOWN_IDS.setdefault(fid, set())
        POSTS.setdefault(fid, [])
    _save_data_to_disk()
    return jsonify(item), 201

@app.put('/filters/<fid>')
def update_filter(fid):
    data = request.get_json(force=True, silent=True) or {}
    with STATE_LOCK:
        if fid not in FILTERS:
            return jsonify({"error": "not found"}), 404
        FILTERS[fid].update({k: v for k, v in data.items() if k in ('name', 'url') and v})
    _save_data_to_disk()
    return jsonify(FILTERS[fid])

@app.delete('/filters/<fid>')
def delete_filter(fid):
    with STATE_LOCK:
        if fid not in FILTERS: return jsonify({"error": "not found"}), 404
        FILTERS.pop(fid, None)
        KNOWN_IDS.pop(fid, None)
        POSTS.pop(fid, None)
    _save_data_to_disk()
    return jsonify({"ok": True})

@app.get('/feed')
def get_feed():
    with STATE_LOCK:
        all_posts = []
        for items in POSTS.values(): all_posts.extend(items)
        merged_by_id = {p['id']: p for p in sorted(all_posts, key=lambda p: p.get('discovered_at', ''))}

    result = []
    for p in sorted(list(merged_by_id.values()), key=lambda p: p.get('discovered_at') or '', reverse=True):
        item = dict(p)
        if item.get('image'):
            item['image_url'] = request.url_root.rstrip('/') + '/images/' + item['image']
        result.append(item)
    return jsonify(result)

@app.get('/filters/<fid>/cars')
def get_filter_cars(fid):
    with STATE_LOCK:
        items = POSTS.get(fid, [])

    result = []
    for p in items:
        item = dict(p)
        if item.get('image'):
            item['image_url'] = request.url_root.rstrip('/') + '/images/' + item['image']
        result.append(item)
    return jsonify(result)

@app.get('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)

@app.post('/register-push-token')
def register_push_token():
    token = (request.get_json(force=True, silent=True) or {}).get('token')
    if token and isinstance(token, str):
        with STATE_LOCK:
            if token not in PUSH_TOKENS:
                print(f"Received and stored new push token starting with: {token[:10]}...")
                PUSH_TOKENS.add(token)
                # vvv ADD THIS BLOCK vvv
                try:
                    with open(PUSH_TOKENS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(list(PUSH_TOKENS), f)
                except Exception as e:
                    print(f"Failed to save push tokens to disk: {e}")

        return jsonify({"status": "ok"})
    return jsonify({"error": "Invalid token provided."}), 400

@app.get('/screenshots')
def list_screenshots():
    """Lists all the filenames in the screenshots directory."""
    if not os.path.isdir(SCREENSHOTS_DIR):
        return jsonify({"error": "Screenshots directory not found."}), 404
    
    files = sorted(
        [f for f in os.listdir(SCREENSHOTS_DIR) if f.endswith('.png')],
        reverse=True
    )
    return jsonify(files)

@app.get('/html_snapshots/<path:filename>')
def serve_html_snapshot(filename):
    """Serves a specific HTML snapshot file."""
    try:
        return send_from_directory(HTML_SNAPSHOTS_DIR, filename)
    except FileNotFoundError:
        abort(404)

@app.get('/screenshots/<path:filename>')
def serve_screenshot(filename):
    """Serves a specific screenshot file."""
    try:
        return send_from_directory(SCREENSHOTS_DIR, filename)
    except FileNotFoundError:
        abort(404)


# --- MODIFIED: Use a single bootstrap function ---
def _test_cookies_validity() -> bool:
    """
    Quick test to check if existing cookies are valid (not rate-limited).
    Returns True if cookies work, False if rate-limited or invalid.
    """
    try:
        print("Testing existing cookies validity...")
        with SB(
            uc=False,
            headless=True,  # Quick headless test
            xvfb=True,
            agent=_realistic_user_agent(),
            locale_code="tr-TR",
            window_size="1600,900",
            chromium_arg=",".join(_get_chrome_args()),
        ) as sb:
            # Load cookies
            if os.path.exists(SESSION_COOKIE_FILE):
                sb.get("https://www.sahibinden.com")
                with open(SESSION_COOKIE_FILE, 'r') as f:
                    cookies = json.load(f)
                for c in cookies:
                    try:
                        sb.driver.add_cookie(c)
                    except Exception:
                        pass
            
            # Test navigation to a search page
            sb.get("https://www.sahibinden.com/audi-a6?sorting=date_desc")
            sb.sleep(2)
            
            # Check for rate-limit page
            page_text = sb.get_page_source().lower()
            if "olağandışı bir durum" in page_text or "destek kodu:" in page_text:
                print("❌ Cookies are rate-limited")
                return False
            
            # Check if we're on login page
            current_url = sb.get_current_url().lower()
            if "login" in current_url or "giris" in current_url:
                print("❌ Cookies are expired (redirected to login)")
                return False
            
            print("✓ Cookies are valid")
            return True
            
    except Exception as e:
        print(f"Cookie validation test failed: {e}")
        return False

def _ensure_valid_session():
    """
    Ensure we have valid session cookies on startup.
    If no cookies exist, they're expired, OR they're rate-limited, perform proxy login.
    Set FORCE_FRESH_LOGIN=1 env to force a new proxy login regardless of existing cookies.
    """
    need_fresh_login = False
    
    # Check for forced fresh login
    if os.getenv("FORCE_FRESH_LOGIN", "0") == "1":
        print("🔄 FORCE_FRESH_LOGIN=1 detected. Deleting old cookies and forcing proxy login...")
        if os.path.exists(SESSION_COOKIE_FILE):
            try:
                os.remove(SESSION_COOKIE_FILE)
                print(f"Deleted old cookies: {SESSION_COOKIE_FILE}")
            except Exception as e:
                print(f"Failed to delete cookies: {e}")
        need_fresh_login = True
    elif not os.path.exists(SESSION_COOKIE_FILE):
        print("No session cookies found. Performing initial proxy login...")
        need_fresh_login = True
    else:
        print(f"Session cookies found at {SESSION_COOKIE_FILE}")
        # Check age
        try:
            import time as time_module
            file_age = time_module.time() - os.path.getmtime(SESSION_COOKIE_FILE)
            hours = file_age / 3600
            print(f"Session cookies age: {hours:.1f} hours")
            
            if hours > 24:
                print("Session cookies are old (>24h). Need refresh.")
                need_fresh_login = True
            else:
                # Age is OK, but test if they actually work
                if not _test_cookies_validity():
                    print("Session cookies failed validation test. Need refresh.")
                    need_fresh_login = True
        except Exception as e:
            print(f"Cookie validation error: {e}")
            need_fresh_login = True
    
    if need_fresh_login:
        print("Performing proxy login to get fresh cookies...")
        with STATE_LOCK:
            filters = list(FILTERS.values())
        target_url = filters[0]['url'] if filters else "https://www.sahibinden.com"
        success = login_with_proxy_and_save_cookies_with_retry(target_url, max_retries=3)
        if success:
            print("✓ Proxy login successful. Fresh cookies saved.")
        else:
            print("⚠ Proxy login failed. Will retry on first scrape.")

def bootstrap():
    """Load data and start background threads. Safe to call multiple times."""
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        print("--- Bootstrapping Application ---")
        _load_data_from_disk()
        
        # Run all heavy initialization in background threads to avoid blocking the first request
        def _background_init():
            try:
                print("Background initialization started...")
                _verify_chrome_binary()
                _ensure_valid_session()
                _start_scraper_thread()
                print("✓ Background initialization complete")
            except Exception as e:
                print(f"❌ Background initialization error: {e}")
                import traceback
                traceback.print_exc()
        
        try:
            t = threading.Thread(target=_background_init, daemon=True)
            t.start()
            print("✓ Bootstrap initiated (heavy tasks running in background)")
        except Exception as e:
            print(f"⚠ Bootstrap warning: {e}")
        
        _BOOTSTRAPPED = True



_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()

# DO NOT bootstrap on module import - let it happen lazily on first request
# This ensures Gunicorn workers start immediately and respond to health checks

@app.before_request
def lazy_bootstrap():
    """Lazy bootstrap: only initialize on first real request (after health checks pass)"""
    # Skip bootstrap for health check to allow Railway to verify app is running
    if request.path == '/health':
        return
    
    # Start bootstrap in background (non-blocking)
    bootstrap()

if __name__ == "__main__":
    print("--- Starting Flask Dev Server. ---")
    # This part is for local development only. Gunicorn is used in production.
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)

