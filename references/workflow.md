# 文稿复盘工作流

## 输入

- 必需：面试文稿，支持粘贴后的 TXT/Markdown、TXT、Markdown、DOCX。
- 可选：当时简历、JD、公司、岗位、轮次、日期、结果和 HR 反馈。
- 缺简历或 JD 时继续处理，但在报告中明确哪些结论因此受限。

## 说话人

- 已明确标注“候选人 / 面试官 / HR”等角色时直接采用。
- 多人面试保留独立身份，如 `interviewer-1`、`interviewer-2`，不要合并原始说话人。
- 不明确时读取 `speaker-map.json` 的代表片段，询问用户。未确认前可以生成草稿，但不能把角色相关结论标为已确认。

## 问题树

- 主树只包含面试官对候选人的考察问题。
- 追问挂到所属主问题下，可多级嵌套。
- 候选人反问单独保存；寒暄与流程确认只留在逐字稿。
- 每个问题和回答都引用 `segment_id`；有时间戳时再同时显示时间。
- “这里开始暴露问题”属于 AI 判断，不写进事实树。

## 报告顺序

1. 本场摘要：四维表现、正向信号、风险信号、待确认信号。
2. 问题与追问树。
3. 逐题复盘：事实、AI 判断、改进建议、技术核验。
4. JD 能力矩阵（有 JD 时）。
5. 答案版本候选：30 秒版、2 分钟版、追问展开。
6. 知识状态与训练任务。
7. 简历表述风险。
8. P0/P1/P2 行动清单，每项写可验证的完成条件。
9. 拟写入长期档案的变化及状态。
10. 默认生成离线 `interview-review.html`；逐题固定展示原始问题、候选人现场回答、回答问题（AI 判断）、推荐答案和相关文档。数据目录同时生成 `interview-history.html` 与可搜索的全量 `interview-question-bank.html`。缺少参考答案或核验来源时必须明确标注，不得自动补造。

用户要求“每题都有参考答案”时，确定覆盖范围后，把原题答案逐题写入对应 case 的 `practice-answers.json`，重点延伸追问的答案写入私有 `profile/practice-focus.json`。技术答案先核验来源；经历题只给基于已确认材料的答题结构，未知职责或数字留“待补充”；算法题的 Java 示例须编译和运行边界测试。生成 HTML 前运行 `check_practice_answers.py --root <data-root> --scope focus|all`，只有所选范围无缺项才宣称完成。参考答案与当场回答始终分开展示。

## 修订

- `transcript.raw.json` 永不修改。
- 分析时优先读取 `transcript.revised.json`，不存在时才读取原始 JSON。
- `transcript.md` 是可编辑视图；同步后生成 `transcript.revised.json` 与 `transcript-revisions.jsonl`。
- 普通错别字修订可不填原因；大幅改写或删除原话必须说明。
- 修订影响到引用片段时，将相关分析产物视为待重算，不静默沿用旧结论；角色映射确认也会触发同样的失效标记。
- `manifest.analysis_status` 为 `stale` 时，旧的追问树、claim、评分、档案 patch、JD 矩阵和报告不能作为当前证据；必须基于最新修订稿和角色映射重算，再用 `validate_case.py --stage analysis --refresh-analysis` 通过完整校验，状态才恢复为 `current`。校验器检查文件是否在失效后重写且内容有变化，但仍需人工核对分析含义。
- 修改公司、岗位或轮次会使已有分析同样失效；结果和 HR 反馈只追加 `outcome` 事件，不改变技术分析状态。
- `record_profile_patch.py` 只接受 `analysis_status=current`；`render_interview_html.py` 遇到 `stale` 场次会停止渲染并保留旧 HTML 不动。
- 重算并记录长期档案后，按 `html-presentation.md` 重建全部 HTML，再运行 presentation 校验。

## 结果反馈

- 状态允许：通过、未通过、进入下一轮、无结果。
- HR 原话单独保存。
- 结果只参与相关性分析，不反向修改技术正确性，也不推断单题因果。
- 使用 `update_case.py` 更新结果或 HR 反馈时，自动追加 `type=outcome` 的长期档案事件；后续修订追加新 revision，不覆盖旧事件。
- 结果更新后必须重建 HTML，使当前页和历史总览同时显示新状态。
