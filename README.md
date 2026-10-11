# interview-system

许可证：[MIT](LICENSE)。本仓库的许可证不授予第三方语音模型、Python 依赖或用户面试资料的再分发权利。

本地优先的面试复盘 Skill。它把逐字稿、JD、简历和可选的录音整理为可追溯的单场报告与长期档案。单场页逐题展示原始问题、现场回答、回答问题（AI 判断）、推荐答案和相关文档；全量题库页提供高频问法、搜索、确认中心、脱稿计时练习和复习计划。浏览器操作先导出本地 JSON，再由 Skill 校验并追加修订。也提供算法题陪练、本地资料备份与安全恢复。当前支持 Codex 入口和 WorkBuddy 适配包。完整工作规则见 [SKILL.md](SKILL.md)。

## 安装与检查

需要 Python 3.10 或更新版本。将本目录复制到 Codex 的 `skills/interview-system`，然后在你的环境安装基础依赖。以下命令以 macOS/Linux 为例；Windows 宿主尚未完成集成验收。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/doctor.py
```

`doctor.py` 只检查当前 Python 和本机命令是否具备候选能力，不读取面试资料或令牌；引擎存在不代表模型已经缓存。扫描版 PDF 还需要 `pdftoppm` 与 Apple Vision 或 Tesseract 中文语言数据。音频安装检查与短片段实测见 [本地语音向导](references/local-audio-setup.md)，转写与说话人分离规则见 [音频说明](references/audio-and-html.md)，扫描件见 [OCR 说明](references/pdf-ocr.md)。
`check_data_compatibility.py` 用于已初始化的数据目录；只读检查版本，不执行自动迁移。

## 虚构材料演示

指定一个不存在或为空的目录，即可生成可打开的单场报告和历史页：

```bash
.venv/bin/python scripts/demo_smoke.py --root /tmp/interview-system-demo
```

脚本只使用仓库内明确标注为虚构的材料；若目录已有内容会拒绝运行。它实际执行导入、结构化产物校验、长期档案更新和 HTML 生成，但不验证真实面试分析质量、语音识别或扫描件 OCR。输出 JSON 会给出单场复盘、历史总览和全量题库三个 HTML 的路径。

## 最小用法

下面的 `<data-root>` 是你自己选择的私有数据目录，请替换为绝对路径。此目录不能放进公开仓库。

```bash
.venv/bin/python scripts/init_store.py --root <data-root> --init-git
.venv/bin/python scripts/check_data_compatibility.py --root <data-root>
.venv/bin/python scripts/ingest_transcript.py \
  --root <data-root> --transcript <文稿路径> \
  --company <公司> --role <岗位> --round <轮次> --date YYYY-MM-DD \
  --jd <JD路径>
```

导入只建立原始证据和 case；分析报告还需按 [SKILL.md](SKILL.md) 的“文稿闭环”核验问题、评分和技术事实。算法陪练支持 Python/Java，默认在本地 Docker 容器中隔离运行；入口和审阅规则见 [算法陪练](references/algorithm-coach.md)。

## 隐私边界

- 公开源码目录只放 Skill 代码、匿名测试夹具和说明。真实录音、逐字稿、JD、简历、报告、模型缓存与日志留在独立的私有数据目录。
- 初始化数据目录时可创建仅本地的 Git 仓库；`.gitignore` 不是已提交历史的清除工具，也不能防止后来手动强制添加敏感文件。不要给该数据仓库配置远程地址。
- 本项目不会自动上传素材、下载模型或发送通知。技术事实核验需要联网时，只发送必要的技术问题，不发送完整面试资料。
- 定时提醒和跨设备同步由宿主或用户选择的服务承担；本 Skill 不保存云端凭据。本地备份使用未加密 ZIP、逐文件哈希校验并只恢复到空目录，模型和虚拟环境不打包。数据升级只执行显式注册并经过验收的逐版本迁移；当前正式 schema 仍为 v1。
- 向 GitHub 发布源码前，运行 `python3 scripts/check_public_release.py --strict`，并人工检查实际待提交文件与历史。该检查器只报告疑似问题的文件路径，不打印匹配到的密钥。

## 开发

```bash
.venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python scripts/check_public_release.py --privacy-only --strict
```

CI 用三个 Python 版本检查核心流程，并在 Linux 上安装 Poppler 与 Tesseract，单独验证扫描 PDF 的英文 OCR。Apple Vision、WhisperX/pyannote、本地模型和中文 OCR 仍需在具备对应依赖的机器上验收。发布检查器会在缺少 `LICENSE` 时阻止正式发布。

提交补丁前请阅读 [贡献指南](CONTRIBUTING.md)。安全问题不要公开提交包含真实面试资料的 Issue，处理方式见 [安全政策](SECURITY.md)。
