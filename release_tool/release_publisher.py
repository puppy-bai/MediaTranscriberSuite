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
* Save the Personal Access Token locally so you don't retype it.
* Create the release (draft optional) and upload the asset with progress.

Run
---
    python release_publisher.py
"""

import json
import os
import sys
import threading
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
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    fp = config_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    tmp.replace(fp)
    # Set the token file hidden+system-ish on Windows (best effort).
    if os.name == "nt":
        try:
            os.system(f'attrib +h "{fp}" >nul')
        except Exception:
            pass


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
        raise RuntimeError(f"HTTP {e.code}: {err}") from e


def api_user(token: str):
    obj, _ = _request(f"{GITHUB_API}/user", token)
    return obj


def find_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict | None:
    try:
        obj, _ = _request(f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}", token)
        return obj
    except RuntimeError as e:
        if "HTTP 404" in str(e):
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


class _ProgressFile:
    """File-like wrapper that reports upload progress as bytes are read."""

    def __init__(self, path: str, callback):
        self._f = open(path, "rb")
        self._total = os.path.getsize(path)
        self._sent = 0
        self._cb = callback

    def read(self, amt: int = -1):
        data = self._f.read(amt)
        self._sent += len(data)
        if self._cb and self._total:
            self._cb(min(1.0, self._sent / self._total))
        return data

    def __len__(self):
        return self._total

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()


def upload_asset(owner: str, repo: str, release_id: int, token: str,
                 file_path: str, progress_cb=None) -> dict:
    asset_name = os.path.basename(file_path)
    total = os.path.getsize(file_path)
    url = (f"{GITHUB_UPLOADS}/repos/{owner}/{repo}/releases/{release_id}"
           f"/assets?name={urllib.parse.quote(asset_name)}")

    with _ProgressFile(file_path, progress_cb) as pf:
        req = urllib.request.Request(url, data=pf, method="POST",
                                     headers=_headers(token, "application/zip"))
        req.add_header("Content-Length", str(total))
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                raw = resp.read().decode("utf-8")
                if progress_cb:
                    progress_cb(1.0)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = ""
            try:
                err = e.read().decode("utf-8")
            except Exception:
                err = str(e)
            raise RuntimeError(f"上传失败 HTTP {e.code}: {err}") from e


def set_release_draft(owner: str, repo: str, release_id: int, token: str, draft: bool) -> None:
    try:
        _request(f"{GITHUB_API}/repos/{owner}/{repo}/releases/{release_id}", token,
                 method="PATCH", body={"draft": draft})
    except RuntimeError:
        pass


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

        self._build_ui()
        self._populate_from_config()

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
            value="Token 会保存在本机（仅限当前用户），用于创建 Release 与上传。")
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
        self.file_entry.bind("<Return>", lambda e: self._clear_file_but_help())
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
        self.token_var.set(self.cfg.get("token", ""))
        self.repo_var.set(self.cfg.get("repo", ""))
        self.tag_var.set(self.cfg.get("tag", ""))
        self.title_var.set(self.cfg.get("title", ""))
        self.body_text.insert("1.0", self.cfg.get("body", ""))
        self.file_var.set(self.cfg.get("last_file", ""))

    # ---- minor helpers ----
    def _clear_file_but_help(self):
        messagebox.showinfo("提示", "可使用“浏览…”按钮选择文件，或在输入框直接粘贴完整路径。")

    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token.get() else "●")

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.publish_btn.config(state=state)
        self.verify_btn.config(state=state)

    def _log(self, msg: str):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

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
        if "/" not in repo:
            raise ValueError("仓库格式应为 owner/name，例如 puppy-bai/MediaTranscriberSuite")
        tag = self.tag_var.get().strip()
        if not tag:
            raise ValueError("请填写 Tag（如 v1.2.0）")
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

        save_config({
            "token": token, "repo": repo, "tag": tag,
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
                                     draft=self.draft_var.get())
                release_id = rel["id"]
                self._log(f"已创建 Release id={release_id}（draft={self.draft_var.get()}）")

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
        self.root.after(0, lambda: self.progress.config(value=pct))

    def _run_async(self, fn, on_success, on_error, status=""):
        self._set_busy(True)
        self.status_label.config(text=status or "工作中…")
        self._log(f">> {status or '执行中'}")

        def target():
            try:
                result = fn()
                self.root.after(0, lambda: self._finish_async(True, result, on_success))
            except Exception as e:  # noqa: BLE001
                self.root.after(0, lambda: self._finish_async(False, str(e), on_error))

        threading.Thread(target=target, daemon=True).start()

    def _finish_async(self, ok, result, cb):
        self._set_busy(False)
        self.status_label.config(text="完成" if ok else "出错")
        cb(result)

    def _on_close(self):
        try:
            save_config({
                "token": self.token_var.get().strip(),
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