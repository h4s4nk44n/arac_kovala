import os
import mimetypes
from urllib.parse import urlsplit

import requests

import config
from proxy_utils import _get_iproyal_requests_proxies


def _extract_img_src(element):
    """Extract image source URL from a Scrapling Adaptor element."""
    try:
        imgs = element.css('img')
        if not imgs:
            return None
        img = imgs[0]
    except Exception:
        return None
    for attr in ('data-src', 'data-original', 'src'):
        try:
            val = img.attrib.get(attr, '')
        except Exception:
            val = ''
        if val and not val.startswith('data:'):
            return ('https:' + val) if val.startswith('//') else val
    return None


def _guess_extension_from_response(resp, fallback_url):
    ct = resp.headers.get('Content-Type') if resp is not None else None
    if ct:
        ext = mimetypes.guess_extension(ct.split(';')[0].strip())
        if ext:
            return ext
    path = urlsplit(fallback_url).path
    base_ext = os.path.splitext(path)[1]
    return base_ext if base_ext else '.jpg'


def delete_image(filename):
    """Delete an image file from the images directory. Handles missing files gracefully."""
    if not filename:
        return
    try:
        path = os.path.join(str(config.IMAGES_DIR), filename)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_orphaned_images(valid_filenames):
    """Delete images not referenced by any active post. Logs freed space."""
    images_dir = str(config.IMAGES_DIR)
    if not os.path.isdir(images_dir):
        return
    valid = set(valid_filenames)
    freed = 0
    removed = 0
    for name in os.listdir(images_dir):
        if name.endswith('.tmp') or name in valid:
            continue
        path = os.path.join(images_dir, name)
        try:
            freed += os.path.getsize(path)
            os.remove(path)
            removed += 1
        except Exception:
            pass
    if removed:
        print(f"[Cleanup] Removed {removed} orphaned images, freed {freed / 1024 / 1024:.1f} MB")
    else:
        print("[Cleanup] No orphaned images found")


def _download_image(image_url, post_id):
    if not image_url:
        return None
    images_dir = str(config.IMAGES_DIR)
    tmp_filename = f"{post_id}.tmp"
    tmp_path = os.path.join(images_dir, tmp_filename)
    for name in os.listdir(images_dir):
        if name.startswith(f"{post_id}.") and not name.endswith('.tmp'):
            return name
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
        proxies = _get_iproyal_requests_proxies()
        resp = requests.get(image_url, headers=headers, timeout=10, proxies=proxies)
        if resp.status_code != 200 or not resp.content:
            return None
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)
        ext = _guess_extension_from_response(resp, image_url).lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
            ext = '.jpg'
        final_filename = f"{post_id}{ext}"
        final_path = os.path.join(images_dir, final_filename)
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
        except Exception:
            pass
        os.replace(tmp_path, final_path)
        return final_filename
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        print(f"Image download failed for {image_url}: {e}")
        return None
