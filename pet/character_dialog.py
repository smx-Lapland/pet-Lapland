import tempfile
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .characters import POSES, SHARE_KEYS, available_characters, clean_manifest, image_files, import_pack, write_pack


class CharacterDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("角色与迁移")
        self.transient(app.root)
        self.attributes("-topmost", True)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        self.roles = available_characters()
        self.names = {f"{r.data['name']} · {k[:8]}": k for k, r in self.roles.items()}
        self.selector = ttk.Combobox(body, values=list(self.names), state="readonly")
        self.selector.pack(fill="x")
        self.selector.set(next(n for n, k in self.names.items() if k == app.character.id))
        self.selector.bind("<<ComboboxSelected>>", self.load)
        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True, pady=8)
        self.fields = {}
        def tab(title):
            frame = ttk.Frame(tabs, padding=10)
            tabs.add(frame, text=title)
            return frame
        def field(frame, key, label, choices=()):
            row = frame.grid_size()[1]
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            var = self.fields[key] = tk.StringVar(self)
            widget = (ttk.Combobox(frame, textvariable=var, values=choices, state="readonly") if choices
                      else ttk.Entry(frame, width=48, textvariable=var))
            widget.grid(row=row, column=1, sticky="ew")
        main = tab("角色与形象")
        field(main, "name", "中文名")
        field(main, "name_en", "英文名")
        ttk.Label(main, text="角色设定与说话风格").grid(row=2, column=0, columnspan=2, sticky="w")
        self.persona = tk.Text(main, width=54, height=7, wrap="word")
        self.persona.grid(row=3, column=0, columnspan=2)
        self.slots = dict(zip(("基础立绘", "待机", "好奇", "思考", "不耐烦", "说话", "完成", "出错", "对话背景"),
                              ("portrait", *POSES, "background")))
        self.slot = ttk.Combobox(main, values=list(self.slots), state="readonly", width=14)
        self.slot.current(0)
        self.slot.grid(row=4, column=0, pady=6, sticky="w")
        ttk.Button(main, text="选择图片…", command=self.choose_image).grid(row=4, column=1, sticky="w")
        model = tab("对话接口")
        for k, label in (("api_base", "API 基础地址"), ("model", "模型 ID"), ("model_key_env", "密钥环境变量")):
            field(model, k, label)
        speech = tab("语音输出")
        field(speech, "speech_provider", "朗读服务", ("sapi", "api", "piper", "edge", "azure", "lapland", "vits"))
        field(speech, "speech_language", "回答语言", ("en", "it"))
        for k, label in (("voice", "系统音色"), ("neural_voice", "Edge / Azure 英语音色"),
                         ("neural_voice_it", "Edge / Azure 意语音色"), ("tts_base", "语音 API 基础地址"),
                         ("tts_model", "语音 API 模型"), ("tts_voice", "语音 API 音色 ID"),
                         ("tts_key_env", "语音密钥环境变量")):
            field(speech, k, label)
        ttk.Button(speech, text="选择本地 Piper 模型…", command=lambda: self.run(self.choose_voice)).grid(
            row=speech.grid_size()[1], column=1, sticky="w", pady=4)
        tasks = tab("DSH 任务")
        ttk.Label(tasks, text="每行一个模板，用「名称 | 任务描述」填写。").pack(anchor="w")
        self.tasks = tk.Text(tasks, height=10, width=56, wrap="word")
        self.tasks.pack(fill="both", expand=True)
        self.status = ttk.Label(body, wraplength=520)
        self.status.pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        for label, fn in (("导入角色包…", self.import_file), ("导出角色包…", self.export),
                          ("另存并启用", self.clone), ("启用所选角色", self.activate)):
            ttk.Button(buttons, text=label, command=lambda f=fn: self.run(f)).pack(side="left", padx=2)
        self.load()
        self.protocol("WM_DELETE_WINDOW", app._close_dialog)
        self.bind("<Escape>", lambda _: app._close_dialog())
        self.grab_set()

    def run(self, func):
        try:
            func()
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
            self.status.configure(text=str(exc))

    def load(self, _event=None):
        role = self.roles[self.names[self.selector.get()]]
        self.assets = image_files(role)
        settings = self.app.role_settings(role)
        for k, var in self.fields.items():
            var.set(role.data[k] if k in ("name", "name_en") else settings[k])
        self.persona.delete("1.0", "end")
        self.persona.insert("1.0", role.data["persona"])
        self.tasks.delete("1.0", "end")
        self.tasks.insert("1.0", "\n".join(k + " | " + v.replace("\n", "\n    ")
                                            for k, v in role.data["tasks"].items()))
        self.status.configure(text="更换基础立绘会让所有动作先使用新图。各动作可以单独替换。")

    def choose_image(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("角色图片", "*.png *.jpg *.jpeg *.webp")])
        if p:
            slot = self.slots[self.slot.get()]
            if slot == "portrait":
                for pose in POSES:
                    self.assets.pop(pose + ".png", None)
            self.assets[slot + ".png"] = Path(p)
            self.status.configure(text=f"{self.slot.get()}已选择 {Path(p).name}")

    def choose_voice(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Piper 语音模型", "*.onnx")])
        if path:
            config = Path(path + ".json")
            if not config.is_file():
                raise ValueError("请把模型和同名的 .onnx.json 文件放在一起。")
            self.assets.update({"voice.onnx": Path(path), "voice.onnx.json": config})
            self.fields["speech_provider"].set("piper")
            self.status.configure(text="将随角色包携带音色模型 " + Path(path).name)

    def manifest(self):
        templates = {}
        current = None
        for line in self.tasks.get("1.0", "end").splitlines():
            if current and line.startswith("    "):
                templates[current] += "\n" + line[4:]
                continue
            if not line.strip():
                continue
            name, sep, task = line.partition("|")
            if not sep or name.strip() in templates:
                raise ValueError("请为每个任务填写唯一的名称和描述，并用 | 分隔。")
            current = name.strip()
            templates[current] = task.strip()
        return clean_manifest({"version": 1, "name": self.fields["name"].get(), "name_en": self.fields["name_en"].get(),
                               "persona": self.persona.get("1.0", "end").strip(), "tasks": templates,
                               "settings": {k: self.fields[k].get().strip() for k in SHARE_KEYS}})

    def export(self):
        data = self.manifest()
        dest = filedialog.asksaveasfilename(parent=self, defaultextension=".petpack", initialfile="character.petpack",
                                            filetypes=[("桌宠角色包", "*.petpack")])
        if dest:
            write_pack(dest, data, self.assets)
            self.status.configure(text=f"已导出至 {dest}")

    def import_file(self):
        src = filedialog.askopenfilename(parent=self, filetypes=[("桌宠角色包", "*.petpack *.zip")])
        if src:
            role = import_pack(src)
            label = f"{role.data['name']} · {role.id[:8]}"
            self.roles[role.id] = role
            self.names[label] = role.id
            self.selector.configure(values=list(self.names))
            self.selector.set(label)
            self.load()
            self.status.configure(text="已导入。请核对接口地址。自定义密钥变量需在本机填写，修改后点「另存并启用」。")

    def clone(self):
        data = self.manifest()
        if not self.confirm_services(data["settings"]):
            return
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "role.petpack"
            write_pack(pack, data, self.assets)
            self.app.activate_character(import_pack(pack, local_copy=True))

    def confirm_services(self, settings):
        targets = ["聊天、已确认的选区和已选项目上下文将发给 " + settings["api_base"]]
        if settings["model_key_env"]:
            targets.append("聊天凭据变量：" + settings["model_key_env"])
        provider = settings["speech_provider"]
        if provider == "api":
            targets.append("朗读文本将发给 " + settings["tts_base"])
            if settings["tts_key_env"]:
                targets.append("语音凭据变量：" + settings["tts_key_env"])
        elif provider in ("edge", "azure"):
            targets.append("朗读文本将发给微软语音服务")
        return messagebox.askyesno("启用角色并使用这些服务？", "\n".join(targets),
                                   parent=self, default="no")

    def activate(self):
        role = self.roles[self.names[self.selector.get()]]
        if role.id not in ("lapland", "muelsyse", self.app.character.id):
            if not self.confirm_services(self.app.role_settings(role)):
                return
        self.app.activate_character(role)
