import json
import ctypes
import inspect
import unittest
from unittest.mock import patch

from app import (
    Config, HotkeyDetector, INPUT, QuickTranslator, calculate_panel_height,
    calculate_window_height, display_line_count,
    detect_translation_direction,
    hotkey_label, normalize_hotkey, normalize_pdf_layout, normalize_translation_output,
    normalize_theme, parse_glossary, resolve_theme, theme_label, translate_qwen_stream,
)


class FakeResponse:
    def __init__(self, lines):
        self.lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)


class TranslationTests(unittest.TestCase):
    def test_direction_detection(self):
        self.assertEqual(detect_translation_direction("细胞培养结果显著。"), ("Chinese", "English"))
        self.assertEqual(detect_translation_direction("Cell viability increased."), ("English", "Chinese"))

    def test_glossary_parsing(self):
        self.assertEqual(parse_glossary("power=统计功效; cell culture=细胞培养"), [
            {"source": "power", "target": "统计功效"},
            {"source": "cell culture", "target": "细胞培养"},
        ])

    def test_pdf_visual_line_breaks_are_reflowed(self):
        text = "所有工艺条件为NUMI\nSHEET 2011会议提出的基准。"
        self.assertEqual(normalize_pdf_layout(text), "所有工艺条件为NUMISHEET 2011会议提出的基准。")
        self.assertEqual(normalize_pdf_layout("inter-\nnational study"), "international study")

    def test_translation_spacing_is_cleaned_safely(self):
        source = "All process conditions follow NUMISHEET 2011."
        output = "所有工 艺条件均符合 NU MISHEET 2011。 HIGH SPEED mode"
        self.assertEqual(
            normalize_translation_output(output, source),
            "所有工艺条件均符合 NUMISHEET 2011。 HIGH SPEED mode",
        )

    def test_name_followed_by_number_does_not_gain_a_hard_break(self):
        self.assertEqual(
            normalize_pdf_layout("proposed under the NUMISHEET\n2011 conference"),
            "proposed under the NUMISHEET 2011 conference",
        )

    def test_pdf_reflow_preserves_paragraphs_lists_and_formulas(self):
        text = "第一段第一行\n第一段第二行\n\n- 条目一\n- 条目二\n\ny = ax + b\n下一段"
        result = normalize_pdf_layout(text)
        self.assertIn("第一段第一行第一段第二行\n\n", result)
        self.assertIn("- 条目一\n- 条目二", result)
        self.assertIn("y = ax + b\n下一段", result)

    def test_windows_input_structure_has_native_size(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_capture_path_never_writes_or_clears_clipboard(self):
        capture_source = "\n".join([
            inspect.getsource(QuickTranslator.capture_and_translate),
            inspect.getsource(QuickTranslator._read_selection),
            inspect.getsource(QuickTranslator._capture_after_hotkey_release),
        ])
        self.assertNotIn("clipboard_clear", capture_source)
        self.assertNotIn("clipboard_append", capture_source)

    def test_main_window_uses_native_windows_menu_bar(self):
        source = inspect.getsource(QuickTranslator._build_ui)
        self.assertIn("self.root.configure(menu=self.menu_bar)", source)
        self.assertIn("self.menu_bar.add_cascade", source)
        self.assertIn("self.window_menu.add_checkbutton", source)
        self.assertIn("font=MENU_FONT", source)
        self.assertNotIn("ttk.Menubutton", source)

    def test_settings_use_native_property_sheet_controls(self):
        source = inspect.getsource(QuickTranslator.open_settings)
        self.assertIn("ttk.Notebook", source)
        self.assertIn('text="确定"', source)
        self.assertIn('text="取消"', source)
        self.assertIn('text="应用"', source)
        self.assertNotIn("FluentButton", source)
        self.assertNotIn("Fluent.TEntry", source)

    def test_topmost_menu_state_is_persisted_without_custom_caption_button(self):
        source = inspect.getsource(QuickTranslator.toggle_topmost)
        self.assertIn("self.cfg.always_on_top = enabled", source)
        self.assertIn("self.cfg.save()", source)
        self.assertNotIn("pin_button", source)

    def test_streaming_chunks_do_not_resize_the_window(self):
        source = inspect.getsource(QuickTranslator.show_message)
        self.assertIn("if resize and not self._user_resized", source)
        self.assertNotIn("else 70", source)

    def test_legacy_double_ctrl_migrates_to_new_default(self):
        self.assertEqual(normalize_hotkey("双击 Ctrl"), "ctrl_double_c")
        self.assertEqual(hotkey_label("双击 Ctrl"), "按住 Ctrl，双击 C")

    def test_theme_values_are_normalized_and_labeled(self):
        self.assertEqual(normalize_theme("DARK"), "dark")
        self.assertEqual(normalize_theme("unknown"), "system")
        self.assertEqual(theme_label("light"), "浅色")
        self.assertEqual(resolve_theme("dark"), "dark")

    def test_window_height_tracks_content_without_old_blank_area(self):
        self.assertEqual(calculate_panel_height(4, 25), 184)
        self.assertEqual(calculate_window_height(11, 25, 1080), 359)
        self.assertEqual(calculate_window_height(1, 25, 1080), 190)
        self.assertEqual(calculate_window_height(100, 25, 1000), 720)

    def test_tk_display_line_transitions_are_converted_to_visible_lines(self):
        self.assertEqual(display_line_count(0), 1)
        self.assertEqual(display_line_count(1), 2)
        self.assertEqual(display_line_count("3"), 4)

    @patch("app.system_theme", return_value="dark")
    def test_system_theme_is_resolved_dynamically(self, mocked_system_theme):
        self.assertEqual(resolve_theme("system"), "dark")
        mocked_system_theme.assert_called_once_with()

    def test_ctrl_double_c_requires_ctrl_and_two_distinct_c_presses(self):
        detector = HotkeyDetector()
        self.assertFalse(detector.update("ctrl_double_c", 1.00, ctrl=True, alt=False, c=True, t=False))
        self.assertFalse(detector.update("ctrl_double_c", 1.05, ctrl=True, alt=False, c=False, t=False))
        self.assertTrue(detector.update("ctrl_double_c", 1.25, ctrl=True, alt=False, c=True, t=False))
        detector.reset()
        self.assertFalse(detector.update("ctrl_double_c", 2.00, ctrl=False, alt=False, c=True, t=False))

    def test_ctrl_alt_t_triggers_once_until_released(self):
        detector = HotkeyDetector()
        self.assertTrue(detector.update("ctrl_alt_t", 1.0, ctrl=True, alt=True, c=False, t=True))
        self.assertFalse(detector.update("ctrl_alt_t", 1.1, ctrl=True, alt=True, c=False, t=True))
        self.assertFalse(detector.update("ctrl_alt_t", 1.2, ctrl=True, alt=True, c=False, t=False))
        self.assertTrue(detector.update("ctrl_alt_t", 1.3, ctrl=True, alt=True, c=False, t=True))

    @patch("urllib.request.urlopen")
    def test_qwen_cumulative_stream_replaces_instead_of_appending(self, urlopen):
        chunks = ["译", "译文", "译文。"]
        lines = [
            "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) + "\n"
            for chunk in chunks
        ] + ["data: [DONE]\n"]
        urlopen.return_value = FakeResponse(lines)
        updates = []
        cfg = Config(qwen_api_key="test-key", glossary="power=统计功效")
        result = translate_qwen_stream("Statistical power", cfg, "qwen-mt-plus", [], updates.append)
        self.assertEqual(result, "译文。")
        self.assertEqual(updates, chunks)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["translation_options"]["source_lang"], "English")
        self.assertEqual(payload["translation_options"]["terms"][0]["target"], "统计功效")


if __name__ == "__main__":
    unittest.main()
