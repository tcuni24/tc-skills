#!/usr/bin/env python3
"""客户交付统计图样式：与报告 HTML 同一套青绿色令牌的 matplotlib 局部主题。

    from plot_kit import plot_style, PALETTE, TIER_COLORS, save
    with plot_style():
        fig, ax = plt.subplots(figsize=(8.4, 5.2))
        ...
        save(fig, "delivery/plot/xxx")   # 写 PNG 160 dpi（可选同名 PDF）

规则：白底、无阴影渐变、轻水平网格、图例无边框、颜色必须配文字或数字标签；
中文字体按环境可用字体回退，若无中文字体则应在文案中改用英文或拼音，不能默认可显示。
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager

INK = "#203b38"
MUTED = "#5f726f"
LINE = "#dbe5e2"
PRIMARY = "#087f73"

# 序列色板：主青绿 → 蓝 → 浅青 → 琥珀 → 珊瑚 → 灰
PALETTE = ["#087f73", "#2b6cb0", "#79b7ad", "#d5b665", "#b44b46", "#81948f", "#677d91", "#b49968"]

# 等级 / 置信度语义色（高=青绿，中=蓝，低=琥珀，无=灰，风险=珊瑚）
TIER_COLORS = {"high": "#087f73", "mid": "#2b6cb0", "low": "#d5b665", "none": "#c3cbc8", "risk": "#b44b46"}

_CJK_CANDIDATES = [
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC",
    "Source Han Sans SC", "Noto Sans CJK JP", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "SimHei", "Droid Sans Fallback",
    "AR PL UMing CN", "AR PL UKai CN",
]


def cjk_font() -> str | None:
    """返回环境中第一个可用的中文字体名，没有返回 None。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            return name
    return None


@contextlib.contextmanager
def plot_style(cjk: bool = True):
    fonts = ["Helvetica Neue", "Arial", "DejaVu Sans"]
    if cjk:
        f = cjk_font()
        if f:
            fonts = [f] + fonts
    rc = {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "font.family": "sans-serif",
        "font.sans-serif": fonts,
        "axes.unicode_minus": False,
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.edgecolor": LINE,
        "axes.linewidth": 0.8,
        "axes.titlesize": 13,
        "axes.titleweight": "semibold",
        "axes.labelsize": 11,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "grid.color": LINE,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 10,
        "axes.prop_cycle": mpl.cycler(color=PALETTE),
        "savefig.facecolor": "white",
    }
    with mpl.rc_context(rc):
        yield


def save(fig, stem: str | Path, pdf: bool = False, dpi: int = 160) -> Path:
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    png = stem.with_suffix(".png")
    fig.savefig(png, dpi=dpi, bbox_inches="tight")
    if pdf:
        fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return png


def annotate_bars(ax, bars, fmt="{:,.0f}", fontsize=9, color=INK, inside=False, rotation=0, pad=0.0):
    """给条形图加数字标签（颜色之外必须有数字）。"""
    for b in bars:
        h = b.get_height()
        if h == 0:
            continue
        x = b.get_x() + b.get_width() / 2
        if inside:
            ax.text(x, b.get_y() + h / 2, fmt.format(h), ha="center", va="center", fontsize=fontsize, color="white")
        else:
            ax.text(x, b.get_y() + h + pad, fmt.format(h), ha="center", va="bottom", fontsize=fontsize, color=color, rotation=rotation)
