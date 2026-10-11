---
name: interview-system
display_name: 个人面试系统
description: Review real interviews from transcripts or local recordings, verify technical claims, and maintain a traceable personal interview archive.
description_zh: 复盘真实面试、核验技术回答，并沉淀可追溯的个人长期能力档案。
description_en: Review real interviews, verify technical answers, and maintain a traceable long-term interview archive.
version: 1.0.0
author: local
---

# 个人面试系统

将一场真实面试转化为可追溯、可核验、可复用的长期资产。适用于用户提供文稿、本地录音、JD、简历、反馈或要求准备下一轮的场景；不用于泛化模拟面试或通过率预测。

## 使用方式

先确认用户要处理真实面试材料，并让用户选择本地数据目录。没有明确授权时，不访问、复制、上传或删除材料。

- 导入或复盘面试：读 @references/workflow.md、@references/scoring.md、@references/html-presentation.md；涉及技术结论时再读 @references/fact-checking.md。
- 修订逐字稿、补充材料或更新面试结果：读 @references/workflow.md，并按 @references/html-presentation.md 重建全部 HTML。
- 查看能力档案或准备下一轮：读 @references/profile-and-prep.md；历史统一入口为数据目录下的 `interview-history.html`。
- 创建或校验 JSON 产物：读 @references/schemas.md，并按本机可用 Python 运行 `scripts/` 中的校验脚本。
- 用户明确要求本地录音转写或用参考稿校对：读 @references/audio-and-html.md，再执行文稿闭环。HTML 是所有路线的默认交付，不需要另行触发。
- 扫描 PDF：读 @references/pdf-ocr.md；算法题陪练：读 @references/algorithm-coach.md。

## 共同约束

- 原始文稿、原始音频和 `transcript.raw.json` 不可覆盖；修订必须独立保存并记录原因。
- 没有时间戳就用 `segment_id`，不得推造时间；文件创建时间和音频时长不能推断面试时间。
- 说话人不明确时先请用户确认代表片段。语义角色分配不等于自动说话人分离，短应答、重叠语音或混合片段不能当作已确认事实。
- 角色确认或逐字稿修订会将已有分析产物标记为 `stale`；必须重算追问树、claim、评分、档案 patch、JD 矩阵和报告，再用 `validate_case.py --stage analysis --refresh-analysis` 通过校验后才能归档或生成 HTML。
- 无时间戳、无标签的参考文字稿只用于核对术语和语义，不能覆盖原始转写、补造时间戳或确认为说话人映射。
- 技术结论拆为原子 claim；无可靠来源标记 `unverified`。输出必须区分已确认事实、AI 判断、改进建议与技术核验。
- 不上传录音、完整逐字稿、简历或未公开项目资料到第三方。使用本地引擎、安装依赖或下载模型前，说明影响并取得用户授权。
- 长期档案只能追加事件；待确认状态为 `pending`，不能被写成已确认能力或事实。
- 不编造经历、数字、面试官意图或通过率预测。

## 本地运行前提

脚本需要宿主提供 Python 与本地文件读写权限。执行前检查运行时、目标目录与依赖；PDF OCR 需要 `pypdf`、`pdftoppm` 和可用的 Apple Vision 或 Tesseract 中文数据。`transcribe_local_audio.py` 可调用宿主已有的 MLX Whisper 或 WhisperX/pyannote 与已缓存模型；不自动安装或下载。
处理已有数据目录前，先运行 `scripts/check_data_compatibility.py --root <data-root>`；核心数据不兼容时停止写入，不直接篡改版本号。仅派生分析产物损坏时可用 `--core-only` 确认原始证据，再重做分析。

已有本地 JSON/SRT/VTT/TXT 转写结果时，使用 `scripts/ingest_audio_transcript.py` 登记音频、引擎元数据和原始输出。该脚本不启动模型、不联网；音频、原始输出和提示词只写入 case 的 `private/`。声明 `diarization` 或 `channel_split` 时必须有原始说话人标签；无可校准置信度时记录 `null`。

用户补充参考文字稿时，使用 `scripts/register_reference_transcript.py` 登记哈希快照，并显式填写时间戳和说话人标签覆盖范围。参考稿不得覆盖 `transcript.raw.json`；只有确认校对结果后，才进入逐字稿修订流程。

准备下一场时，可用 `scripts/build_next_prep.py` 从含 JD 矩阵的场次和长期档案生成 Markdown/JSON 简报。它是确定性汇总，不生成通过率预测，也不把 pending 资产当作 confirmed。

更新结果或 HR 反馈时，`scripts/update_case.py` 会同步追加 `outcome` 档案事件；结果只用于历史相关性，不能解释为单题因果。

每次完成导入、复盘、修订或结果更新后，按 @references/html-presentation.md 生成不含远程资源的单文件 `interview-review.html` 和可搜索的 `interview-history.html`。它们只能作为结构化产物的展示视图，不能反向覆盖原始材料。
