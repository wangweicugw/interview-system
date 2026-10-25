# 贡献指南

感谢你改进 interview-system。这个仓库只接受源码、匿名测试夹具和文档，不接受真实面试资料。

## 开发流程

1. 使用 Python 3.10 或更新版本创建虚拟环境。
2. 安装 `requirements.lock` 中的基础依赖。
3. 修改功能时同步增加或更新测试。
4. 克隆后启用仓库自带的推送门禁：`git config --local core.hooksPath .githooks`。推送前会检查工作区、暂存区和本地可见历史；不要用 `--no-verify` 绕过。
5. 提交前运行：

```bash
python -B -m unittest discover -s tests -p 'test_*.py'
python scripts/check_public_release.py --strict
python scripts/demo_smoke.py --root /tmp/interview-system-demo
```

演示目录必须不存在或为空。重复运行前请换一个新目录。

## 隐私要求

- 不提交真实姓名、公司内部项目名、联系方式、简历、JD、逐字稿、录音、报告或模型缓存。
- 测试夹具必须明确为虚构内容，不能仅做表面替换后复用真实材料。
- 不在 Issue、PR、日志或截图中粘贴令牌和私有面试内容。
- 新增可写路径时，同步检查 `.gitignore` 与 `scripts/check_public_release.py` 的公开文件清单。
- 已配置 `~/.config/interview-system/data-root` 时，在 `<data-root>/profile/private/release-denylist.json` 保存本机专用词表，格式为 `{"schema_version":1,"markers":["只在私有材料中出现的完整短语"]}`。把真实姓名、公司或项目名、专属表述、面试场次 ID 等加入词表；文件必须留在私有数据目录，不能提交。词表缺失或无效会阻断发布。
- CI 会检查完整可见 Git 历史，发行脚本会复查最终 ZIP。自动匹配无法判断所有语义隐私；公开前仍须审阅差异与发行包。

## Pull Request

PR 应说明问题、实现边界、验证命令和已知限制。涉及数据格式时，必须保持旧数据可读，或提供显式迁移与回滚说明；不得静默改写用户原始材料。
