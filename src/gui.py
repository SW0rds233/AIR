#!/usr/bin/env python
"""AIR GUI — 基于 tkinter 的图形化操作界面

用法:
    python -m src.gui
    python src/gui.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


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


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    figures_dir = PROJECT_ROOT / "outputs" / "figures"
    if figures_dir.exists():
        for f in figures_dir.iterdir():
            f.unlink()
        if len(list(figures_dir.iterdir())) == 0:
            removed.append("outputs/figures")
    outputs_root = PROJECT_ROOT / "outputs"
    if outputs_root.exists():
        for f in outputs_root.iterdir():
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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AIR — AIResearch 综述论文撰写系统")
        self.root.geometry("960x720")
        self.root.minsize(800, 600)
        self._running = False

        # 样式
        style = ttk.Style()
        style.theme_use("clam")

        # 顶层 frame
        main = ttk.Frame(root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # ---------- 上部：参数输入区域 ----------
        params_frame = ttk.LabelFrame(main, text="运行参数", padding=10)
        params_frame.pack(fill=tk.X, pady=(0, 8))

        # 第 1 行：主题
        row1 = ttk.Frame(params_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="研究主题 *", width=12).pack(side=tk.LEFT)
        self.topic_var = tk.StringVar(value="")
        self.topic_entry = ttk.Entry(row1, textvariable=self.topic_var)
        self.topic_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 第 2 行：关键词 + 子主题
        row2 = ttk.Frame(params_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="关键词(逗号分隔)", width=12).pack(side=tk.LEFT)
        self.kw_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.kw_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        ttk.Label(row2, text="子主题(逗号)", width=8).pack(side=tk.LEFT)
        self.sub_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.sub_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 第 3 行：时间范围 + 修改轮次 + 评分阈值 + 断点续跑
        row3 = ttk.Frame(params_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="时间范围", width=12).pack(side=tk.LEFT)
        self.time_var = tk.StringVar(value="2019-2026")
        ttk.Entry(row3, textvariable=self.time_var, width=12).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row3, text="最大修改轮次").pack(side=tk.LEFT)
        self.rev_var = tk.StringVar(value="3")
        ttk.Spinbox(row3, textvariable=self.rev_var, from_=1, to=10, width=4).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(row3, text="评分阈值(百分制)").pack(side=tk.LEFT)
        self.thresh_var = tk.StringVar(value=_get_env_value("REVIEW_ACCEPT_THRESHOLD", "75"))
        ttk.Spinbox(row3, textvariable=self.thresh_var, from_=50, to=100, width=4).pack(side=tk.LEFT, padx=(2, 10))
        self.resume_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="断点续跑", variable=self.resume_var).pack(side=tk.LEFT)
        self.skip_retrieval_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="跳过检索(复用缓存)", variable=self.skip_retrieval_var).pack(side=tk.LEFT, padx=(6, 0))

        # 第 4 行：按钮
        row4 = ttk.Frame(params_frame)
        row4.pack(fill=tk.X, pady=(6, 0))
        self.run_btn = ttk.Button(row4, text="▶  开始运行", command=self.start_run)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = ttk.Button(row4, text="■  停止", command=self.stop_run, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.clear_btn = ttk.Button(row4, text="🗑  清除旧数据", command=self.clear_data)
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row4, text="保存设置", command=self.save_settings).pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(main, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # ---------- 下部：输出区域 ----------
        out_frame = ttk.LabelFrame(main, text="运行输出", padding=4)
        out_frame.pack(fill=tk.BOTH, expand=True)
        self.output_text = scrolledtext.ScrolledText(
            out_frame, wrap=tk.WORD, font=("Consolas", 10),
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # 重定向 stdout
        self._stdout_redirect = RedirectText(self.output_text)
        sys.stdout = self._stdout_redirect

        self._pipeline_thread: threading.Thread | None = None

    # ---------------- actions ----------------

    def save_settings(self):
        _set_env_value("REVIEW_ACCEPT_THRESHOLD", self.thresh_var.get())
        messagebox.showinfo("设置", "已保存 REVIEW_ACCEPT_THRESHOLD 到 .env")

    def clear_data(self):
        if self._running:
            messagebox.showwarning("正在运行", "流水线运行中，请先停止。")
            return
        mb = messagebox.askyesno("确认清除", "将删除 data/chroma、outputs/ 中的所有文件和检查点，确定？")
        if not mb:
            return
        try:
            removed = clear_previous_data()
            if removed:
                self.log(f"[清除] {len(removed)} 项: {', '.join(removed)}")
                self.status_var.set(f"已清除 {len(removed)} 项旧数据")
            else:
                self.log("[清除] 无旧数据需要清除")
                self.status_var.set("无旧数据")
        except Exception as e:
            messagebox.showerror("清除失败", str(e))

    def start_run(self):
        topic = self.topic_var.get().strip()
        if not topic:
            messagebox.showwarning("缺少参数", "请输入研究主题。")
            return
        if self._running:
            return

        keywords = [k.strip() for k in self.kw_var.get().split(",") if k.strip()]
        subtopics = [s.strip() for s in self.sub_var.get().split(",") if s.strip()]
        time_range = self.time_var.get().strip() or "2019-2026"
        try:
            max_rev = int(self.rev_var.get())
        except ValueError:
            max_rev = 3

        os.environ["REVIEW_ACCEPT_THRESHOLD"] = self.thresh_var.get()
        resume = self.resume_var.get()
        skip_retrieval = self.skip_retrieval_var.get()

        self._running = True
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.clear_btn.config(state=tk.DISABLED)
        self.status_var.set("运行中...")

        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline,
            args=(topic, keywords, subtopics, time_range, max_rev, resume, skip_retrieval),
            daemon=True,
        )
        self._pipeline_thread.start()

    def stop_run(self):
        self._running = False
        self.status_var.set("已请求停止（等待当前节点完成）...")
        self.log("\n[用户] 已请求停止流水线\n")

    def _run_pipeline(self, topic, keywords, subtopics, time_range, max_rev, resume, skip_retrieval):
        try:
            from src.utils.console import ensure_utf8_console
            from src.graph.pipeline import run_pipeline

            ensure_utf8_console()
            final_state = run_pipeline(
                topic=topic,
                keywords=keywords,
                sub_topics=subtopics,
                time_range=time_range,
                max_revisions=max_rev,
                resume=resume,
                skip_retrieval=skip_retrieval,
            )
            self.root.after(0, self._on_done, final_state)
        except Exception as e:
            self.root.after(0, self._on_error, str(e))

    def _on_done(self, final_state: dict | None):
        self._running = False
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.clear_btn.config(state=tk.NORMAL)
        if final_state is None:
            self.status_var.set("流水线已停止")
            return
        if final_state.get("error"):
            self.status_var.set(f"错误: {final_state['error']}")
            return
        score = final_state.get("review_score", "N/A")
        rev = final_state.get("revision_count", 0)
        self.status_var.set(f"完成 — 评分: {score}/50, 修改: {rev} 轮")
        messagebox.showinfo(
            "运行完成",
            f"审稿评分: {score}/50\n修改轮次: {rev}\n"
            f"初稿: {final_state.get('draft_path', 'N/A')}\n"
            f"LaTeX: {final_state.get('paper_tex_path', 'N/A')}",
        )

    def _on_error(self, msg: str):
        self._running = False
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.clear_btn.config(state=tk.NORMAL)
        self.status_var.set(f"运行出错: {msg}")
        messagebox.showerror("运行错误", msg)

    def log(self, msg: str):
        self.output_text.insert(tk.END, msg + "\n")
        self.output_text.see(tk.END)


def main():
    root = tk.Tk()
    app = App(root)

    # Windows 高分屏适配
    try:
        root.tk.call("tk", "scaling", 1.5)
    except Exception:
        pass

    root.mainloop()


if __name__ == "__main__":
    main()
