"""客户方法说明检查规则回归测试。"""
import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "customer-methods-doc" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from check_methods_doc import check_text, compare_headings, main, read_docx  # noqa: E402


class CheckMethodsDocTests(unittest.TestCase):
    def test_internal_information(self):
        cases = {
            "本地路径": ["/data_0/project/a", "/project/a", "/home/user/a"],
            "文件/脚本名": ["filter_baseline_ssr.py", "step4_extract_ssr.py", "genome.fa", "x.sorted.bam", "regions.bed", "calls.vcf.gz"],
            "硬件型号": ["RTX 5090", "NVIDIA GeForce RTX 5090"],
            "线程数": ["16 线程", "-num_threads", "--threads", "-@ 8"],
            "read group 占位": ["LB=lib1", "PL=ILLUMINA"],
            "输出格式串": ["-outfmt"],
            "草稿标记": ["待核对", "TODO", "黄色高亮"],
        }
        for label, examples in cases.items():
            for text in examples:
                with self.subTest(text=text):
                    errors, _ = check_text([("", text, False)])
                    self.assertIn(label, [item[0] for item in errors])

    def test_allow_is_exact_and_only_for_files(self):
        allow = ["Coriandrum_sativum_genome.fa.zip", "misa.pl", "calls.vcf.gz", "data.tar.gz"]
        for text in allow:
            self.assertEqual(check_text([("", text, False)], allow=allow)[0], [])
        for text, label in [("genome.fa", "文件/脚本名"), ("secret_misa.pl", "文件/脚本名"),
                            ("/project/private/misa.pl", "本地路径")]:
            with self.subTest(text=text):
                self.assertIn(label, [x[0] for x in check_text([("", text, False)], allow=allow)[0]])

    def test_contextual_leaks_and_non_leaks(self):
        for text, label in [("使用genome.fa构建索引", "文件/脚本名"), ("GPU：A100", "硬件型号"),
                            ("A100显卡", "硬件型号"), ("A100 GPU", "硬件型号"),
                            ("使用 16 个线程", "线程数"), ("16 threads", "线程数"),
                            ("8 cores", "线程数"), ("8 核", "线程数")]:
            with self.subTest(text=text):
                self.assertIn(label, [x[0] for x in check_text([("", text, False)])[0]])
        for text in ["样本 A123，GPU 加速分析", "GPU 加速版", "基于 GPU 的比对", "BLAST+ 2.16.0", "Primer3 2.6.1", "Python 3.11", "HipSTR v0.7", "小数 0.25", "e.g."]:
            with self.subTest(text=text):
                self.assertEqual(check_text([("", text, False)])[0], [])

    def test_caption_only_excludes_own_number(self):
        paras = [("", "表 1 结果汇总（指标定义见表 9）", False, True),
                 ("", "表 1 展示了筛选结果。", False)]
        errors, _ = check_text(paras)
        self.assertNotIn("表号不连续", [x[0] for x in errors])
        self.assertIn("引用了不存在的表", [x[0] for x in errors])

    def test_docx_caption_and_other_parts(self):
        ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        document = f'<w:document {ns}><w:body><w:p><w:r><w:t>表 1 标题</w:t></w:r></w:p><w:tbl/><w:p><w:r><w:t>表 1 展示结果</w:t></w:r></w:p></w:body></w:document>'
        footer = f'<w:ftr {ns}><w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>/project/private/x 表 9</w:t></w:r></w:p></w:ftr>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parts.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document)
                archive.writestr("word/footer1.xml", footer)
            paras, _, highlights = read_docx(path)
            self.assertTrue(paras[0][3])
            self.assertFalse(paras[1][3])
            self.assertEqual(highlights, 1)
            errors, _ = check_text(paras)
            self.assertIn("本地路径", [x[0] for x in errors])
            self.assertNotIn("引用了不存在的表", [x[0] for x in errors])

    def test_custom_terms(self):
        errors, _ = check_text([("", "内部代号 ABC-123", False)], terms=["ABC-123"])
        self.assertIn("自定义术语", [item[0] for item in errors])

    def test_caption_sequence_and_references(self):
        cases = [
            (["表 1 标题", "表 3 标题"], "表号不连续"),
            (["表 1 标题", "表 2 标题", "见表 3"], "引用了不存在的表"),
        ]
        for texts, label in cases:
            with self.subTest(label=label):
                errors, _ = check_text([("", text, False, text.startswith("表 ")) for text in texts])
                self.assertIn(label, [item[0] for item in errors])

    def test_table_text_is_not_caption(self):
        errors, _ = check_text([("", "表 1 标题", False, True), ("", "表 2 表内文字", True)])
        self.assertIn("引用了不存在的表", [item[0] for item in errors])
        self.assertNotIn("表号不连续", [item[0] for item in errors])

    def test_valid_tables(self):
        errors, _ = check_text([("", text, False, text.startswith("表 ")) for text in ["表 1 标题", "表 2 标题", "见表 1 和表 2"]])
        self.assertEqual(errors, [])

    def test_gpu_general_terms(self):
        for text in ["GPU 加速版", "基于 GPU 的比对"]:
            with self.subTest(text=text):
                errors, _ = check_text([("", text, False)])
                self.assertNotIn("硬件型号", [item[0] for item in errors])

    def test_nucleotide_counts_are_not_cores(self):
        for text in ["2 核苷酸重复", "3核苷酸 SSR", "仅保留 2–4 核苷酸位点"]:
            with self.subTest(text=text):
                errors, _ = check_text([("", text, False)])
                self.assertNotIn("线程数", [item[0] for item in errors])

    def test_allow_public_names(self):
        names = ["misa.pl", "polymorphic_SSRs_amplicon_sizes.tsv", "Coriandrum_sativum_genome.fa.zip"]
        for name in names:
            with self.subTest(name=name):
                errors, _ = check_text([("", name, False)], allow=names)
                self.assertEqual(errors, [])

    def test_software_versions_are_not_files(self):
        for text in ["BLAST+ 2.16.0", "Primer3 2.6.1", "Python 3.11", "HipSTR v0.7"]:
            with self.subTest(text=text):
                errors, _ = check_text([("", text, False)])
                self.assertNotIn("文件/脚本名", [item[0] for item in errors])

    def test_punctuation_and_range_warnings(self):
        for text in ["中文,", "中文;", "中文:", "中文(", "中文)", ",中文", ";中文", ":中文", "(中文", ")中文"]:
            with self.subTest(text=text):
                _, warnings = check_text([("", text, False)])
                self.assertIn("半角标点紧邻中文", [item[0] for item in warnings])
        _, warnings = check_text([("", "10~20", False)])
        self.assertIn("“~”作范围符，建议用“–”", [item[0] for item in warnings])

    def test_compare_headings_aligns_extra_sections(self):
        left = [("Heading1", "1 概述"), ("Heading2", "3.1 SSR 检索（MISA）"),
                ("Heading2", "3.2 引物设计（Primer3）"), ("Heading1", "4 结果汇总")]
        right = [("Heading1", "1 概述"), ("Heading2", "3.1 原始数据质控（fastp）"),
                 ("Heading2", "3.2 SSR 检索（MISA）"), ("Heading2", "3.3 引物设计（Primer3）"),
                 ("Heading1", "4 结果汇总")]
        rows = compare_headings(left, right)
        self.assertEqual(rows, [
            ("  ", "1 概述", "1 概述"),
            ("≠ ", "", "3.1 原始数据质控（fastp）"),
            ("  ", "3.1 SSR 检索（MISA）", "3.2 SSR 检索（MISA）"),
            ("  ", "3.2 引物设计（Primer3）", "3.3 引物设计（Primer3）"),
            ("  ", "4 结果汇总", "4 结果汇总"),
        ])

    def test_main_reports_highlight(self):
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>文本</w:t></w:r></w:p></w:body></w:document>'
        with tempfile.TemporaryDirectory() as directory:
            docx = Path(directory) / "highlight.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr("word/document.xml", xml)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([str(docx)])
        self.assertEqual(code, 1)
        self.assertIn("文字高亮残留", output.getvalue())
