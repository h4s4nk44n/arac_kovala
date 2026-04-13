import threading
import json
import os

import config

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------
STATE_LOCK = threading.Lock()

FILTERS = {}
POSTS = {}
KNOWN_IDS = {}


def _load_data_from_disk():
    global FILTERS, POSTS, KNOWN_IDS
    if os.path.exists(str(config.FILTERS_FILE)):
        try:
            with open(str(config.FILTERS_FILE), 'r', encoding='utf-8') as f:
                items = json.load(f)
                FILTERS = {item['id']: item for item in items}
        except Exception as e:
            print(f"Failed to read filters.json: {e}")

    if os.path.exists(str(config.POSTS_FILE)):
        try:
            with open(str(config.POSTS_FILE), 'r', encoding='utf-8') as f:
                POSTS = json.load(f)
        except Exception as e:
            print(f"Failed to read posts.json: {e}")

    if os.path.exists(str(config.KNOWN_IDS_FILE)):
        try:
            with open(str(config.KNOWN_IDS_FILE), 'r', encoding='utf-8') as f:
                loaded_ids = json.load(f)
                KNOWN_IDS = {k: set(v) for k, v in loaded_ids.items()}
        except Exception as e:
            print(f"Failed to read known_ids.json: {e}")


def _save_data_to_disk():
    with STATE_LOCK:
        try:
            with open(str(config.FILTERS_FILE), 'w', encoding='utf-8') as f:
                json.dump(list(FILTERS.values()), f, ensure_ascii=False, indent=2)
            with open(str(config.POSTS_FILE), 'w', encoding='utf-8') as f:
                json.dump(POSTS, f, ensure_ascii=False, indent=2)
            savable_ids = {k: list(v) for k, v in KNOWN_IDS.items()}
            with open(str(config.KNOWN_IDS_FILE), 'w', encoding='utf-8') as f:
                json.dump(savable_ids, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to write data to disk: {e}")
