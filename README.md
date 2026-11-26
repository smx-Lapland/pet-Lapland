# pet-Lapland · Windows 与 macOS 双语桌宠

pet-Lapland 可以看你框选的屏幕、陪你练口语，也可以在你选定的 DSH 项目中执行任务。平时桌面只显示角色。点角色后可以打字，回答上方显示英语或意大利语，下方显示中文，每种语言各有两行。内置拉普兰德和缪尔赛思，各自有立绘、人设、语音设置和对话记录。

<p><img src="assets/lapland/victory.png" height="140" alt="拉普兰德完成讲解时的表情">　<img src="assets/characters/muelsyse/portrait.png" height="140" alt="缪尔赛思桌宠立绘"></p>

第一次安装可以只做前两节。能打字收到双语回答，就已经接通了模型。麦克风、角色音色和 DSH 项目可以分别开通。

## 1. 在一台新电脑上启动

### macOS 源码版

Apple Silicon 和 Intel Mac 使用同一份源码。先安装带 Tcl/Tk 的 Python 3.12。[Python 官方 macOS 安装包](https://www.python.org/downloads/macos/)

**下载 ZIP**　解压 Mac 适配分支的源码，在含有 `run_pet.command` 和 `requirements.txt` 的目录打开终端。[下载 Mac 版源码 ZIP](https://github.com/smx-Lapland/pet-Lapland/archive/refs/heads/codex/macos-support.zip)

**Mac 启动时报 `AttributeError: module 'ctypes' has no attribute 'WinDLL'`**　如果解压目录名含 `lapland-bilingual-preview`，你打开的是使用 Windows 启动代码的双语预览包。请从上面的 Mac 版链接重新下载，解压到新目录，确认里面有 `run_pet.command`，再在该目录按下方三行命令创建环境并启动。

**使用 Git**　也可以在终端克隆适配分支并进入项目目录。

```sh
git clone --branch codex/macos-support https://github.com/smx-Lapland/pet-Lapland.git
cd pet-Lapland
```

取得源码后，在项目目录运行下面三行。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --prefer-binary -r requirements.txt
sh run_pet.command
```

以后可以双击 `run_pet.command`，或在终端运行最后一行。ZIP 解压后若双击提示缺少执行权限，在项目目录运行 `chmod +x run_pet.command`。如果启动时报缺少 `_tkinter`，先运行 `python3.12 -m tkinter` 验证所用解释器能打开测试窗口，再用带 Tk 的解释器重建虚拟环境。

| 要做的事 | Mac 上的操作 |
|---|---|
| 打字或打开设置 | 点角色，或按 **Control+Option+Space** 输入。双指点按角色打开菜单，进入「模型与项目…」按第 2 节连接模型 |
| 框选屏幕 | 按 **Control+Option+S**，拖选后检查预览。首次使用按系统提示在「系统设置 → 隐私与安全性」允许启动桌宠的终端或 Python 录制屏幕，授权后重新启动 |
| 隐藏、找回或退出 | **Control+Option+H** 切换显示，**Control+Option+Q** 退出。隐藏后也可点击 Dock 中的桌宠图标。冲突时可在「设置…」改键，支持 `cmd` 和 `option` |
| 听英语或意大利语 | 在「设置…」选回答语言，语音服务选 `sapi` 即可调用 Mac 系统朗读。用 `say -v '?'` 查看已装音色，在「系统朗读音色」填写名称。未匹配时选择同语种已安装的音色 |
| 使用麦克风 | 先运行 `.venv/bin/python tools/setup_voice_input.py` 安装本地识别模型，再点「按住说话」。首次收音在「隐私与安全性 → 麦克风」允许访问 |
| 使用本地角色模型 | PocketTTS 可用 `.venv/bin/python tools/setup_lapland_voice.py` 准备模型。Mac 上的本地推理使用 CPU。第 4 节的 GPT-SoVITS 独立运行库、Qwen3 原声参考意配和 CUDA 安装步骤面向 Windows |

**首次启动自检**

1. 点角色打开对话框。按两次 **Control+Option+H**，确认桌宠可以隐藏并重新显示。
2. 按第 2 节连接模型后，用 **Control+Option+Space** 输入「二加三是多少？」。看到外语回答和中文字幕，即可确认文字聊天已接通。
3. 在「文本编辑」里写下 `apples = 3`，按 **Control+Option+S** 只框住这一行。在预览里核对选区，点「取消」即可完成截图检查。

macOS 15 的 Apple Silicon 和 Intel 运行器各通过了 13 项自动测试，覆盖窗口打开与恢复、快捷键注册、系统朗读生成 WAV、单实例锁和流式回答解析。麦克风收音、扬声器播放以及屏幕录制授权可在自己的 Mac 上按上表试用。[查看两种架构的测试结果](https://github.com/smx-Lapland/pet-Lapland/actions/runs/45665114485)

### Windows 源码版与分享版

源码版需要 Windows 10/11 x64、64 位 Python 3.12 和可写的项目目录。在仓库页面点 **Code → Download ZIP** 并完整解压，或用 Git 克隆。

打开**含有 `run_pet.cmd` 和 `requirements.txt` 的那一层目录**，在资源管理器地址栏输入 `powershell` 并回车。依次运行下面三行。全程使用虚拟环境里的解释器，无需修改 PowerShell 执行策略。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pet.main
```

第三行会打开桌宠。首次运行源码版使用拉普兰德，会在项目目录生成 `config.json`。如提示缺少密钥，按下一节配置。以后直接双击 `run_pet.cmd` 即可。想在桌面启动，可以给这个文件创建 Windows 快捷方式。`py` 无法识别时，先安装带 Python Launcher 和 Tcl/Tk 的 Windows 版 Python 3.12，重新打开 PowerShell。

**想直接运行缪尔赛思的同学**，登录有仓库访问权限的 GitHub 账号，从发布页的 **Assets** 下载 `Muelsyse-Windows-x64.zip`，完整解压到可写目录，双击 `Start.cmd`，然后做第 2 节。这个分享版内置网络与隐私防护。已安装新版桌宠时，可以只下载 `Muelsyse.petpack`，按第 5 节导入。角色包里的立绘和设定由接收方安装的桌宠加载，旧版程序需要单独更新。[缪尔赛思 v0.2.0 下载](https://github.com/smx-Lapland/pet-Lapland/releases/tag/v0.2.0) · [分享版使用说明](docs/portable-start.md)

## 2. 接入模型并发出第一条消息

右键桌宠，打开 **模型与项目…**。初次使用把 **DSH 项目** 设为 **独立对话**。以下三条路线任选一条。

| 你的情况 | API 基础地址 | 模型 ID | 密钥环境变量 |
|---|---|---|---|
| 这台电脑上的 DSH 已登录 DeepSeek | 选 `DeepSeek`，预设为 `https://api.deepseek.com` | 预设 `deepseek-flash`，也可从服务返回的列表选择 | 留空，桌宠先读 `DEEPSEEK_API_KEY`，再查本机 DSH 凭据 |
| 有自己的兼容 API | 填服务商提供的基础地址，保留它要求的 `/v1` 前缀 | 服务商返回的完整模型 ID | 填你在本机设置的变量**名称**，如 `PET_MODEL_KEY` |
| 已运行本地 Ollama | 选 `Ollama`，默认 `http://localhost:11434/v1` | 选这台机器已经安装的模型 | 本机服务允许匿名调用时留空 |

**有自己的密钥时**，可以用 Windows「编辑帐户的环境变量」创建用户变量，再重新启动桌宠。只想临时试用，可以退出当前桌宠，在项目目录的 PowerShell 运行下面几行。密钥在隐藏输入框中粘贴，命令历史只记录读取命令。官方 DeepSeek 路线可将变量名换成 `DEEPSEEK_API_KEY`。

```powershell
$petSecret = Read-Host '输入模型 API 密钥' -AsSecureString
$env:PET_MODEL_KEY = [System.Net.NetworkCredential]::new('', $petSecret).Password
.\.venv\Scripts\python.exe -m pet.main
Remove-Item Env:PET_MODEL_KEY
```

macOS 默认终端 zsh 可在项目目录使用下面的命令。先退出已运行的桌宠，让新进程读到本次输入的密钥。在「模型与项目…」将自定义接口的密钥变量名填为 `PET_MODEL_KEY`。

```zsh
read -rs 'PET_MODEL_KEY?输入模型 API 密钥后按回车：'
echo
export PET_MODEL_KEY
sh run_pet.command
unset PET_MODEL_KEY
```

1. 在 **模型配置名称** 选择预设，或输入一个便于辨认的新名称。填写上表对应的字段。基础地址到服务前缀为止，客户端会自行追加 `/models` 或 `/chat/completions`。
2. 点 **读取模型**。选一个模型并点 **保存并切换**。只提供聊天接口的服务也可以直接手填模型 ID。
3. 按 **Ctrl+Alt+Space**，输入「用一句话告诉我，二加三是多少」，回车。看到一行外语和一行中文，答案是五，即可确认聊天链路。模型列表可读只证明列表接口可用，实际问答才能验证推理权限。
4. 在记事本写 `apples = 3`，按 **Ctrl+Alt+S** 只框住这行，在预览里点 **确认发送**。回答能说出三个苹果，即可确认视觉链路。框选需要支持图像输入的模型。

### 给协助安装的 DSH 或其他 AI

可直接复制下面这段，让对方逐步完成首次接入。它也适用于换电脑后的检查。

```text
我在 Windows 上安装 pet-Lapland。请从含 run_pet.cmd、requirements.txt 的项目根目录开始。
先检查 64 位 Python 3.12，创建 .venv，用 .venv 内的解释器安装 requirements.txt。
请先配置独立文字聊天。沿用本机已登录的官方 DeepSeek，或使用我指定的基础地址、模型 ID 和密钥环境变量名。
需要密钥时让我在本机隐藏输入。不要读取、打印或上传密钥值、凭据文件、环境变量清单、真实截图或历史数据库。
请让我用“二加三是多少”做一次文字测试，分别报告依赖安装、模型列表、实际回答三个结果。
等我需要时再配置框选、麦克风、角色音色或 DSH 项目。遇到错误只报告步骤、HTTP 状态和建议检查项。
```

## 3. 日常操作

同一个 Windows 登录会话只运行一个桌宠。连续双击快捷方式会保留当前实例和对话。桌宠隐藏时，按 `Ctrl+Alt+H` 可以唤出。

| 想做什么 | 操作 | 会发生什么 |
|---|---|---|
| 文字对话 | 点角色或按 `Ctrl+Alt+Space` | 打开输入框，回车发送 |
| 讲解屏幕 | `Ctrl+Alt+S`，拖动选区 | 默认先预览，可拖黑块遮盖敏感区域、撤销或取消。确认后才发送 |
| 针对图片追问 | 说「这张图里的红字是什么意思」，或发送前勾选「本次带上截图」 | 这一次复用已确认的选区。打字或开麦问别的事会自动结束看图，开启新的对话话题 |
| 查看长回答 | 在字幕上滚动鼠标滚轮，或右键「完整讲解记录」 | 上方最多两行外语，下方最多两行中文。超过两行的部分随播放向上滚动，滚轮可停下回看，完整记录可复制 |
| 查看旧对话 | 右键「对话历史…」 | 按问题、回答或日期搜索，可复制、删除 |
| 移动或收起 | 拖动角色，点面板右上角 × | 角色可以单独留在桌面 |
| 调整对话框宽高 | 拖动对话框右下角或左上角的斜纹，或右键角色 →「对话框尺寸…」输入宽 × 高 | 只调整面板，角色保持原大小。字幕按新宽度换行，尺寸会保存到下次启动 |
| 隐藏或退出 | `Ctrl+Alt+H` 切换显示，`Ctrl+Alt+Q` 退出 | 热键可在「设置…」里更改 |

流式模式收到完整外语台词就开始合成一次，中文翻译和标题可以随后到达。兼容旧格式的多个片段会先合并再朗读。设置里的「静音，仅显示字幕」适合先排查模型连接。需要记住偏好时，可以说「请记住，我喜欢简短的回答」，也可以右键 **长期记忆…** 直接管理。启用本地文字记录后，桌宠重开可恢复最近三轮，发送时按当前话题选取。较早内容达到摘要阈值后，会通过当前模型整理成主要事项、未完成事项、次要细节。摘要也会消耗模型额度。

### DeepSeek 怎样接住角色的口气

拉普兰德的人设写在 `assets/lapland/dsh-persona.txt`。共用段落写她在意什么、碰到意外会怎么选、什么时候会收住玩笑。英语和意大利语各自提供场景和一组完整的回答。以「示范 」开头的 JSON 行会变成成对的用户和角色消息，放在真实聊天之前。例子始终属于虚构场景。日常聊天只在接着同一话题时带上最近一轮；明确要求回顾时，可以取最近三轮和先前的交接摘要。新框选从当前画面开始，同一张图的追问可以沿用该图最近三轮。长期记忆按问题匹配，最多取两条。说「换个话题」可以结束当前选区话题。角色对话示范的设计借鉴了 RoleLLM。[RoleLLM，ACL 2024](https://aclanthology.org/2024.findings-acl.878/)

英意各有六组独立的台词和中译。中文字幕跟着已经说出口的那句话走：挖苦时让客气话里的反讽落在句尾，认真时直接说清楚。原句里的人称、否定和具体操作都要对得上，称呼和笑声也要有出处。日常聊天只带所选语种的示例，英文原声表在点名原句时加入。「回合提醒 」这一行跟在当前问题后面，截图追问也会携带。已导入且仍使用旧版标准人设的拉普兰德包会读取新版人设，编辑过的提示词按包内文字读取。

使用官方 DeepSeek Flash 或 V4 Pro 的默认低推理配置时，拉普兰德的文字、听写聊天关闭思考，温度为 0.8。框选和显式高推理保留各自的推理配置。官方接口已弃用 presence_penalty 和 frequency_penalty。桌宠仍在一次请求内先取得完整口播，再接收中文和标题。[DeepSeek 接口参数](https://api-docs.deepseek.com/api/create-chat-completion/) · [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)

**复测角色台词。** 在项目根目录用以下命令对照 Flash 与 Pro。第一条会用已配置的官方 DeepSeek 凭据生成 48 条回答，仅发送 12 个虚构场景及脚本绘制的示例截图。每次请用新的输出目录。

```powershell
.\.venv\Scripts\python.exe tools/evaluate_dialogue.py dist/dialogue-review-01
.\.venv\Scripts\python.exe tools/evaluate_dialogue.py dist/dialogue-review-01 --audio-only
.\.venv\Scripts\python.exe tools/evaluate_dialogue.py dist/dialogue-review-01 --compare-translation
```

第二条读取本机的语速、设备和意配模型设置，用当前设置重新生成与盲样同编号的 WAV，自动把第八题的英配和意配依次拼成 `preview.wav`，在本机播放一遍。第三条针对反语、身体状态、双重否定和反问转折，以同一外语台词单独请求 Flash 翻译，供逐句对照。在 `review.csv` 里先看台词、听同编号音频，分别给去客服腔、口语地道、情绪与实听、中译保真打 1 至 5 分，最后再打开 `key.json` 揭晓模型。每条四项齐全且总分达到 17 才算该样本通过。需要留意的错误包括意语第一人称性别、否定落错人和编造截图细节。[本轮英意双语试听](docs/dialogue-preview.wav) · [对应的中文字幕与外语台词](docs/dialogue-preview.srt)

## 4. 开麦克风、切语言和选音色

**语音输入。** 在「设置… → 麦克风输入语言」选择你说的语言。默认「中文（可夹英文）」使用本机 SenseVoice，适合中文里夹 GitHub、Python 或 API 的提问。选「多语自动识别」「英语」或「意大利语」会使用本机 Whisper。两种收音方式和 DSH 任务听写共用这个设置，角色回答的语言可以单独选。

源码版在项目目录运行一次下面的命令，安装到 `models/sensevoice-small/` 和 `models/whisper-base/`。新版分享包构建时会包含这两套文件。只有 Whisper 的旧安装仍可听写，运行同一命令即可补装中文模型。

```powershell
.\.venv\Scripts\python.exe tools\setup_voice_input.py
```

点 **按住说话**，说完松开即可提问。点 **开始对话** 会持续监听，说完停顿后自动提交，角色说完再听下一轮。默认收音门槛是「较高」，背景声仍触发时可选「很高」。按 Esc、点「结束对话」或收起面板可结束收音。Windows 的麦克风隐私设置需要允许桌面应用访问默认麦克风。

**语音输出。** 右键 **设置… → 回答和朗读语言** 选英语或意大利语，每次只读所选语言。首次试音建议在 **语音服务** 选 `sapi`，选择电脑已安装相应语言的 Windows 声音。可以先用缪尔赛思的默认英语系统音色验证。语音失败时会尝试系统声音，仍不可用则保留字幕。

| 服务值 | 配置方式与用途 |
|---|---|
| `sapi` | 本机系统朗读，Windows 使用 SAPI，macOS 使用 `say`。缺少英语或意大利语音色时可在系统语音设置中添加 |
| `edge` / `azure` | 在线朗读，口播文本会送至微软。Azure 读取本机的 `SPEECH_KEY` 和 `SPEECH_REGION` |
| `api` | 「角色与迁移 → 语音输出」填写语音基础地址、模型、音色 ID 和密钥变量名。客户端向基础地址追加 `/audio/speech`，发送 `model`、`voice`、`input`、`response_format: wav` |
| `piper` | 「角色与迁移 → 语音输出 → 选择本地 Piper 模型…」。准备同名的 `.onnx` 与 `.onnx.json`，另存并启用 |
| `lapland` | 本地 PocketTTS。运行下面的脚本获取固定版本模型和语料，分别构建英语、意大利语音色 |
| `vits` | 装有新版英配人物包和运行库时，英语使用微调的 GPT-SoVITS v2。意大利语以及旧版英配使用 `models/piper-vits-reference/` 中对应语言的 Piper 模型、配置、参考参数和音色转换器 |

```powershell
.\.venv\Scripts\python.exe tools\setup_lapland_voice.py
```

拉普兰德的角色提示词控制措辞、断句和语气。回答首段可标注 `LAZY`、`MOCKING`、`MANIC`、`WHISPER` 或 `NEUTRAL`，合成前会移除标签。音色仍取决于对应语言的声学模型和参考录音。缪尔赛思示例使用 ImageGen 生成的同人立绘和 Windows 系统配音。[拉普兰德情绪参考表](assets/lapland/voice/emotions/references.json) · [缪尔赛思立绘说明](assets/characters/muelsyse/artwork.md)

**使用拉普兰德英配人物包。** `Lapland-EN.petpack` 内含七种状态立绘、头像、对话框背景、人设、任务模板、英配语义与声学权重和五种情绪的参考特征。新版英配会按角色录音的音高范围加强重音、保留停顿。已有独立英配运行库的电脑可以重新执行下面的安装命令以补齐音高处理依赖。`Lapland.petpack` 是只带形象、设定和接口配置的轻量版。先将桌宠更新到 v0.3.0 或更高版本，再导入人物包。[拉普兰德 v0.3.0 私有下载](https://github.com/smx-Lapland/pet-Lapland/releases/tag/v0.3.0)

1. 在新电脑安装 Git 和 64 位 Python 3.10，在新版桌宠的源码根目录执行 `py -3.10 tools/setup_gpt_sovits_en.py`。这会下载固定版本的引擎，在 `models/gpt-sovits-en/` 安装专用运行库。已有运行库的用户重跑同一命令即可获得英配冷启动优化，朗读时会跳过训练用的统计模块。仅用 CPU 的机器可在命令末尾加 `--cpu`。
2. 右键桌宠，打开「角色与迁移… → 导入角色包…」，选择 `Lapland-EN.petpack` 并点「启用所选角色」。默认语音服务为 `vits`，语言为英语。合成和播放在本机完成。在「设置… → 语速」填 `0` 使用模型本身的节奏。
3. 本机有训练权重时，可运行 `.\.venv\Scripts\python.exe tools/package_portable.py --lapland` 在 `dist/` 重新生成两种人物包。内置角色的权重位置为 `models/gpt-sovits-en/active/lapland-en.safetensors`。导入的角色单独保存自己的权重。英配的两个阶段各训练 100 轮，训练集包含本体、异格和同一英配的皮肤，共 45 个切片。另留 8 个切片做对照。对 7 句试听文本比较短训基线及第 20、40、60、80、100 轮的权重，发布包选用第 80 轮。意配继续使用自己的模型和参考音频。

英配桌宠显示时会先在电脑内存中预载模型，打开对话面板时才启用所选计算设备。自动收起后最多在内存待命两分钟，手动隐藏时释放语音进程。输入和持续对话期间复用已加载的模型。音频失败临时回退 CPU，GPU 优先模式会在 30 秒后于下一次使用时重试显卡。

在「设置… → 本地神经语音运算」可以选「GPU 优先（自动回退 CPU）」或「仅 CPU」。意配可在以下两种本地声音之间切换，语音服务均选 `vits`。命中收录原句时直接播放原配音。

- **快速意配**使用 Piper VITS。语速为 0 时保留合成出的音节时长和音高，手动调速由 Piper 在合成阶段完成。勾选「意配使用 VITS 并在播放前复核发音」会用 Whisper 检查读词，偏差较大时再次合成，开口等待也会增加。
- **原声参考意配**使用 Qwen3-TTS 0.6B，按情绪选取意大利语演员的原声和对应台词来生成新句。语速设为 **0** 时按模型原速播放。系统参照原配音节间的响度差，在念得较平的短语中加强已有重音、收轻相邻弱音。生成语音中，超过 0.8 秒且几乎没有声音的内部间隔会收短至约半秒，正常换气和短停顿保留。手动调整语速会启用变速处理。

**意配试听基准（2026-11-25 确认）**　这句调侃使用原声参考模型，语速为 0，全长 6.06 秒。后续调整重音或语气时，用同一句对照电音感和断句，尤其留意最后的 *un capolavoro di precisione* 能否顺着前半句说下来。[播放已确认的意配样本](https://github.com/smx-Lapland/pet-Lapland/blob/ee45cdbf824c4cef11e4f3fe30a09706aef4b491/docs/dialogue-preview.wav)

安装过上一节的意配录音后，在有兼容 NVIDIA GPU 的 Windows 机器上运行下方命令。模型权重约 2.5 GB，脚本另建 PyTorch 环境。完成后选择「GPU 优先」，勾选「意配使用原声参考模型」，并关闭 VITS 发音复核。首次打开对话框时需要加载模型，连续对话可复用。录音、参考特征和生成过程都在本机。GPU 推理失败时使用本地 CPU VITS。[模型说明](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)

```powershell
.\.venv\Scripts\python.exe tools\setup_italian_reference.py
```

**为 Windows x64 源码版启用 CUDA。** 已验证的组合是 64 位 Python 3.12、可用的 NVIDIA 驱动和 CUDA Toolkit 12.8。在项目根目录运行下列命令，将 GPU 版 ONNX Runtime 和 cuDNN 装进语音专用目录。使用 CPU 的电脑只需第 1 节的普通安装。分享版默认携带 CPU 运行库。

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --no-deps --target models/ort-cuda onnxruntime-gpu==1.23.2 nvidia-cudnn-cu12==9.10.2.21
```

GPU 模式会在开始输入或收音时预热拉普兰德的语音模型。回答结束后最多保留 30 秒供追问复用，收起面板、切换设置或中断播放会释放引擎。首次使用显卡可能需要更长的初始化时间，连续朗读更容易受益。打游戏时可以随时在设置里选「仅 CPU」。[ONNX Runtime CUDA 后端与依赖说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)

## 5. 切换、制作和分享角色

1. **只想看缪尔赛思**：右键 **角色与迁移…**，在顶端下拉框选「缪尔赛思」，点 **启用所选角色**。窗口标题变为 `pet-Muelsyse`。切回拉普兰德用相同操作。
2. **想做自己的角色**：先选一个模板。在 **角色与形象** 填中英文名、人设，选择「基础立绘」并导入透明 PNG。替换基础图后，各状态先使用这张图，再按需补上待机、好奇、思考、说话、完成等图片。
3. 在 **对话接口** 配聊天服务，在 **语音输出** 配音色。在 **DSH 任务** 里每行写 `名称 | 任务描述`，例如 `运行测试 | 找出本项目的测试入口并报告测试结果`。
4. 点 **另存并启用** 保存一个独立角色。直接点 **启用所选角色** 会使用下拉框所选的已保存版本。
5. 点 **导出角色包…** 获得 `.petpack`。接收方点 **导入角色包…**，核对图片、人设和服务地址后再启用。导入会清除包内指定的聊天和语音密钥变量名。需要自定义认证时，由接收方填写本机变量名，再点 **另存并启用**。

角色包可以携带接口地址、自定义提示词、任务文本、图片，以及选用的 Piper 或拉普兰德英配权重。分享前检查这些字段，尤其是内网地址和自行填写的提示词。普通角色包适合已经装好桌宠的人。给新电脑制作含运行时的 Windows x64 分享版时，从干净的源码目录安装依赖、运行语音输入安装命令补齐两套模型，再运行以下命令。产物位于本机 `dist/`。

```powershell
.\.venv\Scripts\python.exe tools\package_portable.py
```

## 6. 连接 DSH 项目

打开桌宠对话面板，点 **DSH 项目**，即可看到本机 DSH 已登记的项目，每次展开都会重新读取。选中项目后，问「当前项目的进度如何」会读取所选 DSH 会话摘录。新框选只看当前画面。菜单里的 **查看所选 DSH 项目的最近对话…** 可在本机查看项目上下文，**选择具体会话…** 可改用指定会话。需要执行任务时用右键 **交给 DSH 执行…**。

右键 **模型与项目…**，选择已在这台电脑的 DSH 中登记的项目和会话，点 **保存并切换**。可固定会话，也可自动选最近有记录的一段。普通聊天会将项目名、会话标题和最多三轮问答摘录交给**当前聊天模型**。回到 **独立对话** 即可停止附加这些资料。项目、指定会话及角色有各自的桌宠历史和记忆。

要实际执行任务，先确认当前项目，再右键 **交给 DSH 执行…**，填写任务或选择模板，点执行。桌宠在该项目目录调用本机 DSH 的 headless 模式，使用 DSH 自己的模型和权限。可在任务窗口查看结果或停止任务，停止后已完成的文件改动仍然保留。普通聊天回复和角色输出不会自动启动命令。先在终端验证 `dsh --help` 可用。使用 npm 安装的 DSH 还需可用的 Node.js。

## 7. 隐私与网络防护

| 操作 | 数据会到哪里 |
|---|---|
| 独立文字聊天 | 问题、当前角色设定、近期对话及适用记忆会发给所选聊天服务 |
| 框选讲解 | 默认预览确认后，选区的 JPEG 发给同一聊天服务。明确追问截图或勾选带图时才再次使用该图。`save_captures` 默认关闭 |
| 麦克风 | SenseVoice 或 Whisper 在本机处理录音，识别出的文字进入聊天请求 |
| 朗读 | 系统音色、Piper 和本地角色模型在本机合成；在线语音服务会收到口播文字 |
| 启用 DSH 项目 | 项目名、会话标题和近期摘录会随聊天发送。程序移除自动附加的绝对工作目录，并遮盖摘录中的本机目录和部分常见凭据格式。敏感项目可留在「独立对话」 |
| 保存历史 | Windows 数据目录为 `%LOCALAPPDATA%\Mochi\`，macOS 为 `~/Library/Application Support/Mochi/`。其中 `history.sqlite3` 保存历史，`characters/` 保存导入角色。右键设置可停止保存新对话，已有记录可在历史窗口中删除 |

桌宠通过出站请求连接模型。远程聊天和自定义语音 API 使用 HTTPS。本机 HTTP 允许 `localhost` 和回环 IP，例如 `127.0.0.1`，并绕过系统 HTTP 代理。基础地址禁止内嵌账号、密码、查询参数或片段。聊天、模型列表、自定义语音和 Azure HTTP 客户端拒绝重定向。聊天响应上限为 2 MiB，单行流式事件上限为 256 KiB，单次语音响应上限为 16 MiB。服务商返回 3xx 时，请核对其文档并直接填写最终 HTTPS 基础地址。

角色包导入按文件白名单检查目录、重复条目、大小和图片像素，导入和导出时重新编码图片并去除附带元数据。启用自定义角色时会显示聊天和语音的接收方，使用自定义凭据时还会显示变量名。选择取消即可继续用原角色。

每个接收方使用自己的凭据。配置中的 `model_key_env` 和 `tts_key_env` 都是**变量名**。本地 `config.json`、环境文件、DSH 凭据、历史库、日志、缓存、截图、模型目录和打包目录已列入 Git 忽略范围。提交代码前仍应检查暂存文件；手写提示词、截图像素、对话内容和自定义接口路径都可能包含个人信息。

## 8. 开发者接入与自检

| 要修改的内容 | 入口 |
|---|---|
| 窗口、字幕、热键和对话生命周期 | `pet/app.py`、`pet/subtitles.py`、`pet/hotkeys.py` |
| 模型协议、流式解析和请求边界 | `pet/llm.py`、`pet/connections.py`、`pet/network.py` |
| 角色清单、资源和迁移窗口 | `pet/characters.py`、`pet/character_dialog.py`、`assets/characters/muelsyse/` |
| 录音、朗读和参考音色 | `pet/voice_input.py`、`pet/speech.py`、`pet/neural_worker.py`、`pet/vits_voice.py` |
| 项目摘录、显式任务和长期记忆 | `pet/dsh_projects.py`、`pet/dsh_tasks.py`、`pet/history.py` |

**自建聊天服务的最小协议。** 支持 `POST /chat/completions`（相对于基础地址），接受 `model`、`messages` 和 `stream`，返回 `choices[0].message.content` 字符串。桌宠会把角色提示词放进系统消息。下面是英文回答的可用样例，意大利语把 `en` 换为 `it`。需要显示模型列表时，`GET /models` 返回 `{"data":[{"id":"my-model"}]}`。选择流式时可返回标准 SSE 的 `choices[].delta.content` 并以 `data: [DONE]` 结束，普通 JSON 响应也支持。

```json
{"choices":[{"message":{"content":"{\"segments\":[{\"en\":\"[NEUTRAL] Five.\",\"zh\":\"五。\"}],\"headline_en\":\"Addition\",\"headline_zh\":\"加法\"}"}}]}
```

**后台测试**可在项目根目录运行，使用模拟服务和临时数据。完整测试还包括桌面窗口、热键和框选，适合在空闲桌面执行。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_connections.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_characters.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_history.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_delivery.py -k StreamTest
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_delivery.py -k VoiceTest
```

## 9. 常见故障

| 现象 | 先查什么 |
|---|---|
| 双击没有窗口 | 在源码目录用 `.\.venv\Scripts\python.exe -m pet.main` 看启动错误。已启动时按 `Ctrl+Alt+H` 切换显示 |
| 401 / 403 | 核对接口域名、变量名、模型权限。修改用户环境变量后从新进程启动 |
| 404 / 模型列表为空 | 检查基础地址是否重复带了 `/chat/completions` 或 `/v1`。服务未实现列表接口时手填真实模型 ID |
| 429 / 网络超时 | 检查服务额度、限流和服务运行状态。本地地址指运行桌宠的这台电脑 |
| 能聊天却不能看图 | 确认模型具备视觉能力，兼容接口支持消息里的 `image_url` 数据。先用简单的记事本选区验证 |
| 有字幕、无声音 | 检查静音和默认播放设备，切到 `sapi` 试同一语言。专用音色还需要额外模型 |
| 麦克风没反应 | 检查默认输入设备和输入语言。macOS 在「隐私与安全性 → 麦克风」检查启动桌宠的应用权限，Windows 检查桌面应用麦克风权限。按第 4 节补齐识别模型 |
| DSH 项目列表为空 | 在 DSH 中打开并登记工作区。有多套配置时，确认桌宠和 DSH 使用相同的 `DSH_HOME` |
| 快捷键无效 | 右键菜单可直接操作；在「设置…」更换冲突热键。分享故障信息时只摘出必要的错误码 |

默认配置参见 [config.example.json](config.example.json)。历史设计与验收基线见 [Markdown 报告](docs/Mochi-Design-Report.md) 和 [Word 报告](docs/Mochi-Design-Report-v0.1.docx)。代码许可证见 [LICENSE](LICENSE)。角色素材与第三方模型按各自来源和授权使用。
