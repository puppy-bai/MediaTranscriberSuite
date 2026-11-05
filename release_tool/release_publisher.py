#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media Transcriber Suite - Release Publisher GUI
================================================
A small dependency-free desktop tool (tkinter + Python stdlib) to publish a
GitHub Release and upload one asset (e.g. the built Windows zip).

Features
--------
* Pick a file (zip / exe / anything) via the native file dialog.
* Set repository (owner/name), tag, release title and body.
* Enter a Personal Access Token for this session (never save it to disk).
* Create the release (draft optional) and upload the asset with progress.

Run
---
    python release_publisher.py
"""

import json
import os
import sys
import threading
import queue
import http.client
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import (
    Tk, ttk, StringVar, BooleanVar, filedialog, messagebox, scrolledtext,
)

APP_NAME = "Media Transcriber Suite - Release Publisher"
CONFIG_DIR_NAME = ".media_transcriber_release_tool"
CONFIG_FILE_NAME = "config.json"

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


# --------------------------------------------------------------------------
# Persisted config
# --------------------------------------------------------------------------

def config_file() -> Path:
    return Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config() -> dict:
    try:
        with open(config_file(), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if "token" in cfg:
            save_config(cfg)  # Remove plaintext credentials left by older releases.
        return {key: value for key, value in cfg.items() if key != "token"}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    fp = config_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    # Never persist authentication credentials. Old config files may contain one;
    # replacing the file also removes that legacy plaintext token.
    safe = {key: value for key, value in cfg.items() if key != "token"}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(safe, fh, ensure_ascii=False, indent=2)
    tmp.replace(fp)


# --------------------------------------------------------------------------
# GitHub API (stdlib only)
# --------------------------------------------------------------------------

def _headers(token: str, content_type: str | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": APP_NAME,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def _request(url: str, token: str, *, method: str = "GET",
             body: dict | None = None, data: bytes | None = None,
             content_type: str | None = None, timeout: float = 120):
    payload = data if data is not None else (
        json.dumps(body).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(url, data=payload, method=method,
                                 headers=_headers(token, content_type))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return (json.loads(raw) if raw else {}), resp.status
    except urllib.error.HTTPError as e:
        err = ""
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        raise RuntimeError(f"HTTP {e.code}: {err[:1000]}") from e


def api_user(token: str):
    obj, _ = _request(f"{GITHUB_API}/user", token)
    return obj


def find_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict | None:
    try:
        obj, _ = _request(f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}", token)
        return obj
    except RuntimeError as e:
        if str(e).startswith("HTTP 404:"):
            return None
        raise


def create_release(owner: str, repo: str, token: str, *, tag: str, name: str,
                   body: str, draft: bool) -> dict:
    payload = {
        "tag_name": tag,
        "name": name or tag,
        "body": body,
        "draft": draft,
        "prerelease": False,
        "generate_release_notes": False,
    }
    obj, _ = _request(f"{GITHUB_API}/repos/{owner}/{repo}/releases", token,
                      method="POST", body=payload)
    return obj


def upload_asset(owner: str, repo: str, release_id: int, token: str,
                 file_path: str, progress_cb=None) -> dict:
    """Stream a release asset without loading the whole file into memory."""
    asset_name = os.path.basename(file_path)
    total = os.path.getsize(file_path)
    encoded = urllib.parse.quote(asset_name, safe="")
    content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
    path = f"/repos/{owner}/{repo}/releases/{release_id}/assets?name={encoded}"
    conn = http.client.HTTPSConnection("uploads.github.com", timeout=1800)
    try:
        conn.putrequest("POST", path)
        for key, value in _headers(token, content_type).items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(total))
        conn.endheaders()
        sent = 0
        with open(file_path, "rb") as fh:
            while chunk := fh.read(1024 * 1024):
                conn.send(chunk)
                sent += len(chunk)
                if progress_cb and total:
                    progress_cb(min(0.99, sent / total))
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"上传失败 HTTP {resp.status}: {raw[:1000]}")
        if progress_cb:
            progress_cb(1.0)
        return json.loads(raw)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class ReleasePublisherApp:
    def __init__(self, root: Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("780x780")
        root.minsize(640, 560)

        self.cfg = load_config()
        self._busy = False
        self._events = queue.Queue()

        self._build_ui()
        self._populate_from_config()
        self.root.after(100, self._drain_events)

    # ---- UI ----
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        # --- ① auth ---
        auth = ttk.LabelFrame(self.root, text="① GitHub 连接", padding=10)
        auth.pack(fill="x", **pad)
        ttk.Label(auth, text="Token（个人访问令牌）:").grid(row=0, column=0, sticky="w")
        self.token_var = StringVar()
        self.token_entry = ttk.Entry(auth, textvariable=self.token_var, show="●", width=52)
        self.token_entry.grid(row=0, column=1, sticky="we", padx=8)
        auth.columnconfigure(1, weight=1)

        self.show_token = BooleanVar(value=False)
        ttk.Checkbutton(auth, text="显示", variable=self.show_token,
                        command=self._toggle_token_visibility).grid(row=0, column=2)

        self.verify_btn = ttk.Button(auth, text="验证 Token", command=self._verify_token)
        self.verify_btn.grid(row=1, column=1, sticky="w", pady=(6, 0))

        self.auth_hint = StringVar(
            value="Token 仅在本次运行时保留在内存中，不写入配置文件。")
        ttk.Label(auth, textvariable=self.auth_hint, foreground="#666").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- ② repo & release ---
        info = ttk.LabelFrame(self.root, text="② 仓库与 Release", padding=10)
        info.pack(fill="x", **pad)

        ttk.Label(info, text="仓库（owner/name）:").grid(row=0, column=0, sticky="w")
        self.repo_var = StringVar()
        ttk.Entry(info, textvariable=self.repo_var, width=44).grid(row=0, column=1, sticky="we", padx=8)
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="Tag（如 v1.2.0）:").grid(row=1, column=0, sticky="w")
        self.tag_var = StringVar()
        ttk.Entry(info, textvariable=self.tag_var, width=44).grid(row=1, column=1, sticky="we", padx=8)

        ttk.Label(info, text="Release 标题:").grid(row=2, column=0, sticky="w")
        self.title_var = StringVar()
        ttk.Entry(info, textvariable=self.title_var, width=44).grid(row=2, column=1, sticky="we", padx=8)

        ttk.Label(info, text="发布说明（支持 Markdown）:").grid(row=3, column=0, sticky="nw")
        self.body_text = scrolledtext.ScrolledText(info, width=52, height=8, wrap="word")
        self.body_text.grid(row=3, column=1, sticky="we", padx=8, pady=4)

        ttk.Label(info, text="文件（zip / exe 等）:").grid(row=4, column=0, sticky="w")
        self.file_var = StringVar()
        self.file_entry = ttk.Entry(info, textvariable=self.file_var, width=44)
        self.file_entry.grid(row=4, column=1, sticky="we", padx=8)
        ttk.Button(info, text="浏览…", command=self._browse_file).grid(row=4, column=2)

        # --- ③ options ---
        opts = ttk.LabelFrame(self.root, text="③ 发布选项", padding=10)
        opts.pack(fill="x", **pad)
        self.draft_var = BooleanVar(value=False)
        ttk.Checkbutton(opts, text="保存为草稿（Draft），之后在网页上手动发布",
                        variable=self.draft_var).pack(anchor="w")

        # --- ④ actions ---
        actions = ttk.LabelFrame(self.root, text="④ 操作", padding=10)
        actions.pack(fill="x", **pad)
        btn_row = ttk.Frame(actions)
        btn_row.pack(fill="x")
        self.publish_btn = ttk.Button(btn_row, text="创建 Release 并上传文件", command=self._publish)
        self.publish_btn.pack(side="left")
        self.status_label = ttk.Label(actions, text="就绪")
        self.status_label.pack(side="left", padx=16)

        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))

        # --- log ---
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=10)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(log_frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _populate_from_config(self):
        self.token_var.set("")
        self.repo_var.set(self.cfg.get("repo", ""))
        self.tag_var.set(self.cfg.get("tag", ""))
        self.title_var.set(self.cfg.get("title", ""))
        self.body_text.insert("1.0", self.cfg.get("body", ""))
        self.file_var.set(self.cfg.get("last_file", ""))

    # ---- minor helpers ----
    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token.get() else "●")

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.publish_btn.config(state=state)
        self.verify_btn.config(state=state)

    def _log(self, msg: str):
        self._events.put(("log", msg))

    def _drain_events(self):
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log.config(state="normal")
                self.log.insert("end", value + "\n")
                self.log.see("end")
                self.log.config(state="disabled")
            elif kind == "progress":
                self.progress.config(value=value)
            elif kind == "finish":
                ok, result, cb = value
                self._finish_async(ok, result, cb)
        self.root.after(100, self._drain_events)

    # ---- actions ----
    def _browse_file(self):
        path = filedialog.askopenfilename(title="选择要上传的文件（如打包好的 zip）")
        if path:
            self.file_var.set(path)

    def _validate(self):
        token = self.token_var.get().strip()
        if not token:
            raise ValueError("请先填写 GitHub Token（可在 github.com/settings/tokens 创建）")
        repo = self.repo_var.get().strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("仓库格式应为 owner/name，例如 puppy-bai/MediaTranscriberSuite")
        tag = self.tag_var.get().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
            raise ValueError("Tag 仅可包含英文字母、数字、点、横线和下划线，如 v1.2.0")
        path = self.file_var.get().strip()
        if not path:
            raise ValueError("请选择要上传的文件")
        if not os.path.isfile(path):
            raise ValueError(f"文件不存在：{path}")
        return token, repo, tag, self.title_var.get(), self.body_text.get("1.0", "end").strip(), path

    def _verify_token(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("提示", "请先填写 Token")
            return
        self._run_async(
            lambda: api_user(token),
            on_success=lambda u: self._log(f"Token 有效：登录名 {u.get('login')}"),
            on_error=lambda e: self._log(f"校验失败：{e}"),
            status="正在校验 Token…",
        )

    def _publish(self):
        if self._busy:
            return
        try:
            token, repo, tag, title, body, path = self._validate()
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return
        owner, repo_name = repo.split("/", 1)
        draft = self.draft_var.get()

        save_config({
            "repo": repo, "tag": tag,
            "title": title, "body": body, "last_file": path,
        })

        def job():
            existing = find_release_by_tag(owner, repo_name, tag, token)
            if existing:
                release_id = existing["id"]
                self._log(f"已存在 Release（id={release_id}），直接上传附件。")
            else:
                self._log(f"创建 Release：{tag} @ {repo}")
                rel = create_release(owner, repo_name, token, tag=tag,
                                     name=title or tag, body=body,
                                     draft=draft)
                release_id = rel["id"]
                self._log(f"已创建 Release id={release_id}（draft={draft}）")

            self._log(f"正在上传 {os.path.basename(path)}（{round(os.path.getsize(path)/1e6,1)} MB）…")
            asset = upload_asset(owner, repo_name, release_id, token, path,
                                 progress_cb=self._on_progress)
            self._log(f"上传完成：{asset.get('name')}（{asset.get('size')} bytes）")
            url = f"https://github.com/{repo}/releases/tag/{tag}"
            self._log(f"Release 地址：{url}")
            return url

        self.progress["value"] = 0
        self._run_async(job,
                        on_success=self._publish_done,
                        on_error=lambda e: self._log(f"发布失败：{e}"),
                        status="正在发布…")

    def _publish_done(self, url):
        self.progress["value"] = 100
        messagebox.showinfo("完成", f"已发布：\n{url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _on_progress(self, frac: float):
        pct = int(frac * 100)
        self._events.put(("progress", pct))

    def _run_async(self, fn, on_success, on_error, status=""):
        self._set_busy(True)
        self.status_label.config(text=status or "工作中…")
        self._log(f">> {status or '执行中'}")

        def target():
            try:
                result = fn()
                self._events.put(("finish", (True, result, on_success)))
            except Exception as e:  # noqa: BLE001
                self._events.put(("finish", (False, str(e), on_error)))

        threading.Thread(target=target, daemon=True).start()

    def _finish_async(self, ok, result, cb):
        self._set_busy(False)
        self.status_label.config(text="完成" if ok else "出错")
        cb(result)

    def _on_close(self):
        try:
            save_config({
                "repo": self.repo_var.get().strip(),
                "tag": self.tag_var.get().strip(),
                "title": self.title_var.get(),
                "body": self.body_text.get("1.0", "end").strip(),
                "last_file": self.file_var.get().strip(),
            })
        except Exception:
            pass
        finally:
            self.root.destroy()


def main():
    root = Tk()
    app = ReleasePublisherApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()