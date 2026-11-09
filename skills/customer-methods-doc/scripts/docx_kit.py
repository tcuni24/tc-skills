"""客户方法说明 docx 的通用版式（源自草莓 / 香菜 SSR 方法说明的共用代码）。

正文样式为微软雅黑，四边页边距 2.2 cm，标题色 1F3A5F；表头底色
DDEBF7，跨页重复。表题自动编号“表 N”并返回表号，短内容列整列居中。
代码块使用灰底和 Consolas 字体，参考文献使用悬挂缩进。
"""

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FONT = "微软雅黑"


def _style_font(style, size, bold=None, color=None):
    style.font.name = FONT
    style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if color:
        style.font.color.rgb = RGBColor(*color)
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(a), FONT)


def _shade_cell(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def _shade_para(p, fill="F2F2F2"):
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


class DocWriter:
    def __init__(self):
        self.doc = Document()
        sec = self.doc.sections[0]
        sec.left_margin = sec.right_margin = Cm(2.2)
        sec.top_margin = sec.bottom_margin = Cm(2.2)
        _style_font(self.doc.styles["Normal"], 10.5)
        self.doc.styles["Normal"].paragraph_format.line_spacing = 1.35
        self.doc.styles["Normal"].paragraph_format.space_after = Pt(4)
        for name, size in (("Heading 1", 15), ("Heading 2", 12.5), ("Heading 3", 11)):
            _style_font(self.doc.styles[name], size, True, (0x1F, 0x3A, 0x5F))
        self.n_table = 0

    def title(self, main, sub, tagline):
        t = self.p(main, bold=True, size=20, align=WD_ALIGN_PARAGRAPH.CENTER)
        t.paragraph_format.space_before = Pt(40)
        self.p(sub, bold=True, size=16, align=WD_ALIGN_PARAGRAPH.CENTER)
        self.p(tagline, size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
        self.p("")

    def h(self, text, lv=1):
        self.doc.add_heading(text, lv)

    def p(self, text="", bold=False, size=None, align=None, italic=False, style=None):
        para = self.doc.add_paragraph(style=style)
        if text:
            r = para.add_run(text)
            r.bold = bold
            r.italic = italic
            if size:
                r.font.size = Pt(size)
        if align is not None:
            para.alignment = align
        return para

    def note(self, text):
        return self.p(text, size=9)

    def bullet(self, text):
        para = self.p(text, style="List Bullet")
        para.paragraph_format.space_after = Pt(2)
        return para

    def code(self, text):
        for line in text.strip("\n").split("\n"):
            para = self.doc.add_paragraph()
            r = para.add_run(line)
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(8.5)
            para.paragraph_format.left_indent = Cm(0.6)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.1
            _shade_para(para)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def table(self, caption, header, rows, widths=None, size=9):
        """表题自动编号“表 N”；整列内容均较短时该列居中，否则左对齐。返回表号。"""
        self.n_table += 1
        cp = self.p(f"表 {self.n_table}  {caption}", bold=True, size=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        cp.paragraph_format.space_after = Pt(2)
        cp.paragraph_format.keep_with_next = True
        t = self.doc.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, htxt in enumerate(header):
            c = t.rows[0].cells[i]
            c.text = ""
            r = c.paragraphs[0].add_run(str(htxt))
            r.bold = True
            r.font.size = Pt(size)
            c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            _shade_cell(c, "DDEBF7")
        centered = [all(len(str(r[i])) <= 16 and "\n" not in str(r[i]) for r in rows) for i in range(len(header))]
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                txt = str(v)
                cells[i].text = ""
                para = cells[i].paragraphs[0]
                r = para.add_run(txt)
                r.font.size = Pt(size)
                para.paragraph_format.space_after = Pt(0)
                para.paragraph_format.line_spacing = 1.1
                if centered[i]:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        trpr = t.rows[0]._tr.get_or_add_trPr()
        th = OxmlElement("w:tblHeader")
        th.set(qn("w:val"), "true")
        trpr.append(th)
        if widths:
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Cm(w)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)
        return self.n_table

    def references(self, refs):
        for ref in refs:
            para = self.p(ref, size=9)
            para.paragraph_format.left_indent = Cm(0.6)
            para.paragraph_format.first_line_indent = Cm(-0.6)

    def save(self, path):
        self.doc.save(path)


# 共通参考文献（两份说明共用部分措辞一致）
REF_MISA = "Beier S, et al. MISA-web: a web server for microsatellite prediction. Bioinformatics, 2017, 33(16): 2583–2585."
REF_PRIMER3 = "Untergasser A, et al. Primer3—new capabilities and interfaces. Nucleic Acids Research, 2012, 40(15): e115."
REF_BLAST = "Camacho C, et al. BLAST+: architecture and applications. BMC Bioinformatics, 2009, 10: 421."
