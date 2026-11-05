#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media Transcriber Suite - Release Publisher GUI
================================================
A dependency-free desktop tool (tkinter + Python stdlib) that publishes a
GitHub Release and uploads one or more assets, with a permission self-check,
proxy support and a cancellable progress display.

Features
--------
* Pick several files (zip / exe / anything) and upload them in one run.
* Permission self-check: login, repository access and a real write probe, so a
  token that cannot upload is reported before anything is published.
* Proxy mode: follow the system/environment settings, connect directly, or use
  a custom proxy - useful behind campus or office networks.
* Create the release, reuse an existing one, optionally keep it as a draft.
* Streaming upload with speed / remaining time, and a Cancel button.

Run
---
    python release_publisher.py
"""

import base64
import json
import mimetypes
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import (
    Tk, Toplevel, ttk, StringVar, BooleanVar, filedialog, messagebox, scrolledtext, Listbox,
)

try:
    from . import token_store
except ImportError:  # Direct script launch and PyInstaller onefile.
    import token_store

APP_NAME = "Media Transcriber Suite - Release Publisher"
CONFIG_DIR_NAME = ".media_transcriber_release_tool"
CONFIG_FILE_NAME = "config.json"
DEFAULT_REPO = "puppy-bai/MediaTranscriberSuite"

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


class UploadCancelled(Exception):
    """Raised when the user presses Cancel during an upload."""


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
# Network session (proxy aware)
# --------------------------------------------------------------------------

def build_opener(proxy_mode: str = "system", proxy_url: str = ""):
    """Return a urllib opener honouring the selected proxy mode."""
    proxy_mode = (proxy_mode or "system").lower()
    if proxy_mode == "direct":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if proxy_mode == "custom" and proxy_url.strip():
        addr = proxy_url.strip()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": addr, "https": addr}))
    return urllib.request.build_opener()


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


def request_json(url: str, token: str, opener, *, method: str = "GET",
                 body: dict | None = None, timeout: float = 60,
                 retries: int = 2):
    """Call the GitHub API and return (json, status, headers).

    Transient network errors are retried; HTTP errors surface immediately with
    a short, readable message.
    """
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=payload, method=method,
                                     headers=_headers(token, "application/json" if payload else None))
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return (json.loads(raw) if raw else {}), resp.status, headers
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:1000]
            except Exception:
                detail = str(e)
            raise RuntimeError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"网络错误：{last_error}")


def api_user(token: str, opener) -> dict:
    obj, _, _ = request_json(f"{GITHUB_API}/user", token, opener)
    return obj


def check_permissions(owner: str, repo: str, token: str, opener) -> dict:
    """Diagnose exactly what a token can and cannot do on one repository."""
    info = {
        "login": None, "scopes": None, "repo_ok": False, "user_perms": {},
        "default_branch": None, "can_write": False,
        "repo_error": None, "write_error": None, "notes": [],
    }
    user, _, headers = request_json(f"{GITHUB_API}/user", token, opener)
    info["login"] = user.get("login")
    # Classic tokens advertise their scopes here; fine-grained tokens do not.
    info["scopes"] = headers.get("x-oauth-scopes", "").strip() or None

    try:
        repo_obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}", token, opener)
    except RuntimeError as e:
        info["repo_error"] = str(e)
        return info
    info["repo_ok"] = True
    info["default_branch"] = repo_obj.get("default_branch")
    info["user_perms"] = repo_obj.get("permissions") or {}

    try:
        # Creating an unreferenced blob proves Contents: write without leaving
        # any visible change in the repository.
        request_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs", token, opener,
                     method="POST", body={"content": "release-publisher probe",
                                          "encoding": "utf-8"})
        info["can_write"] = True
    except RuntimeError as e:
        info["write_error"] = str(e)

    scopes = info["scopes"]
    if scopes is not None:
        granted = {s.strip() for s in scopes.split(",") if s.strip()}
        if "repo" not in granted:
            info["notes"].append("Classic token 缺少 repo scope，无法创建 Release。")
        if "workflow" not in granted:
            info["notes"].append("Classic token 缺少 workflow scope：可以发 Release，但无法推送 .github/workflows/ 文件。")
    return info


def find_release_by_tag(owner: str, repo: str, tag: str, token: str, opener) -> dict | None:
    try:
        obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}",
                                 token, opener)
        return obj
    except RuntimeError as e:
        if str(e).startswith("HTTP 404:"):
            return None
        raise


def create_release(owner: str, repo: str, token: str, opener, *, tag: str, name: str,
                   body: str, draft: bool) -> dict:
    payload = {
        "tag_name": tag,
        "name": name or tag,
        "body": body,
        "draft": draft,
        "prerelease": False,
        "generate_release_notes": False,
    }
    obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases", token, opener,
                             method="POST", body=payload)
    return obj


def update_release(owner: str, repo: str, token: str, opener, release_id: int, *,
                   name: str, body: str, draft: bool) -> dict:
    payload = {"name": name or None, "body": body, "draft": draft}
    obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/{release_id}",
                             token, opener, method="PATCH", body=payload)
    return obj


def create_repository(token: str, opener, *, name: str, description: str = "",
                      private: bool = False, auto_init: bool = True) -> dict:
    """Create a repository owned by the authenticated user (POST /user/repos)."""
    payload = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": auto_init,
        "has_issues": True,
        "has_wiki": False,
    }
    obj, _, _ = request_json(f"{GITHUB_API}/user/repos", token, opener,
                             method="POST", body=payload)
    return obj


# --------------------------------------------------------------------------
# Upload plain files / folders into the repository (no Release involved)
# --------------------------------------------------------------------------

MAX_BLOB_BYTES = 100 * 1024 * 1024  # GitHub refuses blobs larger than 100 MB


def _encode_branch(branch: str) -> str:
    return urllib.parse.quote(branch, safe="/")


def get_branch_head(owner: str, repo: str, branch: str, token: str, opener):
    """Return (commit_sha, tree_sha) or (None, None) when the branch is absent."""
    ref_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{_encode_branch(branch)}"
    try:
        ref, _, _ = request_json(ref_url, token, opener)
    except RuntimeError as e:
        if str(e).startswith("HTTP 404:"):
            # A 404 can also mean an inaccessible repository/token. Verify the
            # repository before treating it as an absent branch.
            try:
                request_json(f"{GITHUB_API}/repos/{owner}/{repo}", token, opener)
            except RuntimeError as repo_error:
                raise RuntimeError(
                    f"无法访问仓库 {owner}/{repo}；请检查仓库名称、Token 授权范围和网络："
                    f"{repo_error}") from repo_error
            return None, None
        raise RuntimeError(f"读取分支 {branch} 失败（{ref_url}）：{e}") from e
    commit_sha = (ref.get("object") or {}).get("sha")
    if not commit_sha:
        raise RuntimeError(f"分支 {branch} 的引用没有提交 SHA，无法安全上传。")
    commit, _, _ = request_json(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/commits/{commit_sha}", token, opener)
    tree_sha = (commit.get("tree") or {}).get("sha")
    if not tree_sha:
        raise RuntimeError(f"无法读取分支 {branch} 的基础目录树，已停止以免覆盖仓库内容。")
    return commit_sha, tree_sha


def create_blob(owner: str, repo: str, token: str, opener, data: bytes) -> str:
    payload = {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"}
    obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs", token, opener,
                             method="POST", body=payload)
    return obj["sha"]


def create_tree(owner: str, repo: str, token: str, opener, entries: list[dict],
                base_tree: str | None = None) -> str:
    payload: dict = {"tree": entries}
    if base_tree:
        payload["base_tree"] = base_tree
    obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees", token, opener,
                             method="POST", body=payload)
    return obj["sha"]


def create_commit(owner: str, repo: str, token: str, opener, message: str,
                  tree: str, parents: list[str]) -> dict:
    payload = {"message": message, "tree": tree,
               "parents": [sha for sha in parents if sha]}
    obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/commits", token, opener,
                             method="POST", body=payload)
    return obj


def update_branch_ref(owner: str, repo: str, token: str, opener, branch: str,
                      sha: str, *, create: bool = False) -> dict:
    """Move an existing branch, or create only when absence was confirmed earlier."""
    if create:
        obj, _, _ = request_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/refs", token, opener,
                                 method="POST",
                                 body={"ref": f"refs/heads/{branch}", "sha": sha})
    else:
        obj, _, _ = request_json(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{_encode_branch(branch)}",
            token, opener, method="PATCH", body={"sha": sha, "force": False})
    return obj


def upload_files_to_repo(owner: str, repo: str, token: str, opener,
                         files: list[tuple[str, str]], *, branch: str = "main",
                         target_dir: str = "", message: str = "",
                         progress_cb=None, cancel: threading.Event | None = None,
                         log=None) -> dict:
    """Commit local files into a branch through the Git data API.

    ``files`` holds (local path, path relative to the target directory) pairs.
    Returns a summary dict with the new commit SHA and URL.
    """
    prefix = target_dir.strip().strip("/")
    total = len(files)
    if not total:
        raise ValueError("请先选择至少一个要上传的文件。")
    if log:
        log(f"检查仓库 {owner}/{repo} 的分支 {branch}…")
    commit_sha, tree_sha = get_branch_head(owner, repo, branch, token, opener)
    if log:
        log(f"目标分支 {branch}：" + (f"基于提交 {commit_sha[:10]}" if commit_sha else "尚不存在，将新建"))
    blobs: list[tuple[str, str]] = []
    for index, (local_path, rel) in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            raise UploadCancelled()
        size = os.path.getsize(local_path)
        if size > MAX_BLOB_BYTES:
            raise RuntimeError(f"{rel} 为 {human_size(size)}，超过 GitHub 单个文件 100 MB 的限制。"
                               "请改用 Release 附件或 Git LFS。")
        if log:
            log(f"[{index}/{total}] 读取 {rel}（{human_size(size)}）")
        with open(local_path, "rb") as fh:
            data = fh.read()
        blob_sha = create_blob(owner, repo, token, opener, data)
        blobs.append((rel, blob_sha))
        if progress_cb:
            progress_cb(index / max(total + 2, 1), rel)

    entries = []
    for rel, blob_sha in blobs:
        path = f"{prefix}/{rel}" if prefix else rel
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

    new_tree = create_tree(owner, repo, token, opener, entries, base_tree=tree_sha)
    if progress_cb:
        progress_cb((total + 1) / (total + 2), "tree")

    commit = create_commit(owner, repo, token, opener,
                           message or f"Upload {total} file(s) to {prefix or 'repository root'}",
                           new_tree, [commit_sha] if commit_sha else [])
    update_branch_ref(owner, repo, token, opener, branch, commit["sha"],
                      create=commit_sha is None)
    if progress_cb:
        progress_cb(1.0, "ref")
    return {
        "commit": commit["sha"],
        "url": commit.get("html_url") or f"https://github.com/{owner}/{repo}/commit/{commit['sha']}",
        "count": total,
        "branch": branch,
    }


MIME_OVERRIDES = {
    ".zip": "application/zip",
    ".exe": "application/vnd.microsoft.portable-executable",
    ".msi": "application/x-msi",
    ".7z": "application/x-7z-compressed",
    ".tar.gz": "application/gzip",
    ".tgz": "application/gzip",
}


def guess_content_type(name: str) -> str:
    """Stable MIME type (the Windows registry maps .zip inconsistently)."""
    lowered = name.lower()
    for suffix, mime in MIME_OVERRIDES.items():
        if lowered.endswith(suffix):
            return mime
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class _ProgressBody:
    """File wrapper that reports progress and allows cancellation mid-upload."""

    def __init__(self, path: str, total: int, progress_cb=None, cancel: threading.Event | None = None):
        self._fh = open(path, "rb")
        self._total = total
        self._sent = 0
        self._cb = progress_cb
        self._cancel = cancel

    def read(self, size: int = -1) -> bytes:
        if self._cancel is not None and self._cancel.is_set():
            raise UploadCancelled()
        chunk = self._fh.read(1024 * 1024 if size is None or size < 0 else max(size, 1024 * 1024))
        self._sent += len(chunk)
        if self._cb and self._total:
            self._cb(min(0.999, self._sent / self._total), self._sent)
        return chunk

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def upload_asset(owner: str, repo: str, release_id: int, token: str, file_path: str,
                 opener, progress_cb=None, cancel: threading.Event | None = None) -> dict:
    """Stream a release asset without loading the whole file into memory."""
    asset_name = os.path.basename(file_path)
    total = os.path.getsize(file_path)
    encoded = urllib.parse.quote(asset_name, safe="")
    content_type = guess_content_type(asset_name)
    url = f"{GITHUB_UPLOADS}/repos/{owner}/{repo}/releases/{release_id}/assets?name={encoded}"

    headers = _headers(token, content_type)
    headers["Content-Length"] = str(total)
    body = _ProgressBody(file_path, total, progress_cb, cancel)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=1800) as resp:
            raw = resp.read().decode("utf-8")
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"上传失败 HTTP {resp.status}: {raw[:1000]}")
        if progress_cb:
            progress_cb(1.0, total)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:1000]
        except Exception:
            detail = str(e)
        raise RuntimeError(f"上传失败 HTTP {e.code}: {detail}") from e
    finally:
        body.close()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

PROXY_LABELS = {
    "跟随系统 / 环境变量": "system",
    "强制直连（不走代理）": "direct",
    "使用自定义代理": "custom",
}


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GB"


class ReleasePublisherApp:
    def __init__(self, root: Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("940x1020")
        root.minsize(780, 700)

        self.cfg = load_config()
        self._busy = False
        self._events = queue.Queue()
        self._cancel_evt = threading.Event()
        self._cancel = threading.Event()
        self._files: list[str] = []
        self._repo_files: list[tuple[str, str]] = []
        self._release_url = ""
        self._saved_token_error = None
        self._create_win = None

        self._build_ui()
        self._populate_from_config()
        root.report_callback_exception = self._report_callback_exception
        self.root.after(100, self._drain_events)
        if self._saved_token_error:
            self.root.after(200, lambda: messagebox.showwarning(
                "已保存的 Token 无法读取", self._saved_token_error))

    # ---- UI ----
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        # --- ① auth & network ---
        auth = ttk.LabelFrame(self.root, text="① GitHub 连接与网络", padding=10)
        auth.pack(fill="x", **pad)
        auth.columnconfigure(1, weight=1)

        ttk.Label(auth, text="Token（个人访问令牌）:").grid(row=0, column=0, sticky="w")
        self.token_var = StringVar()
        self.token_entry = ttk.Entry(auth, textvariable=self.token_var, show="●", width=52)
        self.token_entry.grid(row=0, column=1, sticky="we", padx=8)
        self.show_token = BooleanVar(value=False)
        ttk.Checkbutton(auth, text="显示", variable=self.show_token,
                        command=self._toggle_token_visibility).grid(row=0, column=2)

        self.remember_token = BooleanVar(value=False)
        self.remember_checkbox = ttk.Checkbutton(
            auth, text="记住 Token（仅当前 Windows 用户可解密）",
            variable=self.remember_token, command=self._remember_token_changed)
        self.remember_checkbox.grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=(5, 0))

        ttk.Label(auth, text="网络:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        net_row = ttk.Frame(auth)
        net_row.grid(row=2, column=1, columnspan=2, sticky="we", padx=8, pady=(6, 0))
        self.proxy_mode = StringVar(value="跟随系统 / 环境变量")
        self.proxy_combo = ttk.Combobox(net_row, textvariable=self.proxy_mode, width=20,
                                        state="readonly", values=list(PROXY_LABELS))
        self.proxy_combo.pack(side="left")
        self.proxy_var = StringVar()
        self.proxy_entry = ttk.Entry(net_row, textvariable=self.proxy_var, width=32)
        self.proxy_entry.pack(side="left", padx=8, fill="x", expand=True)
        self.proxy_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_proxy_state())

        btn_row = ttk.Frame(auth)
        btn_row.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.verify_btn = ttk.Button(btn_row, text="权限自检", command=self._check_permissions)
        self.verify_btn.pack(side="left")
        ttk.Label(btn_row, text="（检查登录、仓库访问与写入权限）",
                  foreground="#888").pack(side="left", padx=8)

        self.auth_hint = StringVar(
            value="默认不保存；勾选记住后，仅当前 Windows 用户可解密保存的 Token。")
        ttk.Label(auth, textvariable=self.auth_hint, foreground="#666").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # --- ② repo & release ---
        info = ttk.LabelFrame(self.root, text="② 仓库与 Release", padding=10)
        info.pack(fill="x", **pad)
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="仓库（owner/name）:").grid(row=0, column=0, sticky="w")
        self.repo_var = StringVar()
        ttk.Entry(info, textvariable=self.repo_var, width=44).grid(row=0, column=1, sticky="we", padx=8)
        self.create_repo_btn = ttk.Button(info, text="创建仓库…", width=12,
                                          command=self._open_create_repo)
        self.create_repo_btn.grid(row=0, column=2, sticky="w")

        ttk.Label(info, text="Tag（如 v1.2.0）:").grid(row=1, column=0, sticky="w")
        self.tag_var = StringVar()
        ttk.Entry(info, textvariable=self.tag_var, width=44).grid(row=1, column=1, sticky="we", padx=8)

        ttk.Label(info, text="Release 标题:").grid(row=2, column=0, sticky="w")
        self.title_var = StringVar()
        ttk.Entry(info, textvariable=self.title_var, width=44).grid(row=2, column=1, sticky="we", padx=8)

        ttk.Label(info, text="发布说明（支持 Markdown）:").grid(row=3, column=0, sticky="nw")
        self.body_text = scrolledtext.ScrolledText(info, width=52, height=6, wrap="word")
        self.body_text.grid(row=3, column=1, sticky="we", padx=8, pady=4)

        opts_row = ttk.Frame(info)
        opts_row.grid(row=4, column=1, sticky="w", padx=8)
        self.draft_var = BooleanVar(value=False)
        ttk.Checkbutton(opts_row, text="保存为草稿，稍后手动发布",
                        variable=self.draft_var).pack(side="left")
        self.update_body_var = BooleanVar(value=True)
        ttk.Checkbutton(opts_row, text="更新的 Release 说明",
                        variable=self.update_body_var).pack(side="left", padx=16)

        # --- ③ files ---
        files = ttk.LabelFrame(self.root, text="③ 要上传的文件", padding=10)
        files.pack(fill="both", expand=True, **pad)
        files.columnconfigure(0, weight=1)

        self.file_list = Listbox(files, height=5, selectmode="extended", activestyle="dotbox")
        self.file_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        scroll = ttk.Scrollbar(files, orient="vertical", command=self.file_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=scroll.set)

        file_btns = ttk.Frame(files)
        file_btns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(file_btns, text="添加文件…", command=self._add_files).pack(side="left")
        ttk.Button(file_btns, text="移除所选", command=self._remove_selected).pack(side="left", padx=8)
        ttk.Button(file_btns, text="清空", command=self._clear_files).pack(side="left")
        self.file_hint = StringVar(value="尚未选择文件")
        ttk.Label(file_btns, textvariable=self.file_hint, foreground="#888").pack(side="left", padx=12)

        # --- ④ push files into the repository (no Release) ---
        push = ttk.LabelFrame(self.root, text="④ 上传文件/文件夹到仓库（不创建 Release）",
                              padding=10)
        push.pack(fill="both", expand=True, **pad)
        push.columnconfigure(0, weight=1)

        self.repo_file_list = Listbox(push, height=4, selectmode="extended", activestyle="dotbox")
        self.repo_file_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        push_scroll = ttk.Scrollbar(push, orient="vertical", command=self.repo_file_list.yview)
        push_scroll.grid(row=0, column=1, sticky="ns")
        self.repo_file_list.configure(yscrollcommand=push_scroll.set)

        push_btns = ttk.Frame(push)
        push_btns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(push_btns, text="添加文件…", command=self._add_repo_files).pack(side="left")
        ttk.Button(push_btns, text="添加文件夹…", command=self._add_repo_folder).pack(side="left", padx=8)
        ttk.Button(push_btns, text="移除所选",
                   command=self._remove_repo_selected).pack(side="left")
        ttk.Button(push_btns, text="清空", command=self._clear_repo_files).pack(side="left", padx=8)
        self.repo_file_hint = StringVar(value="尚未选择文件")
        ttk.Label(push_btns, textvariable=self.repo_file_hint,
                  foreground="#888").pack(side="left", padx=12)

        push_opts = ttk.Frame(push)
        push_opts.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        push_opts.columnconfigure(1, weight=1)
        ttk.Label(push_opts, text="仓库内目录（留空=根目录）:").grid(row=0, column=0, sticky="w")
        self.repo_dir_var = StringVar()
        ttk.Entry(push_opts, textvariable=self.repo_dir_var, width=28).grid(
            row=0, column=1, sticky="we", padx=8)
        ttk.Label(push_opts, text="分支:").grid(row=0, column=2, sticky="w")
        self.branch_var = StringVar(value="main")
        ttk.Entry(push_opts, textvariable=self.branch_var, width=16).grid(
            row=0, column=3, sticky="w", padx=8)

        ttk.Label(push_opts, text="提交说明:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.commit_msg_var = StringVar()
        ttk.Entry(push_opts, textvariable=self.commit_msg_var, width=48).grid(
            row=1, column=1, columnspan=3, sticky="we", padx=8, pady=(6, 0))

        push_action = ttk.Frame(push)
        push_action.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.push_btn = ttk.Button(push_action, text="提交到仓库",
                                   command=self._push_files_to_repo)
        self.push_btn.pack(side="left")
        ttk.Label(push_action, text="（通过 Git API 直接提交文件，不生成 Release）",
                  foreground="#888").pack(side="left", padx=8)

        # --- ⑤ actions ---
        actions = ttk.LabelFrame(self.root, text="⑤ 发布 Release", padding=10)
        actions.pack(fill="x", **pad)
        btn_row2 = ttk.Frame(actions)
        btn_row2.pack(fill="x")
        self.publish_btn = ttk.Button(btn_row2, text="创建 Release 并上传", command=self._publish)
        self.publish_btn.pack(side="left")
        self.cancel_btn = ttk.Button(btn_row2, text="取消", command=self._cancel_upload, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(btn_row2, text="打开 Release 页面",
                                   command=self._open_release, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        self.status_label = ttk.Label(btn_row2, text="就绪")
        self.status_label.pack(side="left", padx=12)

        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 2))
        self.progress_hint = StringVar(value="")
        ttk.Label(actions, textvariable=self.progress_hint, foreground="#666").pack(anchor="w")

        # --- ⑥ log ---
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=10)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(log_frame, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _populate_from_config(self):
        self.token_var.set("")
        try:
            saved = token_store.load_token(config_file())
            if saved:
                self.token_var.set(saved)
                self.remember_token.set(True)
        except (token_store.TokenStoreError, OSError) as e:
            self._saved_token_error = str(e)
        self.repo_var.set(self.cfg.get("repo") or DEFAULT_REPO)
        self.tag_var.set(self.cfg.get("tag", ""))
        self.title_var.set(self.cfg.get("title", ""))
        self.body_text.insert("1.0", self.cfg.get("body", ""))
        mode = self.cfg.get("proxy_mode", "system")
        for label, value in PROXY_LABELS.items():
            if value == mode:
                self.proxy_mode.set(label)
                break
        self.proxy_var.set(self.cfg.get("proxy_url", ""))
        self._sync_proxy_state()
        for path in self.cfg.get("files", []) or []:
            if os.path.isfile(path):
                self._files.append(path)
        self._refresh_file_list()

    # ---- helpers ----
    def _report_callback_exception(self, exc, val, tb):
        import traceback
        detail = "".join(traceback.format_exception(exc, val, tb))
        self._log(f"内部错误：{val}")
        try:
            messagebox.showerror(
                "发生内部错误",
                "操作没有完成，请把下面的信息发给开发者：\n\n" + detail[-1200:])
        except Exception:  # noqa: BLE001
            pass

    def _remember_token_changed(self):
        if self.remember_token.get():
            token = self.token_var.get().strip()
            if not token:
                self.remember_token.set(False)
                messagebox.showwarning("请先填写 Token", "输入 Token 后再勾选“记住 Token”。")
                return
            try:
                token_store.store_token(config_file(), token)
            except (token_store.TokenStoreError, OSError) as e:
                self.remember_token.set(False)
                messagebox.showerror("保存 Token 失败", str(e))
                return
            self.auth_hint.set("Token 已由 Windows 用户级加密保存；取消勾选即可删除。")
        else:
            try:
                token_store.forget_token(config_file())
            except OSError as e:
                self.remember_token.set(True)
                messagebox.showerror("删除 Token 失败", str(e))
                return
            self.auth_hint.set("不会保存 Token；关闭软件后需重新输入。")

    def _persist_token_if_selected(self):
        if self.remember_token.get():
            token = self.token_var.get().strip()
            if token:
                token_store.store_token(config_file(), token)

    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token.get() else "●")

    def _sync_proxy_state(self):
        custom = PROXY_LABELS.get(self.proxy_mode.get()) == "custom"
        self.proxy_entry.config(state="normal" if custom else "disabled")

    def _current_proxy(self) -> tuple[str, str]:
        return PROXY_LABELS.get(self.proxy_mode.get(), "system"), self.proxy_var.get().strip()

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.publish_btn.config(state=state)
        self.push_btn.config(state=state)
        self.verify_btn.config(state=state)
        self.create_repo_btn.config(state=state)
        self.cancel_btn.config(state="normal" if busy else "disabled")

    def _log(self, msg: str):
        self._events.put(("log", msg))

    def _reset_progress(self):
        self._events.put(("progress", 0))
        self._events.put(("hint", ""))

    def _set_progress(self, percent: float, hint: str):
        self._events.put(("progress", min(max(int(percent), 0), 100)))
        if hint:
            self._events.put(("hint", hint))

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
            elif kind == "hint":
                self.progress_hint.set(value)
            elif kind == "finish":
                ok, result, cb = value
                self._finish_async(ok, result, cb)
        self.root.after(100, self._drain_events)

    # ---- files ----
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择要上传的文件（可多选）",
            filetypes=[("压缩包", "*.zip"), ("可执行文件", "*.exe"), ("所有文件", "*.*")])
        for path in paths:
            if path not in self._files:
                self._files.append(path)
        self._refresh_file_list()

    def _remove_selected(self):
        for index in sorted(self.file_list.curselection(), reverse=True):
            del self._files[index]
        self._refresh_file_list()

    def _clear_files(self):
        self._files.clear()
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.file_list.delete(0, "end")
        total = 0
        for path in self._files:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            total += size
            self.file_list.insert("end", f"{os.path.basename(path)}  ({human_size(size)})")
        self.file_hint.set(
            f"{len(self._files)} 个文件，共 {human_size(total)}" if self._files else "尚未选择文件")

    # ---- repository file upload (no Release) ----
    def _add_repo_files(self):
        paths = filedialog.askopenfilenames(
            title="选择要提交到仓库的文件（可多选）",
            filetypes=[("所有文件", "*.*")])
        for path in paths:
            self._append_repo_file(path, os.path.basename(path))
        self._refresh_repo_file_list()

    def _add_repo_folder(self):
        folder = filedialog.askdirectory(title="选择要整体上传到仓库的文件夹")
        if not folder:
            return
        root_dir = os.path.normpath(folder)
        prefix_name = os.path.basename(root_dir) or "root"
        added = 0
        for base, _dirs, names in os.walk(root_dir):
            for name in sorted(names):
                local = os.path.join(base, name)
                rel = os.path.relpath(local, root_dir)
                self._append_repo_file(local, f"{prefix_name}/{rel}".replace("\\", "/"))
                added += 1
        if added == 0:
            messagebox.showinfo("空文件夹", f"“{prefix_name}” 里没有可上传的文件。")
            return
        self._refresh_repo_file_list()

    def _append_repo_file(self, local_path: str, rel: str):
        entry = (local_path, rel)
        if entry not in self._repo_files:
            self._repo_files.append(entry)

    def _remove_repo_selected(self):
        for index in sorted(self.repo_file_list.curselection(), reverse=True):
            del self._repo_files[index]
        self._refresh_repo_file_list()

    def _clear_repo_files(self):
        self._repo_files.clear()
        self._refresh_repo_file_list()

    def _refresh_repo_file_list(self):
        self.repo_file_list.delete(0, "end")
        total = 0
        for local_path, rel in self._repo_files:
            try:
                size = os.path.getsize(local_path)
            except OSError:
                size = 0
            total += size
            self.repo_file_list.insert("end", f"{rel}  ({human_size(size)})")
        self.repo_file_hint.set(
            f"{len(self._repo_files)} 个文件，共 {human_size(total)}" if self._repo_files
            else "尚未选择文件")

    def _push_files_to_repo(self):
        try:
            token, repo, proxy_mode, proxy_url = self._validate_common()
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return
        if not self._repo_files:
            messagebox.showwarning("没有文件", "请先在“④ 上传文件/文件夹”里添加文件或文件夹。")
            return
        if self._busy:
            return
        branch = self.branch_var.get().strip()
        if not branch or not re.fullmatch(r"[A-Za-z0-9._/-]{1,250}", branch):
            messagebox.showwarning("分支无效", "分支名不能为空，且只能包含字母、数字、点、下划线、斜杠。")
            return
        owner, name = repo.split("/", 1)
        target_dir = self.repo_dir_var.get().strip()
        message = self.commit_msg_var.get().strip() or None
        opener = build_opener(proxy_mode, proxy_url)

        files = list(self._repo_files)
        self._set_busy(True)
        self._cancel_evt.clear()
        self._reset_progress()
        self._set_progress(0, "正在上传文件…")
        self._persist_token_if_selected()

        def worker():
            try:
                result = upload_files_to_repo(
                    owner, name, token, opener, files,
                    branch=branch, target_dir=target_dir, message=message,
                    progress_cb=lambda frac, label: self._set_progress(frac * 90, label),
                    cancel=self._cancel_evt, log=self._log)
                return result
            except Exception as e:  # noqa: BLE001
                return e

        def done(result):
            self._set_busy(False)
            if isinstance(result, dict):
                self._set_progress(100, "完成")
                self._log(f"提交成功：{result['count']} 个文件 → 分支 {result['branch']}")
                self._log(f"提交 {result['url']}")
                messagebox.showinfo("提交成功",
                                    f"已将 {result['count']} 个文件提交到 {branch} 分支。\n\n"
                                    f"{result['url']}")
            elif isinstance(result, Exception):
                self._set_progress(0, "提交失败")
                messagebox.showerror("提交失败", str(result))
            else:
                self._set_progress(0, "已取消")
                self._log("已取消上传。")

        self._run_async(worker, done, done, status="正在上传文件到仓库…")


    # ---- validation ----
    def _validate_common(self):
        token = self.token_var.get().strip()
        if not token:
            raise ValueError("请先填写 GitHub Token（可在 github.com/settings/tokens 创建）")
        repo = self.repo_var.get().strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("仓库格式应为 owner/name，例如 puppy-bai/MediaTranscriberSuite")
        proxy_mode, proxy_url = self._current_proxy()
        if proxy_mode == "custom" and not proxy_url:
            raise ValueError("选择了自定义代理，但代理地址为空（例如 http://127.0.0.1:7890）")
        return token, repo, proxy_mode, proxy_url

    def _validate_publish(self):
        token, repo, proxy_mode, proxy_url = self._validate_common()
        tag = self.tag_var.get().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
            raise ValueError("Tag 仅可包含英文字母、数字、点、横线和下划线，如 v1.2.0")
        if not self._files:
            raise ValueError("请至少添加一个要上传的文件")
        missing = [p for p in self._files if not os.path.isfile(p)]
        if missing:
            raise ValueError("以下文件不存在：" + "、".join(missing))
        body = self.body_text.get("1.0", "end").strip()
        return (token, repo, tag, self.title_var.get().strip(), body,
                list(self._files), proxy_mode, proxy_url)

    # ---- actions ----
    def _check_permissions(self):
        if self._busy:
            return
        try:
            token, repo, proxy_mode, proxy_url = self._validate_common()
            self._persist_token_if_selected()
        except (ValueError, token_store.TokenStoreError, OSError) as e:
            messagebox.showwarning("输入有误", str(e))
            return
        owner, name = repo.split("/", 1)
        opener = build_opener(proxy_mode, proxy_url)

        def job():
            info = check_permissions(owner, name, token, opener)
            self._log(f"登录账号：{info['login']}")
            if info["repo_error"]:
                self._log(f"仓库访问失败：{info['repo_error']}")
                raise RuntimeError("无法访问该仓库，请检查仓库名和 Token 的仓库范围")
            self._log(f"仓库可访问，默认分支 {info['default_branch']}")
            if info["scopes"] is not None:
                self._log(f"Token 类型：classic，scopes={info['scopes'] or '（空）'}")
            else:
                self._log("Token 类型：fine-grained（细粒度）")
            if info["can_write"]:
                self._log("写入检查：通过，可以创建 Release 并上传附件")
            else:
                self._log(f"写入检查：失败 — {info['write_error']}")
            for note in info["notes"]:
                self._log(f"提示：{note}")
            return info

        self._run_async(job, on_success=self._permissions_done,
                        on_error=lambda e: self._log(f"自检失败：{e}"),
                        status="正在自检…")

    def _permissions_done(self, info):
        if not info["can_write"]:
            hint = ""
            if info["scopes"] is None:
                hint = ("\n\n这是细粒度 Token：请到 Token 设置里把 Repository access 选为\n"
                        "Only select repositories（勾选本仓库），并把\n"
                        "Permissions → Repository permissions → Contents 设为 Read and write。")
            else:
                hint = "\n\n这是 classic Token：请勾选 repo scope 后重新生成。"
            messagebox.showerror("没有写权限",
                                 f"该 Token 无法在此仓库创建 Release。{(info['write_error'] or '')[:300]}{hint}")
        else:
            extra = ""
            if info["notes"]:
                extra = "\n\n" + "\n".join(info["notes"])
            messagebox.showinfo("自检通过", f"账号 {info['login']} 可以发布到此仓库。{extra}")

    # ---- create repository ----
    def _open_create_repo(self):
        if self._busy:
            return
        if not self.token_var.get().strip():
            messagebox.showwarning("请先填写 Token", "创建仓库需要先填写 GitHub Token。")
            return
        if self._create_win is not None and self._create_win.winfo_exists():
            self._create_win.deiconify()
            self._create_win.lift()
            return

        win = Toplevel(self.root)
        self._create_win = win
        win.title("创建 GitHub 仓库")
        win.transient(self.root)
        win.resizable(False, False)
        win.columnconfigure(1, weight=1)

        name_var = StringVar()
        desc_var = StringVar()
        private_var = BooleanVar(value=False)
        init_var = BooleanVar(value=True)

        ttk.Label(win, text="仓库名:").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        name_entry = ttk.Entry(win, textvariable=name_var, width=40)
        name_entry.grid(row=0, column=1, sticky="we", padx=12, pady=(12, 4))

        ttk.Label(win, text="描述（可选）:").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(win, textvariable=desc_var, width=40).grid(
            row=1, column=1, sticky="we", padx=12, pady=4)

        ttk.Checkbutton(win, text="私有仓库", variable=private_var).grid(
            row=2, column=1, sticky="w", padx=12)
        ttk.Checkbutton(win, text="自动创建初始提交（README，生成默认分支）",
                        variable=init_var).grid(row=3, column=1, sticky="w", padx=12, pady=(0, 4))

        ttk.Label(
            win, justify="left", foreground="#666",
            text=("会以当前 Token 的账号身份创建一个同名新仓库。\n"
                  "Classic Token 需要 repo scope；细粒度 Token 需要\n"
                  "Administration: Read and write（且覆盖 All repositories）。"),
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        btns = ttk.Frame(win)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", padx=12, pady=(0, 12))

        def submit():
            name = name_var.get().strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name) or name in (".", ".."):
                messagebox.showwarning("仓库名无效",
                                       "仓库名只能包含字母、数字、点、横线和下划线，长度 1-100。",
                                       parent=win)
                return
            existing = self.repo_var.get().strip().strip("/")
            if existing and existing.rsplit("/", 1)[-1].lower() == name.lower():
                if not messagebox.askyesno(
                        "确认创建",
                        f"Repository access 里的仓库名已经是「{name}」。\n"
                        "如果它已存在，GitHub 会返回 422 错误。仍要继续吗？",
                        parent=win):
                    return
            token = self.token_var.get().strip()
            proxy_mode, proxy_url = self._current_proxy()
            opener = build_opener(proxy_mode, proxy_url)
            description = desc_var.get().strip()
            private = private_var.get()
            auto_init = init_var.get()
            win.destroy()
            self._run_async(
                lambda: create_repository(token, opener, name=name, description=description,
                                          private=private, auto_init=auto_init),
                on_success=self._create_repo_done,
                on_error=self._create_repo_failed,
                status="正在创建仓库…")

        ttk.Button(btns, text="创建", command=submit).pack(side="left")
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=8)
        name_entry.focus_set()
        win.bind("<Return>", lambda e: submit())
        win.bind("<Escape>", lambda e: win.destroy())
        win.grab_set()

    def _create_repo_done(self, repo_obj):
        full_name = repo_obj.get("full_name") or ""
        url = repo_obj.get("html_url") or f"https://github.com/{full_name}"
        if full_name:
            self.repo_var.set(full_name)
        self.status_label.config(text="仓库已创建")
        self._log(f"仓库已创建：{full_name}（{url}）")
        messagebox.showinfo("创建成功",
                            f"仓库 {full_name} 已创建。\n{url}\n\n仓库名已填入上方，"
                            "接下来可以发布 Release。")

    def _create_repo_failed(self, err):
        msg = str(err)
        self._log(f"创建仓库失败：{msg}")
        hint = ""
        if "HTTP 401" in msg:
            hint = "\n\nToken 无效或已过期，请重新生成。"
        elif "HTTP 403" in msg:
            hint = ("\n\nToken 没有创建仓库的权限：Classic Token 需勾选 repo；"
                    "细粒度 Token 需 Administration: Read and write。")
        elif "HTTP 422" in msg:
            hint = "\n\n仓库名可能已被占用，或名称不符合 GitHub 规则。"
        messagebox.showerror("创建仓库失败", msg[:800] + hint)

    def _publish(self):
        if self._busy:
            return
        try:
            (token, repo, tag, title, body, files,
             proxy_mode, proxy_url) = self._validate_publish()
            self._persist_token_if_selected()
        except (ValueError, token_store.TokenStoreError, OSError) as e:
            messagebox.showwarning("输入有误", str(e))
            return

        owner, name = repo.split("/", 1)
        draft = self.draft_var.get()
        update_body = self.update_body_var.get()
        opener = build_opener(proxy_mode, proxy_url)
        self._cancel.clear()
        self._release_url = ""

        save_config({
            "repo": repo, "tag": tag, "title": title, "body": body,
            "files": files, "proxy_mode": proxy_mode, "proxy_url": proxy_url,
        })

        def job():
            existing = find_release_by_tag(owner, name, tag, token, opener)
            if existing:
                release_id = existing["id"]
                self._log(f"已存在 Release（id={release_id}），直接上传附件。")
                if update_body:
                    update_release(owner, name, token, opener, release_id,
                                   name=title or tag, body=body, draft=draft)
                    self._log("已更新 Release 标题/说明。")
            else:
                self._log(f"创建 Release：{tag} @ {repo}")
                rel = create_release(owner, name, token, opener, tag=tag,
                                     name=title or tag, body=body, draft=draft)
                release_id = rel["id"]
                self._log(f"已创建 Release id={release_id}（draft={draft}）")

            total = len(files)
            for index, path in enumerate(files, start=1):
                if self._cancel.is_set():
                    raise UploadCancelled()
                size = os.path.getsize(path)
                self._log(f"[{index}/{total}] 上传 {os.path.basename(path)}（{human_size(size)}）…")
                asset = upload_asset(owner, name, release_id, token, path, opener,
                                     progress_cb=self._make_progress(index, total, size),
                                     cancel=self._cancel)
                self._log(f"[{index}/{total}] 完成：{asset.get('name')}（{asset.get('size')} bytes）")

            url = f"https://github.com/{repo}/releases/tag/{tag}"
            self._log(f"Release 地址：{url}")
            return url

        self.progress["value"] = 0
        self.progress_hint.set("")
        self._run_async(job, on_success=self._publish_done,
                        on_error=self._publish_failed, status="正在发布…")

    def _make_progress(self, index: int, total: int, size: int):
        start = time.time()

        def cb(frac: float, sent: int):
            overall = ((index - 1) + frac) / max(total, 1) * 100
            elapsed = max(time.time() - start, 1e-6)
            speed = sent / elapsed
            remaining = (size - sent) / speed if speed > 0 else 0
            self._events.put(("progress", int(overall)))
            self._events.put(("hint",
                              f"[{index}/{total}] {frac*100:5.1f}%  "
                              f"{human_size(sent)} / {human_size(size)}  "
                              f"{human_size(speed)}/s  剩余 {int(remaining)}s"))

        return cb

    def _publish_done(self, url):
        self._release_url = url
        self.progress["value"] = 100
        self.open_btn.config(state="normal")
        messagebox.showinfo("完成", f"已发布：\n{url}")

    def _publish_failed(self, err):
        if isinstance(err, UploadCancelled):
            self._log("已取消上传。已上传的部分附件会留在 Release 上，可稍后重试或手动删除。")
            self.status_label.config(text="已取消")
            return
        self._log(f"发布失败：{err}")
        messagebox.showerror("发布失败", str(err)[:800])

    def _cancel_upload(self):
        self._cancel.set()
        self._cancel_evt.set()
        self._log("正在取消…（当前分块传完后停止）")

    def _open_release(self):
        if self._release_url:
            webbrowser.open(self._release_url)

    # ---- threading ----
    def _run_async(self, fn, on_success, on_error, status=""):
        self._set_busy(True)
        self.status_label.config(text=status or "工作中…")
        self._log(f">> {status or '执行中'}")

        def target():
            try:
                result = fn()
                self._events.put(("finish", (True, result, on_success)))
            except Exception as e:  # noqa: BLE001
                self._events.put(("finish", (False, e, on_error)))

        threading.Thread(target=target, daemon=True).start()

    def _finish_async(self, ok, result, cb):
        self._set_busy(False)
        self.status_label.config(text="完成" if ok else "出错")
        cb(result)

    def _on_close(self):
        try:
            self._persist_token_if_selected()
        except (token_store.TokenStoreError, OSError) as e:
            messagebox.showerror("保存 Token 失败", str(e))
            return
        try:
            save_config({
                "repo": self.repo_var.get().strip(),
                "tag": self.tag_var.get().strip(),
                "title": self.title_var.get(),
                "body": self.body_text.get("1.0", "end").strip(),
                "files": list(self._files),
                "proxy_mode": self._current_proxy()[0],
                "proxy_url": self._current_proxy()[1],
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
