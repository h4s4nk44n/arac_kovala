"""
Configuration management for sahibinden scraper.
Handles environment variables, constants, and paths.
"""

import os
from pathlib import Path


# ============================================================================
# PATHS
# ============================================================================

# Data directory (Railway volume or local)
DATA_DIR = os.getenv('RAILWAY_VOLUME_MOUNT_PATH', os.path.dirname(__file__))
DATA_DIR = Path(DATA_DIR)
DATA_DIR.mkdir(exist_ok=True)

# File paths
KNOWN_IDS_FILE = DATA_DIR / 'known_ids.json'
PUSH_TOKENS_FILE = DATA_DIR / 'push_tokens.json'
FILTERS_FILE = DATA_DIR / 'filters.json'
POSTS_FILE = DATA_DIR / 'posts.json'
SESSION_COOKIE_FILE = os.getenv("SESSION_COOKIE_FILE", str(DATA_DIR / "session_cookies.json"))
IMAGES_DIR = DATA_DIR / 'images'
IMAGES_DIR.mkdir(exist_ok=True)

# Diagnostic directories
SCREENSHOTS_DIR = Path(__file__).parent / 'screenshots'
HTML_SNAPSHOTS_DIR = Path(__file__).parent / 'html_snapshots'
SCREENSHOTS_DIR.mkdir(exist_ok=True)
HTML_SNAPSHOTS_DIR.mkdir(exist_ok=True)


# ============================================================================
# CREDENTIALS & API KEYS
# ============================================================================

# Sahibinden credentials
SAHIBINDEN_USER = os.getenv("SAHIBINDEN_USER", "")
SAHIBINDEN_PASS = os.getenv("SAHIBINDEN_PASS", "")

# Proxy configuration (IPRoyal)
IPROYAL_PROXY = os.getenv("IPROYAL_PROXY", "").strip()  # e.g., "geo.iproyal.com:12321"
IPROYAL_PROXY_AUTH = os.getenv("IPROYAL_PROXY_AUTH", "").strip()  # e.g., "user:pass_country-tr_streaming-1"

# Bright Data proxy (fallback)
BRD_BASE_USER = os.getenv("BRD_BASE_USER", "")
BRD_PASSWORD = os.getenv("BRD_PASSWORD", "")
BRD_HOST = os.getenv("BRD_HOST", "")
BRD_PORT = os.getenv("BRD_PORT", "")

# CapSolver API (for reCAPTCHA v2 solving)
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "").strip()


# ============================================================================
# BROWSER CONFIGURATION
# ============================================================================

HEADLESS = os.getenv("HEADLESS", "1") in ("1", "true", "True", "YES", "yes")


# ============================================================================
# SCRAPER SETTINGS
# ============================================================================

SCRAPE_INTERVAL_SEC = int(os.getenv("SCRAPE_INTERVAL_SEC", "300"))  # 5 min default
ALLOW_LOGIN = os.getenv("ALLOW_LOGIN", "1") in ("1", "true", "True", "YES", "yes")
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "3"))
LOGIN_COOLDOWN_SEC = int(os.getenv("LOGIN_COOLDOWN_SEC", "5"))
FORCE_FRESH_LOGIN = os.getenv("FORCE_FRESH_LOGIN", "0") == "1"


# ============================================================================
# CAPTCHA SETTINGS
# ============================================================================

# ============================================================================
# SCHEDULING SETTINGS
# ============================================================================

SCRAPE_JITTER_SEC = int(os.getenv("SCRAPE_JITTER_SEC", "60"))       # 0-60s random jitter
BACKOFF_MULTIPLIER = float(os.getenv("BACKOFF_MULTIPLIER", "2.0"))
MAX_BACKOFF_SEC = int(os.getenv("MAX_BACKOFF_SEC", "3600"))          # 1 hour max
COOKIE_REFRESH_HOURS = int(os.getenv("COOKIE_REFRESH_HOURS", "20"))


# ============================================================================
# TELEGRAM (OPTIONAL)
# ============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()




# ============================================================================
# HELPERS
# ============================================================================

def env_true(name: str, default="0") -> bool:
    """Check if environment variable is truthy."""
    return os.getenv(name, default) in ("1", "true", "True", "YES", "yes")


def validate_config() -> list[str]:
    """
    Validate required configuration.
    Returns list of error messages (empty if valid).
    """
    errors = []
    
    if not SAHIBINDEN_USER or not SAHIBINDEN_PASS:
        errors.append("SAHIBINDEN_USER and SAHIBINDEN_PASS must be set")
    
    if not IPROYAL_PROXY and not BRD_BASE_USER:
        errors.append("Either IPRoyal (IPROYAL_*) or Bright Data (BRD_*) proxy must be configured")
    
    if IPROYAL_PROXY and not IPROYAL_PROXY_AUTH:
        errors.append("IPROYAL_PROXY_AUTH must be set if IPROYAL_PROXY is set")
    
    if IPROYAL_PROXY_AUTH and "_country-tr" not in IPROYAL_PROXY_AUTH:
        errors.append("⚠️ WARNING: IPROYAL_PROXY_AUTH should include '_country-tr' for Turkey IP")
    
    return errors


def print_config_summary():
    """Print configuration summary for debugging."""
    print("=" * 60)
    print("📋 CONFIGURATION SUMMARY")
    print("=" * 60)
    print(f"Data directory: {DATA_DIR}")
    print(f"Session cookies: {SESSION_COOKIE_FILE}")
    print(f"Screenshots: {SCREENSHOTS_DIR}")
    print(f"Scrape interval: {SCRAPE_INTERVAL_SEC}s")
    print(f"Headless mode: {HEADLESS}")
    print(f"Force fresh login: {FORCE_FRESH_LOGIN}")
    
    print("\n🔐 Credentials:")
    print(f"  Sahibinden user: {'✓ Set' if SAHIBINDEN_USER else '❌ Missing'}")
    print(f"  Sahibinden pass: {'✓ Set' if SAHIBINDEN_PASS else '❌ Missing'}")
    
    print("\n🌐 Proxy:")
    if IPROYAL_PROXY:
        print(f"  Type: IPRoyal")
        print(f"  Host: {IPROYAL_PROXY}")
        print(f"  Auth: {'✓ Set' if IPROYAL_PROXY_AUTH else '❌ Missing'}")
        print(f"  Turkey IP: {'✓ Yes' if '_country-tr' in IPROYAL_PROXY_AUTH else '⚠️ No'}")
    elif BRD_BASE_USER:
        print(f"  Type: Bright Data")
        print(f"  Host: {BRD_HOST}:{BRD_PORT}")
    else:
        print(f"  ❌ Not configured")
    
    print("\n🤖 CapSolver (reCAPTCHA v2):")
    print(f"  API Key: {'✓ Set' if CAPSOLVER_API_KEY else '⚠️ Missing (optional)'}")

    print("\n🌐 Browser:")
    print(f"  Engine: Scrapling StealthyFetcher (patchright)")
    print(f"  Headless: {HEADLESS}")
    
    # Validation
    errors = validate_config()
    if errors:
        print("\n❌ CONFIGURATION ERRORS:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("\n✅ Configuration valid")
    
    print("=" * 60)


if __name__ == "__main__":
    # Test configuration when run directly
    print_config_summary()


