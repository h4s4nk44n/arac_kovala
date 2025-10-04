from seleniumbase import Driver
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
import sys # Import sys to check the operating system

import requests

IMAGES_DIR = os.path.join(os.path.dirname(__file__), 'images')
os.makedirs(IMAGES_DIR, exist_ok=True)

PUSH_TOKENS = set()

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')

FILTERS_FILE = os.path.join(os.path.dirname(__file__), 'filters.json')
POSTS_FILE = os.path.join(os.path.dirname(__file__), 'posts.json')
KNOWN_IDS_FILE = os.path.join(os.path.dirname(__file__), 'known_ids.json')


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

def _add_cookies_for_host(driver, host_url: str, cookies: list):
    """Navigate to host_url, then add cookies. Host-only cookies only on exact host."""
    try:
        driver.get(host_url)
        time.sleep(0.5)
    except Exception as e:
        print("navigate failed:", host_url, e)
        return
    current_host = urlsplit(host_url).hostname or ""
    for c in cookies:
        cookie = {k: v for k, v in c.items() if not k.startswith("_")}
        host_hint = c.get("_host_hint", "")
        is_host_only = ("domain" not in cookie)
        if is_host_only and host_hint and host_hint != current_host:
            continue
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            print("cookie add failed:", cookie.get("name"), e)


def _prime_anon_cookies(driver):
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


def scrape_sahibinden(driver, url, known_posts):
    # --- config from env ---
    SESSION_COOKIES_JSON = os.getenv("SESSION_COOKIES_JSON")
    SAHIBINDEN_USER = os.getenv("SAHIBINDEN_USER", "")
    SAHIBINDEN_PASS = os.getenv("SAHIBINDEN_PASS", "")
    ALLOW_LOGIN = os.getenv("ALLOW_LOGIN", "1").lower() in ("1", "true", "yes")
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "1"))
    LOGIN_COOLDOWN_SEC = int(os.getenv("LOGIN_COOLDOWN_SEC", "5"))
    
    SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    meta = getattr(driver, "_login_meta", {"attempts": 0, "last": 0.0})
    driver._login_meta = meta

    def _is_on_login():
        try:
            u = (driver.current_url or "").lower()
            return ("login" in u or "giris" in u or driver.is_element_visible("#username"))
        except Exception:
            return False

    def _accept_cookie_banner_if_any():
        try:
            if driver.is_element_present("#onetrust-accept-btn-handler"):
                driver.js_click("#onetrust-accept-btn-handler")
                time.sleep(0.5)
        except Exception:
            pass

    def _handle_captcha_if_any():
        captcha_present = driver.is_element_visible('iframe[src*="recaptcha"]')
        if captcha_present:
            print("CAPTCHA detected. Attempting to click it...")
            try:
                driver.uc_gui_click_captcha()
                print("Waiting for CAPTCHA to verify after click...")
                time.sleep(3)
            except Exception as e:
                print(f"An error occurred while trying to click CAPTCHA: {e}")
        else:
            print("No CAPTCHA detected on the page. Skipping CAPTCHA click.")


    def _save_current_cookies():
        try:
            import json as _json
            with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
                _json.dump(driver.get_cookies(), f)
            print(f"Saved session cookies to {SESSION_COOKIE_FILE}")
        except Exception as e:
            print("save cookies failed:", e)

    # --- NEW: PRIMARY LOGIN STRATEGY USING COOKIES ---
    if SESSION_COOKIES_JSON:
        print("Attempting to load session from SESSION_COOKIES_JSON...")
        cookies_loaded_successfully = False
        try:
            # 1. Go to the base domain FIRST.
            driver.get("https://www.sahibinden.com/")
            _accept_cookie_banner_if_any()

            cookies = json.loads(SESSION_COOKIES_JSON)
            loaded_count = 0
            
            # 2. Add cookies one by one, skipping any that cause an error.
            for cookie in cookies:
                try:
                    # Clean the cookie to a format the driver accepts
                    clean_cookie = {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie["domain"],
                    }
                    if "path" in cookie: clean_cookie["path"] = cookie["path"]
                    if "secure" in cookie: clean_cookie["secure"] = cookie["secure"]
                    if "expiry" in cookie: clean_cookie["expiry"] = cookie["expiry"]
                    elif "expirationDate" in cookie: clean_cookie["expiry"] = int(cookie["expirationDate"])

                    driver.add_cookie(clean_cookie)
                    loaded_count += 1
                except Exception as e:
                    print(f"Warning: Could not add cookie '{cookie.get('name')}'. Reason: {e}")
            
            if loaded_count > 0:
                print(f"Successfully loaded {loaded_count}/{len(cookies)} cookies.")
                cookies_loaded_successfully = True

            # 3. Navigate to the final target URL with the active session
            print("Navigating to target URL with session...")
            driver.get(url)
            _accept_cookie_banner_if_any()
            time.sleep(2)

        except Exception as e:
            print(f"A critical error occurred during cookie loading: {e}. Falling back to standard login.")
            driver.uc_open_with_reconnect(url, 4)
            _accept_cookie_banner_if_any()
            
        if not cookies_loaded_successfully:
            driver.uc_open_with_reconnect(url, 4)
            _accept_cookie_banner_if_any()

    else:
        # Navigate normally if no cookies are provided
        driver.uc_open_with_reconnect(url, 4)
        _accept_cookie_banner_if_any()


    # ----------------- FALLBACK: LOGIN IF FORCED -----------------
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
            # (Your entire human-like login block is preserved here)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            screenshot_path = os.path.join(SCREENSHOTS_DIR, f"login_start_{timestamp}.png")
            driver.save_screenshot(screenshot_path)
            print(f"Saved initial login page screenshot to: {screenshot_path}")
            # ... (the rest of your screenshot and typing logic is here) ...
            
            print("Typing username...")
            username_field = driver.find_element("#username")
            for char in SAHIBINDEN_USER:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.08, 0.25))
            time.sleep(0.6)

            print("Typing password character by character...")
            password_field = driver.find_element("#password")
            for char in SAHIBINDEN_PASS:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.08, 0.25))

            _handle_captcha_if_any()
            
            driver.js_click("#userLoginSubmitButton")
            time.sleep(5)

            if _is_on_login():
                print("Login failed. Still on login page.")
                return set(), []
            else:
                print("Login successful! Saving cookies.")
                _save_current_cookies()

        except Exception as e:
            print(f"An exception occurred during the login process: {e}")
            return set(), []
            
    # ----------------- scrape -----------------
    print("Proceeding to scrape data...")
    try:
        driver.wait_for_element_visible("tr.searchResultsItem", timeout=15)
    except Exception as e:
        print(f"Search results not found or page did not load correctly: {e}")
        return set(), []

    new_posts = []
    seen_new_ids = set()
    post_elements = driver.find_elements('css selector', 'tr.searchResultsItem')
    current_ids = set()
    IGNORE_WORDS = {'ilan', 'vasita', 'otomobil', 'arazi-suv-pickup'}
     # 3. Add a log message if no posts are found
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

                model = post.find_element('css selector', '.searchResultsTagAttributeValue, .searchResultsAttributeValue').text.strip()

                price = post.find_element('css selector', '.searchResultsPriceValue span').text.strip()
                href = title_el.get_attribute('href')
                
                # --- CORRECTED BRAND/SERIE PARSING ---
                parsed = urlparse(href or '')
                segments = [seg for seg in (parsed.path or '').split('/') if seg]
                
                # This line filters out all the filler words from the URL
                car_info_segments = [seg for seg in segments if seg not in IGNORE_WORDS]

                brand = ''
                serie = ''

                if len(car_info_segments) > 0:
                    brand = car_info_segments[0].replace('-', ' ').strip().title()
                if len(car_info_segments) > 1:
                    serie = car_info_segments[1].replace('-', ' ').strip().title()
                # --- END OF CORRECTION ---

                def _attr_texts(elem):
                    try:
                        cells = elem.find_elements('css selector', 'td.searchResultsAttributeValue')
                        if not cells:
                            cells = elem.find_elements('css selector', '.searchResultsAttributeValue')
                        return [c.text.strip() for c in cells if c.text]
                    except Exception:
                        return []

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

                new_posts.append({
                    "id": post_id, "brand": brand, "serie": serie, "model": model,
                    "price": price, "url": href, "year": year_val, "km": km_val,
                    "image": saved_name,
                })
                seen_new_ids.add(post_id)
            except Exception as e:
                print(f"Error scraping post with ID {post_id}: {e}")

    if new_posts:
        print(f"Found {len(new_posts)} new posts.")
    else:
        print("Scrape complete. No new posts found on this run.")
    current_ids = set()
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
    global FILTERS, POSTS, KNOWN_IDS
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
def _ensure_driver():
    """Creates and returns a new Selenium driver instance."""
    print("Starting virtual display...")
    display = Display(visible=0, size=(1366, 768))
    display.start()

    print("Starting a new Selenium driver instance...")

    proxy_string = os.getenv("PROXY_STRING")
    if not proxy_string:
        print("WARNING: PROXY_STRING not set. Running without a proxy.")

    driver = Driver(
        uc=True,
        headless=False,
        no_sandbox=True,
        disable_gpu=True,
        agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/141.0.0.0 Safari/537.36"),
        locale_code="tr-TR",
        window_size="1366,768",
        proxy=proxy_string if proxy_string else None
    )

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined
                })
            """
        },
    )
    
    # We no longer need _prime_anon_cookies because the main function handles it.
    return driver, display

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
                driver = None
                display = None
                try:
                    # 1. Create a fresh driver and display for this specific scrape
                    driver, display = _ensure_driver()
                    
                    fid = flt['id']
                    url = flt['url']
                    with STATE_LOCK: known = KNOWN_IDS.setdefault(fid, set())
                    
                    current_ids, new_posts = scrape_sahibinden(driver, url, known)
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
                            title = f"{post.get('brand')} {post.get('serie')} {post.get('model')}"
                            body = f"New price: {post.get('price')}"
                            send_push_notification(title, body, data={'url': post.get('url')})
                    
                    with STATE_LOCK:
                        KNOWN_IDS[fid].update(current_ids)
                    
                    _save_data_to_disk()

                except Exception as e:
                    print(f"Error scraping {url}: {e}")
                finally:
                    # 2. IMPORTANT: Always shut down the driver and display
                    if driver:
                        driver.quit()
                    if display:
                        display.stop()
                    print("Driver and display for this run have been closed.")
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
            print(f"Received and stored new push token starting with: {token[:10]}...")
            PUSH_TOKENS.add(token)
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

