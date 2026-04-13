import os
import re
import json
import time
import random
from datetime import datetime, timezone
from urllib.parse import urlparse

from scrapling.fetchers import StealthyFetcher

import config
from proxy_utils import _get_proxy_url
from cookie_utils import save_cookies, load_cookies
from image_utils import _extract_img_src, _download_image
from captcha import solve_turnstile_if_present, solve_recaptcha_v2_if_present
from state import STATE_LOCK, FILTERS, POSTS, KNOWN_IDS, _save_data_to_disk
from notifications import send_new_post_notification


class NeedsLogin(Exception):
    pass


class Scraper:
    def __init__(self):
        self.last_login_time = 0
        self.consecutive_errors = 0
        self.current_backoff = config.SCRAPE_INTERVAL_SEC

    # ------------------------------------------------------------------
    # Shared fetch kwargs
    # ------------------------------------------------------------------
    def _fetch_kwargs(self, use_proxy=False):
        """Return common kwargs for StealthyFetcher.fetch()."""
        kwargs = dict(
            headless=True,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
            network_idle=True,
            timeout=60000,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        if use_proxy:
            proxy_url = _get_proxy_url(rotate_session=True)
            if proxy_url:
                kwargs["proxy"] = proxy_url
        return kwargs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _accept_cookie_banner(page):
        """Dismiss OneTrust cookie banner if present (Playwright Page)."""
        try:
            btn = page.query_selector("#onetrust-accept-btn-handler")
            if btn:
                btn.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

    @staticmethod
    def _save_diagnostic_from_page(page, prefix):
        """Save screenshot and HTML from a Playwright Page (inside page_action)."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot = os.path.join(str(config.SCREENSHOTS_DIR), f"{prefix}_{ts}.png")
            page.screenshot(path=shot)
            html_path = os.path.join(str(config.HTML_SNAPSHOTS_DIR), f"{prefix}_{ts}.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(page.content())
            print(f"[Diag] Saved: {shot}, {html_path}")
        except Exception:
            pass

    @staticmethod
    def _save_diagnostic_from_response(response, prefix):
        """Save HTML snapshot from a Scrapling response object."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            html_path = os.path.join(str(config.HTML_SNAPSHOTS_DIR), f"{prefix}_{ts}.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(str(response.html_content) if hasattr(response, 'html_content') else str(response))
            print(f"[Diag] Saved HTML: {html_path}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def login(self) -> bool:
        """Perform proxy-backed login via Scrapling StealthyFetcher. Returns True on success."""
        proxy_url = _get_proxy_url(rotate_session=True)
        if not proxy_url:
            print("[Login] No proxy configured. Cannot login from abroad.")
            return False
        if not config.SAHIBINDEN_USER or not config.SAHIBINDEN_PASS:
            print("[Login] Missing SAHIBINDEN_USER/SAHIBINDEN_PASS")
            return False

        print(f"[Login] Starting proxy login session...")

        # Shared result container for the page_action callback
        result = {"success": False, "cookies": []}

        def _login_action(page):
            """Interactive login flow executed inside the browser context."""

            # Dismiss cookie banner
            Scraper._accept_cookie_banner(page)

            # Wait for login form
            try:
                page.wait_for_selector("#username", timeout=15000)
            except Exception:
                print("[Login] Login form not visible after Cloudflare bypass")
                Scraper._save_diagnostic_from_page(page, "login_no_form")
                return

            print("[Login] Login form found, entering credentials...")

            # Type username with human-like delays
            page.wait_for_timeout(500 + int(random.random() * 1000))
            page.click("#username")
            page.wait_for_timeout(300)
            page.type("#username", config.SAHIBINDEN_USER, delay=50 + int(random.random() * 100))

            page.wait_for_timeout(500 + int(random.random() * 500))

            # Type password with human-like delays
            page.click("#password")
            page.wait_for_timeout(300)
            page.type("#password", config.SAHIBINDEN_PASS, delay=50 + int(random.random() * 100))

            # Wait for Turnstile widget to render
            page.wait_for_timeout(3000)

            # Solve embedded Turnstile via CapSolver BEFORE submitting
            has_turnstile = page.evaluate("""() => {
                return !!(document.querySelector('#cloudflareTurnStileSiteKey')
                       || document.querySelector('#turnStileWidget'));
            }""")
            if has_turnstile:
                print("[Login] Turnstile detected on form, solving via CapSolver...")
                solve_turnstile_if_present(page)

            # Submit form via form.submit() to bypass client-side Turnstile validation
            page.wait_for_timeout(1000 + int(random.random() * 1000))
            print("[Login] Submitting form...")
            page.evaluate("""() => {
                const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                          || document.querySelector('form');
                if (form) { form.onsubmit = null; form.submit(); }
            }""")

            page.wait_for_timeout(5000)

            # If still on login page, check for post-submit Turnstile or reCAPTCHA
            current = (page.url or "").lower()
            if "giris" in current or "login" in current:
                # Try Turnstile again (may appear after submit)
                turnstile_after = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
                if turnstile_after:
                    print("[Login] Turnstile appeared after submit, solving again...")
                    if solve_turnstile_if_present(page):
                        page.evaluate("""() => {
                            const form = document.querySelector('#userLoginSubmitButton')?.closest('form')
                                      || document.querySelector('form');
                            if (form) { form.onsubmit = null; form.submit(); }
                        }""")
                        page.wait_for_timeout(5000)

                # Check for reCAPTCHA v2
                try:
                    recaptcha_iframe = page.query_selector(
                        'iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha/api2"]'
                    )
                    if recaptcha_iframe:
                        print("[Login] reCAPTCHA v2 detected, solving with CapSolver...")
                        if solve_recaptcha_v2_if_present(page):
                            print("[Login] reCAPTCHA solved, waiting for redirect...")
                            page.wait_for_timeout(5000)
                except Exception:
                    pass

            page.wait_for_timeout(3000)

            # Check result
            current_url = (page.url or "").lower()
            print(f"[Login] Post-login URL: {current_url}")

            if "iki-asamali" in current_url or "twofactor" in current_url:
                print("[Login] 2FA required - need Turkey IP or manual completion")
                Scraper._save_diagnostic_from_page(page, "login_2fa")
                return

            if "login" in current_url or "giris" in current_url:
                print("[Login] Still on login page - login failed")
                Scraper._save_diagnostic_from_page(page, "login_failed")
                return

            # Extract and save cookies
            try:
                result["cookies"] = page.context.cookies()
                result["success"] = True
                print(f"[Login] Got {len(result['cookies'])} cookies")
            except Exception as e:
                print(f"[Login] Failed to extract cookies: {e}")

        try:
            StealthyFetcher.fetch(
                url="https://www.sahibinden.com/giris",
                headless=True,
                proxy=proxy_url,
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
                self.last_login_time = time.time()
                print(f"[Login] Login successful! Cookies saved to {config.SESSION_COOKIE_FILE}")
                return True

            print("[Login] Login did not succeed")
            return False

        except Exception as e:
            print(f"[Login] Error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def login_with_retry(self, max_retries: int = 3) -> bool:
        """Login with exponential backoff retry."""
        for attempt in range(1, max_retries + 1):
            print(f"[Login] Attempt {attempt}/{max_retries}")

            if attempt > 1 and os.path.exists(config.SESSION_COOKIE_FILE):
                print("[Login] Found existing cookies from previous attempt, using those")
                return True

            if self.login():
                return True

            if os.path.exists(config.SESSION_COOKIE_FILE):
                print("[Login] Cookies saved despite login issues, proceeding")
                return True

            if attempt < max_retries:
                wait = 30 * (2 ** (attempt - 1))
                print(f"[Login] Waiting {wait}s before retry...")
                time.sleep(wait)

        print(f"[Login] All {max_retries} attempts failed")
        return False

    # ------------------------------------------------------------------
    # Session check
    # ------------------------------------------------------------------
    def _check_cookies_valid(self) -> bool:
        """Check if session cookie file exists and is fresh enough."""
        if not os.path.exists(config.SESSION_COOKIE_FILE):
            return False
        try:
            file_age_hours = (time.time() - os.path.getmtime(config.SESSION_COOKIE_FILE)) / 3600
            if file_age_hours > config.COOKIE_REFRESH_HOURS:
                print(f"Cookies are {file_age_hours:.1f}h old (>{config.COOKIE_REFRESH_HOURS}h), need refresh")
                return False
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------
    def _scrape_single_filter(self, url, known_ids) -> tuple:
        """Scrape a single filter URL. Returns (current_ids, new_posts).
        Raises NeedsLogin if session is invalid."""

        cookies = load_cookies(config.SESSION_COOKIE_FILE)
        if not cookies:
            raise NeedsLogin("No cookies available")

        print(f"[Scrape] Opening: {url}")

        # Shared state for page_action
        scrape_result = {
            "needs_login": False,
            "updated_cookies": None,
        }

        def _scrape_action(page):
            """Inject cookies and check session validity inside browser context."""
            # Inject cookies from file
            try:
                page.context.add_cookies(cookies)
            except Exception as e:
                print(f"[Scrape] Failed to inject cookies: {e}")

            # Reload with cookies applied
            page.reload()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            Scraper._accept_cookie_banner(page)

            # Check for rate-limit
            try:
                page_text = (page.content() or "").lower()
                if "destek kodu:" in page_text:
                    print("[Scrape] Rate limited!")
                    Scraper._save_diagnostic_from_page(page, "rate_limit")
                    scrape_result["needs_login"] = True
                    return
            except Exception:
                pass

            # Check if redirected to login
            cur = (page.url or "").lower()
            if "login" in cur or "giris" in cur:
                scrape_result["needs_login"] = True
                return

            # Check for login form
            try:
                if page.query_selector("#username"):
                    scrape_result["needs_login"] = True
                    return
            except Exception:
                pass

            # Save updated cookies
            try:
                scrape_result["updated_cookies"] = page.context.cookies()
            except Exception:
                pass

        try:
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
        except Exception as e:
            print(f"[Scrape] Fetch failed: {e}")
            raise NeedsLogin(f"Fetch error: {e}")

        if scrape_result["needs_login"]:
            if scrape_result.get("_rate_limited"):
                raise NeedsLogin("Rate limited - need fresh session")
            raise NeedsLogin("Cookies expired")

        # Save updated cookies if we got them
        if scrape_result["updated_cookies"]:
            save_cookies(scrape_result["updated_cookies"], config.SESSION_COOKIE_FILE)
            self.last_login_time = time.time()

        # Parse listings from the returned Scrapling response
        return self._parse_listings(response, known_ids)

    def _parse_listings(self, page, known_ids) -> tuple:
        """Parse search result listings from a Scrapling response object.
        Returns (current_ids, new_posts)."""

        new_posts = []
        seen_new_ids = set()
        current_ids = set()

        try:
            post_elements = page.css('tr.searchResultsItem')
        except Exception:
            post_elements = []

        if not post_elements:
            print("[Scrape] No search results found")
            self._save_diagnostic_from_response(page, "no_results")
            return set(), []

        for post in post_elements:
            try:
                post_id = post.attrib.get('data-id')
            except Exception:
                continue
            if not post_id:
                continue
            try:
                post_class = post.attrib.get('class', '')
            except Exception:
                post_class = ''
            if 'nativeAd' in post_class:
                continue
            current_ids.add(post_id)

            if post_id in known_ids or post_id in seen_new_ids:
                continue

            try:
                # Extract title and link
                title_els = post.css('a.classifiedTitle')
                if not title_els:
                    continue
                title_el = title_els[0]
                title_text = (title_el.text or '').strip()
                if not title_text or title_text.lower().startswith('www.sahibinden.com'):
                    continue

                # Extract model
                model_els = post.css('.searchResultsTagAttributeValue, .searchResultsAttributeValue')
                model = (model_els[0].text or '').strip() if model_els else ''

                # Extract price
                price_els = post.css('.searchResultsPriceValue span')
                price = (price_els[0].text or '').strip() if price_els else ''

                # Extract href
                href = title_el.attrib.get('href', '')
                parsed = urlparse(href)
                raw_segments = [seg for seg in (parsed.path or '').split('/') if seg]

                # Extract brand/serie from URL
                brand, serie = '', ''
                category_slugs = ['otomobil', 'arazi-suv-pickup']
                cat_idx = -1
                for slug in category_slugs:
                    if slug in raw_segments:
                        cat_idx = raw_segments.index(slug)
                        break
                if cat_idx != -1:
                    if len(raw_segments) > cat_idx + 1:
                        brand = raw_segments[cat_idx + 1].replace('-', ' ').strip().title()
                    if len(raw_segments) > cat_idx + 2:
                        serie = raw_segments[cat_idx + 2].replace('-', ' ').strip().title()
                else:
                    IGNORE = {'ilan', 'vasita', 'otomobil', 'arazi-suv-pickup', 'detay', 'arazi', 'suv', 'pickup'}
                    filtered = [seg for seg in raw_segments if seg not in IGNORE]
                    words = '-'.join(filtered).split('-')
                    words = [w for w in words if w]
                    if words:
                        brand = words[0].replace('-', ' ').strip().title()
                    if len(words) > 1:
                        serie = words[1].replace('-', ' ').strip().title()

                # Extract year and km
                try:
                    cells = post.css('td.searchResultsAttributeValue')
                    if not cells:
                        cells = post.css('.searchResultsAttributeValue')
                    attrs = [(c.text or '').strip() for c in cells if c.text]
                except Exception:
                    attrs = []

                year_val, km_val = None, None
                if attrs:
                    try:
                        yd = re.sub(r'[^0-9]', '', attrs[0] if attrs else '')
                        year_val = int(yd) if len(yd) >= 4 else None
                    except Exception:
                        pass
                    try:
                        kd = re.sub(r'[^0-9]', '', attrs[1] if len(attrs) > 1 else '')
                        km_val = int(kd) if kd else None
                    except Exception:
                        pass

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
                seen_new_ids.add(post_id)
            except Exception as e:
                print(f"Error scraping post {post_id}: {e}")

        if new_posts:
            print(f"Found {len(new_posts)} new posts.")
        else:
            print("No new posts found.")
        return current_ids, new_posts

    def scrape_filters(self):
        """Scrape all configured filters."""
        with STATE_LOCK:
            items = list(FILTERS.values())

        if not items:
            print("No filters configured. Scraper idle.")
            return

        for flt in items:
            fid = flt['id']
            url = flt['url']

            with STATE_LOCK:
                known = KNOWN_IDS.setdefault(fid, set())

            need_login = False
            current_ids, new_posts = set(), []

            # 1) Try scraping using existing cookies
            if self._check_cookies_valid():
                try:
                    current_ids, new_posts = self._scrape_single_filter(url, known)
                except NeedsLogin as e:
                    if "Rate limited" in str(e):
                        print(f"Rate limited on filter {fid}, backing off...")
                        continue
                    print("Cookie invalid; will refresh via proxy login.")
                    need_login = True
                except Exception as e:
                    print(f"Scrape session failed: {e}")
                    import traceback
                    traceback.print_exc()
                    need_login = True
            else:
                need_login = True

            # 2) If login needed, perform proxy-backed login and retry
            if need_login:
                success = self.login_with_retry(max_retries=3)
                if not success:
                    print(f"Proxy login failed; skipping filter '{flt.get('name')}'")
                    continue

                try:
                    current_ids, new_posts = self._scrape_single_filter(url, known)
                except Exception as e:
                    print(f"Scrape after login failed: {e}")
                    current_ids, new_posts = set(), []

            # 3) Persist results
            if new_posts:
                now_iso = datetime.now(timezone.utc).isoformat()
                for p in new_posts:
                    p['discovered_at'] = now_iso
                    p['filter_id'] = fid
                    p['filter_name'] = flt.get('name')

                with STATE_LOCK:
                    current_posts = POSTS.get(fid, [])
                    combined = new_posts + current_posts
                    unique = []
                    seen = set()
                    for post in combined:
                        if post['id'] not in seen:
                            unique.append(post)
                            seen.add(post['id'])
                    POSTS[fid] = sorted(unique, key=lambda p: p.get('discovered_at', ''), reverse=True)[:50]
                    KNOWN_IDS[fid].update(p['id'] for p in new_posts)

                print(f"Sending notifications for {len(new_posts)} new posts...")
                for post in new_posts:
                    send_new_post_notification(post)

            with STATE_LOCK:
                KNOWN_IDS[fid].update(current_ids)

            _save_data_to_disk()
            print(f"Filter '{flt.get('name')}' processing complete.")

    # ------------------------------------------------------------------
    # Main run (called by scheduler)
    # ------------------------------------------------------------------
    def run(self):
        """Execute one full scrape cycle."""
        try:
            self.scrape_filters()
            self.consecutive_errors = 0
            self.current_backoff = config.SCRAPE_INTERVAL_SEC
        except Exception as e:
            self.consecutive_errors += 1
            self.current_backoff = min(
                self.current_backoff * config.BACKOFF_MULTIPLIER,
                config.MAX_BACKOFF_SEC,
            )
            print(f"Scrape cycle failed (error #{self.consecutive_errors}): {e}")
            import traceback
            traceback.print_exc()
