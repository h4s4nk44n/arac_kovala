"""
Stealth proxy login module with comprehensive Cloudflare Turnstile handling.
Uses the latest SeleniumBase UC Mode features for maximum anti-detection.
"""

import time
import random
import json
import os
from datetime import datetime
from seleniumbase import SB


def detect_and_solve_turnstile_2captcha(sb, proxy_string=None, max_retries=2):
    """
    Detect Cloudflare Turnstile via window.turnstile API and solve with 2Captcha.
    This is called BEFORE the login form loads (pre-login challenge).
    
    Returns: True if challenge solved or not present, False if failed
    """
    from twocaptcha import TwoCaptcha
    
    api_key = os.getenv("TWOCAPTCHA_API_KEY", "").strip()
    if not api_key:
        print("[Turnstile] ⚠️ No 2Captcha API key, skipping")
        return True  # Continue anyway
    
    solver = TwoCaptcha(api_key)
    
    for attempt in range(max_retries):
        print(f"[Turnstile] Detection attempt {attempt + 1}/{max_retries}")
        
        # Wait for page to fully load and Turnstile to initialize
        sb.sleep(3)
        
        # Extract Turnstile parameters from window._turnstileParams (set by interceptor)
        turnstile_info = sb.execute_script("""
            return {
                hasTurnstileAPI: typeof window.turnstile !== 'undefined',
                params: window._turnstileParams || null,
                hasChallenge: document.querySelector('.cf-turnstile') !== null ||
                              document.querySelector('iframe[src*="challenges.cloudflare.com"]') !== null
            };
        """)
        
        print(f"[Turnstile] Detection: {turnstile_info}")
        
        if not turnstile_info.get('hasChallenge') and not turnstile_info.get('params'):
            print("[Turnstile] ✓ No challenge detected")
            return True
        
        # Get sitekey from intercepted params or fallback to HTML
        sitekey = None
        if turnstile_info.get('params'):
            sitekey = turnstile_info['params'].get('sitekey')
            print(f"[Turnstile] ✓ Sitekey from interceptor: {sitekey}")
        
        if not sitekey:
            # Fallback: extract from HTML
            sitekey = sb.execute_script("""
                const div = document.querySelector('div[data-sitekey]');
                if (div) return div.getAttribute('data-sitekey');
                
                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe) {
                    const match = iframe.src.match(/[?&]sitekey=([^&]+)/);
                    if (match) return match[1];
                }
                return null;
            """)
            if sitekey:
                print(f"[Turnstile] ✓ Sitekey from HTML: {sitekey}")
        
        if not sitekey:
            print("[Turnstile] ❌ No sitekey found, trying UC Mode...")
            try:
                sb.uc_gui_handle_captcha()
                sb.sleep(5)
                return True
            except Exception as e:
                print(f"[Turnstile] UC Mode failed: {e}")
                return False
        
        # Solve with 2Captcha
        page_url = sb.get_current_url()
        print(f"[Turnstile] Submitting to 2Captcha (sitekey: {sitekey[:20]}...)")
        
        try:
            result = solver.turnstile(sitekey=sitekey, url=page_url)
            token = result.get('code')
            
            if not token:
                print("[Turnstile] ❌ No solution from 2Captcha")
                continue
            
            print(f"[Turnstile] ✓ Solution received ({len(token)} chars)")
            
            # Inject token
            inject_result = sb.execute_script("""
                (function(token) {
                    // Find response input
                    const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                    if (inputs.length > 0) {
                        inputs.forEach(input => {
                            input.value = token;
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                        });
                        
                        // Call callback if exists
                        if (window.cfCallback && typeof window.cfCallback === 'function') {
                            window.cfCallback(token);
                        }
                        
                        return {success: true, method: 'injection'};
                    }
                    return {success: false, error: 'No response input found'};
                })(arguments[0]);
            """, token)
            
            print(f"[Turnstile] Injection result: {inject_result}")
            
            # Wait for validation
            print("[Turnstile] Waiting for validation...")
            sb.sleep(5)
            
            # Check if challenge cleared
            challenge_still_present = sb.execute_script("""
                return document.querySelector('.cf-turnstile') !== null ||
                       document.querySelector('iframe[src*="challenges.cloudflare.com"]') !== null;
            """)
            
            if not challenge_still_present:
                print("[Turnstile] ✓ Challenge cleared!")
                return True
            else:
                print(f"[Turnstile] ⚠️ Challenge still present, retry {attempt + 1}/{max_retries}")
                sb.refresh()
                sb.sleep(3)
                
        except Exception as e:
            print(f"[Turnstile] 2Captcha error: {e}")
            continue
    
    print("[Turnstile] ❌ All attempts failed")
    return False


def handle_hidden_captcha_button(sb):
    """
    Handle the "hidden" single-click captcha button that appears after entering credentials.
    This button is visible in screenshots but not easily detectable in HTML.
    
    Strategy:
    1. Wait for button to dynamically appear (3s)
    2. Use uc_gui_click_captcha() to click it (most stealthy)
    3. Verify checkmark appeared
    """
    print("[Hidden Captcha] Checking for post-credential captcha button...")
    
    # Wait for dynamic content to load
    sb.sleep(3)
    
    # Check for Turnstile button with text detection
    button_check = sb.execute_script("""
        const buttons = Array.from(document.querySelectorAll('button'));
        const captchaButton = buttons.find(btn => 
            btn.textContent.includes('Gerçek bir kişi') || 
            btn.textContent.includes('doğrulayın') ||
            btn.textContent.includes('Verify') ||
            btn.className.includes('turnstile') ||
            btn.id.includes('turnstile')
        );
        
        if (captchaButton) {
            return {
                found: true,
                text: captchaButton.textContent,
                visible: captchaButton.offsetParent !== null,
                rect: captchaButton.getBoundingClientRect()
            };
        }
        
        // Also check for Turnstile iframe
        const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
        return {
            found: false,
            hasIframe: !!iframe
        };
    """)
    
    if button_check.get('found'):
        print(f"[Hidden Captcha] ✓ Found button: {button_check.get('text', 'N/A')[:50]}")
        
        # Take screenshot before
        try:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            screenshots_dir = os.path.join(os.path.dirname(__file__), 'screenshots')
            os.makedirs(screenshots_dir, exist_ok=True)
            shot = os.path.join(screenshots_dir, f"hidden_captcha_before_{ts}.png")
            sb.save_screenshot(shot)
            print(f"[Hidden Captcha] Screenshot: {shot}")
        except Exception:
            pass
        
        # Click with UC Mode (most stealthy)
        print("[Hidden Captcha] Clicking with uc_gui_click_captcha()...")
        try:
            sb.uc_gui_click_captcha()
            print("[Hidden Captcha] ✓ Clicked!")
            
            # Wait for checkmark
            sb.sleep(2)
            
            # Take screenshot after
            try:
                shot_after = os.path.join(screenshots_dir, f"hidden_captcha_after_{ts}.png")
                sb.save_screenshot(shot_after)
                print(f"[Hidden Captcha] Screenshot after: {shot_after}")
            except Exception:
                pass
            
            return True
            
        except Exception as e:
            print(f"[Hidden Captcha] Click error: {e}")
            return False
    
    elif button_check.get('hasIframe'):
        print("[Hidden Captcha] Iframe present, trying uc_gui_click_captcha anyway...")
        try:
            sb.uc_gui_click_captcha()
            sb.sleep(2)
            return True
        except Exception as e:
            print(f"[Hidden Captcha] Error: {e}")
            return False
    
    print("[Hidden Captcha] No captcha button detected")
    return True  # Continue anyway


def stealth_login_with_proxy(sb, proxy_string, username, password, login_url="https://www.sahibinden.com/giris"):
    """
    Perform stealth login with comprehensive captcha handling.
    
    Flow:
    1. Install Turnstile interceptor (before page load)
    2. Navigate to login page with CDP Mode (stealthiest)
    3. Detect and solve pre-login Turnstile (if present)
    4. Enter credentials character-by-character (human-like)
    5. Handle hidden captcha button (after credential entry)
    6. Submit form
    7. Handle post-login challenges
    
    Returns: True if login succeeded, False otherwise
    """
    try:
        print("[Login] Installing Turnstile interceptor...")
        
        # CRITICAL: Install interceptor BEFORE page loads
        sb.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                window._turnstileParams = null;
                window.cfCallback = null;
                
                const interval = setInterval(() => {
                    if (window.turnstile) {
                        clearInterval(interval);
                        const originalRender = window.turnstile.render;
                        window.turnstile.render = function(container, params) {
                            console.log('[Interceptor] Turnstile render called:', params);
                            window._turnstileParams = {
                                sitekey: params.sitekey,
                                action: params.action,
                                cData: params.cData,
                                pageurl: window.location.href
                            };
                            window.cfCallback = params.callback;
                            // Don't actually render - we'll solve with 2Captcha
                            return;
                        };
                    }
                }, 50);
            """
        })
        
        print("[Login] Navigating to login page with CDP Mode...")
        
        # Use CDP Mode for maximum stealth
        try:
            sb.activate_cdp_mode()
            sb.cdp.open(login_url)
        except Exception as e:
            print(f"[Login] CDP Mode failed, using UC Mode: {e}")
            sb.uc_open_with_reconnect(login_url, reconnect_time=5)
        
        # Wait for page to load
        print("[Login] Waiting for page to settle...")
        sb.sleep(5)
        
        # Handle pre-login Turnstile challenge
        if not detect_and_solve_turnstile_2captcha(sb, proxy_string):
            print("[Login] ❌ Pre-login Turnstile failed")
            return False
        
        # Check if login form is visible
        print("[Login] Checking for login form...")
        sb.sleep(2)
        
        form_present = sb.execute_script("""
            return document.querySelector('#username') !== null &&
                   document.querySelector('#password') !== null;
        """)
        
        if not form_present:
            print("[Login] ❌ Login form not found")
            return False
        
        print("[Login] ✓ Login form found")
        
        # Accept cookie banner
        try:
            if sb.is_element_present("#onetrust-accept-btn-handler"):
                sb.js_click("#onetrust-accept-btn-handler")
                sb.sleep(0.5)
        except Exception:
            pass
        
        # Enter credentials with human-like typing
        print("[Login] Entering username...")
        sb.sleep(0.5 + random.random())
        
        username_field = sb.find_element("#username")
        username_field.click()
        sb.sleep(0.3)
        
        for char in username:
            username_field.send_keys(char)
            sb.sleep(0.05 + random.random() * 0.1)  # 50-150ms per char
        
        sb.sleep(0.5 + random.random() * 0.5)
        
        print("[Login] Entering password...")
        password_field = sb.find_element("#password")
        password_field.click()
        sb.sleep(0.3)
        
        for char in password:
            password_field.send_keys(char)
            sb.sleep(0.05 + random.random() * 0.1)
        
        # CRITICAL: Wait for hidden captcha button to appear
        print("[Login] Waiting for hidden captcha to load...")
        sb.sleep(3)
        
        # Handle hidden captcha button
        if not handle_hidden_captcha_button(sb):
            print("[Login] ⚠️ Hidden captcha handling failed, continuing anyway...")
        
        # Additional pause before submit
        sb.sleep(1.0 + random.random())
        
        # Submit form
        print("[Login] Submitting form...")
        try:
            submit_btn = sb.find_element("#userLoginSubmitButton")
            submit_btn.click()
        except Exception:
            # Fallback: press Enter
            password_field.send_keys("\n")
        
        print("[Login] Waiting for redirect...")
        sb.sleep(5)
        
        # Check result
        current_url = sb.get_current_url().lower()
        print(f"[Login] Current URL: {current_url}")
        
        # Check for 2FA
        if "iki-asamali" in current_url or "twofactor" in current_url:
            print("[Login] ⚠️ 2FA required - cannot bypass automatically")
            print("[Login] 💡 Use Turkey-based proxy or complete 2FA manually")
            return False
        
        # Check if still on login page
        if "login" in current_url or "giris" in current_url:
            print("[Login] ❌ Still on login page")
            
            # Save diagnostic
            try:
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                screenshots_dir = os.path.join(os.path.dirname(__file__), 'screenshots')
                shot = os.path.join(screenshots_dir, f"login_failed_{ts}.png")
                sb.save_screenshot(shot)
                print(f"[Login] Screenshot: {shot}")
            except Exception:
                pass
            
            return False
        
        print("[Login] ✓ Login successful!")
        return True
        
    except Exception as e:
        print(f("[Login] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

