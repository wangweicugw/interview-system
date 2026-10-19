# interview-system

[![tests](https://github.com/wangweicugw/interview-system/actions/workflows/tests.yml/badge.svg)](https://github.com/wangweicugw/interview-system/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

许可证：[MIT](LICENSE)。本仓库的许可证不授予第三方语音模型、Python 依赖或用户面试资料的再分发权利。

本地优先的面试复盘 Skill。它把逐字稿、JD、简历和可选的录音整理为可追溯的单场报告与长期档案。日常统一从本机服务进入，查看与保存都用同一个网址；生成的 HTML 可离线查看，但不作为日常入口。总览页直接展示一道练习题和已保存的参考答案，顶部只保留“练习”和“历史面试”；完整题库、答案目录和按需核对资料从对应内容进入。练习时可复制请求到当前对话，核对内容后发送给 AI 打磨。另提供算法题陪练、本地资料备份与安全恢复。完整工作规则见 [SKILL.md](SKILL.md)。

## 安装与检查

需要 Python 3.10 或更新版本。先下载或克隆本仓库，在仓库目录安装依赖并检查；验证后再复制到 Codex 的 Skills 目录。用户已反馈在 Windows 下载并试跑成功；具体测试范围和环境尚未记录，因此这里只将基础运行列为已有试用反馈，不把录音转写、扫描件 OCR 或所有宿主集成标为已验证。

macOS/Linux：

```bash
git clone https://github.com/wangweicugw/interview-system.git
cd interview-system
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/doctor.py
```

Windows PowerShell（在准备保存仓库的目录中运行；已下载时跳过前两行，直接进入目录）：

```powershell
git clone https://github.com/wangweicugw/interview-system.git
Set-Location interview-system
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe scripts\doctor.py
```

若 `py` 不可用，可用已安装的 Python 3.10+ 可执行文件替代。路径中有空格时，用引号包住路径参数；不要直接照搬文档中的 `/tmp`、`.venv/bin/python` 或 Bash 续行符 `\`。

验证通过后，把该目录复制到你的 Codex Skills 目录；也可以保留克隆目录并按本机配置建立链接。不要把私有数据目录放进此仓库。

`doctor.py` 只检查当前 Python 和本机命令是否具备候选能力，不读取面试资料或令牌；引擎存在不代表模型已经缓存。扫描版 PDF 还需要 `pdftoppm` 与 Apple Vision 或 Tesseract 中文语言数据。音频安装检查与短片段实测见 [本地语音向导](references/local-audio-setup.md)，转写与说话人分离规则见 [音频说明](references/audio-and-html.md)，扫描件见 [OCR 说明](references/pdf-ocr.md)。
`check_data_compatibility.py` 用于已初始化的数据目录；只读检查版本，不执行自动迁移。

## 虚构材料演示

指定一个不存在或为空的目录，即可生成可打开的单场报告和历史页：

```bash
.venv/bin/python scripts/demo_smoke.py --root /tmp/interview-system-demo
```

脚本只使用仓库内明确标注为虚构的材料；若目录已有内容会拒绝运行。上面的路径仅用于演示，不是个人档案的默认保存位置。它实际执行导入、结构化产物校验、长期档案更新和 HTML 生成，但不验证真实面试分析质量、语音识别或扫描件 OCR。输出 JSON 会给出单场复盘、历史总览和全量题库三个 HTML 的路径。

Windows PowerShell 演示（`--root` 须为不存在或空的目录）：

```powershell
.\.venv\Scripts\python.exe scripts\demo_smoke.py --root "$env:TEMP\interview-system-demo"
```

演示通过后，打开输出中的 `history_html`；基础流程跑通不代表可处理录音、扫描 PDF 或已完成真实面试内容核验。

## 最小用法

个人面试档案没有默认保存位置。首次建档前，请自行指定一个私有目录的绝对路径，并用它替换下面所有的 `<data-root>`；未指定时不创建档案或导入真实资料。已有档案继续使用先前明确选定的目录，不会自动迁移。此目录不能放进公开仓库。

```bash
.venv/bin/python scripts/init_store.py --root <data-root>
.venv/bin/python scripts/check_data_compatibility.py --root <data-root>
.venv/bin/python scripts/ingest_transcript.py \
  --root <data-root> --transcript <文稿路径> \
  --company <公司> --role <岗位> --round <轮次> --date YYYY-MM-DD \
  --jd <JD路径>
```

导入只建立原始证据和 case；分析报告还需按 [SKILL.md](SKILL.md) 的“文稿闭环”核验问题、评分和技术事实。

### 打开面试档案

在终端运行一次，`<data-root>` 必须是你已指定的私有档案绝对路径；浏览器会自动打开当前本机会话入口，查看和保存都从这里进入：

```bash
python3 scripts/local_answer_server.py --root <data-root> --open-browser
```

Windows PowerShell 用 `py -3 scripts/local_answer_server.py --root "C:\\你的私有档案目录" --open-browser`。若浏览器未自动打开，复制终端输出的本机网址。服务只绑定 `127.0.0.1`，关闭终端即停止；重启后应使用新网址，不收藏旧的令牌链接。先由 AI 打磨答案，再把选定的 30 秒版和 2 分钟版填入页面；核对事实后点击“保存并确认到档案”，每次点击成功都正式写入。编辑后需重新核对事实；看到成功提示才算写入。系统会校验旧版本、保留修订记录并重建页面；单场保存不会改动现场回答或跨场共用答案。

## 算法陪练

算法陪练是同一个面试复盘 Skill 的一条路线，支持逐级提示、Java 静态检查与真实测试、错误记录、跨会话恢复、D+1/D+3/D+7 复习及面试知识缺口关联。默认练习计划为 60 分钟；到期复习优先。

```bash
.venv/bin/python scripts/algorithm_coach.py --root <data-root> today
.venv/bin/python scripts/algorithm_coach.py --root <data-root> start --problem 1 --language java
.venv/bin/python scripts/algorithm_coach.py --root <data-root> resume
.venv/bin/python scripts/algorithm_coach.py --root <data-root> progress
```

数据保存在 `<data-root>/algorithms/`：`progress.json` 是进度快照，`sessions/` 是会话和追加尝试，`private/` 是源码快照。它与面试档案通过来源 ID 和 `algorithm_weakness` 事件关联，不复制完整面试原文。完整教学、运行、关联、重置和删除规则见 [算法陪练](references/algorithm-coach.md)。删除命令不可恢复，运行前先备份私有数据目录。

进阶能力包括基于实际表现的动态周计划、显式触发的历史考点扫描、已有本地转写的口述结构反馈、错题/周报/冲刺清单导出，以及链表、二叉树、二维字符网格 Java 测试。使用边界和命令见 [算法陪练进阶能力](references/algorithm-advanced.md)。

```bash
.venv/bin/python scripts/algorithm_coach.py --root <data-root> week-plan --week-start YYYY-MM-DD --save
.venv/bin/python scripts/algorithm_coach.py --root <data-root> scan-gaps
.venv/bin/python scripts/algorithm_coach.py --root <data-root> export --kind cram --output <本地Markdown路径>
```

## 数据与隐私

- 公开源码目录只放 Skill 代码、明确标注为虚构的测试夹具和说明。真实录音、逐字稿、JD、简历、报告、模型缓存与日志应位于独立的私有数据目录。`init_store.py` 会拒绝与 Skill 源码目录重叠的路径，也会拒绝在已配置远程的 Git 工作树中初始化数据。
- 默认不创建数据 Git 仓库。只有用户显式添加 `--init-git` 时才初始化，且默认忽略全部数据。`.gitignore` 不能阻止 `git add -f`，也不能清除已提交历史；不要给数据仓库配置远程地址。
- 本地文件处理和已缓存模型的本地 Whisper 可不联网，但这不意味后续 AI 复盘必然离线。Codex、WorkBuddy 或其他宿主如使用云端模型，被提供给模型的逐字稿、简历、JD 或项目细节可能离开用户设备，并受该宿主/模型服务的政策约束。Skill 无法仅靠配置字段验证或强制这些外部边界。
- 首次处理真实资料时，按 [隐私与数据传输边界](references/privacy-and-security.md) 选择 `local-only` 或 `cloud-assisted`，并明确授权的数据类别与用途。`local-only` 下不主动查询外部文档，云端代理不能执行需读取敏感原文的 AI 复盘；`cloud-assisted` 不自动包含录音上传或第三方转写授权。
- 技术事实核验需要联网时，只查询脱离候选人、公司和未公开项目语境的原子技术问题；不发送完整面试资料。模型下载、云同步、通知和第三方转写都是独立外部行为，需相应授权；本 Skill 不保存云端凭据。
- 备份必须显式选择一种模式：`--recipient <age公钥>` 或 `--passphrase` 生成 `age` 加密文件；只有明确添加 `--allow-plaintext` 才生成未加密 ZIP。脚本不安装 `age`，找不到可执行文件或加密失败时直接停止，绝不回退为明文。口令由 `age` 在终端交互读取，不放入命令参数、标准输入重定向或日志。恢复只允许到空目录，不自动删除旧备份；私钥或口令丢失时无法恢复。

```bash
python3 scripts/backup_store.py create --root <data-root> --output <backup.age> --recipient <age公钥>
python3 scripts/backup_store.py verify --backup <backup.age> --identity <私钥文件>
python3 scripts/backup_store.py restore --backup <backup.age> --identity <私钥文件> --target <空目录> --confirm-empty-target
```
- 向 GitHub 发布前运行 `python3 scripts/check_public_release.py --strict`。工具分别报告工作区、实际 Git 暂存区和当前本地可见历史的覆盖状态，只输出文件位置、风险类别和行号，不打印命中的密钥或个人文本。检查不代替专用密钥扫描和人工复核；若发现历史泄露，它不会自动改写历史或强制推送。

对私人数据目录做只读自检：

```bash
python3 scripts/privacy_check.py \
  --root <data-root> \
  --backup-path <要检查的备份文件或目录>
```

自检只查看明确指定的范围，检查源码重叠、Git 远程与跟踪状态、文件权限、符号链接、公开源码风险和明文 ZIP 备份；不打印逐字稿、简历或密钥正文，也不自动修改权限、Git 历史或文件。未指定的磁盘位置不会被扫描。

处理真实简历或录音前，先把 `<data-root>` 放在公开仓库和同步盘之外，收紧本机账号与目录权限，并确认使用 `local-only` 还是 `cloud-assisted`。删除个人档案属于不可逆操作：先验证加密备份确实可恢复，再通过操作系统废纸篓或文件管理器仅删除已核对的精确数据目录；本 Skill 不提供递归自动删除命令，也不会替你删除旧备份。若资料曾被推送到 GitHub，仅删除当前文件并不能清除历史；应立即撤销已泄露凭据、限制仓库访问，再按平台流程人工处理历史和副本。

当前限制：自检无法证明云端宿主的网络隔离、保留或训练政策，无法发现未授权扫描范围之外的备份，也不代替操作系统全盘加密、专用密钥扫描、恶意软件防护和人工发布复核。

## 统一发行包

项目不为 Codex、Claude、Trae、WorkBuddy 等软件分别维护适配包。以下命令直接打包仓库中的规范 `SKILL.md`、运行脚本、参考资料和资源，不改写入口：

```bash
.venv/bin/python scripts/build_release_package.py \
  --source . \
  --output /tmp/interview-system.zip
```

打包前会执行完整发布审计，并拒绝未列入显式清单的资源文件或符号链接；ZIP 内包含 `MANIFEST.json`，列出文件大小和 SHA-256，写出后会重新打开并复验文件清单、类型和哈希。默认拒绝覆盖已有输出，确认覆盖时添加 `--force`。发行包不包含测试、真实面试资料或本地数据目录。`agents/openai.yaml` 是可选宿主元数据，其他宿主可忽略；各软件的自动发现、资源引用和命令权限可能不同。只有经过实际安装与最小流程验收的宿主才能标为“已验证”，其余场景应直接读取 `SKILL.md` 使用，不能宣称原生兼容。

## 开发

```bash
.venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python scripts/check_public_release.py --privacy-only --strict
```

CI 用三个 Python 版本检查核心流程，并在 Linux 上安装 Poppler 与 Tesseract，单独验证扫描 PDF 的英文 OCR。Apple Vision、WhisperX/pyannote、本地模型和中文 OCR 仍需在具备对应依赖的机器上验收。发布检查器会在缺少 `LICENSE` 时阻止正式发布。

提交补丁前请阅读 [贡献指南](CONTRIBUTING.md)。安全问题不要公开提交包含真实面试资料的 Issue，处理方式见 [安全政策](SECURITY.md)。
