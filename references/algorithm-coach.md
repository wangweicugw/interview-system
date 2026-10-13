# 算法题陪练

使用场景：真实面试暴露算法弱项，或用户明确要求算法练习。这是现有面试复盘 Skill 的一个路线，不是独立 Skill 或 App。题库含原有 11 道离线题、第一周 5 道经典题及链表/树/网格 3 道扩展题；不是外部平台完整镜像。动态周计划、历史扫描、口述反馈和报告导出见 [algorithm-advanced.md](algorithm-advanced.md)。

## 教学协议

每次只推进一个动作，并记录阶段：题意理解 → 独立思考 → 优化 → 独立编码 → 代码复盘 → 面试官追问。先让用户脱稿回答，再反馈。不要默认扫描所有历史材料；只有用户明确要求关联真实面试知识缺口时，才写入来源标识。

独立思考阶段实行严格防泄题：

1. 题目标题、题面、约束、公开样例、追问和代码骨架均不得出现算法名称、解法类别、目标复杂度或策略性变量/注释。
2. 不暗示用于求解的数据结构或关键机制，例如 HashMap、HashSet、栈、队列、双指针、滑动窗口、动态规划状态、搜索顺序等。题目本身给定的数组、链表、树、图等输入结构和必要接口签名不算解题提示。
3. 用户提出低效方案时，先让其说明完整步骤，并用正常样例与边界样例检查正确性；正确性未厘清前不主动比较更优方案。
4. 用户卡住时先问如何验证题意、当前方案和失败边界。只有用户明确继续请求提示，才逐级增加信息量；提示调用不得由系统自动触发。
5. 只有用户主动说出某个算法、数据结构或优化方向后，才围绕该方向讨论实现、复杂度和优化空间；记录的 `concept` 必须来自用户原话，不得由系统代填。
6. 优先使用开放式问题，不给候选算法、数据结构或复杂度选项，不让用户靠排除法猜答案。

开始题目时可以展示中性的题目名称、题面、约束、公开样例和必要方法签名，但必须隐藏内部 `topic/topic_key`、参考答案、提示、目标复杂度及策略标签。恢复会话或查看进度时同样不能把内部主题标签重新泄露给仍处于独立思考阶段的用户。

提示严格逐级：

- L0：不提示，只让用户复述题意并提出自己的方案。
- L1：只要求用样例和边界验证用户当前方案，不缩小到某种算法或数据结构。
- L2：用户再次明确要提示后，才给方向性线索；可以逐步指出某类技巧或数据结构。
- L3：用户继续明确要提示后，才给伪代码或策略骨架，保留关键实现。
- L4：完整参考答案。必须先告知会结束引导模式，并在用户确认后运行 `reveal --confirm`；该题标记为 `assisted`。

每次 `hint` 只升一级。用户没有请求提示时，不因沉默、错误或方案低效而自动调用。L1-L4 均计为使用提示；只有全程 L0 且真实用例通过，才可能标为 `independent_passed`。

常用命令：

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> today
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> start --problem 1 --language java
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> record --session <ID> --stage independent_thinking
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> hint --session <ID>
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> resume
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> progress
```

`record` 可重复使用 `--concept` 和 `--mistake`，但只记录用户实际表达或代码已证实的问题。`today` 默认 60 分钟，优先到期复习。第一周为：1 两数之和、217 存在重复元素、283 移动零、26 删除有序数组重复项、125 验证回文串；第 6 天复习，第 7 天模拟。通过后的间隔为 D+1、D+3、D+7；失败、使用 L3/L4 或仍需帮助时回到 D+1。

## Java 检查与真实测试

第一周题使用真实 LeetCode 风格方法签名。静态检查：

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> inspect-java --session <ID>
```

静态检查只能报告候选语法、逻辑和边界问题，返回 `actual_tested=false`，不得称为编译通过或用例通过。实际测试：

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> submit \
  --session <ID> --explanation <思路与复杂度>
```

默认 Docker 禁网络、只读根目录、只挂载单次解答与桥接文件，并限制 CPU、内存、进程、输出和时间。镜像必须已缓存，不自动拉取。只有用户代码已审阅并明确选择本机执行时，才用 `--runner local --trust-solution`；本机模式不是安全隔离。无实际执行证据时不能把题目标为 `independent_passed`。

## 进度、复习与知识缺口

`algorithms/progress.json` 保存当前会话、阶段、掌握度、提示级别、错误分类、复习日期及来源标识。每次提交另在会话目录追加源码快照、哈希和真实用例结果。`resume` 跨会话恢复到 `pending_action`；`today` 优先选择 `next_review_date` 已到期的题目。

只有用户明确要求关联时执行：

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> link-gap \
  --problem 1 --case <case_id> --question <question_id> \
  --knowledge-key <知识点键> --source-path <相对路径>
```

进度记录与长期档案共用稳定事件键 `algorithm:<problem_id>`，并以 `case_id`、`question_id`、知识点键和相对路径互相引用；不得复制完整逐字稿或敏感项目原文。`algorithm_weakness` 仍为 `pending`，测试通过不自动确认长期能力。

## 重置与删除

以下操作不可恢复，必须由用户明确要求并使用完整确认词：

```bash
# 仅删除 progress.json，保留会话和提交快照
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> reset \
  --scope progress --confirm RESET_ALGORITHM_PROGRESS

# 删除 algorithms/ 下全部学习记录
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> reset \
  --scope all --confirm DELETE_ALGORITHM_TRAINING
```
