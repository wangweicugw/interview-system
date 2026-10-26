# 本地音频与参考稿

本规则仅适用于用户明确要求转写或校对真实面试材料的场景。目标是把录音转化为可复核的文稿证据，而不是把自动识别结果伪装成精确逐字稿。统一 HTML 的默认生成与验收规则见 `html-presentation.md`。

## 1. 本地优先与授权边界

- 先检查本机是否已有可用语音识别引擎、模型和硬件加速能力。不得因为“可用”而默认上传音频到云端。
- Apple Silicon 上，经本地可用性检查后，可选用 MLX Whisper；本次验证可用的量化模型是 `mlx-community/whisper-large-v3-turbo-q4`。这只是已验证配置，不是所有机器的强制默认。
- 若模型尚未缓存，下载模型是外部网络行为；先说明模型体积、预期耗时与本地存储影响，并取得用户授权。不能静默安装依赖或下载权重。
- 仅在当前 case 的 `private/` 保存音频与原始识别文件。聊天中只呈现必要摘要，避免贴出完整敏感内容。
- “本地 Whisper”只描述语音识别这一步，不代表后续 AI 复盘也离线。后续模型处理必须遵守 `privacy-and-security.md` 的模式和数据类别授权。

## 2. 转写与原始证据

1. 读取音频元数据：文件哈希、格式、采样率、声道、时长。文件创建/修改时间只能作为文件信息，不能替代用户提供的面试日期、时间或时区。
2. 根据 JD、简历中的少量专有技术词构造简短提示词，帮助术语识别；提示词不得添加录音中没有的事实。
3. 运行本地引擎，至少保存机器原始 JSON 和 TXT；引擎支持时同时保存 SRT/VTT。输出命名为 `whisper.raw.*` 或等价名称，且不做人工改写。
4. 在 `manifest.json` 或修订记录中保存：源音频路径/哈希、识别引擎和模型、语言、提示词、执行时间、输出文件名与失败信息。
5. 把原始识别片段转入 `transcript.raw.json`，保留原始时间戳、文本和 `segment_id`。语病、专有名词和角色修订必须进入 `transcript.revised.json` 与修订日志，不能修改 raw。

已有本地转写结果时，使用 `scripts/ingest_audio_transcript.py` 导入。它支持 JSON、SRT、VTT、TXT 和 Markdown；`--raw-output` 可重复传入，用于一并保存 SRT/TXT 等附加原始输出。脚本只做读取、校验、复制和结构化转换，不会启动模型：

若要从录音直接转写，且本机已有 MLX Whisper/WhisperX 与缓存模型，使用：

```bash
<data-root>/.venv/bin/python <skill-dir>/scripts/transcribe_local_audio.py \
  --root <data-root> --audio <录音路径> --date YYYY-MM-DD
```

默认适配 MLX Whisper 和已缓存的 `mlx-community/whisper-large-v3-turbo-q4`，也可指定 `--model <本地模型目录或缓存ID>`。自动说话人分离用 `--engine whisperx --model <本地模型> --diarize --diarize-model <本地pyannote模型> --align-model <本地对齐模型>`；支持 `--min-speakers`/`--max-speakers` 提示人数。若不运行分离，WhisperX 默认只给片段时间戳，可选词级对齐。分离时必须对齐，导入器按词级标签拆分跨人的长片段，原始引擎 JSON 仍独立保存。包装脚本设置离线模型环境、保存原始 JSON 和文本视图，再调用现有导入脚本。没有缓存模型时会停止，不下载。pyannote 说话人标签是声学输出，候选人/面试官身份仍须确认。若引擎没有可校准置信度，记录 `null`，不得填伪造的 0–1 数字。

本机若把 WhisperX 安装在独立环境，命令需显式指定 `--engine-executable <data-root>/.venv-whisperx/bin/whisperx`。已验证的 Apple Silicon 组合为 Python 3.12、WhisperX 3.8.6、pyannote.audio 4.0.7、CTranslate2 Whisper 权重、中文 wav2vec2 对齐模型和 NLTK `punkt_tab`。pyannote `speaker-diarization-community-1` 是受限模型，首次下载须在 Hugging Face 页面接受模型条款，并在本机用 `hf auth login` 登录；令牌不可写入命令行参数、案例或聊天。模型下载完成后，日常转写使用已缓存的本地目录。若依赖版本或模型尚未齐备，应报告缺项，不把适配器存在等同于真实分离成功。

WhisperX 中文对齐还需本地 `punkt_tab` 句子分割数据。默认查找 `<data-root>/models/nltk_data`，其他位置用 `--nltk-data <目录>` 指定。失败时包装脚本把引擎日志保存在 `<data-root>/logs/`，仅报告本地路径；日志可能包含原始转写内容，不应上传或粘贴到聊天。

```bash
<python> <skill-dir>/scripts/ingest_audio_transcript.py \
  --root <data-root> \
  --audio <录音路径> \
  --transcript <主要原始转写文件> \
  --raw-output <附加原始输出> \
  --engine <引擎名> --model <模型名> --language <语言> \
  --company <公司> --role <岗位> --round <轮次> --date YYYY-MM-DD
```

- JSON 可为顶层 `segments` 数组、片段数组，或仅含 `text` 的对象；每段可含 `start`、`end`、`text`、`speaker`/`speaker_label`、`confidence`。
- 字幕时间必须可解析且整体单调递增；有 `end` 时必须同时有 `start`，且 `end >= start`。无时间戳的 TXT 仍可导入，但只按 `segment_id` 追溯。
- 完全相同的音频哈希视为重复导入；相同文字但不同音频不能静默合并。
- `diarization` 或 `channel_split` 只有在原始输出含说话人标签时才能声明；有可解释的模型置信度时用 `--speaker-confidence`，否则保存 `null` 并保留待确认状态。
- 常见音频格式优先通过本机 `ffprobe` 读取时长、采样率和声道；WAV 可由 Python 标准库读取。探测失败时保留 `null` 并给出警告，不猜测数值。

长音频可先做短片段质量与耗时检查，再执行完整转写。全程至少在开始、模型加载/识别阶段和完成时报告一次状态；进度数值只代表引擎处理进度，不代表实际对话占比。

## 3. 说话人处理

- 双声道不自动等于双人分轨。只有在声道确实分离、或 diarization 工具给出足够证据时，才能将角色标为 `confirmed`。
- 如无可靠分离，先抽取“提问开场”“候选人自我介绍”等少量代表片段，请用户确认角色锚点。
- 用户确认后可基于上下文生成 `semantic_role_assignment`，并在 `speaker-map.json` 记录 `method`、`confidence`、锚点证据与限制。它可用于草拟追问树，但短应答、插话、重叠语音仍标为待确认或低置信度。
- 用户提供的飞书等会议智能纪要若明确说明“说话人1/2”的身份，并包含可与原始转写对应的自我介绍、开场提问等锚点，可替代再次口头确认锚点；必须登记纪要快照，保留“AI 生成、可能不准确”的限制。只有原始转写的分段顺序与至少一组提问/回答锚点一致时，才可继续做语义角色分配。
- 语义角色分配用 `scripts/apply_semantic_speaker_roles.py --case <case_path> --assignment <assignment.json>` 写入。assignment 必须逐段给出 `segment_id` 和角色；无法确定的短应答、插话或重叠片段使用 `unknown`。脚本生成 `transcript.revised.json` 和修订日志，不覆盖 `transcript.raw.json`。
- 不得以“候选人/面试官”的推测身份生成履历、面试结论或负面评价。

## 4. 参考文字稿与修订

用户可能随后提供来自其他识别工具的文字稿。处理方式如下：

若材料是 ZIP，先列出成员并拒绝绝对路径、`..`、符号链接、超大或数量异常的成员；只提取用户指定或唯一的 Markdown 到临时目录。不要执行压缩包内文件，不要把图片 OCR 结果当成更高优先级证据。再按下述流程登记 Markdown 快照。

飞书 `docx/` 链接属于云文档，不等同于妙记原始转写；浏览器登录态与 API 凭证也是两套机制。偶发导入优先使用用户导出的 ZIP，保留原 ZIP 于私有目录并按上段只提取 Markdown。若用户要求长期自动同步，可另行接入[飞书新版文档 OpenAPI](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list)：先取得应用和文档的读取权限，再通过用户授权的 `user_access_token` 读取纯文本或文档块；应用身份的 `tenant_access_token` 不会自动获得个人文档访问权。凭证与刷新信息只能留在本机私有凭据存储，不能写入 Skill、档案 JSON、日志或公开仓库。API 读取结果仍需原样登记为二级证据，不替代原始转写。

1. 使用 `scripts/register_reference_transcript.py` 原样复制到 `private/` 下的哈希快照，并记录来源属性（例如“用户提供、无时间戳、无标签”）。同一哈希不会重复登记，不同版本不会覆盖旧快照。
2. 只用它核对专有名词、否定词、句子语义和漏识别内容；时间位置以带时间戳的原始音频转写为准。
3. 不因参考稿存在就覆盖 `transcript.raw.json`、虚构分段时间或认定说话人。发生实质修订时，在 `transcript.revised.json` 和修订日志记录原因、证据和影响的 `segment_id`。
4. 对被修订片段关联的 qa-tree、claims、scores、jd-matrix 和 profile-patch 重新核对。更正即使针对 `pending` 判断，也新增事件而非修改既有事件。

登记参考稿示例：

```bash
<python> <skill-dir>/scripts/register_reference_transcript.py \
  --case <case_path> --reference <参考稿路径> \
  --timestamp-coverage none --speaker-labels none
```

登记本身不会把参考稿映射到 `transcript.raw.json`；如校对后确实改变片段含义，仍须通过逐字稿修订流程生成 `transcript.revised.json` 和修订日志。

## 5. HTML 衔接

音频或参考稿引发逐字稿、说话人、claim、评分、JD 矩阵或档案事件变化后，必须按 `html-presentation.md` 重建所有场次和历史总览。不得只更新当前页面而留下过期的历史入口。

## 6. 旧场次：文字稿先导入，录音后补充

先核对 `manifest.inputs.transcript_original` 的规范化文本哈希是否等于 `transcript.raw.json.source_sha256`，再核对音频和本地转写原始输出的实际文件哈希。若原始逐字稿确实由文字稿生成，录音属于后补核对证据：在 manifest 将 `inputs.audio.role` 记为 `supplemental_reference`，补齐顶层 `transcription`，并用 `legacy_audio_migration` 记录不可变原始文件和转写输出哈希。不得为通过校验给 `transcript.raw.json` 伪造 `source_kind=local_audio_transcription` 或 `audio_sha256`。

用 `scripts/migrate_legacy_audio.py --case <case_path>` 执行此类迁移。原文字稿若有时间戳回退，先逐项人工核对，再用 `--timeline-exception <前一片段ID> <当前片段ID> <原因>` 精确登记；脚本拒绝未登记或与实际不符的回退。原时间、录音、原始转写及 `transcript.raw.json` 均不修改。迁移后运行 `validate_case.py --stage analysis`，再重建全部 HTML 并校验展示页。
