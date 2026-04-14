import os
import time
import threading

from flask import Flask, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

import config
from state import STATE_LOCK, FILTERS, _load_data_from_disk
from routes import register_routes
from scraper import Scraper
from notifications import load_push_tokens

# ============================================================================
# Flask App
# ============================================================================
app = Flask(__name__)
CORS(app)
register_routes(app)

# ============================================================================
# Scheduler + Bootstrap
# ============================================================================
scraper = Scraper()
scheduler = BackgroundScheduler()
_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()


def _cleanup_diagnostic_dirs():
    """Remove old screenshots and HTML snapshots to free disk space."""
    import glob
    total_freed = 0
    total_removed = 0
    for d in [str(config.SCREENSHOTS_DIR), str(config.HTML_SNAPSHOTS_DIR)]:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            path = os.path.join(d, f)
            try:
                total_freed += os.path.getsize(path)
                os.remove(path)
                total_removed += 1
            except Exception:
                pass
    if total_removed:
        print(f"[Cleanup] Removed {total_removed} diagnostic files, freed {total_freed / 1024 / 1024:.1f} MB")


def _ensure_valid_session():
    """Ensure we have valid session cookies on startup."""
    need_fresh = False

    if config.FORCE_FRESH_LOGIN:
        print("FORCE_FRESH_LOGIN detected, forcing proxy login...")
        if os.path.exists(config.SESSION_COOKIE_FILE):
            try:
                os.remove(config.SESSION_COOKIE_FILE)
            except Exception:
                pass
        need_fresh = True
    elif not os.path.exists(config.SESSION_COOKIE_FILE):
        print("No session cookies found. Need login.")
        need_fresh = True
    else:
        try:
            file_age_hours = (time.time() - os.path.getmtime(config.SESSION_COOKIE_FILE)) / 3600
            print(f"Session cookies age: {file_age_hours:.1f}h")
            if file_age_hours > config.COOKIE_REFRESH_HOURS:
                print(f"Cookies too old (>{config.COOKIE_REFRESH_HOURS}h), need refresh")
                need_fresh = True
        except Exception:
            need_fresh = True

    if need_fresh:
        print("Performing proxy login for fresh cookies...")
        with STATE_LOCK:
            filters = list(FILTERS.values())
        scraper.login_with_retry(max_retries=3)


def _start_scheduler():
    """Start APScheduler with jittered intervals."""
    interval = config.SCRAPE_INTERVAL_SEC
    jitter = config.SCRAPE_JITTER_SEC

    scheduler.add_job(
        scraper.run,
        'interval',
        seconds=interval,
        jitter=jitter,
        id='scrape_job',
        name='Sahibinden scrape cycle',
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    print(f"Scheduler started: interval={interval}s, jitter={jitter}s")


def bootstrap():
    """Load data and start background threads. Safe to call multiple times."""
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        print("--- Bootstrapping Application ---")
        config.print_config_summary()
        from proxy_utils import test_proxy_connectivity
        test_proxy_connectivity()
        _load_data_from_disk()

        # Clean up orphaned images to free disk space
        from image_utils import cleanup_orphaned_images
        from state import get_all_image_filenames
        cleanup_orphaned_images(get_all_image_filenames())

        # Clean up diagnostic files (screenshots + HTML snapshots)
        _cleanup_diagnostic_dirs()

        load_push_tokens()

        def _background_init():
            try:
                _ensure_valid_session()
                _start_scheduler()
                print("Background initialization complete")
            except Exception as e:
                print(f"Background initialization error: {e}")
                import traceback
                traceback.print_exc()

        t = threading.Thread(target=_background_init, daemon=True)
        t.start()
        _BOOTSTRAPPED = True


@app.before_request
def lazy_bootstrap():
    """Lazy bootstrap: only on first real request (skip health checks)."""
    if request.path == '/health':
        return
    bootstrap()


if __name__ == "__main__":
    print("--- Starting Flask Dev Server. ---")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
