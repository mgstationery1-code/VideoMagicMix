import unittest
import tempfile
from pathlib import Path

from openpyxl import Workbook

from backend.asr import TranscriptSegment, TranscriptWord, match_body_cues, match_transcript
from backend.excel_parser import import_workbook
from backend.mixer import MediaInfo, body_combination, body_combinations, body_threshold_for_count, complete_hook_candidates, list_videos
from backend.models import SubtitleStyle
from backend.planning import allocate_round_robin, normalize_output_limit
from backend.subtitles import build_ass, sanitize_style, split_subtitle_text


class CoreTests(unittest.TestCase):
    def test_all_hooks_are_complete_candidates(self):
        files = [MediaInfo("a.mp4", 9), MediaInfo("b.mp4", 10.5), MediaInfo("c.mp4", 5)]
        matches = complete_hook_candidates(files)
        self.assertEqual([x.files[0].path for x in matches], ["a.mp4", "b.mp4", "c.mp4"])
        self.assertTrue(all(item.speed_factor == 1.0 for item in matches))
        self.assertTrue(all(item.source_duration == item.target_duration for item in matches))

    def test_body_combination_no_duplicate(self):
        result = body_combination([MediaInfo("a", 4), MediaInfo("b", 5), MediaInfo("c", 7)], 9, 1.1)
        self.assertTrue(result.accepted)
        self.assertEqual({x.path for x in result.files}, {"a", "b"})

    def test_body_combinations_estimate_multiple_options(self):
        results = body_combinations([MediaInfo("a", 4), MediaInfo("b", 5), MediaInfo("c", 7), MediaInfo("d", 5)], 9, 1.1)
        self.assertGreaterEqual(len(results), 2)
        self.assertTrue(all(len({item.path for item in result.files}) == len(result.files) for result in results))

    def test_body_threshold_for_count_covers_requested_candidates(self):
        files = [MediaInfo("a", 4), MediaInfo("b", 5), MediaInfo("c", 7), MediaInfo("d", 5)]
        threshold = body_threshold_for_count(files, 9, 4)
        self.assertIsNotNone(threshold)
        combinations = body_combinations(files, 9, threshold)
        self.assertGreaterEqual(len(combinations), 4)

    def test_body_threshold_for_count_returns_none_without_combinations(self):
        self.assertIsNone(body_threshold_for_count([MediaInfo("a", 0)], 9, 1))

    def test_ass_style_has_explicit_size_and_position(self):
        ass = build_ass([(0, 2.5, "这是一段字幕文本")], SubtitleStyle(font_name="Test Font", font_size=52, alignment=2, outline_width=3, shadow=1))
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn("Style: Default,Test Font,52", ass)
        self.assertIn(",3,1,2,40,40,120,1", ass)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.50", ass)
        self.assertIn(r"{\an2\pos(540,1800)}", ass)

    def test_ass_events_never_overlap(self):
        ass = build_ass([(0, 2, "第一句"), (1.9, 3, "第二句"), (3, 4, "第三句")], SubtitleStyle())
        dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue), 3)
        self.assertIn("0:00:00.00,0:00:01.90", dialogue[0])
        self.assertIn("0:00:01.90,0:00:03.00", dialogue[1])

    def test_ass_style_keeps_one_fixed_line(self):
        ass = build_ass([(0, 2, "甲乙丙丁戊己庚辛壬癸" * 20)], SubtitleStyle(font_name="Test Font", font_size=52, max_width=50))
        self.assertNotIn(r"\N", ass)
        self.assertIn(r"{\an2\pos(540,1800)}", ass)

    def test_subtitle_style_is_clamped(self):
        style = sanitize_style(SubtitleStyle(font_name="", font_size=999, outline_width=-2, shadow=99, margin_vertical=9999, max_width=1, max_chars_per_cue=99), None)
        self.assertEqual(style.font_size, 96)
        self.assertEqual(style.outline_width, 0)
        self.assertEqual(style.shadow, 6)
        self.assertEqual(style.margin_vertical, 600)
        self.assertEqual(style.max_width, 50)
        self.assertEqual(style.max_chars_per_cue, 50)

    def test_subtitle_splits_punctuation_then_character_limit(self):
        source = "这学期开学，我把孩子的铅笔都换成了晨光这支绿杆黑木铅笔"
        cues = split_subtitle_text(source, 10)
        self.assertEqual(cues[0], "这学期开学")
        self.assertTrue(all(1 <= len(cue) <= 10 for cue in cues))
        self.assertEqual("".join(cues), source.replace("，", ""))
        self.assertEqual(split_subtitle_text("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳", 10), ["甲乙丙丁戊己庚辛壬癸", "子丑寅卯辰巳"])

    def test_body_cues_follow_word_timestamps_and_keep_pause(self):
        transcript = [TranscriptSegment("这学期开学 我把孩子的铅笔", 0, 4, words=[
            TranscriptWord("这学期开学", 0.2, 1.0, 0.9),
            TranscriptWord("我把孩子的铅笔", 2.0, 4.0, 0.8),
        ])]
        matches, failures = match_body_cues([("Body1", "这学期开学，我把孩子的铅笔")], transcript, 10)
        self.assertFalse(failures)
        cues = matches[0]["subtitle_cues"]
        self.assertEqual([cue.text for cue in cues], ["这学期开学", "我把孩子的铅笔"])
        self.assertAlmostEqual(cues[0].end_seconds, 1.0)
        self.assertAlmostEqual(cues[1].start_seconds, 2.0)

    def test_list_videos_recurses_into_subdirectories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "root.mp4").write_bytes(b"")
            nested = root / "nested" / "deeper"
            nested.mkdir(parents=True)
            (nested / "child.MOV").write_bytes(b"")
            self.assertEqual({Path(item).name for item in list_videos(str(root))}, {"root.mp4", "child.MOV"})

    def test_output_limit_and_round_robin_allocation(self):
        self.assertIsNone(normalize_output_limit(""))
        self.assertIsNone(normalize_output_limit("0"))
        self.assertEqual(normalize_output_limit("50"), 50)
        with self.assertRaises(ValueError):
            normalize_output_limit("-1")
        self.assertEqual(allocate_round_robin(5, [4, 4]), [3, 2])
        self.assertEqual(allocate_round_robin(50, [104]), [50])
        self.assertEqual(allocate_round_robin(None, [4, 2]), [4, 2])

    def test_ordered_transcript_matching(self):
        transcript = [TranscriptSegment("\u4f60\u597d\u6211\u662f", 0, 1), TranscriptSegment("AAA", 1, 2), TranscriptSegment("\u5173\u952e\u662f\u523b\u5b57", 2, 4)]
        matches, failures = match_transcript(["\u4f60\u597d\u6211\u662fAAA", "\u5173\u952e\u662f\u523b\u5b57"], transcript)
        self.assertFalse(failures)
        self.assertEqual(matches[0]["start"], 0)
        self.assertEqual(matches[1]["start"], 2)

    def test_empty_body_is_warning_and_disabled(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["行标识", "分组", "字幕1", "字幕2"])
        sheet.append(["Hook", "", "你好", "你好"])
        sheet.append(["Body1", "刻字", "刻字文案", "刻字文案2"])
        sheet.append(["Body4", "防咬笔", "", "防咬笔文案"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "script.xlsx"
            workbook.save(path)
            _, schemes, errors, warnings = import_workbook(str(path))
        self.assertFalse(errors)
        self.assertFalse(schemes[0].segments[0].enabled)
        self.assertIn("已忽略", schemes[0].segments[0].skip_reason)
        self.assertTrue(any("Body4" in warning for warning in warnings))
        self.assertFalse(schemes[0].segments[-1].enabled)
        self.assertTrue(schemes[1].segments[-1].enabled)

    def test_hook_text_is_fully_ignored_even_when_empty(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["行标识", "分组", "字幕1"])
        sheet.append(["Hook", "", ""])
        sheet.append(["Body1", "正文", "正文文案"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hook-ignored.xlsx"
            workbook.save(path)
            _, schemes, errors, warnings = import_workbook(str(path))
        self.assertFalse(errors)
        self.assertFalse(any("Hook" in warning for warning in warnings))
        self.assertFalse(schemes[0].segments[0].enabled)

    def test_empty_body_does_not_break_following_matching(self):
        transcript = [TranscriptSegment("你好", 0, 1), TranscriptSegment("Body5", 1, 2)]
        matches, failures = match_transcript(["你好", "Body5"], transcript)
        self.assertFalse(failures)
        self.assertEqual(matches[1]["start"], 1)


if __name__ == "__main__":
    unittest.main()
