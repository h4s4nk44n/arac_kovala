import uuid
import socket
import secrets

import config


def build_brightdata_proxy_string(country_code="tr", session_id=None):
    base_user = config.BRD_BASE_USER
    password = config.BRD_PASSWORD
    host = config.BRD_HOST
    port = config.BRD_PORT
    if not (base_user and password and host and port):
        return None
    if not session_id:
        session_id = secrets.token_hex(4)
    username = f"{base_user}-country-{country_code}-session-{session_id}"
    return f"http://{username}:{password}@{host}:{port}"


def _get_iproyal_requests_proxies():
    """Build a requests proxies dict from IPRoyal env vars."""
    if config.IPROYAL_PROXY and config.IPROYAL_PROXY_AUTH:
        proxy_url = f"http://{config.IPROYAL_PROXY_AUTH}@{config.IPROYAL_PROXY}"
        return {"http": proxy_url, "https": proxy_url}
    return None


def _get_proxy_url(rotate_session=False):
    """Return proxy URL for Scrapling: 'http://user:pass@host:port'."""
    proxy_host_port = config.IPROYAL_PROXY
    proxy_auth = config.IPROYAL_PROXY_AUTH
    if proxy_host_port and proxy_auth:
        if "_country-" not in proxy_auth:
            proxy_auth = f"{proxy_auth}_country-tr"
        if rotate_session and "_sessionid-" not in proxy_auth:
            sid = str(uuid.uuid4())[:8]
            proxy_auth = f"{proxy_auth}_sessionid-{sid}"
            print(f"[Proxy] Rotating session: {sid}")
        return f"http://{proxy_auth}@{proxy_host_port}"
    return build_brightdata_proxy_string()


def test_proxy_connectivity():
    """Test proxy connectivity and log diagnostics. Call at startup."""
    proxy_host_port = config.IPROYAL_PROXY
    if not proxy_host_port:
        print("[Proxy Test] No proxy configured, skipping connectivity test")
        return

    # Parse host and port
    if ":" in proxy_host_port:
        host, port_str = proxy_host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = proxy_host_port
        port = 12321

    # Test 1: Raw TCP connection to proxy
    print(f"[Proxy Test] Testing TCP connection to {host}:{port}...")
    try:
        sock = socket.create_connection((host, port), timeout=10)
        sock.close()
        print(f"[Proxy Test] TCP connection to {host}:{port} OK")
    except socket.timeout:
        print(f"[Proxy Test] FAILED: TCP connection to {host}:{port} timed out (port likely blocked by Railway)")
        return
    except OSError as e:
        print(f"[Proxy Test] FAILED: TCP connection to {host}:{port} error: {e}")
        return

    # Test 2: HTTP request through the proxy
    proxy_url = _get_proxy_url(rotate_session=False)
    if not proxy_url:
        print("[Proxy Test] Could not build proxy URL, skipping HTTP test")
        return

    print("[Proxy Test] Testing HTTP request through proxy...")
    try:
        import requests
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://ipv4.icanhazip.com", proxies=proxies, timeout=15)
        ip = resp.text.strip()
        print(f"[Proxy Test] HTTP through proxy OK. Exit IP: {ip}")
    except Exception as e:
        print(f"[Proxy Test] FAILED: HTTP through proxy error: {e}")
