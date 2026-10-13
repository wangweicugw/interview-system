# 算法陪练进阶能力

只有用户明确要求周计划、历史关联、口述反馈或报告导出时才读取本文件。所有分析均在本地完成，不创建独立 App、后台任务或第三方账户。

## 动态周计划

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> week-plan \
  --week-start YYYY-MM-DD [--save]
```

计划只依据实际持久化记录：独立通过率高且重复错误少时把新题提高到 6 道；独立通过率低于 40% 或重复错误较多时降到 3 道；其他情况维持 5 道。到期题和未独立掌握的薄弱题优先。`--save` 写入 `algorithms/plans/week-<日期>.json`。计划是建议，不是后台日程或能力认证。
保存后，普通 `today --on-date YYYY-MM-DD` 会读取当天任务；已到期复习仍覆盖计划中的新题优先级。

## 历史算法考点候选

```bash
# 只读扫描，只返回来源 ID、命中词和候选题
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> scan-gaps

# 用户确认后才建立学习记录与知识缺口链接
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> scan-gaps --apply
```

扫描范围仅为本地 `interviews/*/qa-tree.json`；失效分析和符号链接会跳过。关键词命中只是候选，不证明原问题与题目完全等价。输出和进度只保存 `case_id`、`question_id`、相对路径、知识点键及命中词，不复制问题或回答原文。重复执行 `--apply` 不追加重复链接。

## 口述思路反馈

先用现有本地音频流程得到 TXT、Markdown 或 DOCX 转写，再运行：

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> oral-review \
  --session <ID> --transcript <本地转写文件>
```

工具检查是否覆盖核心思路、复杂度、边界和题目专项要点。它使用明确标注的关键词启发式：`semantic_correctness_verified=false`，不会因为术语出现就提升掌握度。原转写快照留在该会话的 `algorithms/private/`，反馈日志只保存哈希、覆盖项和缺项，不保存全文。

## 报告导出

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> export \
  --kind mistakes|weekly|cram --output <本地Markdown路径>
```

- `mistakes`：按题汇总错误类别、次数和复习日期。
- `weekly`：汇总指定周的真实提交与当前薄弱点；可加 `--week-start YYYY-MM-DD`。
- `cram`：按到期、掌握度和重复错误生成面试前清单。

默认拒绝覆盖已有文件；用户明确要求后加 `--force`。报告不复制面试问题或逐字稿，仅显示来源 ID。

## Java 扩展范围

第一周数组/字符串题之外，桥接器还支持基本类型、多维数组、`char[][]`、`ListNode` 和 `TreeNode`，内置 206 反转链表、104 二叉树最大深度、200 岛屿数量。用户代码自行声明 `ListNode`/`TreeNode` 时，桥接器会停用对应内置声明以避免重名。

这不是任意 LeetCode 类型系统：图节点、自定义泛型对象、交互题、并发题和需要外部库的题仍需专门适配。静态或口述分析不能代替真实编译测试。
