import os
import uuid
import json
from datetime import datetime, timezone

from flask import jsonify, request, send_from_directory, abort

import config
from state import STATE_LOCK, FILTERS, POSTS, KNOWN_IDS, _save_data_to_disk
from notifications import PUSH_TOKENS


def register_routes(app):
    """Register all Flask endpoints on the given app."""

    @app.get('/health')
    def health():
        return jsonify({"status": "ok"})

    @app.get('/')
    def root():
        return jsonify({"message": "Sahibinden tracker API", "status": "ok"})

    @app.get('/filters')
    def list_filters():
        with STATE_LOCK:
            return jsonify(list(FILTERS.values()))

    @app.post('/filters')
    def create_filter():
        data = request.get_json(force=True, silent=True) or {}
        name, url = data.get('name') or 'Filtre', data.get('url')
        if not url:
            return jsonify({"error": "url is required"}), 400
        fid = str(uuid.uuid4())
        item = {'id': fid, 'name': name, 'url': url, 'created_at': datetime.now(timezone.utc).isoformat()}
        with STATE_LOCK:
            FILTERS[fid] = item
            KNOWN_IDS.setdefault(fid, set())
            POSTS.setdefault(fid, [])
        _save_data_to_disk()
        return jsonify(item), 201

    @app.put('/filters/<fid>')
    def update_filter(fid):
        data = request.get_json(force=True, silent=True) or {}
        with STATE_LOCK:
            if fid not in FILTERS:
                return jsonify({"error": "not found"}), 404
            FILTERS[fid].update({k: v for k, v in data.items() if k in ('name', 'url') and v})
        _save_data_to_disk()
        return jsonify(FILTERS[fid])

    @app.delete('/filters/<fid>')
    def delete_filter(fid):
        with STATE_LOCK:
            if fid not in FILTERS:
                return jsonify({"error": "not found"}), 404
            FILTERS.pop(fid, None)
            KNOWN_IDS.pop(fid, None)
            POSTS.pop(fid, None)
        _save_data_to_disk()
        return jsonify({"ok": True})

    @app.get('/feed')
    def get_feed():
        with STATE_LOCK:
            all_posts = []
            for items in POSTS.values():
                all_posts.extend(items)
            merged_by_id = {p['id']: p for p in sorted(all_posts, key=lambda p: p.get('discovered_at', ''))}

        result = []
        for p in sorted(list(merged_by_id.values()), key=lambda p: p.get('discovered_at') or '', reverse=True):
            item = dict(p)
            if item.get('image'):
                item['image_url'] = request.url_root.rstrip('/') + '/images/' + item['image']
            result.append(item)
        return jsonify(result)

    @app.get('/filters/<fid>/cars')
    def get_filter_cars(fid):
        with STATE_LOCK:
            items = POSTS.get(fid, [])
        result = []
        for p in items:
            item = dict(p)
            if item.get('image'):
                item['image_url'] = request.url_root.rstrip('/') + '/images/' + item['image']
            result.append(item)
        return jsonify(result)

    @app.get('/images/<path:filename>')
    def serve_image(filename):
        return send_from_directory(str(config.IMAGES_DIR), filename)

    @app.post('/register-push-token')
    def register_push_token_endpoint():
        token = (request.get_json(force=True, silent=True) or {}).get('token')
        if token and isinstance(token, str):
            with STATE_LOCK:
                if token not in PUSH_TOKENS:
                    print(f"Registered push token: {token[:10]}...")
                    PUSH_TOKENS.add(token)
                    try:
                        with open(str(config.PUSH_TOKENS_FILE), 'w', encoding='utf-8') as f:
                            json.dump(list(PUSH_TOKENS), f)
                    except Exception as e:
                        print(f"Failed to save push tokens: {e}")
            return jsonify({"status": "ok"})
        return jsonify({"error": "Invalid token provided."}), 400

    @app.get('/screenshots')
    def list_screenshots():
        if not os.path.isdir(str(config.SCREENSHOTS_DIR)):
            return jsonify({"error": "Screenshots directory not found."}), 404
        files = sorted(
            [f for f in os.listdir(str(config.SCREENSHOTS_DIR)) if f.endswith('.png')],
            reverse=True,
        )
        return jsonify(files)

    @app.get('/html_snapshots/<path:filename>')
    def serve_html_snapshot(filename):
        try:
            return send_from_directory(str(config.HTML_SNAPSHOTS_DIR), filename)
        except FileNotFoundError:
            abort(404)

    @app.get('/screenshots/<path:filename>')
    def serve_screenshot(filename):
        try:
            return send_from_directory(str(config.SCREENSHOTS_DIR), filename)
        except FileNotFoundError:
            abort(404)
