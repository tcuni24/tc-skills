"""Behavior tests for the shared methods document layout."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "customer-methods-doc" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm
except ImportError:
    Document = None

if Document is not None:
    from docx_kit import DocWriter


@unittest.skipUnless(Document is not None, "python-docx is not installed")
class DocWriterTests(unittest.TestCase):
    def test_table_numbers_and_column_alignment(self):
        writer = DocWriter()
        self.assertEqual(writer.table("第一", ["短", "长"], [["短", "超过十六个字符的长内容abcdefghijklmnop"]]), 1)
        self.assertEqual(writer.table("第二", ["列"], [["值"]]), 2)
        self.assertEqual([p.text for p in writer.doc.paragraphs if p.text.startswith("表 ")],
                         ["表 1  第一", "表 2  第二"])
        cells = writer.doc.tables[0].rows[1].cells
        self.assertEqual(cells[0].paragraphs[0].alignment, WD_ALIGN_PARAGRAPH.CENTER)
        self.assertNotEqual(cells[1].paragraphs[0].alignment, WD_ALIGN_PARAGRAPH.CENTER)

    def test_saved_document_layout(self):
        writer = DocWriter()
        writer.p("正文")
        writer.table("表题", ["列"], [["值"]])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.docx"
            writer.save(path)
            doc = Document(path)
        style = doc.paragraphs[0].style
        self.assertEqual(style.font.name, "微软雅黑")
        self.assertEqual(style.element.rPr.rFonts.get(qn("w:eastAsia")), "微软雅黑")
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            self.assertAlmostEqual(getattr(doc.sections[0], side).cm, 2.2, places=2)
        self.assertIsNotNone(doc.tables[0].rows[0]._tr.trPr.find(qn("w:tblHeader")))
        shd = doc.tables[0].cell(0, 0)._tc.tcPr.find(qn("w:shd"))
        self.assertEqual(shd.get(qn("w:fill")), "DDEBF7")

    def test_code_shading_and_reference_hanging_indent(self):
        writer = DocWriter()
        writer.code("example")
        writer.references(["reference"])
        code = writer.doc.paragraphs[0]
        shd = code._p.pPr.find(qn("w:shd")) if code._p.pPr is not None else None
        self.assertIsNotNone(shd)
        self.assertEqual(shd.get(qn("w:fill")), "F2F2F2")
        ref = writer.doc.paragraphs[-1].paragraph_format
        self.assertAlmostEqual(ref.left_indent.cm, 0.6, places=2)
        self.assertAlmostEqual(ref.first_line_indent.cm, -0.6, places=2)
