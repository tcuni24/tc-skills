#!/usr/bin/env python3
"""客户报告体检：检查一份单文件 HTML 报告的正文是否对非专业读者友好。

用法：
  audit_report.py 报告.html [--appendix-id sec-appendix] [--extra-terms 词1,词2,...]

输出：
  - 正文字数（附录之前，去掉图片 / SVG / 样式 / 脚本）
  - 缺图占位数、内嵌图数、折叠块标题
  - 正文里命中的术语 / 列名 / 英文枚举值及次数
  - 正文里数字密集的句子（一句里 ≥4 个数字）

退出码：正文有术语命中或有缺图时为 1，否则为 0。
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

# 默认术语表：统计 / 算法 / 列名 / 枚举值。按项目用 --extra-terms 追加。
DEFAULT_TERMS = [
    # 统计与算法
    "ρ", "rho", "ρ₀", "ρ₁", "分离度", "D_min", "NoCall", "HMM", "变点", "归一化", "非整倍体", "三体",
    "单倍型", "假阴性", "假阳性", "PAV", "共线", "mappability", "N50", "p05", "p95", "中位数",
    # 探针设计项目常见列名与枚举值
    "locus_type", "probe_type", "probe_class", "probe_level", "hairpin_level", "readout",
    "akd_", "at_chr", "at_start", "at_end", "xgeno", "ay61_state", "aneu_chr",
    "AT-P", "AK-P", "SR-P", "PAIRED", "AT_ONLY", "HALF_AT", "HALF_AK", "SHARED_REF",
    "ratiometric", "presence_absence", "normalization_control", "Class-A", "Class-B", "Class-C", "Class-D",
    # 中文里的防御性表述
    "不是概率问题", "您无需就此决策", "不代表判读方法", "不是参数设置保守",
]


def strip_to_text(fragment: str) -> str:
    fragment = re.sub(r"<svg.*?</svg>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<style.*?</style>|<script.*?</script>", " ", fragment, flags=re.S)
    fragment = re.sub(r'data:image/[^"\']+', " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--appendix-id", default="sec-appendix", help="附录 section 的 id；其后的内容不计入正文")
    ap.add_argument("--extra-terms", default="", help="逗号分隔的额外术语")
    args = ap.parse_args()

    raw = args.report.read_text(encoding="utf-8")
    marker = f'id="{args.appendix_id}"'
    body_html = raw.split(marker)[0] if marker in raw else raw
    body = strip_to_text(body_html)

    terms = DEFAULT_TERMS + [t for t in args.extra_terms.split(",") if t.strip()]
    hits = {t: body.count(t) for t in terms if body.count(t)}

    missing = len(re.findall(r"图片缺失", raw))
    imgs = len(re.findall(r'<img src="data:image/', raw))
    collapses = re.findall(r'<details[^>]*>\s*<summary>([^<]+)</summary>', raw)
    nav = re.findall(r"<em>\d+</em>([^<]+)</a>", raw)

    sentences = re.split(r"[。；！？]", body)
    dense = [s.strip() for s in sentences if len(re.findall(r"\d[\d,\.]*", s)) >= 4]

    print(f"报告: {args.report}")
    print(f"正文（附录之前）字数: {len(body)}")
    print(f"章节导航: {nav}")
    print(f"内嵌图: {imgs}   缺图占位: {missing}")
    print(f"折叠块: {collapses}")
    print(f"正文命中术语 / 列名: {hits if hits else '无'}")
    if dense:
        print(f"正文数字密集句（≥4 个数字）共 {len(dense)} 句，前 5 句：")
        for s in dense[:5]:
            print("  - " + s[:120])
    else:
        print("正文数字密集句: 无")

    return 1 if (hits or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
