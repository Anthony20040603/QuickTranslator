import json
import ctypes
import inspect
import unittest
from unittest.mock import patch

from app import (
    Config, INPUT, PROVIDER_PRESETS, QuickTranslator, detect_translation_direction, parse_glossary,
    normalize_pdf_layout, normalize_translation_output, translate_qwen_stream,
    translate_glm_stream,
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
            inspect.getsource(QuickTranslator._capture_after_ctrl_release),
        ])
        self.assertNotIn("clipboard_clear", capture_source)
        self.assertNotIn("clipboard_append", capture_source)

    def test_main_provider_presets_are_complete(self):
        for provider in ("智谱 GLM", "DeepSeek", "Kimi / Moonshot", "硅基流动", "OpenAI"):
            self.assertTrue(PROVIDER_PRESETS[provider]["api_url"].startswith("https://"))
            self.assertTrue(PROVIDER_PRESETS[provider]["model"])

    @patch("urllib.request.urlopen")
    def test_non_zhipu_provider_omits_zhipu_only_parameter(self, urlopen):
        urlopen.return_value = FakeResponse([
            'data: {"choices":[{"delta":{"content":"译文"}}]}\n',
            "data: [DONE]\n",
        ])
        cfg = Config(provider="DeepSeek", api_key="test-key", model="deepseek-chat")
        self.assertEqual(translate_glm_stream("Paper", cfg, lambda chunk: None), "译文")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("thinking", payload)

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
