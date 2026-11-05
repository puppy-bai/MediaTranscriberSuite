import json
import time
import uuid
from pathlib import Path

from .paths import CAPTURES_DIR, ensure_data_dirs


CAPTURE_DB = CAPTURES_DIR / "media_captures.json"


def _load_all():
    ensure_data_dirs()
    if not CAPTURE_DB.exists():
        legacy_db = CAPTURES_DIR.parent / "records.json"
        if not legacy_db.exists():
            return []
        try:
            legacy_items = json.loads(legacy_db.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = []
        for old in legacy_items if isinstance(legacy_items, list) else []:
            if not isinstance(old, dict) or not old.get("url"):
                continue
            items.append({
                "id": old.get("id") or uuid.uuid4().hex[:12],
                "url": old["url"],
                "page_url": old.get("page_url") or "",
                "page_title": old.get("title") or old["url"],
                "tab_id": "",
                "method": "",
                "mime": "",
                "ext": "",
                "size": "",
                "headers": {},
                "source": old.get("source") or "desktop",
                "status": old.get("status") or "captured",
                "created_at": old.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_seen_at": old.get("updated_at") or old.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                "seen_count": 1,
                "notes": [],
                "result": old.get("result") or {},
                "timings": old.get("timings") or {},
            })
        _save_all(items)
        return items
    try:
        return json.loads(CAPTURE_DB.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_all(items):
    ensure_data_dirs()
    CAPTURE_DB.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add_capture(payload):
    items = _load_all()
    url = str(payload.get("url") or "").strip()
    page_url = str(payload.get("pageUrl") or payload.get("page_url") or "").strip()
    if not url:
        raise ValueError("missing url")

    for item in items:
        if item.get("url") == url and item.get("page_url") == page_url:
            item["last_seen_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            item["seen_count"] = int(item.get("seen_count") or 1) + 1
            _save_all(items)
            return item

    item = {
        "id": uuid.uuid4().hex[:12],
        "url": url,
        "page_url": page_url,
        "page_title": payload.get("pageTitle") or payload.get("page_title") or "",
        "tab_id": payload.get("tabId") or payload.get("tab_id") or "",
        "method": payload.get("method") or "",
        "mime": payload.get("mime") or payload.get("contentType") or "",
        "ext": payload.get("ext") or "",
        "size": payload.get("size") or "",
        "headers": payload.get("headers") or {},
        "source": payload.get("source") or "extension",
        "status": "captured",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seen_count": 1,
        "notes": [],
    }
    items.insert(0, item)
    _save_all(items[:500])
    return item


def list_captures(limit=200):
    return _load_all()[:limit]


def get_capture(capture_id):
    for item in _load_all():
        if item.get("id") == capture_id:
            return item
    return None


def delete_captures(capture_ids):
    ids = set(capture_ids or [])
    items = _load_all()
    kept = [item for item in items if item.get("id") not in ids]
    _save_all(kept)
    return len(items) - len(kept)


def update_capture(capture_id, **updates):
    items = _load_all()
    for item in items:
        if item.get("id") == capture_id:
            item.update(updates)
            _save_all(items)
            return item
    return None
