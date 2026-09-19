#!/usr/bin/env python3
"""客户报告体检：检查一份单文件 HTML 报告的正文是否对非专业读者友好。

用法：
  audit_report.py 报告.html [--appendix-id sec-appendix]
                           [--extra-terms 词1,词2,...] [--terms-file 词表.txt]
                           [--extra-warn-terms 词1,词2,...] [--warn-terms-file 提示词表.txt]
                           [--strict]

检查项分级：
  1. 阻断项（导致退出码为 1）：
     - 缺图占位符（“图片缺失”）
     - 防御性表述（如“不是概率问题”、“您无需就此决策”等）
     - 用户显式指定的违禁术语（--extra-terms / --terms-file）
  2. 可读性提示项（默认仅输出提示，退出码为 0；--strict 时升为阻断项）：
     - 算法与高深统计术语（如 HMM、变点、归一化等，通俗词汇如“中位数”不在此列）
     - 用户指定的提示术语（--extra-warn-terms / --warn-terms-file）
     - 数字密集句（一句内出现 ≥4 个数字）
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

# 默认违禁词（阻断项）：典型的推脱/防御性表述，违背交付报告核心原则
DEFAULT_FORBIDDEN_TERMS = [
    "不是概率问题",
    "您无需就此决策",
    "不代表判读方法",
    "不是参数设置保守",
]

# 默认可读性提示词（非阻断建议）：算法与统计行话，提示作者评估是否需要通俗化或移至附录
DEFAULT_WARN_TERMS = [
    "ρ", "rho", "ρ₀", "ρ₁", "分离度", "D_min", "NoCall", "HMM", "变点", "归一化",
    "非整倍体", "三体", "单倍型", "假阴性", "假阳性", "PAV", "共线", "mappability",
    "N50", "p05", "p95",
]


def load_terms_file(path: Path) -> list[str]:
    """读取词表文件，忽略空行与 # 开头的注释行。"""
    if not path.is_file():
        raise FileNotFoundError(f"词表文件不存在: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def strip_to_text(fragment: str) -> str:
    """去除 HTML 标签、脚本、样式、SVG 及内嵌图片，提取纯文本。"""
    fragment = re.sub(r"<svg.*?</svg>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<style.*?</style>|<script.*?</script>", " ", fragment, flags=re.S)
    fragment = re.sub(r'data:image/[^"\']+', " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def audit_report(
    raw_html: str,
    appendix_id: str = "sec-appendix",
    extra_forbidden: list[str] | None = None,
    extra_warn: list[str] | None = None,
    strict: bool = False,
) -> dict:
    """审计报告 HTML 内容，返回结构化诊断结果。"""
    marker = f'id="{appendix_id}"'
    body_html = raw_html.split(marker)[0] if marker in raw_html else raw_html
    body = strip_to_text(body_html)

    forbidden_terms = list(DEFAULT_FORBIDDEN_TERMS)
    if extra_forbidden:
        forbidden_terms.extend([t for t in extra_forbidden if t.strip()])
    forbidden_hits = {t: body.count(t) for t in forbidden_terms if body.count(t)}

    warn_terms = list(DEFAULT_WARN_TERMS)
    if extra_warn:
        warn_terms.extend([t for t in extra_warn if t.strip()])
    warn_hits = {t: body.count(t) for t in warn_terms if body.count(t)}

    missing_count = len(re.findall(r"图片缺失", raw_html))
    img_count = len(re.findall(r'<img src="data:image/', raw_html))
    collapses = re.findall(r'<details[^>]*>\s*<summary>([^<]+)</summary>', raw_html)
    nav_titles = re.findall(r"<em>\d+</em>([^<]+)</a>", raw_html)

    sentences = re.split(r"[。；！？]", body)
    dense_sentences = [s.strip() for s in sentences if len(re.findall(r"\d[\d,\.]*", s)) >= 4]

    has_error = bool(forbidden_hits or missing_count)
    if strict:
        has_error = has_error or bool(warn_hits)

    return {
        "body_length": len(body),
        "nav_titles": nav_titles,
        "embedded_images": img_count,
        "missing_images": missing_count,
        "collapses": collapses,
        "forbidden_hits": forbidden_hits,
        "warn_hits": warn_hits,
        "dense_sentences": dense_sentences,
        "passed": not has_error,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("report", type=Path, help="待检查的 HTML 报告文件")
    ap.add_argument("--appendix-id", default="sec-appendix", help="附录 section 的 id；其后内容不计入正文")
    ap.add_argument("--extra-terms", default="", help="逗号分隔的违禁术语（阻断项，如内部列名/样本号）")
    ap.add_argument("--terms-file", type=Path, default=None, help="每行一个违禁术语的文件路径（如 references/probe-terms.txt）")
    ap.add_argument("--extra-warn-terms", default="", help="逗号分隔的可读性提示词（非阻断建议）")
    ap.add_argument("--warn-terms-file", type=Path, default=None, help="每行一个可读性提示词的文件路径")
    ap.add_argument("--strict", action="store_true", help="开启严格模式：可读性提示词命中也会导致退出码为 1")
    args = ap.parse_args()

    if not args.report.is_file():
        print(f"[ERROR] 报告文件不存在: {args.report}", file=sys.stderr)
        return 2

    raw = args.report.read_text(encoding="utf-8")

    extra_forbidden: list[str] = [t.strip() for t in args.extra_terms.split(",") if t.strip()]
    if args.terms_file:
        try:
            extra_forbidden.extend(load_terms_file(args.terms_file))
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 2

    extra_warn: list[str] = [t.strip() for t in args.extra_warn_terms.split(",") if t.strip()]
    if args.warn_terms_file:
        try:
            extra_warn.extend(load_terms_file(args.warn_terms_file))
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 2

    res = audit_report(
        raw_html=raw,
        appendix_id=args.appendix_id,
        extra_forbidden=extra_forbidden,
        extra_warn=extra_warn,
        strict=args.strict,
    )

    print(f"报告: {args.report}")
    print(f"正文（附录之前）字数: {res['body_length']}")
    print(f"章节导航: {res['nav_titles']}")
    print(f"内嵌图: {res['embedded_images']}   缺图占位: {res['missing_images']}")
    print(f"折叠块: {res['collapses']}")
    print(f"正文违禁术语命中（阻断项）: {res['forbidden_hits'] if res['forbidden_hits'] else '无'}")
    print(f"正文可读性建议术语（非阻断）: {res['warn_hits'] if res['warn_hits'] else '无'}")

    dense = res["dense_sentences"]
    if dense:
        print(f"正文数字密集句（≥4 个数字）共 {len(dense)} 句，前 5 句：")
        for s in dense[:5]:
            print("  - " + s[:120])
    else:
        print("正文数字密集句: 无")

    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
