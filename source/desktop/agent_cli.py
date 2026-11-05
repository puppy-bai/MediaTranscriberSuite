"""JSON command-line interface for local AI agents and the Media Text Suite skill."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.capture_store import add_capture, update_capture
from core.media_pipeline import download_media, summarize_text, transcribe_file
from core.paths import DATA_DIR, ensure_data_dirs


ACTIONS = ("download", "transcribe", "summarize", "download-transcribe", "download-transcribe-summarize")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def extract_urls(text):
    return list(dict.fromkeys(value.rstrip("，。！？；;,.、)") for value in URL_RE.findall(text or "")))


def load_settings():
    try:
        return json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def process_url(url, action, settings, args):
    started = time.perf_counter()
    capture = add_capture({"url": url, "page_title": url, "source": "agent"}) if url else None
    result = {"capture": {"id": capture["id"], "url": url}} if capture else {}
    timings = {}
    try:
        media_path = args.file or ""
        transcript_text = ""
        if action in ("download", "download-transcribe", "download-transcribe-summarize"):
            if capture:
                update_capture(capture["id"], status="downloading")
            downloaded = download_media(url, title=url, audio_only=False)
            result["download"] = downloaded
            media_path = downloaded["media_path"]
            timings["download_seconds"] = downloaded.get("download_seconds", 0)
        if action in ("transcribe", "download-transcribe", "download-transcribe-summarize"):
            if not media_path:
                raise RuntimeError("转写需要 --file 本地媒体文件，或选择 download-transcribe")
            if capture:
                update_capture(capture["id"], status="transcribing")
            transcript = transcribe_file(
                media_path,
                model_size=args.model or settings.get("model_size", "base"),
                device=args.device or settings.get("device", "cuda"),
                compute_type=args.compute_type or settings.get("compute_type", "float16"),
                beam_size=args.beam_size or settings.get("beam_size", 1),
            )
            result["transcript"] = transcript
            transcript_text = transcript.get("text", "")
            timings.update(transcript.get("timings") or {})
        if action == "summarize":
            transcript_text = args.text or ""
            if args.transcript:
                transcript_text = Path(args.transcript).read_text(encoding="utf-8")
            if not transcript_text:
                raise RuntimeError("总结需要 --text 或 --transcript 指定转写文本")
        if action == "download-transcribe-summarize" or action == "summarize":
            if capture:
                update_capture(capture["id"], status="summarizing")
            result["summary"] = summarize_text(transcript_text, args.api_key or settings.get("api_key", ""), mode=args.summary_mode or settings.get("summary_mode", "通用内容总结"))
            timings["summary_seconds"] = result["summary"].get("summary_seconds", 0)
        timings["total_seconds"] = round(time.perf_counter() - started, 3)
        result["timings"] = timings
        if capture:
            update_capture(capture["id"], status="processed", result=result, timings=timings)
        return {"ok": True, **result}
    except Exception as exc:
        timings["total_seconds"] = round(time.perf_counter() - started, 3)
        error = {"error": str(exc), "timings": timings}
        if capture:
            update_capture(capture["id"], status="failed", result=error, timings=timings)
        return {"ok": False, **result, **error}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Media Text Suite agent JSON interface")
    parser.add_argument("--action", required=True, choices=ACTIONS)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--text", default="", help="Pasted text or transcript text")
    parser.add_argument("--file", help="Local media file for transcribe action")
    parser.add_argument("--transcript", help="Transcript text file for summarize action")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cuda", "cpu"))
    parser.add_argument("--compute-type")
    parser.add_argument("--beam-size", type=int)
    parser.add_argument("--summary-mode")
    args = parser.parse_args(argv)
    ensure_data_dirs()
    urls = list(dict.fromkeys([*args.url, *extract_urls(args.text)]))
    settings = load_settings()
    if args.action in ("download", "download-transcribe", "download-transcribe-summarize") and not urls:
        parser.error("download actions require at least one URL")
    targets = urls or [""]
    results = [process_url(url, args.action, settings, args) for url in targets]
    print(json.dumps({"ok": all(row["ok"] for row in results), "count": len(results), "results": results}, ensure_ascii=False))
    return 0 if all(row["ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
