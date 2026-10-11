# 产物数据契约

所有 JSON 使用 `schema_version: 1`。ID 在同一场次内唯一，引用必须指向实际存在的对象。

使用已有数据目录前先运行 `scripts/check_data_compatibility.py --root <data-root>`。检查器只读版本号和文件结构，不迁移、不覆盖数据。缺少版本号、遇到其他版本或无法读取的核心 JSON 时停止写入；不能通过把 `schema_version` 改成 1 来伪造迁移。若仅派生分析产物损坏，可先用 `--core-only` 确认原始证据仍兼容，再从证据重新生成分析；修复前不能归档或渲染。只有明确写出源版本、目标版本和字段转换规则并经过验收的迁移脚本，才能处理后续格式升级。当前仍只有 v1；`migrate_legacy_audio.py` 处理的是 v1 内旧音频元数据，不是通用 schema 升级。

## manifest.json 的分析状态

`analysis_status` 取值为 `not_started`、`current` 或 `stale`。导入或没有分析产物时为 `not_started`；分析产物基于当前逐字稿和角色映射完成并通过校验时为 `current`；逐字稿修订或角色确认改变后为 `stale`。`stale` 只允许保留旧产物作历史对照，不得作为当前证据、长期档案输入或展示件输入。
通过校验时，manifest 的 `analysis_fingerprint` 记录本次证据、上下文和分析文件的 SHA-256；失效时记录 `analysis_invalidated_at_ns` 与 `analysis_stale_outputs`。这些字段用于阻止旧产物直接恢复及校验后静默改写，不证明结论在语义上已正确重算。旧场次缺少失效标记时需重新标记后重算。
公司、岗位或轮次等上下文变更也会将已有分析标为 `stale`；结果和 HR 反馈属于独立的历史事件，不触发技术分析失效。

## transcript.raw.json

文稿和音频路线共用片段结构：每段至少包含 `segment_id`、`start`、`end`、`speaker_id`、`speaker_label`、`role`、`text_raw` 和 `text_corrected`。没有可靠时间戳时 `start/end` 为 `null`；不得补造。

音频路线额外包含：

```json
{
  "schema_version": 1,
  "case_id": "int-...",
  "source_kind": "local_audio_transcription",
  "source_sha256": "...",
  "audio_sha256": "...",
  "segments": []
}
```

音频 case 的 `manifest.json` 必须在 `inputs.audio` 记录 case 内原始音频相对路径、SHA-256、格式、时长、采样率、声道、探测方法和 `processing: local_only`；在 `transcription` 记录引擎、模型、语言、执行/导入时间、原始输出、提示词文件及说话人方法。音频、原始转写和提示词路径必须位于 `private/`。
`speaker_confidence` 可为 0–1 数值或 `null`。WhisperX/pyannote 没有提供可校准的场次级置信度时使用 `null`，不得编造。匿名 `SPEAKER_00` 等标签并不表示候选人或面试官身份。

旧场次若由文字稿先生成 `transcript.raw.json`，后来才附加本地录音，`inputs.audio.role` 记为 `supplemental_reference`。此时 `raw.source_sha256` 必须仍对应原文字稿的规范化文本哈希，不能补写音频来源字段。`legacy_audio_migration` 需保存原始逐字稿、原文字稿、原始识别输出和辅助声学证据的 SHA-256；时间戳回退只可登记精确的相邻片段 ID、原始时间及原因，不得静默修正。验证器按文字稿来源和补充音频两个证据链分别校验。

PDF 输入的逐页提取方法记录在 `manifest.inputs.transcript_pdf_pages` 或 `inputs.jd/resume.pdf_pages`：每项含 `page`、`method`（`text_layer`、`apple_vision` 或 `tesseract:<语言>`）及 `confidence_floor`（无法提供时为 `null`）。OCR 文本需人工核对，原 PDF 保留在 `private/`。

## 参考文字稿登记

`manifest.inputs.reference_transcripts` 为按登记顺序排列的快照数组；每项包含 `original`、`sha256`、`registered_at`、`source_label`、`timestamp_coverage`、`speaker_labels` 和 `use`。路径必须位于 `private/`，覆盖值只能为 `none`、`partial` 或 `full`。`manifest.inputs.reference_transcript` 保留为指向最新快照的兼容字段。参考稿是校对证据，不是事实源，不得直接覆盖 `transcript.raw.json`。

## qa-tree.json

```json
{
  "schema_version": 1,
  "case_id": "int-...",
  "questions": [{
    "question_id": "q-001",
    "parent_id": null,
    "kind": "main",
    "category": "technical",
    "question": "...",
    "question_evidence": ["s-0001"],
    "answer_evidence": ["s-0002"],
    "relation_confidence": 1.0
  }],
  "candidate_questions": []
}
```

`kind` 为 `main` 或 `follow_up`。追问必须有有效 `parent_id`。

## claims.json

```json
{
  "schema_version": 1,
  "case_id": "int-...",
  "claims": [{
    "claim_id": "c-001",
    "question_id": "q-001",
    "claim": "...",
    "version_context": "MySQL 8.0",
    "verdict": "unverified",
    "evidence_segments": ["s-0002"],
    "sources": [],
    "explanation": "待联网核验"
  }]
}
```

来源字段：`title`、`section`、`url`、`accessed_at`、`relation`、`summary`。

## scores.json

```json
{
  "schema_version": 1,
  "case_id": "int-...",
  "questions": [{
    "question_id": "q-001",
    "dimensions": {
      "correctness": 3,
      "completeness": 2,
      "structure": 3,
      "clarity": 4
    },
    "total": 12,
    "evidence": ["s-0002"],
    "notes": "..."
  }],
  "overall": {
    "professional": 3,
    "credibility": 4,
    "structure": 3,
    "stability": 3
  }
}
```

## profile-patch.json

```json
{
  "schema_version": 1,
  "case_id": "int-...",
  "changes": [{
    "change_id": "knowledge-mysql-mvcc-001",
    "type": "knowledge_state",
    "key": "mysql/mvcc",
    "status": "pending",
    "evidence": ["s-0002"],
    "reason": "...",
    "value": {
      "grade": "B",
      "scores": {
        "accuracy": 4,
        "completeness": 3,
        "retrieval_stability": 4,
        "expression": 3
      }
    }
  }]
}
```

`type` 可为 `answer_version`、`knowledge_state`、`interview_pattern`、`algorithm_weakness`、`resume_risk`、`outcome`。`status` 为 `pending`、`confirmed`、`rejected`。

`outcome` 事件的 `value` 至少包含 `result` 和 `hr_feedback`；事件 `key` 使用 `interview/<case_id>/outcome`，同一场次的结果更新通过 `revision` 追加。它只表达历史结果事实，不作为技术能力或单题因果证据。

## HTML 派生产物

`interview-review.html`、根目录 `interview-history.html` 和 `interview-question-bank.html` 不属于事实数据契约。它们必须由上述 JSON、`review.md` 和逐字稿视图重新生成，不能被读取后反向修改结构化产物。

## 练习参考答案

每场可保存私有 `practice-answers.json`：`{"schema_version":1,"questions":[{"question_id":"q-001","reference_answer":{"scope":"适用前提","steps":[{"heading":"要点","body":"具体答法"}],"validation":"验收方式","sources":[{"title":"官方资料","url":"https://..."}],"code":"可选的 Java 示例"}}]}`。`question_id` 必须来自本场 `qa-tree.json`。参考答案是学习材料，不替代 `answer_evidence`、`scores.json` 或技术 claim 核验。

私有 `profile/practice-focus.json` 的重点题引用可附 `reference_answer` 和 `followup_answers`；后者按 `followups` 的顺序一题一答。渲染器优先使用重点题引用中的答案，其次使用单场 `practice-answers.json`。运行 `check_practice_answers.py --root <data-root> --scope focus|all` 检查所要求范围的原题与延伸追问是否全部有答案。

## 跨场成熟答案

私有 `profile/standard-answers.json` 保存对同一高频主题多次回答的归并结果：

```json
{
  "schema_version": 1,
  "answers": [{
    "answer_id": "self-introduction",
    "topic": "self-intro",
    "title": "自我介绍",
    "status": "pending",
    "basis": "跨场回答整理，待用户确认",
    "source_questions": [{"case_id": "int-...", "question_id": "q-001"}],
    "versions": {
      "short_30s": "30 秒版",
      "standard_2m": "2 分钟版",
      "followups": [{"question": "常见追问", "answer": "展开答法"}]
    },
    "limitations": ["缺少可核验规模数据"],
    "sources": [{"title": "官方资料", "url": "https://..."}]
  }]
}
```

`topic` 使用题库主题键；`status` 只能为 `pending`、`confirmed` 或 `rejected`。成熟答案必须同时提供 30 秒版和 2 分钟版。`source_questions` 只引用真实问答树，技术资料仅接受 HTTP(S) 链接。跨场整理不会自动把内容升级为已确认，也不替代逐场现场回答；被驳回版本保留修订记录但不作为题库推荐答案。

保存后运行 `<python> <skill-dir>/scripts/check_standard_answers.py --root <data-root>`；校验器检查答案 ID 唯一性、主题合法性、版本完整性、真实问题引用及来源 URL。同一主题可以保存多份不同高频问法的成熟答案。文件存在但校验失败时，HTML 渲染器拒绝重建，避免静默丢失成熟答案。

用户明确确认或撤回确认时，使用 `set_standard_answer_status.py` 修改状态。脚本在答案中记录 `reviewed_at`，并向私有 `profile/standard-answer-revisions.jsonl` 追加原状态、新状态、原因和时间；不得覆盖旧修订。通过确认中心修改内容时，修订记录还必须保存 `edited_fields`、`before` 和 `after` 完整快照。

## 跨场重复问法

私有 `profile/question-clusters.json` 保存语义相同或面试意图相近的真实问题引用：

```json
{
  "schema_version": 1,
  "clusters": [{
    "cluster_id": "self-introduction",
    "label": "自我介绍与近年职责",
    "topic": "self-intro",
    "status": "pending",
    "note": "归并直接要求介绍个人经历和职责的问题。",
    "questions": [
      {"case_id": "int-...", "question_id": "q-001"},
      {"case_id": "int-...", "question_id": "q-001"}
    ]
  }]
}
```

每组至少覆盖两场面试；同一道题只能进入一个主聚类，避免重复计算频率。`status` 为 `pending`、`confirmed` 或 `rejected`，AI 新建分组默认为 `pending`。被驳回分组保留修订历史但不参与题库频率。运行 `<python> <skill-dir>/scripts/check_question_clusters.py --root <data-root>` 检查引用、跨场覆盖和重复归类；文件存在但失败时 HTML 渲染器拒绝重建。

## 离线确认操作

题库导出的操作文件为 `{"schema_version":1,"generated_at":"...","actions":[...]}`。支持：

- `standard_answer`：包含 `id`、`expected_status`、`status`、`reason`，可附 `patch.title`、`patch.basis`、`patch.short_30s`、`patch.standard_2m`、`patch.followups`、`patch.limitations`、`patch.sources` 和 `patch.source_questions`。
- `question_cluster`：包含相同的状态与原因字段，可附 `patch.label`、`patch.note` 和 `patch.questions`。
- `profile_event`：包含相同的状态与原因字段。
- `practice_attempt`：包含 `case_id`、`question_id`、`answer`、`duration_seconds`、`self_rating` 和可选 `note`。

`apply_review_actions.py` 在任何写入前，会在临时目录同时校验成熟答案和高频分组；任一状态、字段或真实问题引用不合法时，两个正式 JSON 都不得改变。状态只能为 `pending`、`confirmed`、`rejected`。练习正文不进入结构化事实源，而是保存到 `practice/private/<attempt_id>.txt`；`practice/attempts.jsonl` 只保存题目引用、时长、自评、哈希和私有路径。

## 派生审计与复习计划

- `profile/question-cluster-candidates.json`：新增问题的相似候选、分数、置信等级和 `requires_review=true`。不是事实源。
- `profile/standard-answer-audit.json`：聚类覆盖、新问题引用、现场回答摘录和数字差异线索。数字不同不自动等于冲突。
- `profile/review-plan.json`：确定性复习队列与分主题评分快照，包含方法说明、优先分和原因。它不是能力认证。
- `profile/reference-health.json`：官方资料访问日期、是否超过复核期限及可选的在线可达性结果。默认离线，不发请求。

## next-interview-prep.json

这是由 `build_next_prep.py` 从既有 case 与 `profile/state.json` 生成的派生快照，包含 `schema_version`、`generated_at`、`source_case`、`requirements`、`profile.confirmed`、`profile.pending`、`profile.rejected_count` 和 `actions`。它不新增事实，不把 `pending` 自动升级为 `confirmed`；预测性追问不属于该脚本的输出。

## jd-matrix.json

每项记录 `requirement`、`coverage`（`not_asked/shallow/deep`）、`performance`（`unverified/strong/medium/weak`）、`evidence_questions`、`next_priority`（`high/medium/low`）和 `inference_note`。

这些是 JSON 的稳定枚举值。展示时分别转换为中文：覆盖＝未考察／浅度涉及／深入考察；表现＝未核验／表现较强／表现一般／表现较弱；优先级＝高／中／低。历史 JSON 无需改写。

## 算法陪练

`algorithms/sessions/<session_id>/session.json` 保存题目、开始时间、语言（`python` 或 `java`）、已使用提示层级和用户解答路径；旧会话缺少语言时按 `python` 读取。`attempts.jsonl` 追加每次提交的源码哈希、用例结果、语言、运行方式（`docker` 或显式信任的 `local`）、解释和 `human_review_status=pending`。解答快照位于 `algorithms/private/`。测试通过不自动确认为能力掌握；长期档案的 `algorithm_weakness` 事件仍需人工复核。
