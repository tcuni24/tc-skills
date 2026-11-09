#!/usr/bin/env python3
"""客户方法说明 docx 体检（仅标准库）。

检查项（ERROR 使退出码为 1，WARN 只提示）：
  ERROR  内部信息：本地绝对路径、脚本/中间文件名、硬件型号、线程数、read group 占位值、-outfmt 串、
         草稿标记（待核对/待确认/TODO/高亮提示）、自定义 --terms
  ERROR  文字高亮（w:highlight）残留
  ERROR  表题“表 N”不连续，或正文“表 N”引用超出范围
  WARN   中文与半角 , ; : ( ) 紧邻；“~”作范围符
  --compare B.docx  并排列出两份文档的标题结构，便于核对章节是否统一

用法：
  check_methods_doc.py A.docx [--compare B.docx] [--allow misa.pl,result.tsv] [--terms 内部码1,内部码2]
"""
from __future__ import annotations

import argparse
import difflib
import itertools
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

FILE_EXT = r"(?:py|sh|pl|R|nf|smk|ini|cfg|config|bed|bam|cram|sam|vcf|tar|fa|fasta|fna|fq|fastq|tsv|csv|json|pkl|log|txt)"
LEAK_PATTERNS = [
    ("本地路径", re.compile(r"(?<![\w:/.])/(?:data\w*|project|public|home|work|tmp|mnt|scratch|opt)/[\w./-]*")),
    ("文件/脚本名", re.compile(r"[A-Za-z0-9_*-]+(?:\.[A-Za-z0-9_*-]+)*\." + FILE_EXT + r"(?:\.gz|\.zip)?(?![A-Za-z0-9_])")),
    ("硬件型号", re.compile(r"(?<![A-Za-z0-9])(?:RTX|GTX)\s?\d{3,4}[A-Za-z0-9]*|(?<![A-Za-z0-9])[AHVL]\d{2,3}(?![A-Za-z0-9])(?=\s*(?:GPU|显卡))|GPU\s*[:：]\s*[AHVL]\d{2,3}(?![A-Za-z0-9])|NVIDIA\s+(?:RTX|GeForce|Tesla|A\d|H\d)[A-Za-z0-9]*", re.I)),
    ("线程数", re.compile(r"\d+\s*(?:个\s*)?(?:线程|核(?![苷酸]))|\d+\s*(?:threads?|cores?)\b|线程数|-(?:-)?(?:num_)?threads?\b|-@\s*\d+", re.I)),
    ("read group 占位", re.compile(r"\bLB\s*=\s*lib\w*|\bPL\s*=\s*ILLUMINA", re.I)),
    ("输出格式串", re.compile(r"-outfmt\b")),
    ("草稿标记", re.compile(r"待核对|待确认|待补充|TODO|FIXME|XXX|黄色高亮|删除高亮")),
]
CJK = r"[\u4e00-\u9fff]"
ASCII_PUNCT = re.compile(rf"{CJK}[,;:()]|[,;:()]{CJK}")
CAPTION = re.compile(r"^\s*表\s*(\d+)(?!\d)")
TABLE_REF = re.compile(r"表\s*(\d+)")


def read_docx(path):
    """返回段落、标题和高亮数；段落为 (样式, 文字, 表内, 表题, 来源)。"""
    paras, heads = [], []
    n_hl = 0
    with zipfile.ZipFile(path) as z:
        names = ["word/document.xml"] + sorted(
            name for name in z.namelist()
            if re.fullmatch(r"word/(?:header[^/]*|footer[^/]*|footnotes|endnotes)\.xml", name))
        for name in names:
            root = ET.fromstring(z.read(name))
            n_hl += sum(1 for _ in root.iter(W + "highlight"))
            document = name == "word/document.xml"
            def walk(el, in_table=False):
                children = list(el)
                for i, child in enumerate(children):
                    if child.tag == W + "p":
                        ps = child.find(f"{W}pPr/{W}pStyle")
                        style = ps.get(W + "val", "") if ps is not None else ""
                        text = "".join(t.text or "" for t in child.iter(W + "t"))
                        caption = (document and not in_table and i + 1 < len(children)
                                   and children[i + 1].tag == W + "tbl" and bool(CAPTION.match(text)))
                        paras.append((style, text, in_table, caption, name if not document else ""))
                        if document and style.lower().startswith("heading") and text.strip():
                            heads.append((style, text.strip()))
                    else:
                        walk(child, in_table or child.tag == W + "tbl")
            walk(root)
    return paras, heads, n_hl


def allowed(hit, allow):
    return hit in allow


def check_text(paras, allow=(), terms=()):
    """返回 (errors, warnings)，每项为 (类别, 片段)。纯函数，便于测试。"""
    errors, warns = [], []
    extra = [("自定义术语", re.compile(re.escape(t))) for t in terms if t]
    for para in paras:
        text = para[1]
        source = para[4] if len(para) > 4 else ""
        prefix = f"[{source}] " if source else ""
        for label, pat in LEAK_PATTERNS + extra:
            for m in pat.finditer(text):
                hit = m.group(0)
                if label != "文件/脚本名" or not allowed(hit, allow):
                    errors.append((label, prefix + _ctx(text, m)))
        for m in ASCII_PUNCT.finditer(text):
            warns.append(("半角标点紧邻中文", prefix + _ctx(text, m)))
        if re.search(r"\d\s*~\s*\d", text):
            warns.append(("“~”作范围符，建议用“–”", prefix + text[:60]))
    caps = [int(m.group(1)) for p in paras if len(p) > 3 and p[3]
            and (m := CAPTION.match(p[1]))]
    if caps and caps != list(range(1, len(caps) + 1)):
        errors.append(("表号不连续", str(caps)))
    for p in paras:
        text = p[1]
        if len(p) > 4 and p[4]:
            continue
        caption = CAPTION.match(text) if len(p) > 3 and p[3] else None
        for m in TABLE_REF.finditer(text):
            if caption and m.start() < caption.end():
                continue
            if int(m.group(1)) > len(caps) or int(m.group(1)) == 0:
                errors.append(("引用了不存在的表", _ctx(text, m)))
    return errors, warns


def _ctx(text, m, w=25):
    return text[max(0, m.start() - w): m.end() + w].replace("\n", " ")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docx")
    ap.add_argument("--compare", help="另一份应保持结构一致的 docx")
    ap.add_argument("--allow", default="", help="逗号分隔；可出现的完整交付文件名、公开文件名、软件脚本名（如 misa.pl）")
    ap.add_argument("--terms", default="", help="逗号分隔；额外视为内部信息的词（内部样本码、项目代号等）")
    a = ap.parse_args(argv)
    allow = [x.strip() for x in a.allow.split(",") if x.strip()]
    terms = [x.strip() for x in a.terms.split(",") if x.strip()]

    paras, heads, n_hl = read_docx(a.docx)
    errors, warns = check_text(paras, allow, terms)
    if n_hl:
        errors.append(("文字高亮残留", f"{n_hl} 处 w:highlight"))
    n_cap = sum(1 for p in paras if p[3])
    print(f"# {a.docx}\n段落 {len(paras)}，标题 {len(heads)}，表 {n_cap}")
    for kind, items in (("ERROR", errors), ("WARN", warns)):
        for label, frag in items:
            print(f"{kind}\t{label}\t{frag}")
    if a.compare:
        _, heads_b, _ = read_docx(a.compare)
        print(f"\n# 标题结构对照：左 {a.docx} | 右 {a.compare}")
        for mark, left, right in compare_headings(heads, heads_b):
            print(f"{mark}{left:<36}| {right}")
    print(f"\nERROR {len(errors)}，WARN {len(warns)}")
    return 1 if errors else 0


def compare_headings(heads_a, heads_b):
    """返回 [(标记, 左标题, 右标题)]；标记为“  ”表示两边一致，“≠ ”表示不一致或一侧缺失。

    按去掉章节号和括号内容后的标题文字对齐（difflib），一侧多出的小节单独成行，不会让后续行错位。
    """
    ka = [(s, _norm(t)) for s, t in heads_a]
    kb = [(s, _norm(t)) for s, t in heads_b]
    rows = []
    sm = difflib.SequenceMatcher(a=ka, b=kb, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            rows += [("  ", heads_a[i][1], heads_b[j][1]) for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        for x, y in itertools.zip_longest(heads_a[i1:i2], heads_b[j1:j2], fillvalue=("", "")):
            rows.append(("≠ ", x[1], y[1]))
    return rows


def _norm(h):
    h = re.sub(r"^[\d.]+\s+", "", h.strip())
    return re.sub(r"\s+", " ", re.sub(r"[（(].*?[)）]", "", h)).strip()


if __name__ == "__main__":
    sys.exit(main())
