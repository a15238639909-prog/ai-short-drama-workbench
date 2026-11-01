"""Timeline entry must not skip holes or call failed prompts complete."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ["V41_NO_MODEL"] = "1"

from api import timeline, segment_api, saga_api
from cores import authoring, saga_core, store


class TimelineGenerationCompletionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="timeline_completion_test_"))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, "DATA", self.root))
        self.stack.enter_context(patch.object(timeline, "_JOBS", {}))
        self.stack.enter_context(patch.object(timeline, "_ep_body", return_value="已确认的剧本"))
        self.stack.enter_context(patch.object(timeline.story_core, "get_story", return_value={}))
        self.stack.enter_context(patch.object(timeline, "_confirmed_arrangement", return_value=None))
        self.stack.enter_context(patch.object(saga_core, "recover_segment_media", return_value=False))
        self.stack.enter_context(patch.object(saga_core, "timeline_stale", return_value=False))
        self.stack.enter_context(patch.object(authoring, "_ep", return_value=({}, {})))
        self.stack.enter_context(patch.object(saga_core, "save_ep_timeline", return_value=True))
        self.gate = self.stack.enter_context(patch.object(saga_api, "_preflight_gate", return_value={"blocked": False}))
        self.references = self.stack.enter_context(patch.object(timeline, "_ensure_refs"))
        self.opening = self.stack.enter_context(patch.object(segment_api, "render_opening_all", return_value=(0, "")))
        self.render = self.stack.enter_context(patch.object(segment_api, "render_segment_checked", side_effect=lambda *args, **kwargs: {"video": "generated-%s.mp4" % args[3]}))
        self.current = self.stack.enter_context(patch.object(segment_api, "is_segment_current", return_value=False))
        real_problems = segment_api._render_critical_problems
        self.stack.enter_context(patch.object(segment_api, "_render_critical_problems",
            side_effect=lambda st, tl, row, prompt, ep, no: real_problems(st, tl, row, prompt, ep, no)
            if (row.get("plan") or {}).get("flow") == "source-v1" else list(row.get("critical_problems") or [])))
        self.data = {"scenes": [{"no": 1, "segments": []}]}
        self.stack.enter_context(patch.object(saga_core, "ep_timeline", side_effect=lambda *args, **kwargs: self.data))
        self.prompts = self.stack.enter_context(patch.object(authoring, "next_prompts", return_value=([], 4)))

    def row(self, no, **changes):
        return dict({"no": no, "prompt": "合法提示词%d" % no, "seconds": 8, "scene": "院子"}, **changes)

    def test_three_of_four_prompts_is_partial_but_renders_available_segments(self):
        for prompts_only in (True, False):
            with self.subTest(prompts_only=prompts_only):
                self.data = {"scenes": [{"no": 1, "segments": []}]}
                def write_some(*args, **kwargs):
                    self.data["scenes"][0]["segments"] = [self.row(no) for no in (1, 2, 3)]
                    self.data["prompt_failures"] = {"4": "第4段校验失败，已保存前3段"}
                    return [1, 2, 3], 4
                self.prompts.side_effect = write_some
                timeline._gen_next("STORY_TEST", 2, 4, prompts_only=prompts_only)
                job = timeline._job("STORY_TEST")
                self.assertEqual(job["status"], "partial")
                self.assertEqual(job["done"], 3 if prompts_only else 6)
                self.assertEqual(job["missing_prompt_segments"], [4])
                self.assertIn("3/4", job["partial"])
                self.assertTrue(job["err"])
                if prompts_only:
                    self.render.assert_not_called()
                    self.opening.assert_not_called()
                    self.references.assert_not_called()
                else:
                    self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
                    self.assertEqual([call.args[2] for call in self.references.call_args_list], [[1], [2], [3]])

    def test_missing_second_video_is_not_hidden_by_successful_third(self):
        self.data["scenes"][0]["segments"] = [self.row(no, video="clip%d.mp4" % no if no != 2 else "") for no in (1, 2, 3, 4)]
        self.current.side_effect = lambda sid, ep, scene, no, **kwargs: no in (1, 3)
        timeline._gen_next("STORY_TEST", 2, 1, size_tier="1.0")
        self.assertEqual(self.prompts.call_args.args[2], 2)
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [2])
        self.assertEqual(timeline._job("STORY_TEST")["status"], "completed")
        self.assertTrue(all(call.kwargs.get("size_tier") == "1.0" for call in self.current.call_args_list))

    def test_invalid_nonempty_source_v1_prompt_stays_partial(self):
        self.data["scenes"][0]["segments"] = [self.row(1, prompt="非空但是漏掉原文对白",
            plan={"flow": "source-v1"}, source_text="林禾：别出声。")]
        self.prompts.return_value = ([], 1)
        timeline._gen_next("STORY_TEST", 2, 1, prompts_only=True)
        job = timeline._job("STORY_TEST")
        self.assertEqual(job["status"], "partial")
        self.assertIn("对白", job["err"])
        self.assertEqual(job["done"], 0)
        self.render.assert_not_called()
        self.gate.assert_not_called()

    def test_valid_video_inside_requested_window_is_reused(self):
        self.data["scenes"][0]["segments"] = [self.row(no, video="clip%d.mp4" % no) for no in (1, 2, 3, 4)]
        self.current.side_effect = lambda sid, ep, scene, no, **kwargs: no in (1, 3)
        timeline._gen_next("STORY_TEST", 2, 2)
        self.assertEqual(self.prompts.call_args.args[2], 3)
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [2])
        self.assertTrue(any(call.args[3] == 3 for call in self.current.call_args_list))
        self.assertEqual(timeline._job("STORY_TEST")["done"], 4)
        self.assertEqual(timeline._job("STORY_TEST")["status"], "completed")

    def test_missing_second_prompt_is_not_hidden_by_third_prompt(self):
        self.data["scenes"][0]["segments"] = [self.row(1), self.row(3)]
        def fill_second(*args, **kwargs):
            self.data["scenes"][0]["segments"].append(self.row(2))
            return [2], 3
        self.prompts.side_effect = fill_second
        timeline._gen_next("STORY_TEST", 2, 1, prompts_only=True)
        self.assertEqual(self.prompts.call_args.args[2], 2)
        self.assertEqual(timeline._job("STORY_TEST")["status"], "completed")
        self.render.assert_not_called()

    def test_middle_render_failure_keeps_attempting_later_segments(self):
        self.data["scenes"][0]["segments"] = [self.row(no) for no in (1, 2, 3)]
        self.prompts.return_value = ([], 3)
        def render(*args, **kwargs):
            if args[3] == 2:
                raise RuntimeError("单段服务返回错误")
            return {"video": "generated-%s.mp4" % args[3]}
        self.render.side_effect = render
        timeline._gen_next("STORY_TEST", 2, 3)
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
        job = timeline._job("STORY_TEST")
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["missing_video_segments"], [2])
        self.assertIn("2/3", job["partial"])

    def test_missing_middle_reference_does_not_block_later_scene(self):
        self.data["scenes"] = [{"no": no, "segments": [self.row(no)]} for no in (1, 2, 3)]
        self.prompts.return_value = ([], 3)
        def refs(sid, ep, targets, *args):
            if targets == [2]:
                raise RuntimeError("第2段人设图未就绪")
        self.references.side_effect = refs
        timeline._gen_next("STORY_TEST", 2, 3)
        self.assertEqual([(call.args[2], call.args[3]) for call in self.render.call_args_list], [(1, 1), (3, 3)])
        self.assertEqual(timeline._job("STORY_TEST")["missing_video_segments"], [2])

    def test_review_block_is_advisory_but_empty_video_never_counts_complete(self):
        self.data["scenes"][0]["segments"] = [self.row(no) for no in (1, 2)]
        self.prompts.return_value = ([], 2)
        self.gate.return_value = {"blocked": True}
        self.render.side_effect = [{}, {"video": "two.mp4"}]
        timeline._gen_next("STORY_TEST", 2, 2)
        self.assertEqual(self.render.call_count, 2)
        job = timeline._job("STORY_TEST")
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["missing_video_segments"], [1])

    def test_user_cancel_is_honored_instead_of_submitting_later_segments(self):
        self.data["scenes"][0]["segments"] = [self.row(no) for no in (1, 2, 3)]
        self.prompts.return_value = ([], 3)
        self.render.side_effect = timeline._Cancelled()
        timeline._gen_next("STORY_TEST", 2, 3)
        self.assertEqual(self.render.call_count, 1)
        self.assertEqual(timeline._job("STORY_TEST")["status"], "cancelled")

    def test_selected_rerender_attempts_all_requested_slots_and_keeps_errors(self):
        from types import SimpleNamespace
        from cores import runtime_reset
        self.data["scenes"][0]["segments"] = [self.row(no) for no in (1, 2, 3)]
        def render(*args, **kwargs):
            if args[3] == 2:
                raise RuntimeError("one selected render failed")
            return {"video": "rendered-%s.mp4" % args[3]}
        self.render.side_effect = render
        with patch.object(timeline, "_load", return_value=({}, self.data)), \
             patch.object(runtime_reset, "worker", side_effect=lambda fn: fn), \
             patch.object(timeline.threading, "Thread", side_effect=lambda target, **kw: SimpleNamespace(start=target)):
            timeline.timeline_rerender(None, "/api/timeline/STORY_TEST/rerender", {"ep": 2, "segs": [1, 2, 9, 3]})
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
        job = timeline._job("STORY_TEST")
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["done"], 2)
        self.assertEqual(job["missing_video_segments"], [2, 9])


if __name__ == "__main__":
    unittest.main()
