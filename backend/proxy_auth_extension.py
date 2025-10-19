"""
Create a Chrome extension for proxy authentication.
This is the most reliable way to handle HTTP proxy auth in Chrome/Selenium.
"""

import os
import zipfile
import tempfile

def create_proxy_auth_extension(proxy_host, proxy_port, proxy_user, proxy_pass):
    """
    Create a Chrome extension that handles proxy authentication.
    Returns the path to the extension directory.
    """
    manifest_json = """
{
    "version": "1.0.0",
    "manifest_version": 2,
    "name": "Proxy Auth",
    "permissions": [
        "proxy",
        "tabs",
        "unlimitedStorage",
        "storage",
        "<all_urls>",
        "webRequest",
        "webRequestBlocking"
    ],
    "background": {
        "scripts": ["background.js"]
    },
    "minimum_chrome_version":"22.0.0"
}
"""

    background_js = """
var config = {
        mode: "fixed_servers",
        rules: {
          singleProxy: {
            scheme: "http",
            host: "%s",
            port: parseInt(%s)
          },
          bypassList: ["localhost"]
        }
      };

chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

function callbackFn(details) {
    return {
        authCredentials: {
            username: "%s",
            password: "%s"
        }
    };
}

chrome.webRequest.onAuthRequired.addListener(
            callbackFn,
            {urls: ["<all_urls>"]},
            ['blocking']
);
""" % (proxy_host, proxy_port, proxy_user, proxy_pass)

    # Create extension directory
    ext_dir = tempfile.mkdtemp(prefix='proxy_auth_ext_')
    
    manifest_path = os.path.join(ext_dir, 'manifest.json')
    with open(manifest_path, 'w') as f:
        f.write(manifest_json)
    
    background_path = os.path.join(ext_dir, 'background.js')
    with open(background_path, 'w') as f:
        f.write(background_js)
    
    return ext_dir

