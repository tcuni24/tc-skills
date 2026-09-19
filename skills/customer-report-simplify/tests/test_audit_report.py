#!/usr/bin/env python3
"""Regression and unit tests for customer report audit script."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from audit_report import (  # noqa: E402
    audit_report,
    load_terms_file,
    strip_to_text,
)


class AuditReportTests(unittest.TestCase):
    """Test suite for audit_report functionality and boundary rules."""

    def test_strip_to_text(self):
        html_input = """
        <html>
          <head>
            <style>body { color: red; }</style>
            <script>console.log("secret script");</script>
          </head>
          <body>
            <h1>报告标题</h1>
            <svg><text>SVG内部图例文案</text></svg>
            <p>正文段落包含 &amp; 符号和 &lt;标签&gt;。</p>
            <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==">
          </body>
        </html>
        """
        text = strip_to_text(html_input)
        self.assertIn("报告标题", text)
        self.assertIn("正文段落包含 & 符号和 <标签>。", text)
        self.assertNotIn("color: red", text)
        self.assertNotIn("secret script", text)
        self.assertNotIn("SVG内部图例文案", text)
        self.assertNotIn("data:image", text)

    def test_median_is_not_forbidden_and_not_warned(self):
        # “中位数”应当被作为常规通俗统计词汇接受，不报错误，也不报警告
        html_sample = """
        <html>
          <body>
            <h2>数据分布</h2>
            <p>样本深度的中位数为 32x，满足交付要求。</p>
          </body>
        </html>
        """
        res = audit_report(html_sample)
        self.assertTrue(res["passed"])
        self.assertNotIn("中位数", res["forbidden_hits"])
        self.assertNotIn("中位数", res["warn_hits"])

    def test_warn_terms_non_blocking_by_default(self):
        # 算法名词如 HMM、变点、归一化默认仅作为可读性提示，不阻断通过（passed 为 True）
        html_sample = """
        <html>
          <body>
            <h2>算法说明</h2>
            <p>本次分析采用 HMM 模型进行变点检测，数据经过中位数归一化处理。</p>
          </body>
        </html>
        """
        res = audit_report(html_sample, strict=False)
        self.assertTrue(res["passed"], "Default mode should not fail on warning terms")
        self.assertIn("HMM", res["warn_hits"])
        self.assertIn("变点", res["warn_hits"])
        self.assertIn("归一化", res["warn_hits"])
        self.assertEqual(res["forbidden_hits"], {})

    def test_strict_mode_blocks_on_warn_terms(self):
        # --strict 模式下，可读性提示词命中会导致 passed 为 False
        html_sample = """
        <html>
          <body>
            <p>采用 HMM 模型分析。</p>
          </body>
        </html>
        """
        res = audit_report(html_sample, strict=True)
        self.assertFalse(res["passed"], "Strict mode should fail on warning terms")
        self.assertIn("HMM", res["warn_hits"])

    def test_defensive_statement_blocks_by_default(self):
        # 防御性表述直接判定为阻断错误
        html_sample = """
        <html>
          <body>
            <p>这不是概率问题，是确定性差异。</p>
          </body>
        </html>
        """
        res = audit_report(html_sample)
        self.assertFalse(res["passed"])
        self.assertIn("不是概率问题", res["forbidden_hits"])

    def test_missing_image_blocks_by_default(self):
        # 缺图占位符直接判定为阻断错误
        html_sample = """
        <html>
          <body>
            <p>图片缺失，待补充实验图</p>
          </body>
        </html>
        """
        res = audit_report(html_sample)
        self.assertFalse(res["passed"])
        self.assertGreater(res["missing_images"], 0)

    def test_appendix_terms_ignored(self):
        # 附录区域内的违禁词或警告词不应计入正文审查
        html_sample = """
        <html>
          <body>
            <div class="content">
              <h1>客户结论</h1>
              <p>本次交付结果完全符合预期。</p>
            </div>
            <section id="sec-appendix">
              <h2>附录：方法与字段说明</h2>
              <p>附录允许出现 HMM、locus_type、这不是概率问题 等方法细节。</p>
            </section>
          </body>
        </html>
        """
        res = audit_report(html_sample, extra_forbidden=["locus_type"])
        self.assertTrue(res["passed"])
        self.assertEqual(res["forbidden_hits"], {})
        self.assertEqual(res["warn_hits"], {})

    def test_load_terms_file_and_blocking(self):
        # 测试读取外部词表文件，并作为违禁项拦截
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
            f.write("# 示例词表\n\ncustom_col_a\ncustom_col_b\n# 注释项\n")
            terms_path = Path(f.name)

        try:
            terms = load_terms_file(terms_path)
            self.assertEqual(terms, ["custom_col_a", "custom_col_b"])

            html_sample = "<html><body><p>结果见 custom_col_a 对应的数据。</p></body></html>"
            res = audit_report(html_sample, extra_forbidden=terms)
            self.assertFalse(res["passed"])
            self.assertIn("custom_col_a", res["forbidden_hits"])
        finally:
            terms_path.unlink(missing_ok=True)

    def test_probe_terms_file_exists_and_usable(self):
        # 验证抽离的 probe-terms.txt 存在并可加载
        probe_terms_file = REFERENCES_DIR / "probe-terms.txt"
        self.assertTrue(probe_terms_file.is_file())
        terms = load_terms_file(probe_terms_file)
        self.assertIn("locus_type", terms)
        self.assertIn("ay61_state", terms)
        self.assertIn("Class-A", terms)

        # 加载 probe-terms.txt 应当成功拦截包含 probe 列名的报告正文
        html_sample = "<html><body><p>本批探针的 locus_type 均为核心型。</p></body></html>"
        res = audit_report(html_sample, extra_forbidden=terms)
        self.assertFalse(res["passed"])
        self.assertIn("locus_type", res["forbidden_hits"])

    def test_dense_numbers_detection(self):
        # 单句包含 ≥4 个数字应被检出为数字密集句
        html_sample = """
        <html>
          <body>
            <p>本次分析中 1 号样本包含 12 个位点，2 号样本包含 34 个位点。</p>
            <p>正常句只有 2 个数字：共 10 份样本。</p>
          </body>
        </html>
        """
        res = audit_report(html_sample)
        self.assertEqual(len(res["dense_sentences"]), 1)
        self.assertIn("1 号样本包含 12 个位点", res["dense_sentences"][0])


if __name__ == "__main__":
    unittest.main()
