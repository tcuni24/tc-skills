#!/usr/bin/env python3
"""Unit and regression tests for audit_delivery.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
AUDIT_SCRIPT = SCRIPTS_DIR / "audit_delivery.py"


class AuditDeliveryTests(unittest.TestCase):
    """Test suite for delivery package compliance and leak detection."""

    def run_audit(self, target_dir: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(AUDIT_SCRIPT), str(target_dir)]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_clean_package_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # 写入合法的空 xlsx
            import openpyxl
            wb = openpyxl.Workbook()
            wb.save(root / "project.xlsx")

            (root / "plot").mkdir()
            (root / "plot" / "figure1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "report.html").write_text(
                "<html><body><h1>交付报告</h1><img src='plot/figure1.png'></body></html>",
                encoding="utf-8",
            )

            res = self.run_audit(root)
            self.assertEqual(res.returncode, 0, f"Clean package should pass. Stderr: {res.stderr}\nStdout: {res.stdout}")
            self.assertIn("文件 / 引用问题: 无", res.stdout)
            self.assertIn("内容泄漏疑点: 无", res.stdout)

    def test_root_hidden_file_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "project.xlsx").write_bytes(b"dummy")
            (root / ".DS_Store").write_bytes(b"garbage")

            res = self.run_audit(root)
            self.assertEqual(res.returncode, 1)
            self.assertIn("隐藏文件: .DS_Store", res.stdout)

    def test_hidden_directory_and_nested_hidden_file_fails(self):
        # 针对在白名单子目录下的隐藏子目录（如 plot/.cache/a.png）的回归检查
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "project.xlsx").write_bytes(b"dummy")
            cache_dir = root / "plot" / ".cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "temp.png").write_bytes(b"png-data")

            res = self.run_audit(root)
            self.assertEqual(res.returncode, 1)
            self.assertIn("隐藏目录: plot/.cache/", res.stdout)
            self.assertIn("隐藏文件: plot/.cache/temp.png", res.stdout)

    def test_disallowed_file_extension_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "project.xlsx").write_bytes(b"dummy")
            (root / "intermediate_data.tsv").write_text("a\tb\n1\t2", encoding="utf-8")

            res = self.run_audit(root)
            self.assertEqual(res.returncode, 1)
            self.assertIn("非白名单类型文件: intermediate_data.tsv", res.stdout)

    def test_missing_image_reference_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "project.xlsx").write_bytes(b"dummy")
            (root / "report.html").write_text(
                "<html><body><img src='plot/nonexistent.png'></body></html>",
                encoding="utf-8",
            )

            res = self.run_audit(root)
            self.assertEqual(res.returncode, 1)
            self.assertIn("图片文件不存在 plot/nonexistent.png", res.stdout)

    def test_content_leak_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "project.xlsx").write_bytes(b"dummy")
            (root / "report.html").write_text(
                "<html><body><p>内部工作目录: /data_0/analysis/work/run1</p><p>TODO: 补齐数据</p></body></html>",
                encoding="utf-8",
            )

            res = self.run_audit(root, ["--terms", "analysis"])
            self.assertEqual(res.returncode, 1)
            self.assertIn("绝对路径", res.stdout)
            self.assertIn("开发标记", res.stdout)
            self.assertIn("自定义术语", res.stdout)


if __name__ == "__main__":
    unittest.main()
