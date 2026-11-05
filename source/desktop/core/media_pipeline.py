import hashlib
import json
import os
import re
import time
from pathlib import Path

from .paths import DOWNLOADS_DIR, TRANSCRIPTS_DIR, ensure_data_dirs


def sanitize_filename(value, fallback="media"):
    value = str(value or fallback)
    value = re.sub(r"[\\/*?:\"<>|#\r\n\t]", "_", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value[:100].strip(" ._-") or fallback


def extract_first_url(text):
    match = re.search(r"https?://[^\s，。！？、<>\"']+", str(text or ""))
    return match.group(0) if match else str(text or "").strip()


def guess_ext(url="", mime=""):
    lower = f"{url} {mime}".lower()
    for ext in ["m3u8", "mpd", "mp4", "m4a", "mp3", "webm", "wav", "aac", "ogg", "flv", "ts"]:
        if f".{ext}" in lower or ext in lower:
            return ext
    return "media"


def normalize_headers(headers):
    normalized = {}
    for key, value in (headers or {}).items():
        if isinstance(key, str) and isinstance(value, str) and value:
            normalized[key] = value
    return normalized


def site_headers(url, referer=""):
    host = url.lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    if "bilibili.com" in host or "bilivideo.com" in host:
        headers.setdefault("Referer", "https://www.bilibili.com/")
        headers["Origin"] = "https://www.bilibili.com"
    return headers


def friendly_download_error(url, exc):
    message = str(exc)
    if "bilibili.com" in url.lower() and ("HTTP Error 412" in message or "Precondition Failed" in message):
        return (
            "B 站页面解析被 412 拦截。请先在浏览器里加载插件，打开该 B 站视频并播放几秒，"
            "等插件捕获到 bilivideo.com / m4s / m3u8 等真实媒体地址后，再在软件里处理那条捕获记录。"
        )
    if "HTTP Error 404" in message and "example.com" in url.lower():
        return "这是测试用的假视频地址 example.com/video.mp4，不能下载。请删除或忽略这条记录。"
    return message


def download_media(url, title="", referer="", headers=None, audio_only=True, stop_event=None):
    from yt_dlp import YoutubeDL

    ensure_data_dirs()
    url = extract_first_url(url)
    if not url:
        raise RuntimeError("没有可下载的 URL")

    safe_id = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:10]
    prefix = sanitize_filename(title, fallback=f"capture_{safe_id}")
    outtmpl = str(DOWNLOADS_DIR / f"{prefix}_{safe_id}.%(ext)s")

    http_headers = site_headers(url, referer)
    http_headers.update(normalize_headers(headers))

    format_selector = "bestaudio[ext=m4a]/bestaudio/best" if audio_only else "bv*+ba/best"
    started = time.perf_counter()

    def progress_hook(_data):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("任务已中断")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "format": format_selector,
        "outtmpl": outtmpl,
        "continuedl": True,
        "overwrites": False,
        "windowsfilenames": True,
        "http_headers": http_headers,
        "socket_timeout": 25,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [progress_hook],
    }
    before = {str(item.resolve()) for item in DOWNLOADS_DIR.glob("*") if item.is_file()}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = ydl.prepare_filename(info)
    except Exception as exc:
        raise RuntimeError(friendly_download_error(url, exc)) from exc

    media_path = requested if requested and Path(requested).exists() else ""
    if not media_path:
        latest = [
            item for item in DOWNLOADS_DIR.glob("*")
            if item.is_file() and str(item.resolve()) not in before
        ]
        latest = sorted(latest, key=lambda item: item.stat().st_mtime, reverse=True)
        media_path = str(latest[0]) if latest else ""
    if not media_path:
        raise RuntimeError("下载完成但未找到输出文件")

    return {
        "media_path": str(Path(media_path).resolve()),
        "title": info.get("title") or title or prefix,
        "duration": float(info.get("duration") or 0),
        "extractor": info.get("extractor_key") or info.get("extractor") or "yt-dlp",
        "download_seconds": round(time.perf_counter() - started, 3),
    }


def transcribe_file(media_path, model_size="base", device="cuda", compute_type="float16", beam_size=1, language=None, stop_event=None):
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    model_started = time.perf_counter()
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception:
        if str(device).lower() != "cuda":
            raise
        device = "cpu"
        compute_type = "int8"
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    model_seconds = time.perf_counter() - model_started

    transcribe_started = time.perf_counter()
    segments_iter, info = model.transcribe(
        str(media_path),
        language=language or None,
        beam_size=int(beam_size),
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        language_detection_segments=1,
    )
    segments = []
    for segment in segments_iter:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("任务已中断")
        text = str(segment.text or "").strip()
        if text:
            segments.append({
                "start": float(segment.start or 0),
                "end": float(segment.end or 0),
                "text": text,
            })
    transcribe_seconds = time.perf_counter() - transcribe_started
    text = "\n".join(
        f"[{item['start']:.2f}-{item['end']:.2f}] {item['text']}"
        for item in segments
    )

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(Path(media_path).stem, fallback=f"transcript_{int(time.time())}")
    payload = {
        "media_path": str(media_path),
        "engine": "faster-whisper",
        "model_size": model_size,
        "device": device,
        "compute_type": compute_type,
        "beam_size": int(beam_size),
        "language": getattr(info, "language", "") or "",
        "language_probability": float(getattr(info, "language_probability", 0) or 0),
        "segments": segments,
        "text": text,
        "timings": {
            "model_load_seconds": round(model_seconds, 3),
            "transcribe_seconds": round(transcribe_seconds, 3),
            "total_seconds": round(time.perf_counter() - started, 3),
        },
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    json_path = TRANSCRIPTS_DIR / f"{stem}_transcript.json"
    md_path = TRANSCRIPTS_DIR / f"{stem}_transcript.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(f"# {stem}\n\n{text}\n", encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(md_path)
    return payload


def summarize_text(text, api_key, mode="通用内容总结"):
    from openai import OpenAI

    if not api_key:
        raise RuntimeError("请先填写 MiniMax API Key")
    started = time.perf_counter()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    model_name = os.getenv("OPENAI_MODEL_NAME", "MiniMax-M2.5")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=90, max_retries=1)
    prompt = f"""
请用中文总结以下音视频转写内容。模式：{mode}
输出结构：
1. 一句话总结
2. 关键要点
3. 如果涉及产品/选品，列出卖点、受众、风险和下一步建议

转写内容：
{text[:60000]}
"""
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "你是中文视频内容分析助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    return {
        "summary": response.choices[0].message.content or "",
        "summary_seconds": round(time.perf_counter() - started, 3),
        "mode": mode,
    }
