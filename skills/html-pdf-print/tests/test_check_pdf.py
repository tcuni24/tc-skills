#!/usr/bin/env python3
"""Unit tests for check_pdf.py coordinate and footer band evaluation logic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_pdf import parse_bbox_xml  # noqa: E402


class CheckPdfLayoutTests(unittest.TestCase):
    """Test suite for PDF bounding-box layout parsing and footer detection."""

    def test_clean_single_page_passes(self):
        # 页面尺寸 595.28 x 841.89 (A4)，文字在安全区域内 (x: 50~200, y: 50~100)
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="50.0" xMax="120.0" yMax="70.0">测试</word>
                <word xMin="125.0" yMin="50.0" xMax="200.0" yMax="70.0">正文</word>
              </line>
            </flow>
          </page>
        </doc>
        """
        res = parse_bbox_xml(xml_data, "sample.pdf", inset=30.0, footer=None, band=90.0)
        self.assertTrue(res["passed"])
        self.assertEqual(len(res["pages"]), 1)
        self.assertEqual(res["words"], 2)
        self.assertEqual(len(res["boundary_violations"]), 0)

    def test_margin_inset_violation_detected(self):
        # 文字贴边 (xMin=10.0 < inset=30.0)
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="10.0" yMin="50.0" xMax="80.0" yMax="70.0">贴边文字</word>
              </line>
            </flow>
          </page>
        </doc>
        """
        res = parse_bbox_xml(xml_data, "sample.pdf", inset=30.0, footer=None, band=90.0)
        self.assertFalse(res["passed"])
        self.assertEqual(len(res["boundary_violations"]), 1)
        self.assertEqual(res["boundary_violations"][0]["text"], "贴边文字")

    def test_multi_page_and_last_page_footer_passes(self):
        # 双页文档，页脚精确出现在第 2 页（末页）页底带 (y: 800~820，高度 841.89，band=90)
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="50.0" xMax="100.0" yMax="70.0">第一页</word>
              </line>
            </flow>
          </page>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="50.0" xMax="100.0" yMax="70.0">第二页</word>
              </line>
              <line>
                <word xMin="50.0" yMin="800.0" xMax="150.0" yMax="820.0">机密报告</word>
                <word xMin="160.0" yMin="800.0" xMax="220.0" yMax="820.0">末页页脚</word>
              </line>
            </flow>
          </page>
        </doc>
        """
        res = parse_bbox_xml(xml_data, "sample.pdf", inset=20.0, footer="机密报告 末页页脚", band=90.0)
        self.assertTrue(res["passed"])
        self.assertEqual(len(res["footer_band_matches"]), 1)
        self.assertEqual(res["footer_band_matches"][0]["page"], 2)

    def test_footer_on_non_last_page_fails(self):
        # 双页文档，页脚出现在第 1 页
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="800.0" xMax="150.0" yMax="820.0">机密报告</word>
                <word xMin="160.0" yMin="800.0" xMax="220.0" yMax="820.0">末页页脚</word>
              </line>
            </flow>
          </page>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="50.0" xMax="100.0" yMax="70.0">第二页</word>
              </line>
            </flow>
          </page>
        </doc>
        """
        res = parse_bbox_xml(xml_data, "sample.pdf", inset=20.0, footer="机密报告 末页页脚", band=90.0)
        self.assertFalse(res["passed"])

    def test_footer_outside_footer_band_fails(self):
        # 页脚文案匹配，但在页面顶部 (y: 100~120)，超出页底带范围 (height - band = 751.89)
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow>
              <line>
                <word xMin="50.0" yMin="100.0" xMax="150.0" yMax="120.0">机密报告</word>
                <word xMin="160.0" yMin="100.0" xMax="220.0" yMax="120.0">末页页脚</word>
              </line>
            </flow>
          </page>
        </doc>
        """
        res = parse_bbox_xml(xml_data, "sample.pdf", inset=20.0, footer="机密报告 末页页脚", band=90.0)
        self.assertFalse(res["passed"])
        self.assertEqual(len(res["footer_text_matches"]), 1)
        self.assertEqual(len(res["footer_band_matches"]), 0)

    def test_empty_words_raises_runtime_error(self):
        # 页面无文字，不应误判为边界通过
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
        <doc>
          <page width="595.28" height="841.89">
            <flow></flow>
          </page>
        </doc>
        """
        with self.assertRaises(RuntimeError) as ctx:
            parse_bbox_xml(xml_data, "sample.pdf", inset=20.0, footer=None, band=90.0)
        self.assertIn("没有可提取的文字", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
