# 本地语音安装与实测

目标：在本机验证“引擎可运行、模型可用、短语音产生文字和时间戳”；如需说话人分离，还要实际得到声学标签。`check` 仅查安装与缓存，`smoke` 才运行真实推理。实测不导入面试档案，不输出完整转写文本，不上传音频。说话人标签不等于候选人/面试官身份。

## 检查与实测

```bash
<python> <skill-dir>/scripts/audio_setup.py --root <data-root> plan --engine whisperx --diarize
<python> <skill-dir>/scripts/audio_setup.py --root <data-root> check --engine whisperx --diarize
<python> <skill-dir>/scripts/audio_setup.py --root <data-root> smoke --engine whisperx --diarize --audio <1到30秒的人声片段>
```

`plan` 只根据缺项生成结构化安装和模型下载命令，不执行、不联网；需要下载或登录时仍须取得用户授权。`smoke` 报告非空文本片段数、时间戳片段数、声学说话人标签和耗时。推荐使用自有录音中声音清晰的 10–25 秒片段；短片段不足以证明多说话人切换准确，需另选包含交替发言的片段人工核对。失败日志默认存于 `<data-root>/logs/`，权限为 0600；可用 `--log-dir` 改到其他本地目录。日志可能含私密内容，勿提交仓库。

Apple Silicon 仅测转写可改成 `--engine mlx-whisper`，不加 `--diarize`。自定义安装位置可传 `--engine-executable`、`--model`、`--align-model`、`--diarize-model`、`--nltk-data`。`smoke` 设置离线模式，缺少模型会停止，不会自动下载。若 `check` 显示 `ready`，仍须运行 `smoke`；若实测无片段，先确认截取位置确实有人声。

## 缺少依赖时安装

使用独立 Python 3.12 环境，安装到个人数据目录而非 Skill 仓库。例如：

```bash
python3.12 -m venv <data-root>/.venv-whisperx
<data-root>/.venv-whisperx/bin/python -m pip install --upgrade pip
<data-root>/.venv-whisperx/bin/python -m pip install 'whisperx==3.8.6' 'pyannote.audio==4.0.7' huggingface_hub
```

这是当前本机验收过的一组版本，其他平台应检查 Python、PyTorch 与设备兼容性。还需在 PATH 中安装 `ffprobe`。依赖与模型下载较大，先确认网络、空间与授权。以下是可替换的模型示例；`hf` 可执行程序在虚拟环境 `bin/` 下：

```bash
<data-root>/.venv-whisperx/bin/hf download h2oai/faster-whisper-large-v3-turbo --local-dir <data-root>/models/faster-whisper-large-v3-turbo
<data-root>/.venv-whisperx/bin/hf download jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn --local-dir <data-root>/models/chinese-wav2vec2-align
<data-root>/.venv-whisperx/bin/hf download pyannote/speaker-diarization-community-1 --local-dir <data-root>/models/pyannote-speaker-diarization-community-1
<data-root>/.venv-whisperx/bin/python -m nltk.downloader -d <data-root>/models/nltk_data punkt_tab
```

三个模型分别用于 [CTranslate2 转写](https://huggingface.co/h2oai/faster-whisper-large-v3-turbo)、[中文对齐](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn) 和 [pyannote 分离](https://huggingface.co/pyannote/speaker-diarization-community-1)；下载命令的 `--local-dir` 见 [Hugging Face 官方说明](https://huggingface.co/docs/huggingface_hub/en/guides/download)。pyannote 模型须先在模型页面接受条件，并在本机用 `hf auth login` 登录；不要在命令参数、聊天或仓库中粘贴令牌。下载后运行 `check`、`smoke`，不要把目录存在等同于模型可用。模型来源和转写规则见 [audio-and-html.md](audio-and-html.md)。

若只需 Apple Silicon 本地转写，可另建 `<data-root>/.venv`，安装 `mlx-whisper`，缓存 `mlx-community/whisper-large-v3-turbo-q4`；它不提供该流程中的 pyannote 分离。安装和模型下载都需要用户对相应资源与网络行为的授权。正式导入仍使用 `transcribe_local_audio.py`，并保留原始输出。
