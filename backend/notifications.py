"""
Push notification handling via Expo push service.
"""

import requests
import config


# Global push tokens storage
PUSH_TOKENS = set()


def _get_requests_proxies():
    """Build requests-compatible proxy dict from config values."""
    if config.IPROYAL_PROXY and config.IPROYAL_PROXY_AUTH:
        proxy_url = f"http://{config.IPROYAL_PROXY_AUTH}@{config.IPROYAL_PROXY}"
        return {"http": proxy_url, "https": proxy_url}
    return None


def load_push_tokens():
    """Load push tokens from disk."""
    global PUSH_TOKENS
    import json

    if config.PUSH_TOKENS_FILE.exists():
        try:
            with open(config.PUSH_TOKENS_FILE, 'r', encoding='utf-8') as f:
                tokens_list = json.load(f)
                PUSH_TOKENS = set(tokens_list)
                print(f"Loaded {len(PUSH_TOKENS)} push tokens")
        except Exception as e:
            print(f"Failed to load push tokens: {e}")


def save_push_tokens():
    """Save push tokens to disk."""
    import json

    try:
        with open(config.PUSH_TOKENS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(PUSH_TOKENS), f)
        print(f"Saved {len(PUSH_TOKENS)} push tokens")
    except Exception as e:
        print(f"Failed to save push tokens: {e}")


def register_push_token(token: str) -> bool:
    """
    Register a new push token.
    Returns True if token was newly added, False if already existed.
    """
    global PUSH_TOKENS

    if not token or not isinstance(token, str):
        return False

    if token in PUSH_TOKENS:
        return False

    PUSH_TOKENS.add(token)
    save_push_tokens()
    print(f"Registered new push token: {token[:10]}...")
    return True


def send_push_notification(title: str, body: str, data: dict = None):
    """
    Send push notification to all registered devices via Expo.
    Falls back to direct connection if proxy fails (Expo API isn't geo-restricted).
    """
    if not PUSH_TOKENS:
        print("[Push] No registered devices")
        return

    url = 'https://exp.host/--/api/v2/push/send'
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip, deflate',
    }

    proxies = _get_requests_proxies()

    for token in PUSH_TOKENS:
        payload = {
            'to': token,
            'sound': 'default',
            'title': title,
            'body': body,
            'data': data or {},
        }

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=10, proxies=proxies
            )
            response.raise_for_status()
            print(f"[Push] Sent to {token[:10]}...")
        except requests.exceptions.RequestException:
            # Retry without proxy -- Expo API doesn't need Turkish IP
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=10
                )
                response.raise_for_status()
                print(f"[Push] Sent to {token[:10]}... (no proxy)")
            except requests.exceptions.RequestException as e:
                print(f"[Push] Error sending to {token[:10]}...: {e}")


def send_new_post_notification(post: dict):
    """Send notification for a new post."""
    title = post.get('title', 'New Car')
    body = f"Price: {post.get('price', 'N/A')}"
    data = {'url': post.get('url')}

    send_push_notification(title, body, data)
