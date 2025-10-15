from seleniumbase import SB
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS
import threading
import time
import uuid
import json
import os
import re
import json
import random
from urllib.parse import urlparse
from datetime import datetime, timezone
import mimetypes
from urllib.parse import urlsplit
from pyvirtualdisplay import Display
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchFrameException
import sys # Import sys to check the operating system

import requests



PUSH_TOKENS = set()

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')
HTML_SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'html_snapshots')

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
SESSION_COOKIE_FILE = os.getenv("SESSION_COOKIE_FILE", "/app/session_cookies.json")


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


def _prime_anon_cookies(sb):
    """
    This function is intentionally left blank.
    Session handling is now managed by scrape_sahibinden.
    """
    print("Bypassing legacy cookie priming. Session will be handled by the scraper.")
    pass


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
    
    for token in PUSH_TOKENS:
        payload = {
            'to': token, 'sound': 'default', 'title': title,
            'body': body, 'data': data,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
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
        resp = requests.get(image_url, headers=headers, timeout=10)
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
            return ("login" in u or "giris" in u or sb.is_element_visible("#username"))
        except Exception:
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

    def solve_captcha_with_buster(sb):
        print("Attempting to solve CAPTCHA using Buster (audio method)...")
        try:
            # Save a screenshot before any CAPTCHA interactions
            try:
                ts_pre = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                pre_cap_path = os.path.join(SCREENSHOTS_DIR, f"captcha_before_{ts_pre}.png")
                sb.save_screenshot(pre_cap_path)
                print(f"Saved pre-captcha screenshot: {pre_cap_path}")
            except Exception as _e:
                print("Failed to capture pre-captcha screenshot:", _e)
            # Try to switch to the visible challenge iframe first; if not found, fall back to scanning all iframes
            challenge_iframe_element = None
            try:
                challenge_iframe_selector = 'iframe[title*="recaptcha challenge"], iframe[src*="bframe"], iframe[src*="recaptcha"], iframe[name^="c-"]'
                sb.wait_for_element_present(challenge_iframe_selector, timeout=20)
                challenge_iframe_element = sb.find_element("css selector", challenge_iframe_selector)
                sb.switch_to_frame(challenge_iframe_element)
                print("Switched to CAPTCHA challenge iframe (direct selector).")
            except Exception as _e1:
                print("Direct challenge iframe wait failed, scanning all iframes...", _e1)
                try:
                    # Click the anchor checkbox if present to trigger the challenge
                    try:
                        sb.switch_to_default_content()
                    except Exception:
                        pass
                    anchor_iframe_selector = 'iframe[title="reCAPTCHA"], iframe[src*="anchor"]'
                    if sb.is_element_present(anchor_iframe_selector):
                        try:
                            anchor_iframe = sb.find_element("css selector", anchor_iframe_selector)
                            sb.switch_to_frame(anchor_iframe)
                            if sb.is_element_present('#recaptcha-anchor'):
                                try:
                                    sb.click('#recaptcha-anchor')
                                except Exception:
                                    sb.js_click('#recaptcha-anchor')
                            sb.switch_to_default_content()
                            sb.sleep(1.0)
                        except Exception:
                            try:
                                sb.switch_to_default_content()
                            except Exception:
                                pass

                    # Enumerate all iframes to find the challenge
                    try:
                        frames = sb.find_elements('css selector', 'iframe')
                    except Exception:
                        frames = []
                    for fr in frames:
                        try:
                            sb.switch_to_frame(fr)
                            if sb.is_element_present('#recaptcha-audio-button') or sb.is_element_present('.rc-imageselect') or sb.is_element_present('button#recaptcha-verify-button'):
                                challenge_iframe_element = fr
                                print("Found CAPTCHA challenge by scanning iframes.")
                                break
                            sb.switch_to_default_content()
                        except Exception:
                            try:
                                sb.switch_to_default_content()
                            except Exception:
                                pass
                    if challenge_iframe_element is None:
                        print("Could not locate a visible reCAPTCHA challenge iframe.")
                        return False
                except Exception as _e2:
                    print("Scanning iframes for challenge failed:", _e2)
                    return False

            # Screenshot inside the challenge for debugging
            try:
                ts_cap = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                sb.save_screenshot(os.path.join(SCREENSHOTS_DIR, f"captcha_iframe_{ts_cap}.png"))
            except Exception:
                pass

            # Click the audio button to get an audio challenge
            audio_button_selector = "#recaptcha-audio-button"
            sb.wait_for_element_visible(audio_button_selector, timeout=15)
            try:
                sb.hover(audio_button_selector)
            except Exception:
                pass
            try:
                sb.uc_click(audio_button_selector)
            except Exception:
                sb.js_click(audio_button_selector)
            print("Hovered and clicked the audio challenge button.")

            sb.sleep(random.uniform(1.5, 2.5))

            # Try multiple selectors for the Buster button; extension may change over time
            buster_selectors = [
                ".help-button-holder",  # host with shadow-root (closed)
                "#help-button-holder",
                "#solver-button",       # inside shadow root (may not be directly reachable)
            ]

            found_selector = None
            # Poll briefly for the button to render; search current frame, then all frames
            for _ in range(30):  # ~15 seconds total
                # Check current context first
                for sel in buster_selectors:
                    try:
                        if sb.is_element_present(sel):
                            found_selector = sel
                            break
                    except Exception:
                        continue
                if found_selector:
                    break

                # Search other iframes
                try:
                    sb.switch_to_default_content()
                    frames2 = sb.find_elements('css selector', 'iframe')
                except Exception:
                    frames2 = []
                for fr2 in frames2:
                    try:
                        sb.switch_to_frame(fr2)
                        for sel in buster_selectors:
                            try:
                                if sb.is_element_present(sel):
                                    found_selector = sel
                                    break
                            except Exception:
                                continue
                        if found_selector:
                            break
                        sb.switch_to_default_content()
                    except Exception:
                        try:
                            sb.switch_to_default_content()
                        except Exception:
                            pass
                if found_selector:
                    break
                sb.sleep(0.5)

            if found_selector:
                print("Buster UI found (selector):", found_selector)
                # Ensure the host is in view
                try:
                    el = sb.find_element("css selector", found_selector)
                    sb.driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
                except Exception:
                    el = None
                sb.sleep(0.3)

                # Try multiple click strategies, prioritizing GUI and coordinate clicks
                clicked = False
                for click_try in ("cdp_gui", "coords", "cdp", "normal", "js"):
                    try:
                        if click_try == "cdp_gui":
                            sb.cdp.gui_click_element(found_selector)
                        elif click_try == "coords":
                            if el is None:
                                el = sb.find_element("css selector", found_selector)
                            # Use viewport coordinates from boundingClientRect
                            rect = sb.driver.execute_script("var r = arguments[0].getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2, left:r.left, top:r.top, width:r.width, height:r.height};", el)
                            cx, cy = int(rect.get('x', 0)), int(rect.get('y', 0))
                            # Try a few jittered clicks
                            for dx, dy in ((0,0), (2,2), (-2,-2)):
                                sb.cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx+dx, "y": cy+dy, "button": "left", "clickCount": 1})
                                sb.cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx+dx, "y": cy+dy, "button": "left", "clickCount": 1})
                                sb.sleep(0.2)
                        elif click_try == "cdp":
                            sb.cdp.click(found_selector)
                        elif click_try == "normal":
                            sb.click(found_selector)
                        else:
                            sb.js_click(found_selector)
                        clicked = True
                        print("Clicked Buster via:", click_try)
                        # small wait after click to allow UI to respond
                        sb.sleep(1.0)
                        break
                    except Exception:
                        continue
                if not clicked:
                    print("Falling back to Buster hotkey (ALT+SHIFT+B)")
                    try:
                        sb.press_keys("body", "ALT+SHIFT+B")
                    except Exception:
                        try:
                            sb.cdp.press_keys("body", "ALT+SHIFT+B")
                        except Exception:
                            pass
            else:
                print("Buster UI not found. Trying hotkeys as fallback...")
                for keys in ("ALT+SHIFT+B", "ALT+B", "CTRL+B"):
                    try:
                        sb.press_keys("body", keys)
                        sb.sleep(1.0)
                        break
                    except Exception:
                        try:
                            sb.cdp.press_keys("body", keys)
                            sb.sleep(1.0)
                            break
                        except Exception:
                            continue

            # Return to default content and wait for the challenge iframe to disappear
            sb.switch_to_default_content()
            print("Waiting for Buster to solve the audio challenge...")

            try:
                long_wait = WebDriverWait(sb.driver, 180)
                long_wait.until(EC.staleness_of(challenge_iframe_element))
                print("CAPTCHA solved successfully! The challenge has disappeared.")
                return True
            except Exception:
                print("Challenge iframe still present after waiting. CAPTCHA may not be solved.")
                return False
        except (TimeoutException, NoSuchFrameException):
            print("CAPTCHA challenge did not appear as expected or an element was not found.")
            print("Assuming no CAPTCHA was needed or it was solved by other means.")
            try:
                sb.switch_to_default_content()
            except Exception:
                pass
            return False
        except Exception as e:
            print(f"An unexpected error occurred during CAPTCHA solving: {e}")
            try:
                sb.switch_to_default_content()
            except Exception:
                pass
            return False

    if SESSION_COOKIES_JSON:
        print("Attempting to load session from SESSION_COOKIES_JSON...")
        cookies_loaded_successfully = False
        try:
            sb.get("https://www.sahibinden.com/")
            _accept_cookie_banner_if_any()
            cookies_raw = json.loads(SESSION_COOKIES_JSON)
            prepared = []
            for cookie in cookies_raw:
                try:
                    clean_cookie = { "name": cookie.get("name"), "value": cookie.get("value") }
                    if cookie.get("path"): clean_cookie["path"] = cookie["path"]
                    if "secure" in cookie: clean_cookie["secure"] = bool(cookie.get("secure"))
                    # Normalize expiry
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
                except Exception as e:
                    print(f"Warning: Could not prepare cookie '{cookie.get('name')}': {e}")
            # Prime cookies on relevant hosts
            hosts = [
                "https://www.sahibinden.com/",
                "https://secure.sahibinden.com/",
                "https://secure2.sahibinden.com/",
            ]
            for host in hosts:
                try:
                    _add_cookies_for_host(sb, host, prepared)
                except Exception as e:
                    print("Cookie priming on host failed:", host, e)
            loaded_count = len(prepared)
            if loaded_count > 0:
                print(f"Successfully loaded {loaded_count}/{len(cookies_raw)} cookies.")
                cookies_loaded_successfully = True
            print("Navigating to target URL with session...")
            sb.get(url)
            _accept_cookie_banner_if_any()
            time.sleep(2)
        except Exception as e:
            print(f"A critical error occurred during cookie loading: {e}. Falling back to standard login.")
            sb.uc_open_with_reconnect(url, 4)
            _accept_cookie_banner_if_any()
        if not cookies_loaded_successfully:
            sb.uc_open_with_reconnect(url, 4)
            _accept_cookie_banner_if_any()
    else:
        sb.uc_open_with_reconnect(url, 4)
        _accept_cookie_banner_if_any()

    if _is_on_login():
        print("Redirected to login page. Attempting robust login...")
        now = time.time()
        if meta["attempts"] >= MAX_LOGIN_ATTEMPTS:
            print("Max login attempts reached; backing off.")
            return set(), []
        if meta["attempts"] > 0 and now - meta["last"] < LOGIN_COOLDOWN_SEC:
            wait_left = int(LOGIN_COOLDOWN_SEC - (now - meta["last"]))
            print(f"Login cooldown active ({wait_left}s left); skipping.")
            return set(), []
        meta["attempts"] += 1
        meta["last"] = now
        try:
            print("Activating CDP Mode for stealthy login...")
            sb.activate_cdp_mode()
            # Capture immediate screenshot and HTML snapshot for diagnostics
            try:
                ts0 = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                pre_login_shot = os.path.join(SCREENSHOTS_DIR, f"login_page_{ts0}.png")
                sb.save_screenshot(pre_login_shot)
                try:
                    html_source = sb.get_page_source()
                except Exception:
                    try:
                        html_source = sb.driver.page_source
                    except Exception:
                        html_source = ""
                if html_source:
                    pre_login_html = os.path.join(HTML_SNAPSHOTS_DIR, f"login_page_{ts0}.html")
                    with open(pre_login_html, 'w', encoding='utf-8') as f:
                        f.write(html_source)
                print(f"Saved login page screenshot: {pre_login_shot}")
            except Exception as _e:
                print(f"Failed to capture early login snapshots: {_e}")

            # In case an extension opened a new tab, switch to last tab and ensure page is ready
            try:
                handles = sb.driver.window_handles
                if handles:
                    sb.driver.switch_to.window(handles[-1])
                    try:
                        print("Switched to tab:", sb.get_current_url())
                    except Exception:
                        pass
            except Exception as _e:
                print("Window handle switch failed:", _e)

            try:
                sb.wait_for_ready_state_complete()
            except Exception:
                pass

            # If the page appears blank or fields missing, navigate directly to known login URLs
            try:
                page_source_len = len((sb.driver.page_source or "").strip())
            except Exception:
                page_source_len = 0
            if page_source_len < 300 or not sb.is_element_present("#username"):
                LOGIN_URLS = [
                    "https://secure2.sahibinden.com/giris",
                    "https://secure.sahibinden.com/giris",
                    "https://www.sahibinden.com/giris",
                ]
                for login_url in LOGIN_URLS:
                    try:
                        print("Navigating directly to login URL:", login_url)
                        sb.uc_open_with_reconnect(login_url, 4)
                        _accept_cookie_banner_if_any()
                        sb.wait_for_ready_state_complete()
                        if sb.is_element_present("#username") and sb.is_element_present("#password"):
                            break
                        sb.sleep(1.0)
                    except Exception as _e:
                        print("Login URL attempt failed:", _e)

                # Save another snapshot after navigation attempts
                try:
                    ts1 = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    post_nav_shot = os.path.join(SCREENSHOTS_DIR, f"login_page_after_nav_{ts1}.png")
                    sb.save_screenshot(post_nav_shot)
                    html_source2 = ""
                    try:
                        html_source2 = sb.get_page_source()
                    except Exception:
                        try:
                            html_source2 = sb.driver.page_source
                        except Exception:
                            html_source2 = ""
                    if html_source2:
                        post_nav_html = os.path.join(HTML_SNAPSHOTS_DIR, f"login_page_after_nav_{ts1}.html")
                        with open(post_nav_html, 'w', encoding='utf-8') as f:
                            f.write(html_source2)
                    print(f"Saved post-navigation login page screenshot: {post_nav_shot}")
                except Exception as _e:
                    print("Failed to capture post-navigation snapshots:", _e)
            # Ensure consent overlays don't block fields on the login page
            _accept_cookie_banner_if_any()
            # Wait for fields to be visible
            try:
                sb.wait_for_element_visible("#username", timeout=20)
                sb.wait_for_element_visible("#password", timeout=20)
            except Exception:
                # Give the page a moment and try once more
                sb.sleep(1.5)
                sb.wait_for_element_visible("#username", timeout=10)
                sb.wait_for_element_visible("#password", timeout=10)
            print("Typing username using CDP...")
            try:
                sb.cdp.click("#username")
            except Exception:
                pass
            try:
                sb.cdp.press_keys("#username", SAHIBINDEN_USER)
            except Exception:
                sb.type("#username", SAHIBINDEN_USER)
            sb.sleep(random.uniform(0.5, 1.0))
            print("Typing password using CDP...")
            try:
                sb.cdp.click("#password")
            except Exception:
                pass
            try:
                sb.cdp.press_keys("#password", SAHIBINDEN_PASS)
            except Exception:
                sb.type("#password", SAHIBINDEN_PASS)
            sb.sleep(random.uniform(0.5, 1.0))
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            screenshot_path = os.path.join(SCREENSHOTS_DIR, f"before_login_click_{timestamp}.png")
            sb.save_screenshot(screenshot_path)
            print(f"Saved screenshot before login click to: {screenshot_path}")
            print("Clicking login button using CDP...")
            clicked_login = False
            # Try visible submit elements first
            for sel in ("#userLoginSubmitButton", "button[type='submit']", "input[type='submit']"):
                try:
                    if sb.is_element_visible(sel):
                        sb.cdp.click(sel)
                        clicked_login = True
                        break
                except Exception:
                    continue
            if not clicked_login:
                # Try regular click
                for sel in ("#userLoginSubmitButton", "button[type='submit']", "input[type='submit']"):
                    try:
                        if sb.is_element_present(sel):
                            sb.click(sel)
                            clicked_login = True
                            break
                    except Exception:
                        continue
            if not clicked_login:
                try:
                    sb.cdp.press_keys("#password", "\n")
                    clicked_login = True
                except Exception:
                    try:
                        sb.press_keys("#password", "\n")
                    except Exception:
                        pass
            print("Waiting for page to react after login click...")
            sb.sleep(5)
            # Try solving captcha, but don't fail the whole flow if Buster UI isn't present
            try:
                solve_captcha_with_buster(sb)
            except Exception as _e:
                print("Captcha solver raised:", _e)
            sb.sleep(3)
            if _is_on_login():
                print("Login failed, still on login page after CAPTCHA attempt.")
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                screenshot_path = os.path.join(SCREENSHOTS_DIR, f"login_failed_{timestamp}.png")
                sb.save_screenshot(screenshot_path)
                return set(), []
            else:
                print("Login successful!")
                _save_current_cookies()
        except Exception as e:
            print(f"An exception occurred during the CDP login process: {e}")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            screenshot_path = os.path.join(SCREENSHOTS_DIR, f"login_error_{timestamp}.png")
            sb.save_screenshot(screenshot_path)
            return set(), []

    print("Proceeding to scrape data...")
    try:
        sb.wait_for_element_visible("tr.searchResultsItem", timeout=15)
    except Exception as e:
        print(f"Search results not found or page did not load correctly: {e}")
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
    while not STOP_EVENT.is_set():
        try:
            with STATE_LOCK: items = list(FILTERS.values())
            if not items:
                print("No filters configured. Scraper is idle.")
                time.sleep(poll_seconds)
                continue

            for flt in items:
                proxy_string = os.getenv("PROXY_STRING")
                if not proxy_string:
                    print("WARNING: PROXY_STRING not set. Running without a proxy.")
                
                try:
                    # CORRECTED: Removed invalid 'no_sandbox' and 'disable_gpu' arguments
                    with SB(
                        uc=True,
                        headless=False,  # Must be False for xvfb and GUI actions
                        xvfb=True,       # Use virtual display on server
                        agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
                        locale_code="tr-TR",
                        window_size="1366,768",
                        proxy=proxy_string if proxy_string else None,
                        extension_dir="buster_chrome"
                    ) as sb:
                        sb.driver.execute_cdp_cmd(
                            "Page.addScriptToEvaluateOnNewDocument",
                            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                        )

                        fid = flt['id']
                        url = flt['url']
                        with STATE_LOCK: known = KNOWN_IDS.setdefault(fid, set())
                        
                        current_ids, new_posts = scrape_sahibinden(sb, url, known)
                        
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

                            print(f"Found {len(new_posts)} new posts for filter '{flt.get('name')}'. List capped at 10. Sending notifications...")
                            
                            for post in new_posts:
                                title = f"{post.get('title')}"
                                body = f"Price: {post.get('price')}"
                                send_push_notification(title, body, data={'url': post.get('url')})
                        
                        with STATE_LOCK:
                            KNOWN_IDS[fid].update(current_ids)
                        
                        _save_data_to_disk()

                except Exception as e:
                    print(f"Error during SB session for {flt.get('url')}: {e}")
                
                print("SB session for this run has been closed.")

            print(f"Scrape cycle complete. Waiting for {poll_seconds} seconds...")
            time.sleep(poll_seconds)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(poll_seconds)

    print("Scraper loop stopped.")

def _start_scraper_thread():
    global SCRAPER_THREAD
    if SCRAPER_THREAD and SCRAPER_THREAD.is_alive(): return
    STOP_EVENT.clear()
    SCRAPER_THREAD = threading.Thread(target=_scrape_loop, args=(60,), daemon=True)
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
def bootstrap():
    """Load data and start background threads. Safe to call multiple times."""
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        print("--- Bootstrapping Application ---")
        _load_data_from_disk()
        _start_scraper_thread()
        _BOOTSTRAPPED = True



_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()

# When imported by Gunicorn (not __main__), Gunicorn calls the 'app' object.
# We need to ensure bootstrap() is called before the first request.
@app.before_request
def before_request_func():
    bootstrap()

if __name__ == "__main__":
    bootstrap()
    print("--- Initialization Complete. Starting Flask Dev Server. ---")
    # This part is for local development only. Gunicorn is used in production.
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)

