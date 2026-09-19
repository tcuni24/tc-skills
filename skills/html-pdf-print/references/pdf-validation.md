# 用实际 PDF 验证

先查当前环境已有的 Chromium/Chrome、浏览器自动化和 PDF 工具。复用现有依赖；不要把某台机器的缓存路径或 Playwright 模块路径写入生产代码。

## 打印命令

下面变量需替换为已确认的路径。工作目录与 runtime 父目录应先确认；临时位置及 slot 等调度要求沿用当前环境规则。浏览器启动权限单独按当前会话授权处理，不能用换调度器绕过拒绝。

```bash
TMPDIR="$runtime_dir" "$chromium_bin" \
  --headless --disable-dev-shm-usage \
  --user-data-dir="$runtime_dir/browser-profile" \
  --no-pdf-header-footer \
  --print-to-pdf="$output_pdf" "$report_url"
```

`report_url` 可为本地 HTML 的 `file:///...` URI。沙箱参数沿用受信任的运行配置，不默认关闭浏览器自己的沙箱。CLI 返回成功后仍检查 PDF 的页数、内容和位置。

若使用 Playwright，按需复用下面顺序；文件下载/身份认证等其他流程不属于本技能的隐含授权：

```javascript
await page.goto(reportUrl, {waitUntil: 'load'});
await page.evaluate(async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images, image => image.decode().catch(() => {})));
});
// decode 的异常在这里仅用于收集完成状态；随后必须确认哪些图片应该成功加载。
const images = await page.evaluate(() => Array.from(document.images, image => ({
  src: image.getAttribute('src'), loaded: image.complete && image.naturalWidth > 0
})));
// 对预期可用图片断言 loaded；明确的缺图回退场景另验。
await page.pdf({path: outputPdf, preferCSSPageSize: true, printBackground: true});
```

检查实际打印事件路径：只测试“预先 emulateMedia(print) 再 PDF”可能漏掉 `beforeprint` 早于打印样式的问题。至少有一次从正常 screen 状态直接打印。

## 坐标、图片和视觉证据

```bash
pdfinfo report.pdf
pdftotext -bbox-layout report.pdf report.xml
pdfimages -list report.pdf
pdftoppm -f 3 -l 3 -scale-to 1200 -png -singlefile report.pdf page-3
```

随附脚本只依赖 Python 标准库和 Poppler `pdftotext`，不创建中间文件：

```bash
python scripts/check_pdf.py report.pdf --edge-inset-pt 15
python scripts/check_pdf.py report.pdf --edge-inset-pt 15 \
  --footer-text 'Targeted Capture Panel Design Report' --footer-band-pt 90
```

从技能目录运行，或给脚本传绝对路径。默认 inset=0 只抓纸外文字；15pt 是安全留白示例，应按版式选择。PDF 坐标单位为 pt（72pt=1in），上缘通常 y=0。

脚本输出 JSON：纸张尺寸、边界违规、指定完整文案的所有同行精确匹配、以及页底范围内的匹配。指定 footer 时，整份 PDF 必须恰好一次精确匹配，且在末页页底范围内；任何其他页面或区域的重复匹配都会失败。退出码 0=所请求检查通过，1=几何/页脚断言失败，2=依赖或输入错误。

**脚本不是完整验收器**：页脚文案需是一个完整文本行（空白规范化后精确匹配）；若换行、PDF 是扫描件、字体抽取异常，应改用 DOM/图像证据，不能因此改坏页面。如果正文恰好也有同样的完整行，检查会保守失败，应选择页脚独有的完整行或使用 DOM/图像定位；不要忽略重复匹配来使检查变绿。脚本文案匹配不能识别其他措辞的误放页脚，遮挡、图片裁切与表格语义仍需独立检查。

对图片的检查以预期图清单为准：缺失图片可能只留下回退框；同一图像对象跨页出现可能意味着裁切，也可能是有意复用。核对图片尺寸/对象与页码，并查看图表页，不机械要求所有 PDF 图片对象只能出现一次（品牌标志经常重复）。

对长表/连续说明，用足够明确的首尾标记核对页码：同一短说明及标题同页、行首与行末同页、所有行各出现一次、续页实际表头存在。不能用正文中也出现的“字段”“说明”泛词假装验证了表头。

## 交付证据

保留修复前后的同源 PDF、问题页/图表页/末页预览、运行命令及浏览器版本。最终说明实际通过的范围和限制；重新生成的 HTML 才包含模板变化。既有 CI 阻断需独立列出并提供基线证据，不把它混入本次修复的通过结论。
