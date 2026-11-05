import json
import os
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.capture_store import add_capture, list_captures, update_capture
from core.media_pipeline import download_media, summarize_text, transcribe_file
from core.paths import APP_ROOT, DATA_DIR, configure_cuda_dll_paths, ensure_data_dirs


configure_cuda_dll_paths()
SETTINGS_PATH = DATA_DIR / "settings.json"
BG = "#f3f5f8"
PANEL = "#ffffff"
INK = "#182230"
MUTED = "#657286"
ACCENT = "#1769e0"


def extract_urls(text):
    return list(dict.fromkeys(match.rstrip("，。！？；;,.、)") for match in re.findall(r"https?://[^\s<>\"']+", text or "")))


class MediaTextApp(tk.Tk):
    def __init__(self):
        super().__init__()
        ensure_data_dirs()
        self.title("Media Text Suite")
        self.geometry("1080x760")
        self.minsize(920, 640)
        self.configure(bg=BG)
        self.settings = self.load_settings()
        self.items = []
        self.busy = False
        self.cancel_event = threading.Event()
        self.model = tk.StringVar(value=self.settings.get("model_size", "base"))
        self.device = tk.StringVar(value=self.settings.get("device", "cuda"))
        self.compute = tk.StringVar(value=self.settings.get("compute_type", "float16"))
        self.beam = tk.StringVar(value=str(self.settings.get("beam_size", 1)))
        self.summary_mode = tk.StringVar(value=self.settings.get("summary_mode", "通用内容总结"))
        self.api_key = tk.StringVar(value=self.settings.get("api_key", ""))
        self.settings_window = None
        self.build_ui()
        self.refresh_captures()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_settings(self):
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"model_size": "base", "device": "cuda", "compute_type": "float16", "beam_size": 1, "summary_mode": "通用内容总结", "api_key": ""}

    def save_settings(self):
        self.settings.update({"api_key": self.api_key.get().strip(), "model_size": self.model.get(), "device": self.device.get(), "compute_type": self.compute.get(), "beam_size": int(self.beam.get()), "summary_mode": self.summary_mode.get()})
        SETTINGS_PATH.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=34, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#edf1f6", foreground=MUTED, relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#dceaff")], foreground=[("selected", INK)])
        style.configure("TCombobox", padding=6)

        header = tk.Frame(self, bg=BG, padx=28, pady=20)
        header.pack(fill="x")
        tk.Label(header, text="MEDIA TEXT SUITE", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(header, text="媒体工作台", bg=BG, fg=INK, font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(header, text="下载 · 转写 · 总结", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 0))
        self.button(header, "设置", self.open_settings, secondary=True).pack(side="right", anchor="ne", pady=(10, 0))

        body = tk.Frame(self, bg=BG, padx=22, pady=2)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=PANEL, padx=16, pady=16, highlightthickness=1, highlightbackground="#e5eaf0")
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=PANEL, padx=16, pady=16, width=410, highlightthickness=1, highlightbackground="#e5eaf0")
        right.pack(side="right", fill="both", padx=(14, 0))
        right.pack_propagate(False)

        tk.Label(left, text="任务列表", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        self.url_box = tk.Text(left, height=4, wrap="word", font=("Segoe UI", 10), bg="#f7f9fc", fg=INK, relief="flat", padx=11, pady=10, insertbackground=INK)
        self.url_box.pack(fill="x", pady=(12, 8))
        self.url_box.insert("1.0", "粘贴一个或多个视频链接，每行一个")
        row = tk.Frame(left, bg=PANEL)
        row.pack(fill="x", pady=(0, 12))
        self.button(row, "添加链接", self.add_links).pack(side="left")
        self.button(row, "导入文本", self.import_text, secondary=True).pack(side="left", padx=8)
        self.button(row, "删除所选", self.delete_selected, secondary=True).pack(side="left")
        self.button(row, "打开保存位置", self.open_data_dir, secondary=True).pack(side="right")

        columns = ("title", "status", "duration")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("title", text="链接 / 标题")
        self.tree.heading("status", text="状态")
        self.tree.heading("duration", text="耗时")
        self.tree.column("title", width=440, anchor="w")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("duration", width=85, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)

        tk.Label(right, text="处理方式", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        options = tk.Frame(right, bg="#f7f9fc", padx=12, pady=9)
        options.pack(fill="x", pady=(11, 13))
        self.do_download = tk.BooleanVar(value=True)
        self.do_transcribe = tk.BooleanVar(value=True)
        self.do_summary = tk.BooleanVar(value=False)
        for label, var in [("下载视频", self.do_download), ("转写文字", self.do_transcribe), ("MiniMax 总结", self.do_summary)]:
            tk.Checkbutton(options, text=label, variable=var, bg="#f7f9fc", fg=INK, activebackground="#f7f9fc", selectcolor=PANEL, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=2)

        actions = tk.Frame(right, bg=PANEL)
        actions.pack(fill="x", pady=(7, 12))
        self.run_button = self.button(actions, "开始处理所选", self.process_selected)
        self.run_button.pack(side="left", fill="x", expand=True)
        self.cancel_button = self.button(actions, "中断", self.cancel_task, secondary=True)
        self.cancel_button.pack(side="left", padx=(8, 0))
        self.cancel_button.configure(state="disabled")
        self.status = tk.StringVar(value="就绪")
        tk.Label(right, textvariable=self.status, bg=PANEL, fg=ACCENT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 8))
        tk.Label(right, text="记录详情", bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.open_media_button = self.button(right, "打开所选媒体", self.open_selected_media, secondary=True)
        self.open_media_button.pack(anchor="e", pady=(0, 6))
        self.detail_tabs = ttk.Notebook(right)
        self.detail_tabs.pack(fill="both", expand=True)
        self.detail_views = {}
        for key, label in (("transcript", "转写"), ("summary", "总结"), ("log", "日志")):
            pane = tk.Frame(self.detail_tabs, bg="#f7f9fc")
            view = tk.Text(pane, wrap="word", bg="#f7f9fc", fg=INK, relief="flat", padx=11, pady=10, font=("Segoe UI", 9))
            view.pack(fill="both", expand=True)
            self.detail_tabs.add(pane, text=label)
            self.detail_views[key] = view

    @staticmethod
    def button(parent, text, command, secondary=False):
        return tk.Button(parent, text=text, command=command, bg="#edf2f8" if secondary else ACCENT, fg=INK if secondary else "white", activebackground="#dfe8f4" if secondary else "#0f56bd", activeforeground=INK if secondary else "white", relief="flat", bd=0, padx=13, pady=9, cursor="hand2", font=("Microsoft YaHei UI", 9, "bold"))

    @staticmethod
    def field(parent, label, variable, values):
        tk.Label(parent, text=label, bg="#f7f9fc", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 2))
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").pack(fill="x", pady=(0, 4))

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(self)
        window.title("设置")
        window.geometry("480x560")
        window.resizable(False, False)
        window.configure(bg=BG)
        window.transient(self)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", self.save_and_close_settings)
        tk.Label(window, text="偏好设置", bg=BG, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", padx=26, pady=(24, 2))
        tk.Label(window, text="转写模型与 MiniMax 接口", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=26, pady=(0, 18))
        panel = tk.Frame(window, bg=PANEL, padx=20, pady=16, highlightthickness=1, highlightbackground="#e5eaf0")
        panel.pack(fill="both", expand=True, padx=22, pady=(0, 16))
        self.field(panel, "转写模型", self.model, ("tiny", "base", "small", "medium", "large-v3"))
        self.field(panel, "运行设备", self.device, ("cuda", "cpu"))
        self.field(panel, "计算精度", self.compute, ("float16", "int8_float16", "int8"))
        self.field(panel, "总结方式", self.summary_mode, ("通用内容总结", "会议纪要", "课程笔记", "产品分析"))
        tk.Label(panel, text="MiniMax API Key", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 4))
        tk.Entry(panel, textvariable=self.api_key, show="•", relief="flat", bg="#f3f6fa", fg=INK, insertbackground=INK, font=("Segoe UI", 10)).pack(fill="x", ipady=9)
        tk.Label(panel, text="密钥保存在本机设置文件中。", bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
        footer = tk.Frame(window, bg=BG)
        footer.pack(fill="x", padx=22, pady=(0, 20))
        self.button(footer, "保存设置", self.save_and_close_settings).pack(side="right")
        self.button(footer, "取消", window.destroy, secondary=True).pack(side="right", padx=(0, 8))

    def save_and_close_settings(self):
        self.save_settings()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()

    def add_links(self):
        urls = extract_urls(self.url_box.get("1.0", "end"))
        if not urls:
            messagebox.showinfo("提示", "请粘贴有效的视频或音频链接")
            return
        for url in urls:
            add_capture({"url": url, "page_title": url, "source": "desktop"})
        self.url_box.delete("1.0", "end")
        self.refresh_captures()

    def import_text(self):
        path = filedialog.askopenfilename(filetypes=[("文本文件", "*.txt *.md"), ("所有文件", "*.*")])
        if path:
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", Path(path).read_text(encoding="utf-8", errors="replace"))

    def refresh_captures(self):
        self.items = list_captures()
        for row in self.tree.get_children():
            self.tree.delete(row)
        for item in self.items:
            result = item.get("result") or {}
            timings = item.get("timings") or result.get("timings") or {}
            elapsed = timings.get("total_seconds")
            title = item.get("page_title") or item.get("url", "")
            self.tree.insert("", "end", iid=item["id"], values=(title[:80], item.get("status", "captured"), f"{elapsed:.1f}s" if elapsed else "—"))

    def selected_items(self):
        selected = set(self.tree.selection())
        return [item for item in self.items if item.get("id") in selected]

    def delete_selected(self):
        chosen = set(self.tree.selection())
        if not chosen:
            return
        from core.capture_store import delete_captures
        delete_captures(chosen)
        self.refresh_captures()

    def show_selected(self, _event=None):
        chosen = self.selected_items()
        if not chosen:
            return
        item = chosen[0]
        result = item.get("result") or {}
        transcript = result.get("transcript") or {}
        summary = result.get("summary") or {}
        values = {
            "transcript": transcript.get("text") or "尚无转写内容",
            "summary": summary.get("summary") or "尚无总结内容",
            "log": json.dumps(result, ensure_ascii=False, indent=2) if result else item.get("url", ""),
        }
        for key, text in values.items():
            view = self.detail_views[key]
            view.delete("1.0", "end")
            view.insert("1.0", text)

    def open_selected_media(self):
        selected = self.selected_items()
        if not selected:
            return
        result = selected[0].get("result") or {}
        media_path = (result.get("download") or {}).get("media_path")
        if not media_path or not Path(media_path).is_file():
            messagebox.showinfo("提示", "所选记录还没有已下载的媒体文件")
            return
        os.startfile(media_path)

    def process_selected(self):
        chosen = self.selected_items()
        if not chosen:
            messagebox.showinfo("提示", "请先选择一条或多条记录")
            return
        if not (self.do_download.get() or self.do_transcribe.get() or self.do_summary.get()):
            messagebox.showinfo("提示", "至少勾选一项处理动作")
            return
        if self.do_summary.get() and not self.api_key.get().strip():
            messagebox.showinfo("提示", "请先打开右上角“设置”填写 MiniMax API Key")
            return
        self.save_settings()
        self.busy = True
        self.cancel_event.clear()
        self.task_options = {"download": self.do_download.get(), "transcribe": self.do_transcribe.get(), "summary": self.do_summary.get(), "model": self.model.get(), "device": self.device.get(), "compute": self.compute.get(), "beam": int(self.beam.get()), "summary_mode": self.summary_mode.get(), "api_key": self.api_key.get().strip()}
        self.cancel_button.configure(state="normal")
        self.run_button.configure(state="disabled")
        threading.Thread(target=self.worker, args=(chosen,), daemon=True).start()

    def worker(self, items):
        try:
            for index, item in enumerate(items, 1):
                if self.cancel_event.is_set():
                    break
                label = item.get("page_title") or item.get("url", "")[:45]
                self.after(0, lambda i=index, n=len(items), title=label: self.status.set(f"处理中 {i}/{n} · {title}"))
                self.process_one(item)
        finally:
            self.after(0, self.finish_worker)

    def process_one(self, item):
        import time
        started = time.perf_counter()
        capture_id = item["id"]
        try:
            result = dict(item.get("result") or {})
            media_path = (result.get("download") or {}).get("media_path")
            options = self.task_options
            if options["download"] or (options["transcribe"] and not media_path):
                update_capture(capture_id, status="downloading")
                downloaded = download_media(item["url"], title=item.get("page_title") or "media", referer=item.get("page_url", ""), headers=item.get("headers") or {}, audio_only=False, stop_event=self.cancel_event)
                result["download"] = downloaded
                media_path = downloaded["media_path"]
            if options["transcribe"]:
                update_capture(capture_id, status="transcribing")
                result["transcript"] = transcribe_file(media_path, model_size=options["model"], device=options["device"], compute_type=options["compute"], beam_size=options["beam"], stop_event=self.cancel_event)
            if options["summary"]:
                transcript = result.get("transcript") or {}
                text = transcript.get("text") or ""
                if not text and item.get("result", {}).get("transcript", {}).get("text"):
                    text = item["result"]["transcript"]["text"]
                if not text:
                    raise RuntimeError("没有可总结的转写文本，请先勾选转写或提供已有转写")
                result["summary"] = summarize_text(text, options["api_key"], mode=options["summary_mode"])
            result["timings"] = {"total_seconds": round(time.perf_counter() - started, 3)}
            update_capture(capture_id, status="processed", result=result, timings=result["timings"])
        except Exception as exc:
            status = "cancelled" if self.cancel_event.is_set() else "failed"
            update_capture(capture_id, status=status, result={"error": str(exc), "timings": {"total_seconds": round(time.perf_counter() - started, 3)}})

    def cancel_task(self):
        self.cancel_event.set()
        self.status.set("正在中断当前任务…")

    def finish_worker(self):
        self.busy = False
        self.cancel_button.configure(state="disabled")
        self.run_button.configure(state="normal")
        self.status.set("任务已中断" if self.cancel_event.is_set() else "处理完成")
        self.refresh_captures()

    def open_data_dir(self):
        ensure_data_dirs()
        os.startfile(str(DATA_DIR))

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno("退出", "任务仍在运行。中断并退出吗？"):
                return
            self.cancel_event.set()
        self.destroy()


if __name__ == "__main__":
    MediaTextApp().mainloop()
