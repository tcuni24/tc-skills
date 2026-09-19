#!/usr/bin/env python3
"""客户交付统计图样式：与报告 HTML 同一套青绿色令牌的 matplotlib 局部主题。

    from plot_kit import plot_style, PALETTE, TIER_COLORS, save
    with plot_style():
        fig, ax = plt.subplots(figsize=(8.4, 5.2))
        ...
        save(fig, "delivery/plot/xxx")   # 写 PNG 160 dpi（可选同名 PDF）

规则：白底、无阴影渐变、轻水平网格、图例无边框、颜色必须配文字或数字标签；
中文字体只使用经过字形与区域版本检查的简体中文字体。若无可用字体，应改用英文标签并传
``plot_style(cjk=False)``，不能让 matplotlib 静默回退到日文区域字形。
"""
from __future__ import annotations

import contextlib
import functools
from pathlib import Path
import re
import warnings

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font
from matplotlib.text import Text

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
    "Source Han Sans SC", "Source Han Sans CN", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "SimHei",
    "AR PL UMing CN", "AR PL UKai CN",
]

_SC_PROBE = "汉字图表一致率径个关"
_UNSAFE_REGION_TOKENS = {"jp", "kr", "tc", "hk", "japanese", "korean", "traditional"}
_SAFE_SC_FAMILIES = {
    "pingfang sc", "hiragino sans gb", "microsoft yahei", "noto sans cjk sc", "noto sans sc",
    "source han sans sc", "source han sans cn", "wenquanyi micro hei", "wenquanyi zen hei",
    "simhei", "simsun", "ar pl uming cn", "ar pl ukai cn",
}


@functools.lru_cache(maxsize=32)
def _face_details(path: str) -> tuple[str, str, frozenset[int]]:
    face = FT2Font(path)
    return face.family_name, face.style_name, frozenset(face.get_charmap())


def _is_simplified_chinese_face(_path: str, family: str) -> bool:
    """Conservatively accept only faces identifiable as simplified Chinese."""
    # 文件名可以重命名，区域版本必须以字体内部 family 为准。
    normalized = " ".join(family.casefold().split())
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    if tokens & _UNSAFE_REGION_TOKENS:
        return False
    return normalized in _SAFE_SC_FAMILIES


def _resolve_font(properties: font_manager.FontProperties) -> tuple[str, str, str, frozenset[int]]:
    path = font_manager.findfont(properties, fallback_to_default=False)
    family, style, charmap = _face_details(path)
    return path, family, style, charmap


def _validated_family(name: str) -> bool:
    for weight in ("normal", "semibold"):
        properties = font_manager.FontProperties(family=[name], weight=weight)
        try:
            path, family, _style, charmap = _resolve_font(properties)
        except (OSError, RuntimeError, ValueError):
            return False
        if not _is_simplified_chinese_face(path, family):
            return False
        if any(ord(char) not in charmap for char in _SC_PROBE):
            return False
    return True


def cjk_font() -> str | None:
    """返回首个经过简体中文区域字形检查的字体名，没有则返回 None。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available and _validated_family(name):
            return name
    return None


@contextlib.contextmanager
def plot_style(cjk: bool = True):
    fonts = ["Helvetica Neue", "Arial", "DejaVu Sans"]
    if cjk:
        f = cjk_font()
        if not f:
            raise RuntimeError(
                "未找到可验证的简体中文字体；请安装简体中文字体，或将标签改为英文并使用 plot_style(cjk=False)"
            )
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


def _cjk_codepoints(text: str) -> set[int]:
    return {
        ord(char)
        for char in text
        if "\u3000" <= char <= "\u303f"
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\ufe10" <= char <= "\ufe1f"
        or "\ufe30" <= char <= "\ufe4f"
        or "\uff01" <= char <= "\uff60"
        or "\uffe0" <= char <= "\uffee"
        or "\uf900" <= char <= "\ufaff"
        or "\U00020000" <= char <= "\U0003134f"
    }


def audit_fonts(fig) -> list[dict[str, str]]:
    """验证图中可见中文文本（含全角标点）的实际字体及全部字形。"""
    # 刻度、图例和 colorbar 文本会在 draw 后完整生成；缺字由下方检查给出可定位错误。
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Glyph .* missing from font")
        fig.canvas.draw()
    audited: dict[tuple[str, str, str], dict[str, str]] = {}
    for artist in fig.findobj(match=Text):
        text = artist.get_text()
        codepoints = _cjk_codepoints(text)
        if not artist.get_visible() or not codepoints:
            continue
        properties = artist.get_fontproperties()
        try:
            path, family, style, charmap = _resolve_font(properties)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"中文文本无法解析字体：{text!r}") from exc
        if not _is_simplified_chinese_face(path, family):
            raise RuntimeError(
                f"中文文本解析到非简体中文区域字体：{text!r} -> {family} ({path})"
            )
        missing = sorted(chr(codepoint) for codepoint in codepoints if codepoint not in charmap)
        if missing:
            raise RuntimeError(
                f"中文字体缺少字形：{text!r} -> {family} ({path})，缺少 {''.join(missing)}"
            )
        key = (path, family, style)
        audited[key] = {"path": path, "family": family, "style": style}
    return list(audited.values())


def save(fig, stem: str | Path, pdf: bool = False, dpi: int = 160) -> Path:
    audit_fonts(fig)
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
