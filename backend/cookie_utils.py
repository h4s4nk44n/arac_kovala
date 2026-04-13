"""
Cookie utilities for Scrapling/Playwright browser sessions.

Handles saving and loading cookies between sessions, with backward
compatibility for existing SeleniumBase-format cookie files.
"""

import json
import os


def save_cookies(cookies_list, filepath):
    """
    Save cookies from Playwright context to a JSON file.

    Args:
        cookies_list: List of cookie dicts from page.context.cookies()
        filepath: Path to write the JSON file
    """
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cookies_list, f)
    except Exception as e:
        print(f"[Cookies] Failed to save cookies: {e}")


def load_cookies(filepath):
    """
    Load cookies from a JSON file, normalizing to Playwright format.

    Handles both Playwright format (expires) and legacy SeleniumBase
    format (expiry) for backward compatibility.

    Args:
        filepath: Path to the cookie JSON file

    Returns:
        List of cookie dicts ready for page.context.add_cookies(),
        or empty list if file doesn't exist or is invalid.
    """
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_cookies = json.load(f)
    except Exception as e:
        print(f"[Cookies] Failed to load cookies: {e}")
        return []

    if not isinstance(raw_cookies, list):
        return []

    normalized = []
    for cookie in raw_cookies:
        if not cookie.get("name") or not cookie.get("value"):
            continue

        c = {
            "name": cookie["name"],
            "value": cookie["value"],
        }

        # Domain is required by Playwright
        if cookie.get("domain"):
            c["domain"] = cookie["domain"]
        else:
            continue  # Skip cookies without domain

        if cookie.get("path"):
            c["path"] = cookie["path"]
        else:
            c["path"] = "/"

        # Handle expiry/expires differences between SeleniumBase and Playwright
        if "expires" in cookie and isinstance(cookie["expires"], (int, float)):
            c["expires"] = cookie["expires"]
        elif "expiry" in cookie and isinstance(cookie["expiry"], (int, float)):
            c["expires"] = float(cookie["expiry"])
        elif "expirationDate" in cookie and isinstance(cookie["expirationDate"], (int, float)):
            c["expires"] = float(cookie["expirationDate"])

        if "secure" in cookie:
            c["secure"] = bool(cookie["secure"])

        if "httpOnly" in cookie:
            c["httpOnly"] = bool(cookie["httpOnly"])

        if "sameSite" in cookie:
            # Playwright expects "Strict", "Lax", or "None"
            ss = str(cookie["sameSite"]).capitalize()
            if ss in ("Strict", "Lax", "None"):
                c["sameSite"] = ss

        normalized.append(c)

    return normalized
