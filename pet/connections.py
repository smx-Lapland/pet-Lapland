"""Model connections and DSH workspace selection in the desktop pet."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
from urllib.error import HTTPError

from .config import DEFAULTS, apply_credentials, validate_connection
from .dsh_projects import projects, sessions
from .llm import DeepSeekClient


class ConnectionDialog(tk.Toplevel):
    def __init__(self, root, cfg, save, close):
        super().__init__(root)
        self.title("模型与 DSH 项目")
        self.transient(root)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.save = save
        self.results = queue.SimpleQueue()
        self.timer = None
        self.catalog = {
            "DeepSeek": {k: DEFAULTS[k] for k in ("api_base", "model", "model_key_env")},
            "DeepSeek Pro": {"api_base": DEFAULTS["api_base"], "model": "deepseek-v4-pro", "model_key_env": ""},
            "Ollama": {"api_base": "http://localhost:11434/v1", "model": "", "model_key_env": ""},
            "OpenAI": {"api_base": "https://api.openai.com/v1", "model": "", "model_key_env": "OPENAI_API_KEY"},
            **cfg.get("model_profiles", {}),
        }
        active = cfg.get("model_profile") or "DeepSeek"
        self.catalog[active] = {k: cfg.get(k, "") for k in ("api_base", "model", "model_key_env")}
        self.fields = {}
        self.widgets = {}
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        rows = [("name", "模型配置名称"), ("api_base", "API 基础地址"), ("model", "模型 ID"),
                ("model_key_env", "密钥环境变量"), ("project", "DSH 项目"), ("session", "项目对话")]
        for index, (key, label) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=index, column=0, padx=(0, 10), pady=5, sticky="w")
            var = self.fields[key] = tk.StringVar(self)
            w = self.widgets[key] = ttk.Combobox(body, width=48, textvariable=var) if key in (
                "name", "model", "project", "session") else ttk.Entry(body, textvariable=var, width=48)
            w.grid(row=index, column=1, sticky="ew")
        self.widgets["name"].configure(values=tuple(self.catalog))
        self.widgets["name"].bind("<<ComboboxSelected>>", self._select_model)
        self.fields["name"].set(active)
        self._select_model()
        self.fetch = ttk.Button(body, text="读取模型", command=self._fetch_models)
        self.fetch.grid(row=2, column=2, padx=(5, 0))
        self.workspaces = projects()
        self.project_names = {"独立对话": ""}
        for p in self.workspaces.values():
            label = f"{p.title}  ·  {p.path}"
            if label in self.project_names:
                label += f"  ({p.id[:8]})"
            self.project_names[label] = p.id
        self.widgets["project"].configure(values=tuple(self.project_names), state="readonly")
        self.widgets["project"].bind("<<ComboboxSelected>>", self._select_project)
        self.widgets["session"].configure(state="readonly")
        self.fields["project"].set(next((label for label, key in self.project_names.items()
                                         if key == cfg.get("dsh_project", "")), "项目已移除，请重新选择"))
        self._select_project(saved=cfg.get("dsh_session", ""))
        ttk.Label(body, wraplength=500, text="选择已有配置可切换服务，输入新名称可另存。支持 Chat Completions 接口。"
                  "选中 DSH 项目后，最近三轮项目对话摘录会随提问发给当前模型。").grid(
                      row=6, column=0, columnspan=3, sticky="w", pady=(10, 4))
        self.status = ttk.Label(body, wraplength=500)
        self.status.grid(row=7, column=0, columnspan=3, sticky="w")
        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="取消", command=close).pack(side="left", padx=5)
        ttk.Button(actions, text="保存并切换", command=self.apply).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", close)
        self.bind("<Escape>", lambda _: close())
        self.grab_set()

    def _select_model(self, _event=None):
        for key, value in self.catalog[self.fields["name"].get()].items():
            if key in ("api_base", "model", "model_key_env"):
                self.fields[key].set(value)
        self.widgets["model"].configure(values=())

    def _select_project(self, _event=None, saved=""):
        project = self.workspaces.get(self.project_names.get(self.fields["project"].get()))
        self.session_names = {"自动选最近有记录的会话": ""}
        if project:
            for _, sid, title, _ in sessions(project):
                self.session_names[f"{title[:55]}  ({sid.removeprefix('session-')[:8]})"] = sid
        self.widgets["session"].configure(values=tuple(self.session_names), state="readonly" if project else "disabled")
        self.fields["session"].set(next((k for k, v in self.session_names.items() if v == saved),
                                        "会话已移除，请重新选择"))

    def _connection(self, for_list=False):
        model = self.fields["model"].get().strip()
        return validate_connection(self.fields["api_base"].get(), model or ("_" if for_list else ""),
                                   self.fields["model_key_env"].get().strip())

    def _fetch_models(self):
        try:
            cfg = self._connection(for_list=True)
            apply_credentials(cfg)
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return
        self.fetch.configure(state="disabled")
        self.status.configure(text="正在读取模型列表…")
        src = (cfg["api_base"], cfg["model_key_env"])
        def fetch():
            try:
                models = DeepSeekClient(cfg).list_models()
                message = f"读到 {len(models)} 个模型，也可以手动填写模型 ID。"
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                models = []
                detail = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else "请检查地址与凭据"
                message = f"读取失败（{detail}）。也可以手动填写模型 ID。"
            self.results.put((src, models, message))
        threading.Thread(target=fetch, daemon=True).start()
        self._poll_models()

    def _poll_models(self):
        self.timer = None
        try:
            source, models, message = self.results.get_nowait()
        except queue.Empty:
            self.timer = self.after(80, self._poll_models)
            return
        self.fetch.configure(state="normal")
        if source == (self.fields["api_base"].get().strip().rstrip("/"), self.fields["model_key_env"].get().strip()):
            self.widgets["model"].configure(values=models)
            if models and not self.fields["model"].get().strip():
                self.fields["model"].set(models[0])
            self.status.configure(text=message)
        else:
            self.status.configure(text="")

    def apply(self):
        try:
            name = self.fields["name"].get().strip()
            if not name:
                raise ValueError("请输入模型配置名称。")
            connection = self._connection()
            project = self.project_names.get(self.fields["project"].get())
            session = self.session_names.get(self.fields["session"].get())
            if project is None or session is None:
                raise ValueError("请重新选择 DSH 项目或会话。")
            self.save({**connection, "model_profile": name, "model_profiles": {**self.catalog, name: connection},
                       "dsh_project": project, "dsh_session": session})
        except (ValueError, OSError) as exc:
            self.status.configure(text=str(exc))

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        super().destroy()
