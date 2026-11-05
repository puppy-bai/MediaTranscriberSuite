import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .capture_store import add_capture, get_capture, list_captures, update_capture
from .media_pipeline import download_media, summarize_text, transcribe_file
from .paths import configure_cuda_dll_paths, ensure_data_dirs


class BridgeState:
    def __init__(self):
        self.api_key = ""
        self.default_model_size = "base"
        self.default_device = "cuda"
        self.default_compute_type = "float16"
        self.default_beam_size = 1
        self.jobs = []
        self.active_stop_event = None
        self.lock = threading.Lock()


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "MediaTextBridge/0.2"

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json({"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"ok": True, "name": "Media Text Bridge", "version": "0.2"})
            return
        if parsed.path == "/captures":
            self._send_json({"ok": True, "items": list_captures()})
            return
        if parsed.path == "/jobs":
            self._send_json({"ok": True, "items": self.server.state.jobs[-100:]})
            return
        self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(raw or "{}")
            parsed = urlparse(self.path)

            if parsed.path == "/capture":
                item = add_capture(payload)
                self._send_json({"ok": True, "item": item})
                return

            if parsed.path == "/settings":
                for key in ["api_key", "default_model_size", "default_device", "default_compute_type", "default_beam_size"]:
                    if key in payload:
                        setattr(self.server.state, key, payload[key])
                self._send_json({"ok": True})
                return

            if parsed.path == "/cancel":
                with self.server.state.lock:
                    if self.server.state.active_stop_event:
                        self.server.state.active_stop_event.set()
                self._send_json({"ok": True, "status": "cancelling"})
                return

            if parsed.path == "/process":
                capture_id = payload.get("id")
                item = get_capture(capture_id) if capture_id else payload
                if not item:
                    raise RuntimeError("找不到捕获记录")
                if payload.get("async", False):
                    thread = threading.Thread(
                        target=process_capture,
                        args=(self.server.state, item, payload),
                        daemon=True,
                    )
                    thread.start()
                    self._send_json({"ok": True, "status": "started"})
                    return
                result = process_capture(self.server.state, item, payload)
                self._send_json({"ok": True, "result": result})
                return

            self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def log_message(self, _fmt, *_args):
        return


def process_capture(state, item, options=None):
    options = options or {}
    capture_id = item.get("id")
    audio_only = bool(options.get("audio_only", True))
    summary_enabled = bool(options.get("summary", False))
    stop_event = threading.Event()
    total_started = time.perf_counter()
    timings = {}

    with state.lock:
        state.active_stop_event = stop_event

    try:
        if capture_id:
            update_capture(capture_id, status="downloading")
        download_started = time.perf_counter()
        downloaded = download_media(
            item.get("url", ""),
            title=item.get("page_title") or item.get("id") or "capture",
            referer=item.get("page_url") or "",
            headers=item.get("headers") or {},
            audio_only=audio_only,
            stop_event=stop_event,
        )
        timings["download_seconds"] = round(time.perf_counter() - download_started, 3)

        if capture_id:
            update_capture(capture_id, status="transcribing")
        transcript = transcribe_file(
            downloaded["media_path"],
            model_size=options.get("model_size") or state.default_model_size,
            device=options.get("device") or state.default_device,
            compute_type=options.get("compute_type") or state.default_compute_type,
            beam_size=int(options.get("beam_size") or state.default_beam_size or 1),
            stop_event=stop_event,
        )
        timings.update(transcript.get("timings") or {})

        result = {
            "capture": item,
            "download": downloaded,
            "transcript": transcript,
            "timings": timings,
        }

        if summary_enabled:
            if capture_id:
                update_capture(capture_id, status="summarizing")
            summary_started = time.perf_counter()
            result["summary"] = summarize_text(
                transcript.get("text", ""),
                options.get("api_key") or state.api_key,
                mode=options.get("summary_mode") or "通用内容总结",
            )
            timings["summary_seconds"] = round(time.perf_counter() - summary_started, 3)

        timings["total_seconds"] = round(time.perf_counter() - total_started, 3)
        result["timings"] = timings
        if capture_id:
            update_capture(capture_id, status="processed", result=result, timings=timings)
        state.jobs.append(result)
        return result
    except Exception as exc:
        status = "cancelled" if stop_event.is_set() else "failed"
        payload = {
            "capture": item,
            "error": str(exc),
            "status": status,
            "timings": {"total_seconds": round(time.perf_counter() - total_started, 3)},
        }
        if capture_id:
            update_capture(capture_id, status=status, result=payload)
        state.jobs.append(payload)
        if status == "cancelled":
            return payload
        raise
    finally:
        with state.lock:
            if state.active_stop_event is stop_event:
                state.active_stop_event = None


def start_bridge_server(host="127.0.0.1", port=18991, state=None):
    ensure_data_dirs()
    configure_cuda_dll_paths()
    server = ThreadingHTTPServer((host, int(port)), BridgeHandler)
    server.state = state or BridgeState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
