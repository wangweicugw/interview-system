# 扫描 PDF 本地 OCR

面试文稿、JD 和简历可输入 PDF。`pdf_ocr.py` 逐页先检查文字层；文字不足的页面用 `pdftoppm` 在本地渲染，再由 macOS Apple Vision 或 Tesseract 识别。原始 PDF 保存在 case 的 `private/`，提取文本进入文稿或 `jd.md` / `resume.md`。manifest 的 `transcript_pdf_pages` 或 `inputs.jd/resume.pdf_pages` 记录每页方法与可用的最低置信度。

- 优先用本机 Apple Vision 做简体中文与英文 OCR。非 macOS 或 Vision 不可用时可用已安装的 Tesseract；在自动模式下若只剩英文模型，不会静默用它处理可能是中文的扫描件。
- 如需显式使用 Tesseract（例如英文资料测试），设置 `INTERVIEW_OCR_ENGINE=tesseract`；可设为 `vision` 强制 Apple Vision，默认 `auto`。
- OCR 是机器识别证据，不保证文字准确。尤其核对数字、否定词、技术术语、公司与岗位名称；不能把低质量扫描结果直接用于事实判断。
- 页面文字识别失败时报告页码并停止，不编造空白内容。无法读取的加密 PDF 仍需用户提供可读取版本。

基础依赖：当前 Python 环境有 `pypdf`；扫描页还需 `pdftoppm`，以及可用的 Apple Vision 或 Tesseract 中文语言数据。不会自动安装工具、下载模型或上传文件。
