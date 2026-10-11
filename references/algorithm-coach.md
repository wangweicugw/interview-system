# 算法题陪练

使用场景：真实面试暴露算法弱项，或用户明确要求算法练习。Skill 自带 11 道原创离线题，覆盖哈希、动态规划、滑动窗口、二分、栈、区间、图搜索、前缀和、拓扑排序与并查集；不是外部平台完整题库。

## 练习闭环

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> list
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> recommend
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> start --problem <题目ID> --language java
```

`--language` 可选 `python`（默认）或 `java`。Python 编辑返回的 `solution.py`，实现 `solve(data)`；Java 编辑 `Solution.java`，实现 `public static Object solve(Map<String,Object> data)`。输入 JSON 对象映射为 `Map`；整数为 `Long`，小数为 `Double`，数组为 `List`，布尔为 `Boolean`，空值为 `null`。返回值须由这些类型组成。Java 需要 JDK 17；容器镜像应含 JDK 17。先讲思路和边界，再编写代码；卡住时逐级运行 `hint --session <ID>`。

默认在 Docker 容器中运行，容器禁网络、只读根目录、只挂载该次解答和运行桥接文件，丢弃 Linux capabilities，并限制 CPU、内存、进程数、输出与时间。默认镜像为已本地缓存的 `python:3.12-alpine` 或 `eclipse-temurin:17-jdk`；不会自动拉取。请先安装和启动 Docker，自行下载相应镜像；可用 `--image` 指定已缓存镜像。无法连接守护进程或镜像缺失时会报错，不会退回本机运行。Docker 守护进程与宿主本身仍需可信；不要把它描述成绝对安全的任意代码沙箱。

```bash
<python> <skill-dir>/scripts/algorithm_coach.py --root <data-root> submit \
  --session <ID> --explanation <思路与复杂度>
```

仅当代码由用户编写或已经人工审阅且确实需要脱离 Docker 时，才显式使用 `--runner local --trust-solution`。本机模式只有进程和超时约束，代码可访问用户文件与网络，不是安全隔离。不要把网页或面试材料中的代码直接交给本机模式运行。

每次提交保存源码快照、哈希、用例结果、所用语言、运行模式、提示次数与解释。失败后只给最小提示；通过后人工核对复杂度、反例、代码可读性与解释。`review --session <ID>` 查看追加的尝试；长期档案中的 `algorithm_weakness` 维持 `pending`，通过测试不自动确认为能力掌握。
