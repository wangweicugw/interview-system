# 贡献指南

感谢你改进 interview-system。这个仓库只接受源码、匿名测试夹具和文档，不接受真实面试资料。

## 开发流程

1. 使用 Python 3.10 或更新版本创建虚拟环境。
2. 安装 `requirements.lock` 中的基础依赖。
3. 修改功能时同步增加或更新测试。
4. 提交前运行：

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

## Pull Request

PR 应说明问题、实现边界、验证命令和已知限制。涉及数据格式时，必须保持旧数据可读，或提供显式迁移与回滚说明；不得静默改写用户原始材料。
