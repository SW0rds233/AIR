#!/usr/bin/env python
"""AIR GUI — 基于 tkinter 的图形化操作界面（三大模块分步推进）

用法:
    python -m src.gui
    python src/gui.py
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


class RedirectText(io.StringIO):
    """将 stdout 重定向到 tkinter 文本框"""

    def __init__(self, widget: tk.Text, tag: str = "stdout"):
        super().__init__()
        self.widget = widget
        self.tag = tag
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._flush_scheduled = False

    def write(self, s: str):
        with self._lock:
            self._buffer.append(s)
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.widget.after(100, self._flush)

    def _flush(self):
        with self._lock:
            if not self._buffer:
                self._flush_scheduled = False
                return
            text = "".join(self._buffer)
            self._buffer.clear()
            self._flush_scheduled = False
        self.widget.insert(tk.END, text)
        self.widget.see(tk.END)
        self.widget.update_idletasks()

    def flush(self):
        pass


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _get_env_value(key: str, default: str = "") -> str:
    try:
        if _env_path().exists():
            for line in _env_path().read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


def _set_env_value(key: str, value: str):
    p = _env_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    found = False
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            result.append(f"{key}={value}")
            found = True
        else:
            result.append(line)
    if not found:
        result.append(f"{key}={value}")
    p.write_text("\n".join(result) + "\n", encoding="utf-8")


def clear_previous_data():
    """清除旧数据：向量库 + 输出 + 检查点 + 检索缓存"""
    removed = []
    chroma_dir = PROJECT_ROOT / "data" / "chroma"
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)
        removed.append("data/chroma")
    figures_dir = OUTPUTS_DIR / "figures"
    if figures_dir.exists():
        for f in figures_dir.iterdir():
            f.unlink()
        if len(list(figures_dir.iterdir())) == 0:
            removed.append("outputs/figures")
    if OUTPUTS_DIR.exists():
        for f in OUTPUTS_DIR.iterdir():
            if f.is_file():
                f.unlink()
                removed.append(f"outputs/{f.name}")
    ckpt = PROJECT_ROOT / "data" / "pipeline_checkpoints.sqlite"
    if ckpt.exists():
        ckpt.unlink()
        removed.append("data/pipeline_checkpoints.sqlite")
    cache_dir = PROJECT_ROOT / "data" / "pipeline_cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        removed.append("data/pipeline_cache")
    return removed


# 主题配色 (现代扁平风格)
COLORS = {
    "bg": "#f4f6f9",
    "card": "#ffffff",
    "primary": "#2f6fed",
    "primary_dark": "#2457c4",
    "success": "#2e9e5b",
    "danger": "#d64545",
    "warn": "#e0a100",
    "text": "#1f2937",
    "muted": "#6b7280",
    "border": "#e5e7eb",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AIR — AIResearch 综述论文撰写系统")
        self.root.geometry("1180x780")
        self.root.minsize(960, 640)
        self.root.configure(bg=COLORS["bg"])
        self._running = False
        self._current_module = ""

        self._setup_style()
        self._build_ui()
        self._redirect_stdout()
        self.refresh_artifacts()

    # ---------------- 样式 ----------------
    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 9))
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("Card.TLabelframe", background=COLORS["card"])
        style.configure("Card.TLabelframe.Label", background=COLORS["card"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 8))
        style.configure("Primary.TButton", background=COLORS["primary"], foreground="white",
                        font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0, padding=(14, 6))
        style.map("Primary.TButton", background=[("active", COLORS["primary_dark"]), ("disabled", "#a9c0f0")])
        style.configure("Module.TButton", background=COLORS["card"], foreground=COLORS["text"],
                        font=("Microsoft YaHei UI", 9, "bold"), borderwidth=1, padding=(10, 6))
        style.map("Module.TButton", background=[("active", "#e8eefc")])
        style.configure("Danger.TButton", background=COLORS["danger"], foreground="white",
                        borderwidth=0, padding=(10, 6))
        style.map("Danger.TButton", background=[("active", "#b93434"), ("disabled", "#e5a8a8")])
        style.configure("Status.TLabel", background=COLORS["card"], font=("Microsoft YaHei UI", 8))

    # ---------------- UI ----------------
    def _build_ui(self):
        # 顶部标题
        header = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text="AIR — AIResearch 综述论文撰写系统", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="三大模块分步推进：文献查找/入库 → 文件分析 → 撰写",
                  style="Muted.TLabel", background=COLORS["bg"]).pack(side=tk.RIGHT)

        # 参数卡片
        params = ttk.LabelFrame(self.root, text=" 运行参数 ", style="Card.TLabelframe", padding=12)
        params.pack(fill=tk.X, padx=16, pady=(4, 8))

        row1 = ttk.Frame(params, style="Card.TFrame")
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="研究主题 *", width=14, background=COLORS["card"]).pack(side=tk.LEFT)
        self.topic_var = tk.StringVar(value="")
        ttk.Entry(row1, textvariable=self.topic_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row2 = ttk.Frame(params, style="Card.TFrame")
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="关键词", width=14, background=COLORS["card"]).pack(side=tk.LEFT)
        self.kw_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.kw_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        ttk.Label(row2, text="子主题", width=7, background=COLORS["card"]).pack(side=tk.LEFT)
        self.sub_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.sub_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row3 = ttk.Frame(params, style="Card.TFrame")
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="时间范围", width=14, background=COLORS["card"]).pack(side=tk.LEFT)
        self.time_var = tk.StringVar(value="2019-2026")
        ttk.Entry(row3, textvariable=self.time_var, width=12).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row3, text="修改轮次", background=COLORS["card"]).pack(side=tk.LEFT)
        self.rev_var = tk.StringVar(value="3")
        ttk.Spinbox(row3, textvariable=self.rev_var, from_=1, to=10, width=4).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(row3, text="评分阈值", background=COLORS["card"]).pack(side=tk.LEFT)
        self.thresh_var = tk.StringVar(value=_get_env_value("REVIEW_ACCEPT_THRESHOLD", "80"))
        ttk.Spinbox(row3, textvariable=self.thresh_var, from_=50, to=100, width=4).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Button(row3, text="保存设置", command=self.save_settings).pack(side=tk.RIGHT)

        # 模块按钮卡片
        modules = ttk.LabelFrame(self.root, text=" 执行模块 ", style="Card.TLabelframe", padding=12)
        modules.pack(fill=tk.X, padx=16, pady=(0, 8))

        btns = ttk.Frame(modules, style="Card.TFrame")
        btns.pack(fill=tk.X)
        self.m1_btn = ttk.Button(btns, text="① 文献查找/入库", style="Module.TButton", command=lambda: self.start_module("retrieval"))
        self.m1_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.m2_btn = ttk.Button(btns, text="② 文件分析", style="Module.TButton", command=lambda: self.start_module("analysis"))
        self.m2_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.m3_btn = ttk.Button(btns, text="③ 撰写", style="Module.TButton", command=lambda: self.start_module("writing"))
        self.m3_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.full_btn = ttk.Button(btns, text="▶ 一键全跑", style="Primary.TButton", command=lambda: self.start_module("full"))
        self.full_btn.pack(side=tk.LEFT, padx=(0, 6))

        # 模块状态行
        status_row = ttk.Frame(modules, style="Card.TFrame")
        status_row.pack(fill=tk.X, pady=(8, 0))
        self.status_labels = {}
        for key, text in (("retrieval", "① 文献查找/入库"), ("analysis", "② 文件分析"), ("writing", "③ 撰写")):
            lbl = tk.Label(status_row, text=f"○ {text}", bg=COLORS["card"], fg=COLORS["muted"],
                           font=("Microsoft YaHei UI", 8), anchor=tk.W)
            lbl.pack(side=tk.LEFT, padx=(0, 24))
            self.status_labels[key] = lbl

        self.stop_btn = ttk.Button(status_row, text="■ 停止", style="Danger.TButton", command=self.stop_run,
                                   state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Button(status_row, text="🗑 清除旧数据", command=self.clear_data).pack(side=tk.RIGHT, padx=(0, 8))

        # 主体：左右分栏 (日志 | 产物)
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        # 左：运行输出
        out_frame = ttk.LabelFrame(paned, text=" 运行输出 ", style="Card.TLabelframe", padding=4)
        self.output_text = scrolledtext.ScrolledText(out_frame, wrap=tk.WORD, font=("Consolas", 9),
                                                     bg="#1e222a", fg="#d4d7dd", insertbackground="white",
                                                     relief=tk.FLAT, padx=8, pady=8)
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # 右：中间产物
        art_frame = ttk.LabelFrame(paned, text=" 中间产物 (outputs/) ", style="Card.TLabelframe", padding=4)
        topbar = ttk.Frame(art_frame, style="Card.TFrame")
        topbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(topbar, text="🔄 刷新", command=self.refresh_artifacts).pack(side=tk.LEFT)
        ttk.Button(topbar, text="📂 打开文件夹", command=self.open_outputs_dir).pack(side=tk.LEFT, padx=(4, 0))
        self.artifact_tree = ttk.Treeview(art_frame, columns=("time",), show="tree headings", height=12)
        self.artifact_tree.heading("#0", text="文件")
        self.artifact_tree.heading("time", text="修改时间")
        self.artifact_tree.column("#0", width=260)
        self.artifact_tree.column("time", width=90, anchor=tk.W)
        self.artifact_tree.pack(fill=tk.BOTH, expand=True)
        self.artifact_tree.bind("<<TreeviewSelect>>", self.on_artifact_select)
        self.artifact_tree.bind("<Double-1>", self.on_artifact_double_click)

        self.preview = scrolledtext.ScrolledText(art_frame, wrap=tk.WORD, font=("Microsoft YaHei UI", 9),
                                                 bg="#fafafa", fg=COLORS["text"], relief=tk.FLAT, height=8,
                                                 padx=8, pady=8)
        self.preview.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        paned.add(out_frame, weight=3)
        paned.add(art_frame, weight=2)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = tk.Label(self.root, textvariable=self.status_var, anchor=tk.W, bg=COLORS["card"],
                              fg=COLORS["muted"], font=("Microsoft YaHei UI", 8), padx=12, pady=3)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _redirect_stdout(self):
        sys.stdout = RedirectText(self.output_text)

    # ---------------- 中间产物 ----------------
    def _artifact_files(self) -> list[Path]:
        if not OUTPUTS_DIR.exists():
            return []
        files = [f for f in OUTPUTS_DIR.iterdir() if f.is_file()]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return files

    def refresh_artifacts(self):
        for item in self.artifact_tree.get_children():
            self.artifact_tree.delete(item)
        for f in self._artifact_files():
            mt = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
            self.artifact_tree.insert("", tk.END, text=f.name, values=(mt,), iid=f.name)
        n = len(self.artifact_tree.get_children())
        self.status_var.set(f"中间产物: {n} 个文件")

    def on_artifact_select(self, _event):
        sel = self.artifact_tree.selection()
        if not sel:
            return
        name = sel[0]
        path = OUTPUTS_DIR / name
        self.preview.delete("1.0", tk.END)
        if path.suffix.lower() in (".md", ".tex", ".txt", ".json", ".log"):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                content = f"[读取失败] {e}"
            self.preview.insert("1.0", content)
        else:
            self.preview.insert("1.0", f"[二进制文件] {name}\n\n双击可尝试用系统默认程序打开。")
        self.preview.see("1.0")

    def open_outputs_dir(self):
        if sys.platform == "win32":
            os.startfile(str(OUTPUTS_DIR))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(OUTPUTS_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(OUTPUTS_DIR)])

    def on_artifact_double_click(self, _event):
        sel = self.artifact_tree.selection()
        if not sel:
            return
        path = OUTPUTS_DIR / sel[0]
        if path.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"):
            try:
                if sys.platform == "win32":
                    os.startfile(str(path))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            except Exception as e:
                messagebox.showerror("打开失败", str(e))

    # ---------------- 参数 ----------------
    def _params(self):
        topic = self.topic_var.get().strip()
        if not topic:
            messagebox.showwarning("缺少参数", "请输入研究主题。")
            return None
        return {
            "topic": topic,
            "keywords": [k.strip() for k in self.kw_var.get().split(",") if k.strip()],
            "sub_topics": [s.strip() for s in self.sub_var.get().split(",") if s.strip()],
            "time_range": self.time_var.get().strip() or "2019-2026",
        }

    def save_settings(self):
        _set_env_value("REVIEW_ACCEPT_THRESHOLD", self.thresh_var.get())
        messagebox.showinfo("设置", "已保存 REVIEW_ACCEPT_THRESHOLD 到 .env")

    def clear_data(self):
        if self._running:
            messagebox.showwarning("正在运行", "请先停止当前模块。")
            return
        if not messagebox.askyesno("确认清除", "将删除 data/chroma、outputs/ 中的所有文件和检查点，确定？"):
            return
        try:
            removed = clear_previous_data()
            self.status_var.set(f"已清除 {len(removed)} 项旧数据")
            self.refresh_artifacts()
        except Exception as e:
            messagebox.showerror("清除失败", str(e))

    # ---------------- 模块执行 ----------------
    def start_module(self, module: str):
        if self._running:
            return
        params = self._params()
        if params is None:
            return
        os.environ["REVIEW_ACCEPT_THRESHOLD"] = self.thresh_var.get()
        try:
            max_rev = int(self.rev_var.get())
        except ValueError:
            max_rev = 3

        self._running = True
        self._current_module = module
        for btn in (self.m1_btn, self.m2_btn, self.m3_btn, self.full_btn):
            btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._set_module_running(module)

        self._pipeline_thread = threading.Thread(
            target=self._run_module, args=(module, params, max_rev), daemon=True
        )
        self._pipeline_thread.start()

    def stop_run(self):
        self._running = False
        self.status_var.set("已请求停止（等待当前节点完成）...")

    def _set_module_running(self, module: str):
        if module == "full":
            for m in ("retrieval", "analysis", "writing"):
                self._set_module_status(m, "running")
        else:
            self._set_module_status(module, "running")

    def _set_module_status(self, module: str, state: str):
        marks = {"pending": ("○", COLORS["muted"]), "running": ("●", COLORS["warn"]),
                 "done": ("✅", COLORS["success"]), "error": ("❌", COLORS["danger"])}
        mark, color = marks[state]
        text = {"retrieval": "① 文献查找/入库", "analysis": "② 文件分析", "writing": "③ 撰写"}[module]
        label = self.status_labels[module]
        label.config(text=f"{mark} {text}", fg=color)

    def _run_module(self, module: str, params: dict, max_rev: int):
        try:
            from src.utils.console import ensure_utf8_console
            ensure_utf8_console()

            if module == "retrieval":
                from src.graph.pipeline import run_retrieval_module
                result = run_retrieval_module(params["topic"], params["keywords"], params["sub_topics"], params["time_range"])
            elif module == "analysis":
                from src.graph.pipeline import run_analysis_module
                result = run_analysis_module(params["topic"], params["keywords"], params["sub_topics"], params["time_range"])
            elif module == "writing":
                from src.graph.pipeline import run_writing_module
                result = run_writing_module(params["topic"], params["keywords"], params["sub_topics"], params["time_range"], max_rev)
            else:  # full
                from src.graph.pipeline import run_pipeline
                result = run_pipeline(params["topic"], params["keywords"], params["sub_topics"],
                                      params["time_range"], max_rev, skip_retrieval=False)
            self.root.after(0, self._on_done, module, result)
        except Exception as e:
            self.root.after(0, self._on_error, module, str(e))

    def _on_done(self, module: str, result: dict | None):
        self._running = False
        self._reset_buttons()
        if result and result.get("error"):
            self._set_module_status(module, "error")
            self.status_var.set(f"{module} 错误: {result['error']}")
            messagebox.showerror("运行出错", result["error"])
            return
        if module == "full":
            self._set_module_status("retrieval", "done")
            self._set_module_status("analysis", "done")
            self._set_module_status("writing", "done")
        else:
            self._set_module_status(module, "done")
        self.status_var.set(f"{module} 完成")
        self.refresh_artifacts()
        if module == "writing" and result:
            score = result.get("review_score", "N/A")
            messagebox.showinfo("撰写完成", f"审稿评分: {score}/50\n修改轮次: {result.get('revision_count', 0)}")
        elif module == "full" and result:
            messagebox.showinfo("运行完成", f"审稿评分: {result.get('review_score', 'N/A')}/50")

    def _on_error(self, module: str, msg: str):
        self._running = False
        self._reset_buttons()
        if module == "full":
            for m in ("retrieval", "analysis", "writing"):
                self._set_module_status(m, "error")
        else:
            self._set_module_status(module, "error")
        self.status_var.set(f"{module} 出错: {msg}")
        messagebox.showerror("运行错误", msg)

    def _reset_buttons(self):
        for btn in (self.m1_btn, self.m2_btn, self.m3_btn, self.full_btn):
            btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)


def main():
    root = tk.Tk()
    App(root)
    try:
        root.tk.call("tk", "scaling", 1.5)
    except Exception:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()
