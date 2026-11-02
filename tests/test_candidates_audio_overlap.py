from __future__ import annotations

import itertools
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.combination_previews import PreviewStore, render_body_preview
from backend.mixer import MediaInfo, Selection, body_combinations, body_speed_range_for_count, combination_id, probe_media, render_video, resolve_binary
from backend.models import Project, ScriptScheme, ScriptSegment
from backend.planning import RenderJob, diversified_jobs, footage_overlap, select_low_overlap_jobs, select_batch_jobs
from backend.rotation import body_footage, body_overlap
from backend.runtime import text_subprocess_kwargs


def selected(path: str, source: float, target: float | None = None) -> Selection:
    target = target if target is not None else source
    return Selection([MediaInfo(path, source)], source, target, target / source, 1.4, True)


class SpeedAndOverlapTests(unittest.TestCase):
    def test_fifty_outputs_out_of_one_hundred_four_available(self):
        schemes = [("s", [selected(f"h{i}", 3) for i in range(2)],
                    [[selected(f"a{i}", 10) for i in range(13)], [selected(f"b{i}", 10) for i in range(4)]])]
        jobs, report = select_batch_jobs(schemes, 50)
        self.assertEqual(len(jobs), 50)
        self.assertEqual(report["examined"], 104)
        self.assertFalse(report["shortage"])
        self.assertEqual(report["pair_count"], 1225)

    def test_quantity_reduction_reselects_diverse_batch_without_cap(self):
        schemes = [("s", [selected(f"h{i}", 3) for i in range(2)],
                    [[selected(f"b{g}-{i}", 10) for i in range(3)] for g in range(2)])]
        full, report = select_batch_jobs(schemes, None)
        reduced, smaller = select_batch_jobs(schemes, 3)
        self.assertEqual(len(full), 18)
        self.assertEqual(len(reduced), 3)
        self.assertLess(smaller["max_pair_overlap"], report["max_pair_overlap"])
        self.assertAlmostEqual(smaller["max_pair_overlap"], max(footage_overlap(a, b) for a, b in itertools.combinations(reduced, 2)))
        self.assertEqual(smaller["pair_count"], 3)
        self.assertIsNone(smaller["upper_limit"])
        first = list(itertools.islice(diversified_jobs(schemes), 3))
        self.assertLess(smaller["max_pair_overlap"], max(footage_overlap(a, b) for a, b in itertools.combinations(first, 2)))
        self.assertEqual([[s.files[0].path for s in job.selections] for job in reduced],
                         [[s.files[0].path for s in job.selections] for job in select_batch_jobs(schemes, 3)[0]])

    def test_single_video_and_bounded_search_are_explicit(self):
        schemes = [("s", [selected("h", 3)], [[selected(str(i), 10) for i in range(10)]])]
        jobs, report = select_batch_jobs(schemes, 1)
        self.assertIsNone(report["max_pair_overlap"])
        self.assertEqual(report["pair_count"], 0)
        jobs, report = select_batch_jobs(schemes, None, max_candidates=4, max_outputs=3)
        self.assertEqual(len(jobs), 3)
        self.assertTrue(report["search_limited"])
        self.assertTrue(report["shortage"])
        self.assertFalse(report["all_candidates_examined"])
        self.assertEqual(report["stop_reason"], "search_limit")
        self.assertAlmostEqual(report["max_pair_overlap"], max(footage_overlap(a, b) for a, b in itertools.combinations(jobs, 2)))

    def test_default_speed_bounds_include_slowdown_and_speedup(self):
        files = [MediaInfo(str(index), duration) for index, duration in enumerate([8.9, 9, 10, 14, 14.1])]
        options = body_combinations(files, 10, 1.4)
        self.assertEqual([item.source_duration for item in options], [10, 9, 14])
        self.assertEqual(body_combinations([MediaInfo("short", 9)], 10, 1.4)[0].speed_factor, 10 / 9)

    def test_requested_count_expands_outside_defaults_and_respects_exclusions(self):
        files = [MediaInfo("a", 9), MediaInfo("b", 10), MediaInfo("c", 14), MediaInfo("d", 15)]
        excluded = [combination_id(selected("b", 10))]
        low, high = body_speed_range_for_count(files, 10, 3, excluded)
        options = [item for item in body_combinations(files, 10, high, min_speed=low) if combination_id(item) not in excluded]
        self.assertGreaterEqual(len(options), 3)
        self.assertGreaterEqual(high, 1.5)

    def test_shared_body_with_new_hook_is_ninety_percent_overlap(self):
        first = RenderJob("s", [selected("hook-a", 3), selected("body", 30)])
        second = RenderJob("s", [selected("hook-b", 3), selected("body", 30)])
        self.assertAlmostEqual(footage_overlap(first, second), 30 / 33)
        jobs, report = select_low_overlap_jobs([("s", [selected("hook-a", 3), selected("hook-b", 3)], [[selected("body", 30)]])], 2, 0.3)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["examined"], 2)

    def test_every_selected_pair_obeys_overlap_limit(self):
        schemes = [("s", [selected(f"hook-{i}", 3) for i in range(3)],
                    [[selected(f"body-{g}-{i}", 10) for i in range(3)] for g in range(3)])]
        jobs, report = select_low_overlap_jobs(schemes, 20, .30)
        self.assertGreaterEqual(len(jobs), 3)
        self.assertTrue(all(footage_overlap(a, b) <= .3 + 1e-9 for a, b in itertools.combinations(jobs, 2)))
        self.assertLessEqual(report["max_pair_overlap"], .30)

    def test_diversification_visits_each_tuple_once_and_varies_body_across_hooks(self):
        hooks = [selected("h1", 3), selected("h2", 3)]
        pools = [[selected(f"b{g}-{i}", 5) for i in range(3)] for g in range(2)]
        jobs = list(diversified_jobs([("s", hooks, pools), ("s2", hooks, pools)]))
        identities = {(j.scheme_id, *(s.files[0].path for s in j.selections)) for j in jobs}
        self.assertEqual(len(jobs), 36)
        self.assertEqual(len(identities), len(jobs))
        self.assertNotEqual([s.files[0].path for s in jobs[0].selections[1:]], [s.files[0].path for s in jobs[1].selections[1:]])
        self.assertEqual({job.scheme_id for job in jobs[:4]}, {"s", "s2"})


class ProjectCandidateAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        hooks, body = self.root / "hooks", self.root / "body"
        hooks.mkdir(); body.mkdir()
        self.infos = {}
        for folder, name, duration in [(hooks, "h1.mp4", 3), (hooks, "h2.mp4", 3), (body, "A.mp4", 9), (body, "B.mp4", 10), (body, "C.mp4", 14)]:
            file = folder / name
            file.touch()
            self.infos[str(file)] = MediaInfo(str(file), duration)
        self.project = Project("feature-test", "测试", str(self.root / "script.xlsx"), "Sheet", hook_dir=str(hooks), output_dir=str(self.root))
        self.project.schemes = [ScriptScheme("s1", "C", "字幕1", [ScriptSegment("Body1", "刻字", "这是第一段", "这是第一段", start_seconds=0, end_seconds=10)], voice_path=str(self.root / "voice.wav"), match_status="matched")]
        Path(self.project.schemes[0].voice_path).touch()
        self.project.body_mapping["Body1"] = str(body)
        main.projects[self.project.project_id] = self.project
        self.probe = patch.object(main, "probe_media_cached", side_effect=lambda path: self.infos[str(path)])
        self.probe.start()
        self.client = TestClient(main.app)
        self.endpoint = f"/api/projects/{self.project.project_id}"
        self.body = self.endpoint + "/schemes/s1/bodies/Body1"

    def tearDown(self):
        self.client.close()
        self.probe.stop()
        main.projects.pop(self.project.project_id, None)
        main.render_plans.pop(self.project.project_id, None)
        main.preflight_requests.pop(self.project.project_id, None)
        self.temp.cleanup()

    def test_defaults_and_unchecked_candidates_are_consistent_in_preflight_and_render(self):
        listing = self.client.get(self.body + "/combinations").json()
        self.assertEqual(listing["selected_count"], 3)
        rejected = listing["combinations"][0]["id"]
        response = self.client.post(self.body + "/selection", data={"identity": rejected, "selected": "false"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["config"]["actual_candidate_count"], 2)
        updated = self.client.get(self.body + "/combinations").json()
        self.assertEqual(len(updated["combinations"]), 3)  # Still available for rechecking.
        self.assertFalse(next(item for item in updated["combinations"] if item["id"] == rejected)["selected"])
        check = self.client.post(self.endpoint + "/preflight").json()
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["estimated_outputs"], 4)  # 2 Hooks x 2 selected Body options.
        self.project.output_limit = 3
        self.project.voice_volume, self.project.hook_volume, self.project.bgm_volume = 4, 3, 2
        check = self.client.post(self.endpoint + "/preflight").json()
        with patch.object(main, "render_video") as renderer:
            response = self.client.post(self.endpoint + "/render", data={"plan_id": check["plan_id"], "accept_warnings": "true"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 3)
        self.assertTrue(all(combination_id(call.args[0][1]) != rejected for call in renderer.call_args_list))
        self.assertTrue(all(call.kwargs["voice_volume"] == 4 and call.kwargs["hook_volume"] == 3 for call in renderer.call_args_list))

    def test_preflight_overlap_plan_is_exactly_what_render_uses(self):
        self.project.max_overlap = .30
        self.project.output_limit = 6
        check = self.client.post(self.endpoint + "/preflight").json()
        self.assertEqual(check["estimated_outputs"], 6)
        self.assertEqual(check["planned_outputs"], 6)
        self.assertTrue(check["requires_acceptance"])
        blocked = self.client.post(self.endpoint + "/render", data={"plan_id": check["plan_id"]})
        self.assertEqual(blocked.status_code, 409)
        locked = main.render_plans[self.project.project_id]["jobs"]
        with patch.object(main, "render_video") as renderer:
            result = self.client.post(self.endpoint + "/render", data={"plan_id": check["plan_id"], "accept_warnings": "true"}).json()
        jobs = [RenderJob("s1", call.args[0]) for call in renderer.call_args_list]
        self.assertEqual(result["count"], 6)
        self.assertEqual(jobs, locked)
        self.assertGreater(result["overlap"]["violating_pairs"], 0)

    def test_render_requires_preflight_and_consumes_the_exact_plan_once(self):
        self.assertEqual(self.client.post(self.endpoint + "/render").status_code, 409)
        self.project.output_limit = 3
        check = self.client.post(self.endpoint + "/preflight").json()
        self.assertTrue(check["ok"], check)
        jobs = main.render_plans[self.project.project_id]["jobs"]
        self.assertAlmostEqual(check["overlap"]["max_pair_overlap"], max(body_overlap(body_footage(a), body_footage(b)) for a, b in itertools.combinations(jobs, 2)))
        with patch.object(main, "preflight", side_effect=AssertionError("Must not preflight during rendering")), patch.object(main, "select_batch_jobs", side_effect=AssertionError("Must not reselect")), patch.object(main, "render_video") as renderer:
            response = self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([call.args[0] for call in renderer.call_args_list], [job.selections for job in jobs])
        self.assertEqual(response.json()["plan_id"], check["plan_id"])
        self.assertEqual(self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"]}).status_code, 409)

    def test_changed_settings_selection_or_timing_invalidates_plan(self):
        edits = [lambda: setattr(self.project, "output_limit", 2),
                 lambda: setattr(self.project, "voice_volume", 3),
                 lambda: setattr(self.project.subtitle_style, "font_size", 64),
                 lambda: setattr(self.project.schemes[0].segments[0], "end_seconds", 11),
                 lambda: self.project.body_configs["s1"]["Body1"].excluded_combinations.append("changed")]
        for edit in edits:
            check = self.client.post(self.endpoint + "/preflight").json()
            self.assertTrue(check["ok"], check)
            edit()
            with patch.object(main, "render_video") as renderer:
                result = self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"]})
                self.assertEqual(result.status_code, 409, result.text)
                renderer.assert_not_called()

    def test_source_added_removed_or_modified_invalidates_plan(self):
        source = Path(self.project.body_mapping["Body1"]) / "A.mp4"
        extra = source.parent / "extra.mp4"
        for edit in [lambda: extra.touch(), lambda: extra.unlink(), lambda: source.write_bytes(b"changed"), lambda: source.unlink()]:
            if extra.exists():
                self.infos[str(extra)] = MediaInfo(str(extra), 10)
            check = self.client.post(self.endpoint + "/preflight").json()
            self.assertTrue(check["ok"], check)
            edit()
            result = self.client.post(self.endpoint + "/render", data={"plan_id": check["plan_id"]})
            self.assertEqual(result.status_code, 409, result.text)

    def test_saving_same_settings_or_reading_candidates_does_not_expire_plan(self):
        self.project.output_limit = None
        self.project.max_overlap = None
        check = self.client.post(self.endpoint + "/preflight").json()
        saved = self.client.post(self.endpoint + "/settings", data={"hook_dir":self.project.hook_dir, "output_dir":self.project.output_dir})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.client.get(self.body + "/combinations")
        with patch.object(main, "render_video"):
            result = self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"], "accept_warnings": "true"})
        self.assertEqual(result.status_code, 200, result.text)

    def test_failure_reports_counts_and_plan_cannot_be_reused(self):
        self.project.output_limit = 3
        check = self.client.post(self.endpoint + "/preflight").json()
        with patch.object(main, "render_video", side_effect=[None, RuntimeError("FFmpeg test failure")]):
            result = self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"]})
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.json()["detail"]["generated_count"], 1)
        self.assertEqual(result.json()["detail"]["failed_index"], 2)
        self.assertEqual(result.json()["detail"]["remaining_count"], 2)
        self.assertEqual(main.render_plans[self.project.project_id]["status"], "failed")

    def test_changes_during_preflight_and_replaced_plan_are_rejected(self):
        original = main.select_batch_jobs
        def changing(*args):
            self.project.hook_volume = 3
            return original(*args)
        with patch.object(main, "select_batch_jobs", side_effect=changing):
            check = self.client.post(self.endpoint + "/preflight").json()
        self.assertFalse(check["ok"])
        self.assertIsNone(check["plan_id"])
        first = self.client.post(self.endpoint + "/preflight").json()
        second = self.client.post(self.endpoint + "/preflight").json()
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(self.client.post(self.endpoint + "/render", data={"plan_id":first["plan_id"]}).status_code, 409)

    def test_older_preflight_cannot_replace_newer_plan(self):
        original = main.select_batch_jobs
        nested = []
        def newer_preflight(*args):
            if not nested:
                nested.append(None)
                nested[0] = main.preflight(self.project.project_id)
            return original(*args)
        with patch.object(main, "select_batch_jobs", side_effect=newer_preflight):
            older = main.preflight(self.project.project_id)
        self.assertFalse(older["ok"])
        self.assertTrue(nested[0]["ok"])
        self.assertEqual(main.render_plans[self.project.project_id]["plan_id"], nested[0]["plan_id"])

    def test_running_batch_uses_snapshot_and_blocks_another_preflight(self):
        self.project.output_limit = 3
        check = self.client.post(self.endpoint + "/preflight").json()
        def mutate_current(*args, **kwargs):
            self.project.voice_volume = 5
            blocked = self.client.post(self.endpoint + "/preflight")
            self.assertEqual(blocked.status_code, 409)
        with patch.object(main, "render_video", side_effect=mutate_current) as renderer:
            result = self.client.post(self.endpoint + "/render", data={"plan_id":check["plan_id"]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(renderer.call_count, 3)
        self.assertTrue(all(call.kwargs["voice_volume"] == 1 for call in renderer.call_args_list))

    def test_gain_range_and_overlap_validation(self):
        response = self.client.post(self.endpoint + "/settings", data={"voice_volume": "5", "hook_volume": "4", "bgm_volume": "3", "max_overlap": "30"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["voice_volume"], 5)
        self.assertEqual(response.json()["max_overlap"], .3)
        for data in [{"voice_volume": "5.1"}, {"bgm_volume": "nan"}, {"max_overlap": "-1"}, {"max_overlap": "101"}]:
            self.assertEqual(self.client.post(self.endpoint + "/settings", data=data).status_code, 400)

    def test_window_settings_and_acceptance_are_bound_to_current_plan(self):
        for value in ['0', '11', '1.5']:
            response = self.client.post(self.endpoint + '/settings', data={'recent_window':value})
            self.assertIn(response.status_code, [400,422])
        self.assertEqual(self.client.post(self.endpoint + '/settings', data={'output_limit':'5001'}).status_code, 400)
        self.project.output_limit = 6
        self.project.recent_window = 3
        self.project.max_overlap = 0
        check = self.client.post(self.endpoint + '/preflight').json()
        self.assertTrue(check['requires_acceptance'])
        with patch.object(main, 'render_video') as renderer:
            blocked = self.client.post(self.endpoint + '/render', data={'plan_id':check['plan_id']})
            self.assertEqual(blocked.status_code, 409)
            renderer.assert_not_called()
            self.project.recent_window = 1
            stale = self.client.post(self.endpoint + '/render', data={'plan_id':check['plan_id'], 'accept_warnings':'true'})
            self.assertEqual(stale.status_code, 409)
            renderer.assert_not_called()

    def test_skipped_body_labels_survive_preflight_audit(self):
        segment = self.project.schemes[0].segments[0]
        segment.row_key = 'Body5'
        self.project.body_mapping['Body5'] = self.project.body_mapping.pop('Body1')
        self.project.schemes[0].segments.insert(0, ScriptSegment('Body1', '空', '', '', enabled=False))
        check = self.client.post(self.endpoint + '/preflight').json()
        self.assertTrue(check['ok'], check)
        self.assertEqual(check['overlap']['groups'][0]['row_key'], 'Body5')
        self.assertEqual(check['overlap']['preview_jobs'][0]['bodies'][0]['row_key'], 'Body5')

    def test_listing_is_lazy_and_prerender_contains_only_checked_options(self):
        with patch.object(main.body_previews, "start", return_value={"task_id": "fake", "total": 2}) as start:
            listing = self.client.get(self.body + "/combinations").json()
            start.assert_not_called()
            self.client.post(self.body + "/selection", data={"identity": listing["combinations"][0]["id"], "selected": "false"})
            response = self.client.post(self.body + "/pre-render")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(start.call_args.args[1]), 2)
            self.assertNotIn(listing["combinations"][0]["id"], [combination_id(option) for option in start.call_args.args[1]])

    def test_count_expansion_is_per_scheme_and_threshold_edit_updates_count(self):
        self.project.schemes.append(ScriptScheme("s2", "D", "字幕2", [ScriptSegment("Body1", "刻字", "第二段", "第二段", start_seconds=0, end_seconds=20)]))
        config = self.client.post(self.endpoint + "/body-config", data={"scheme_id":"s1", "row_key":"Body1", "changed_by":"candidate_count", "candidate_count":"4"}).json()["config"]
        self.assertGreaterEqual(config["actual_candidate_count"], 4)
        self.assertGreater(config["threshold"], 1.4)
        other = self.client.get(self.endpoint + "/schemes/s2/bodies/Body1/combinations").json()
        self.assertEqual((other["min_speed"], other["threshold"]), (.9, 1.4))
        config = self.client.post(self.endpoint + "/body-config", data={"scheme_id":"s1", "row_key":"Body1", "changed_by":"threshold", "min_speed":".99", "threshold":"1.01"}).json()["config"]
        self.assertEqual(config["candidate_count"], 1)

    def test_audio_audition_uses_selected_body_time_and_gain_without_bgm(self):
        segment = self.project.schemes[0].segments[0]
        segment.start_seconds, segment.end_seconds = 5, 15
        self.project.voice_volume = 4
        def ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(b"RIFFmock")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(main, "probe_duration", return_value=20), patch.object(main.subprocess, "run", side_effect=ffmpeg) as run:
            response = self.client.post(self.endpoint + "/audio-preview", data={"scheme_id":"s1", "row_key":"Body1"})
        self.assertEqual(response.status_code, 200, response.text)
        command = run.call_args.args[0]
        filters = command[command.index("-filter_complex") + 1]
        self.assertIn("atrim=start=5.000000:duration=10.000000", filters)
        self.assertIn("volume=4.0000", filters)

    def test_hook_audition_never_loads_bgm_and_body_music_uses_narration_time(self):
        self.project.bgm_path = str(self.root / "music.wav")
        segment = self.project.schemes[0].segments[0]
        segment.start_seconds, segment.end_seconds = 5, 15

        def ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(b"RIFFmock")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(main, "probe_duration", return_value=20), patch.object(main.subprocess, "run", side_effect=ffmpeg) as run:
            response = self.client.post(self.endpoint + "/audio-preview", data={"scheme_id": "s1", "row_key": "Hook"})
            self.assertEqual(response.status_code, 200, response.text)
            command = run.call_args.args[0]
            self.assertNotIn(self.project.bgm_path, command)
            self.assertNotIn("amix", command[command.index("-filter_complex") + 1])

            response = self.client.post(self.endpoint + "/audio-preview", data={"scheme_id": "s1", "row_key": "Body1"})
            self.assertEqual(response.status_code, 200, response.text)
            command = run.call_args.args[0]
            self.assertIn(self.project.bgm_path, command)
            filters = command[command.index("-filter_complex") + 1]
            self.assertIn("[1:a:0]atrim=start=5.000000:duration=10.000000", filters)


class RealMediaTests(unittest.TestCase):
    def test_real_retimed_preview_and_mixed_video_are_readable_and_amplified(self):
        import array
        with tempfile.TemporaryDirectory(prefix="VMM 音画测试 ") as folder:
            root = Path(folder)
            ffmpeg = resolve_binary("ffmpeg.exe")
            if not Path(ffmpeg).is_file():
                self.skipTest("Bundled FFmpeg unavailable")
            def run(args):
                binary_options = text_subprocess_kwargs()
                binary_options.pop("encoding", None)
                binary_options.pop("errors", None)
                result = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, timeout=90, **binary_options)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                return result.stdout
            hook, body, voice, music = (root / name for name in ["hook.mp4", "body.mp4", "voice.wav", "music.wav"])
            run(["-f", "lavfi", "-i", "color=blue:s=180x320:r=30:d=1", "-f", "lavfi", "-i", "sine=frequency=300:duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(hook)])
            run(["-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=1.8", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(body)])
            run(["-f", "lavfi", "-i", "sine=frequency=600:duration=2", "-af", "volume=0.1", str(voice)])
            run(["-f", "lavfi", "-i", "sine=frequency=900:duration=0.5", str(music)])
            body_selection = selected(str(body), 1.8, 2)
            clip = root / "preview.mp4"
            render_body_preview(body_selection, clip)
            self.assertAlmostEqual(probe_media(str(clip)).duration, 2, delta=.08)
            self.assertFalse(probe_media(str(clip)).has_audio)
            selections = [selected(str(hook), 1), body_selection]
            outputs = []
            for gain in [1, 5]:
                output = root / f"gain{gain}.mp4"
                render_video(selections, str(voice), str(output), width=180, height=320, voice_volume=gain, hook_volume=2)
                self.assertTrue(probe_media(str(output)).has_audio)
                self.assertAlmostEqual(probe_media(str(output)).duration, 3, delta=.12)
                pcm = run(["-ss", "1.2", "-i", str(output), "-t", "0.5", "-vn", "-f", "s16le", "-ac", "1", "pipe:1"])
                samples = array.array("h", pcm)
                outputs.append((sum(x*x for x in samples) / len(samples)) ** .5)
            self.assertGreater(outputs[1] / outputs[0], 4.5)
            import numpy as np

            def tone_amplitudes(path, start):
                pcm = run(["-ss", str(start), "-i", str(path), "-t", "0.5", "-vn", "-f", "f32le", "-ar", "48000", "-ac", "1", "pipe:1"])
                samples = np.frombuffer(pcm, dtype="<f4")
                times = np.arange(len(samples)) / 48000
                return [abs(np.sum(samples * np.exp(-2j * np.pi * hz * times))) / len(samples) for hz in [300, 600, 900]]

            # Check actual decoded sound, including repeated short BGM and
            # trimmed long BGM: no music in Hook, music at both ends of Body.
            for music_duration in [.5, 4]:
                with self.subTest(music_duration=music_duration):
                    run(["-f", "lavfi", "-i", f"sine=frequency=900:duration={music_duration}", str(music)])
                    mixed = root / f"mixed-{music_duration}.mp4"
                    render_video(selections, str(voice), str(mixed), width=180, height=320, voice_volume=5, hook_volume=3, bgm_path=str(music), bgm_volume=2)
                    self.assertAlmostEqual(probe_media(str(mixed)).duration, 3, delta=.12)
                    hook_tone, narration, background = tone_amplitudes(mixed, .2)
                    self.assertGreater(hook_tone, .1)
                    self.assertLess(background, hook_tone * .01)
                    self.assertLess(narration, hook_tone * .01)
                    for start in [1.2, 2.2]:
                        hook_tone, narration, background = tone_amplitudes(mixed, start)
                        self.assertGreater(narration, .02)
                        self.assertGreater(background, .1)
                        self.assertLess(hook_tone, background * .01)
                    run(["-i", str(mixed), "-f", "null", "-"])

    def test_preview_store_only_renders_requested_and_skips_deselected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ["a", "b"]:
                (root / name).touch()
            options = [selected(str(root / name), 1) for name in ["a", "b"]]
            store = PreviewStore(root)
            def fake_render(selection, output):
                output.touch()
            with patch("backend.combination_previews.render_body_preview", side_effect=fake_render) as renderer:
                self.assertIsNone(store.ready(options[0]))
                renderer.assert_not_called()
                task = store.start("owner", options, lambda item: item.files[0].path.endswith("a"))
                deadline = time.monotonic() + 5
                while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(task["completed"], 1)
                self.assertEqual(task["skipped"], 1)
                self.assertEqual(renderer.call_count, 1)
                store.executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
