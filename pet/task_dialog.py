import threading
import tkinter as tk
from tkinter import ttk

from .voice_input import VoiceInput, VoiceInputError


class TaskDialog(tk.Toplevel):
    def __init__(self, app, project, scope, draft, templates):
        super().__init__(app.root)
        self.app, self.scope = app, scope
        self.voice = VoiceInput(app.cfg.get("mic_language", "zh"))
        self.dictation = None
        self.title("交给 DSH 执行")
        self.transient(app.root)
        self.attributes("-topmost", True)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{project.title}  ·  {project.path}").pack(anchor="w")
        ttk.Label(body, text="DSH 会在该目录启动一次新任务，使用其自身的模型和权限设置。").pack(anchor="w")
        if templates:
            names = ttk.Combobox(body, values=list(templates), state="readonly")
            names.pack(fill="x", pady=(8, 0))
            def choose(_):
                self.input.delete("1.0", "end")
                self.input.insert("1.0", templates[names.get()])
            names.bind("<<ComboboxSelected>>", choose)
        self.input = tk.Text(body, height=4, width=62, wrap="word", font=("Microsoft YaHei UI", 10))
        self.input.pack(fill="x", pady=8)
        job = app.project_tasks.get(scope)
        self.input.insert("1.0", draft or (job.text if job else ""))
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.mic = ttk.Button(row, text="语音填写", command=self.toggle_mic)
        self.mic.pack(side="left")
        self.send = ttk.Button(row, text="交给 DSH 执行", command=self.submit)
        self.send.pack(side="right")
        self.stop = ttk.Button(row, text="停止任务", command=self.cancel_job)
        self.stop.pack(side="right", padx=6)
        self.status = ttk.Label(body)
        self.status.pack(anchor="w", pady=6)
        self.output = tk.Text(body, height=8, width=62, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True)
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", app._close_dialog)
        self.bind("<Escape>", lambda _: app._close_dialog())
        self.grab_set()

    def toggle_mic(self):
        if self.voice.device is None:
            try:
                self.voice.start()
            except VoiceInputError as exc:
                self.status.configure(text=str(exc))
                return
            self.dictation = threading.Event()
            self.mic.configure(text="结束录音")
            self.send.configure(state="disabled")
            return
        samples, token = self.voice.finish(), self.dictation
        self.mic.configure(text="识别中…", state="disabled")
        def decode():
            try:
                text = self.voice.transcribe(samples, token)
            except Exception:
                text = ""
            self.app.events.put(("task_dictation", self, token, text))
        threading.Thread(target=decode, daemon=True).start()

    def decoded(self, token, text):
        if token is self.dictation and not token.is_set():
            self.dictation = None
            self.mic.configure(text="语音填写", state="normal")
            self.input.insert("insert", text)
            self.refresh()
            if not text:
                self.status.configure(text="没有识别到语音，可以重录或直接输入。")

    def submit(self):
        text = self.input.get("1.0", "end").strip()
        if text and self.dictation is None:
            self.app.start_project_task(self.scope, text)
            self.refresh()

    def cancel_job(self):
        job = self.app.project_tasks.get(self.scope)
        if job and job.status == "执行中":
            job.stop.set()
            self.status.configure(text="正在停止 DSH 任务…")

    def refresh(self):
        job = self.app.project_tasks.get(self.scope)
        running = job is not None and job.status == "执行中"
        self.send.configure(state="disabled" if running or self.dictation else "normal")
        self.stop.configure(state="normal" if running else "disabled")
        self.status.configure(text=job.status if job else "输入任务后点击执行")
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        if job:
            self.output.insert("1.0", job.output)
        self.output.configure(state="disabled")

    def destroy(self):
        if self.dictation is not None:
            self.dictation.set()
        self.voice.finish()
        super().destroy()
