from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "plot_kit.py"
SPEC = importlib.util.spec_from_file_location("customer_delivery_plot_kit", MODULE_PATH)
plot_kit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(plot_kit)


class PlotKitFontTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")
        plot_kit._face_details.cache_clear()

    def test_cjk_font_rejects_requested_sc_when_actual_ttc_face_is_jp(self):
        installed = [SimpleNamespace(name="Noto Sans CJK SC")]
        jp_face = ("/fonts/NotoSansCJK-Regular.ttc", "Noto Sans CJK JP", "Regular", frozenset(map(ord, plot_kit._SC_PROBE)))
        with mock.patch.object(plot_kit.font_manager.fontManager, "ttflist", installed), \
             mock.patch.object(plot_kit, "_CJK_CANDIDATES", ["Noto Sans CJK SC"]), \
             mock.patch.object(plot_kit, "_resolve_font", return_value=jp_face):
            self.assertIsNone(plot_kit.cjk_font())

    def test_renamed_file_cannot_make_traditional_face_safe(self):
        self.assertFalse(
            plot_kit._is_simplified_chinese_face("/fonts/custom-sc.otf", "Source Han Sans TC")
        )

    def test_generic_fallback_family_is_rejected(self):
        self.assertFalse(
            plot_kit._is_simplified_chinese_face("/fonts/DroidSansFallback.ttf", "Droid Sans Fallback")
        )

    def test_installed_japanese_face_is_rejected(self):
        properties = font_manager.FontProperties(family=["Noto Sans CJK JP"])
        try:
            path = font_manager.findfont(properties, fallback_to_default=False)
        except ValueError:
            self.skipTest("Noto Sans CJK JP is not installed")
        family, _style, _charmap = plot_kit._face_details(path)
        self.assertEqual(family, "Noto Sans CJK JP")
        self.assertFalse(plot_kit._is_simplified_chinese_face(path, family))

    def test_cjk_font_checks_normal_and_semibold(self):
        installed = [SimpleNamespace(name="WenQuanYi Zen Hei")]
        valid = ("/fonts/wqy.ttf", "WenQuanYi Zen Hei", "Regular", frozenset(map(ord, plot_kit._SC_PROBE)))
        missing_bold = ("/fonts/wqy.ttf", "WenQuanYi Zen Hei", "Regular", frozenset())
        with mock.patch.object(plot_kit.font_manager.fontManager, "ttflist", installed), \
             mock.patch.object(plot_kit, "_CJK_CANDIDATES", ["WenQuanYi Zen Hei"]), \
             mock.patch.object(plot_kit, "_resolve_font", side_effect=[valid, missing_bold]) as resolve:
            self.assertIsNone(plot_kit.cjk_font())
            self.assertEqual(resolve.call_count, 2)

    def test_plot_style_fails_clearly_without_safe_font(self):
        with mock.patch.object(plot_kit, "cjk_font", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "plot_style\\(cjk=False\\)"):
                with plot_kit.plot_style():
                    pass

    def test_audit_rejects_japanese_face_even_with_glyphs(self):
        fig, ax = plt.subplots()
        ax.set_title("全部样品对的一致率分布")
        jp_face = ("/fonts/NotoSansCJK-Regular.ttc", "Noto Sans CJK JP", "Regular", frozenset(map(ord, ax.get_title())))
        with mock.patch.object(plot_kit, "_resolve_font", return_value=jp_face):
            with self.assertRaisesRegex(RuntimeError, "非简体中文区域字体"):
                plot_kit.audit_fonts(fig)

    def test_audit_rejects_missing_chinese_glyph(self):
        fig, ax = plt.subplots()
        ax.set_xlabel("检出率，")
        sc_face = ("/fonts/wqy.ttf", "WenQuanYi Zen Hei", "Regular", frozenset(map(ord, "检出率")))
        with mock.patch.object(plot_kit, "_resolve_font", return_value=sc_face):
            with self.assertRaisesRegex(RuntimeError, "缺少 ，"):
                plot_kit.audit_fonts(fig)

    def test_punctuation_only_text_cannot_bypass_region_audit(self):
        fig, ax = plt.subplots()
        ax.set_title("〇，。")
        jp_face = ("/fonts/jp.otf", "Noto Sans CJK JP", "Regular", frozenset(map(ord, "〇，。")))
        with mock.patch.object(plot_kit, "_resolve_font", return_value=jp_face):
            with self.assertRaisesRegex(RuntimeError, "非简体中文区域字体"):
                plot_kit.audit_fonts(fig)

    def test_save_does_not_write_when_font_audit_fails(self):
        with tempfile.TemporaryDirectory(prefix="plot-kit-", dir=Path(__file__).parent) as directory:
            output = Path(directory) / "invalid-font"
            fig, ax = plt.subplots()
            ax.set_title("一致率")
            jp_face = ("/fonts/renamed-sc.otf", "Source Han Sans JP", "Regular", frozenset(map(ord, "一致率")))
            with mock.patch.object(plot_kit, "_resolve_font", return_value=jp_face):
                with self.assertRaisesRegex(RuntimeError, "非简体中文区域字体"):
                    plot_kit.save(fig, output)
            self.assertFalse(output.with_suffix(".png").exists())

    def test_valid_chinese_figure_uses_real_safe_font_and_saves(self):
        family = plot_kit.cjk_font()
        if family is None:
            self.skipTest("环境没有经过验证的简体中文字体")
        with tempfile.TemporaryDirectory(prefix="plot-kit-", dir=Path(__file__).parent) as directory:
            output = Path(directory) / "valid-chinese"
            with plot_kit.plot_style():
                fig, ax = plt.subplots()
                ax.set_title("关联样品的两两一致率")
                records = plot_kit.audit_fonts(fig)
                self.assertTrue(records)
                self.assertTrue(all(plot_kit._is_simplified_chinese_face(r["path"], r["family"]) for r in records))
                self.assertEqual(plot_kit.save(fig, output), output.with_suffix(".png"))
                self.assertTrue(output.with_suffix(".png").is_file())

    def test_english_only_save_works_without_cjk_font(self):
        with tempfile.TemporaryDirectory(prefix="plot-kit-", dir=Path(__file__).parent) as directory:
            output = Path(directory) / "english-only"
            with mock.patch.object(plot_kit, "cjk_font", return_value=None):
                with plot_kit.plot_style(cjk=False):
                    fig, ax = plt.subplots()
                    ax.set_title("Pairwise concordance")
                    self.assertEqual(plot_kit.audit_fonts(fig), [])
                    self.assertEqual(plot_kit.save(fig, output), output.with_suffix(".png"))
                    self.assertTrue(output.with_suffix(".png").is_file())


if __name__ == "__main__":
    unittest.main()
