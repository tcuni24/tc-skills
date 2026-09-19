# Chromium：页脚仅在末页底部

仅在用户选择“末页底部”时使用。本范式在 Chromium m153、A4、100% 缩放、自然块流报告上实测；不是任意浏览器、任意纸张、任意 DOM 的分页算法。采用前验证目标引擎；不要求用户改用某个浏览器来迁就实现。

## 为什么需要测量

常见的固定定位页脚会逐页重复；绝对定位到初始包含块底部可能跑到第一页；整篇报告的 flex `margin-top:auto` 不能保证填满最后一个分页片段。连续滚动高度也不含原生分页产生的空白。

可控打印几何下，建立**与打印内容区同宽同高的分栏副本**，让浏览器执行自然分片。末页页脚在最后一栏的底部坐标，给出待填充的余量。同步移除副本，仅给原页脚增加余量，实际打印仍使用原始 DOM 和原生分页。

分栏与分页并非对所有 CSS 都等价：named pages、不同页尺寸、`break-before: page` 与 column 的差异、嵌套分栏、浮动/定位元素、变化的页眉高度，都要单独验证。不能匹配的版式应改用目标引擎的分页能力或专门分页方案。

## 一组可适配的实现

以下选择器是示例；把逻辑放入项目已有模板/打印脚本。示例假设 `.report` 中最后一个元素是 `.report-footer`；同一报告中的所有 `@page` 配置需先收敛。

页面参数从同一组 CSS 变量派生。这里左右页边距为 18mm，因此测量宽度是 174mm；如果项目把横向留白放在报告内部 padding，应保留那种方式，让副本与原报告实际布局一致，不要叠加两套留白。

```css
@media print {
  :root {
    --print-sheet-width: 210mm;
    --print-sheet-height: 297mm;
    --print-margin-y: 12mm;
    --print-margin-x: 18mm;
    --print-content-width: calc(var(--print-sheet-width) - 2 * var(--print-margin-x));
    --print-content-height: calc(var(--print-sheet-height) - 2 * var(--print-margin-y));
  }
  @page { size: A4; margin: var(--print-margin-y) var(--print-margin-x); }
  body { margin: 0; }
  .report { width: var(--print-content-width); max-width: none; margin: 0; }
  .report-footer {
    break-inside: avoid;
    break-before: auto;
    margin-top: var(--print-footer-space, 0px);
  }
  .report[data-print-measure] {
    position: absolute;
    left: -100000px;
    top: 0;
    height: var(--print-content-height);
    column-width: var(--print-content-width);
    column-gap: 0;
    column-fill: auto;
    visibility: hidden;
  }
}
```

实际项目若存在 `!important`、max-width、祖先定位、zoom、padding 或 transform，须检查最终计算值；直接粘贴以上规则并不能保证副本和纸张相同。

```javascript
(() => {
  const printMedia = window.matchMedia('print');
  const clearFooterSpace = () => {
    const footer = document.querySelector('.report .report-footer');
    if (footer) footer.style.removeProperty('--print-footer-space');
  };
  const alignLastFooter = () => {
    const report = document.querySelector('.report');
    const footer = report && report.querySelector('.report-footer');
    if (!footer) return;
    clearFooterSpace(); // 重复打印不累加上一次间距。
    const measurement = report.cloneNode(true);
    measurement.setAttribute('data-print-measure', '');
    measurement.setAttribute('aria-hidden', 'true');
    document.body.appendChild(measurement);
    try {
      const pageBox = measurement.getBoundingClientRect();
      const footerBox = measurement.querySelector('.report-footer').getBoundingClientRect();
      const gap = Math.max(0, pageBox.bottom - footerBox.bottom - 1);
      footer.style.setProperty('--print-footer-space', gap + 'px');
    } finally {
      measurement.remove();
    }
  };
  printMedia.addEventListener('change', event => {
    if (event.matches) alignLastFooter();
    else clearFooterSpace();
  });
  window.addEventListener('beforeprint', () => {
    if (printMedia.matches) alignLastFooter();
  });
  window.addEventListener('afterprint', clearFooterSpace);
})();
```

关键时序：本次 Chromium 实测中 `beforeprint` 触发时 `matchMedia('print').matches` 仍可能为 false。此时测到的是屏幕布局，间距可能恒为零。监听打印媒体变为 true 后再测；`beforeprint` 分支只处理媒体已经生效的情况（如自动化预先切换 print media）。

`-1px` 是舍入余量，防止刚好顶出新页；它不是用于掩盖尺寸不匹配的调参按钮。若误差明显大于像素级，应检查副本布局、打印缩放、重复表头、图片/字体加载和媒体条件。

## 实际 PDF 判据

分别用短尾页、接近满页的尾页、长表后尾页，以及实际含图报告验证：

- 原页脚一次且只在最后一页，末页有正文；页脚与正文不重叠。
- 不同正文长度下，页脚距纸边基本一致且在用户约定的安全区；距离按 footer 实际盒子/文字计算，不能盲用某个历史数值。
- 桌面/窄屏入口、重复打印与支持的语言状态均正确，打印结束/取消后无 `[data-print-measure]` 节点，无新增屏幕间距。

若用户需要支持改纸张或缩放，另跑这些设置；上面固定几何的验证不能代表打印对话框里所有设置都正确。
