# 长期档案与下一场准备

## 状态

所有变更进入统一档案：

- `pending`：系统生成、尚未人工确认。
- `confirmed`：用户确认或补充过。
- `rejected`：保留历史，但不得作为有效依据。

系统不得把 pending 自动升级为 confirmed。修改通过追加修订实现，不抹掉旧版本。

## 答案版本

- 不同场次的同类问题可以自动提出归并，但不确定时分开保存。
- 每次回答新增版本，不覆盖旧版本；最新版本不自动成为推荐答案。
- 推荐答案依据事实准确、表达完整、真实经历证据和确认状态共同确定。
- 成熟答案保存 30 秒版、2 分钟版和追问展开。
- 缺少真实职责或数据时写“待补充”，不得编造。
- 多次同类回答归并后写入 `profile/standard-answers.json`，绑定真实场次与问题 ID；未经用户确认保持 `pending`。
- 归并时不是简单拼接：先提取各场稳定主线，再列出矛盾、缺口和未经核验数字；30 秒版只保留定位、主线和价值，2 分钟版再展开阶段、职责与结果，追问答案用于解释取舍和责任边界。
- 保存后运行 `check_standard_answers.py --root <data-root>`，通过后再重建题库。
- 高频统计分两层：主题频率只表示某领域被问及的场次；真实问法频率来自 `profile/question-clusters.json`，按相同面试意图人工或 AI 归并并绑定原题。AI 分组保持 `pending`，不得把关键词命中直接当作语义等价。
- 用户明确确认某份成熟答案后，运行 `set_standard_answer_status.py --root <data-root> --answer-id <id> --status confirmed --reason <原因>`；脚本追加 `profile/standard-answer-revisions.jsonl`，不能直接手改状态绕过修订记录。
- 独立待确认页 `interview-pending-review.html` 可离线排队 `standard_answer`、`question_cluster` 和 `profile_event` 操作；默认只展开最多 5 组最高频的 `question_cluster`，按覆盖场次和原题数排序。页面必须明确说明：确认高频问题是在确认组内原题是否询问同一核心问题，不是在确认答案。成熟答案是多场回答归并后的参考答法；长期档案项在页面上称为“AI 给你的复习提醒”，并说明它们是根据面试记录整理的复习、追问和简历表述提醒，不是已确认结论。每条提醒尽量同时显示原始问题、候选人现场回答和 AI 提醒；无法可靠定位时明确标注，不得编造。后二者默认折叠并说明本轮可以跳过，不能把所有 `pending` 都表述成必须立即确认的任务。题库页的练习组件继续排队 `practice_attempt`。浏览器不能直接改写事实源；导出的 JSON 必须交给 `apply_review_actions.py`，它校验页面打开时的旧状态，防止用过期页面覆盖新修订。同机使用时，用户导出后只需说“应用刚导出的审核结果”，无需手工上传文件。
- 新场次导入后运行 `refresh_question_clusters.py`。默认只写 `profile/question-cluster-candidates.json`；`--apply-high-confidence` 也只会追加高阈值候选，并把受影响聚类降回 `pending`。
- 运行 `audit_standard_answers.py --reset-confirmed-stale` 对比聚类和成熟答案。新问题未被答案引用时，已确认答案回到待确认；现场回答中的数字只作为冲突线索，不自动判断真假。

## 历史分析

- 比较重复问题、知识状态变化、经常被深挖的项目和简历表述风险。
- 同一公司多轮单独形成趋势；不同岗位不直接比较总分。
- 面试结果只做相关性分析，不推断单一失败原因。
- 数据目录下的 `interview-history.html` 是面试统一总览，包含历史场次和全量真实题库；`interview-question-bank.html` 仅作为兼容入口跳回总览题库，`interview-pending-review.html` 是待确认审核入口；每场 `interview-review.html` 也必须嵌入可搜索的历史列表并链接统一总览与待确认页。
- `rejected` 事件可供追溯但不得作为当前能力依据；`pending` 必须显示状态，不能与 `confirmed` 混用。

## 准备下一场

默认输出：

1. 新 JD 能力矩阵。
2. 可直接复用的 confirmed 成熟答案。
3. pending 或存在风险的答案。
4. 上一轮未覆盖的 JD 能力。
5. P0/P1 行动清单。
6. 预测性追问树，并明确标记为 AI 推测。

已有一场含 `jd-matrix.json` 的复盘后，可生成确定性准备简报：

```bash
<python> <skill-dir>/scripts/build_next_prep.py \
  --root <data-root> [--case <case_path>]
```

输出 `profile/next-interview-prep.md` 和同名 JSON。它按 `next_priority`、覆盖程度和表现状态整理重点，并只把 `confirmed` 资产列为可直接复用；`pending` 会保留状态，`rejected` 不作为当前能力依据。它不能替代新的 JD 分析，也不会生成通过率或因果判断。
Markdown 和 HTML 中的覆盖、表现、优先级与档案状态使用中文标签；JSON 保留稳定枚举值供校验与后续汇总使用。

## 针对知识点继续追问

- 从真实题目、当场追问、逐题评分备注及 JD 要求定位一个具体知识点。先问一道，不一次给出整套答案；用户回答后再依据其实际回答追问原理、失效边界、证据或测试，直到能验证理解与落地能力。需要时可让用户运行 Java 代码和边界测试。
- 将问答树里已有的追问称为“问答树记录的追问”，并保留其证据限制；新编的问题称为“延伸练习题，非面试原话”。不要把自己构造的情境、数字、约束写成历史事实。
- 每轮反馈指出回答中已覆盖的点、尚缺的证据和下一道追问。技术事实不确定时先核验官方资料或明确保留待核验，不用猜测的标准答案给用户扣分。
- 用户希望离线自练时，把重点题的 `knowledge_point` 和 2–3 道递进 `followups` 存在私有 `profile/practice-focus.json`，按 [HTML 展示规则](html-presentation.md) 重建页面；原始问答树与逐字稿保持不变。
- 用户直接要答案时给出参考答法与适用前提；需要写入页面时只更新私有练习配置和派生 HTML，明确区分原始面试回答、AI 参考答案与官方技术依据。
- 题库卡可计时、记录脱稿回答和 1–5 自评。操作应用后，正文写入 `practice/private/`，元数据追加到 `practice/attempts.jsonl`；不得把自评当成掌握证明。
- 运行 `build_review_plan.py` 生成 `profile/review-plan.json` 和 Markdown。排序综合跨场频率、本场失分、JD 高优先级、是否到复习日和最近自评；页面显示的是复习建议和趋势快照，不跨岗位排名。
