#!/usr/bin/env python3
"""只读检查 PDF 文字边界及可选的末页页底文案，输出 JSON。"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def distance(value: str) -> float:
    """距离必须为有限非负值。"""
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("距离必须为有限非负数")
    return number


def inspect(pdf: Path, inset: float, footer: str | None, band: float) -> dict:
    """读取真实 PDF 坐标；同行文案匹配用于辅助识别页脚。"""
    if not pdf.is_file():
        raise RuntimeError(f"PDF 不存在：{pdf}")
    if not shutil.which("pdftotext"):
        raise RuntimeError("缺少 Poppler pdftotext")
    result = subprocess.run(
        ["pdftotext", "-bbox-layout", str(pdf.resolve()), "-"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    pages = ET.fromstring(result.stdout).findall(".//{*}page")
    if not pages:
        raise RuntimeError("未提取到 PDF 页面")
    sizes, violations, matches = [], [], []
    word_count = 0
    for number, page in enumerate(pages, 1):
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        sizes.append({"page": number, "width_pt": width, "height_pt": height})
        for word in page.findall(".//{*}word"):
            word_count += 1
            x0, y0, x1, y1 = (
                float(word.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax")
            )
            if x0 < inset or y0 < inset or x1 > width - inset or y1 > height - inset:
                violations.append(
                    {"page": number, "text": word.text, "box_pt": [x0, y0, x1, y1]}
                )
        if footer:
            for line in page.findall(".//{*}line"):
                words = line.findall("./{*}word")
                text = " ".join(word.text or "" for word in words)
                if " ".join(footer.split()) == text and words:
                    top = min(float(word.attrib["yMin"]) for word in words)
                    bottom = max(float(word.attrib["yMax"]) for word in words)
                    matches.append(
                        {
                            "page": number,
                            "line": text,
                            "bottom_gap_pt": height - bottom,
                            "in_footer_band": top >= height - band and bottom <= height,
                        }
                    )
    if not word_count:
        raise RuntimeError("没有可提取的文字；改用图像/DOM 验证，不应视为边界检查通过")
    footer_hits = [hit for hit in matches if hit["in_footer_band"]]
    footer_ok = footer is None or (
        len(matches) == 1
        and len(footer_hits) == 1
        and footer_hits[0]["page"] == len(pages)
    )
    return {
        "pdf": str(pdf.resolve()),
        "pages": sizes,
        "words": word_count,
        "boundary_violations": violations,
        "footer_text_matches": matches,
        "footer_band_matches": footer_hits,
        "passed": not violations and footer_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--edge-inset-pt",
        type=distance,
        default=0,
        help="文字距四边的最小距离，默认仅检查纸外",
    )
    parser.add_argument(
        "--footer-text", help="完整同行页脚文案（精确匹配）；须仅末页页底出现一次"
    )
    parser.add_argument(
        "--footer-band-pt", type=distance, default=90, help="页底识别带高度，默认 90pt"
    )
    args = parser.parse_args()
    if args.footer_text is not None and not args.footer_text.strip():
        parser.error("--footer-text 不能为空")
    try:
        result = inspect(
            args.pdf, args.edge_inset_pt, args.footer_text, args.footer_band_pt
        )
    except (
        RuntimeError,
        OSError,
        ValueError,
        ET.ParseError,
        subprocess.SubprocessError,
    ) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
