"""Recovery integration tests: fake models, isolated retained temp directories."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["V41_NO_MODEL"] = "1"

from api import saga_api as sa, segment_api
from cores import generation_recovery as gr, authoring, saga_core, reference_ready, episode_ledger, store
from cores import episode_merge, video_continuity, shot_edit, manual_story
from models import gpu_manager


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="generation_recovery_test_"))
        self.store = gr.RecoveryStore(self.directory)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(gr, "RecoveryStore", return_value=self.store))
        self.stack.enter_context(patch.object(sa, "log"))
        self.stack.enter_context(patch.object(sa, "_generation_input_signature", return_value="input-v1"))
        self.stack.enter_context(patch.object(gpu_manager, "assert_local_run_allowed", create=True))
        self.stack.enter_context(patch.object(sa, "_timeline_busy", return_value=False))
        self.stack.enter_context(patch.object(sa, "_JOBS", {}))
        self.stack.enter_context(patch.object(store, "DATA", self.directory / "data"))
        self.stack.enter_context(patch.object(sa.time, "sleep"))

    def job(self, sid="STORY_TEST", stage="video", phase="text"):
        record = self.store.register(sid, {"ep": 2, "stage": stage, "one": "第二话", "n": None}, "input-v1")
        record["phase"] = phase
        job = sa._start_job(sid, "生成视频", 9)
        job.update(_sid=sid, _recovery=record)
        sa._persist_job(job)
        return job

    def test_atomic_record_roundtrip_preserves_history(self):
        job = self.job()
        job["_recovery"]["attempts"]["render"] = 1
        sa._persist_job(job)
        self.assertEqual(self.store.load("STORY_TEST")["attempts"]["render"], 1)
        self.store.register("STORY_TEST", {"ep": 3}, "new")
        self.assertEqual(len(list((self.directory / "history").glob("*.json"))), 1)

    def test_partial_cancel_and_merge_failure_never_100(self):
        for field in ("partial", "cancel", "blocked"):
            job = self.job(sid="STORY_" + field)
            job.update(done=9, total=9)
            job[field] = True if field != "partial" else "缺第 7 段"
            sa._finish_job(job)
            self.assertLess(sa._job_view(job)["percent"], 100)
            self.assertNotEqual(self.store.load(job["_sid"])["status"], "completed")
        job = self.job()
        sa._finish_job(job, "ffmpeg 合成失败")
        self.assertEqual(job["status"], "failed")

    def test_cancelled_and_old_unregistered_jobs_do_not_resume(self):
        self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
        job = self.job()
        job["cancel"] = True
        sa._finish_job(job)
        with patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
            thread.assert_not_called()

    def test_invalid_json_does_not_block_other_job_recovery(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "CORRUPTED.json").write_text("[1, 2]", encoding="utf-8")
        self.job(phase="merge")
        sa._JOBS.clear()
        with patch.object(sa.threading, "Thread"):
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), ["STORY_TEST"])

    def test_waiting_video_reconciles_only_registered_request(self):
        job = self.job(phase="render")
        job["status"] = "waiting_task"
        job["_recovery"]["external_inflight"] = "video:2:1"
        sa._persist_job(job)
        sa._JOBS.clear()
        with patch.object(segment_api, "can_resume_segment", return_value=False, create=True), \
                patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
            thread.assert_not_called()
        self.assertEqual(self.store.load("STORY_TEST")["status"], "paused")

    def test_registered_video_request_can_resume_without_new_request(self):
        job = self.job(phase="render")
        job["status"] = "waiting_task"
        job["_recovery"]["external_inflight"] = "video:2:1"
        sa._persist_job(job)
        sa._JOBS.clear()
        with patch.object(segment_api, "can_resume_segment", return_value=True, create=True) as check, \
                patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), ["STORY_TEST"])
            check.assert_called_once_with("STORY_TEST", 2, 1, size_tier=None)
            thread.return_value.start.assert_called_once()

    def test_restart_recovers_registered_safe_checkpoint(self):
        job = self.job(phase="merge")
        job["expected_segments"] = 2
        sa._persist_job(job)
        sa._JOBS.clear()
        with patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), ["STORY_TEST"])
            thread.return_value.start.assert_called_once()
        self.assertEqual(self.store.load("STORY_TEST")["restarts"], 1)

    def test_changed_input_and_unknown_image_request_pause(self):
        for sid, changes in (("STORY_CHANGED", {"input_hash": "other"}),
                             ("STORY_UNKNOWN", {"external_inflight": "images"})):
            job = self.job(sid=sid)
            job["_recovery"].update(changes)
            sa._persist_job(job)
        sa._JOBS.clear()
        with patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
            thread.assert_not_called()
        self.assertEqual(self.store.load("STORY_UNKNOWN")["status"], "paused")

    def test_pubg_pauses_without_spending_restart_budget(self):
        self.job(phase="video")
        sa._JOBS.clear()
        with patch.object(gpu_manager, "assert_local_run_allowed", side_effect=RuntimeError("PUBG 正在运行")), \
                patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
            thread.assert_not_called()
        record = self.store.load("STORY_TEST")
        self.assertEqual(record["status"], "waiting_resource")
        self.assertEqual(record["restarts"], 0)

    def test_merge_restart_does_not_require_gpu_or_stop_for_pubg(self):
        self.job(phase="merge")
        sa._JOBS.clear()
        with patch.object(gpu_manager, "assert_local_run_allowed", side_effect=RuntimeError("PUBG 正在运行")) as guard, \
                patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), ["STORY_TEST"])
            guard.assert_not_called()
            thread.return_value.start.assert_called_once()

    def test_cancel_during_restart_guard_is_not_erased(self):
        self.job(phase="video")
        sa._JOBS.clear()
        def cancel_during_check():
            current = sa._job("STORY_TEST")
            current.update(cancel=True, status="cancelled")
            sa._persist_job(current)
        with patch.object(gpu_manager, "assert_local_run_allowed", side_effect=cancel_during_check), \
                patch.object(sa.threading, "Thread") as thread:
            self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
            thread.assert_not_called()
        self.assertTrue(self.store.load("STORY_TEST")["cancel"])
        self.assertEqual(self.store.load("STORY_TEST")["status"], "cancelled")

    def test_cancel_waiting_project_does_not_interrupt_other_project_or_resume(self):
        from api import timeline
        from models import h3_client
        self.job(sid="STORY_OTHER", phase="render")
        for waiting_status in ("waiting_task", "waiting_resource"):
            sid = "STORY_" + waiting_status
            job = self.job(sid=sid, phase="render")
            job.update(running=False, status=waiting_status)
            sa._persist_job(job)
            with patch.object(timeline, "_job", return_value={"running": False}), \
                    patch.object(h3_client, "request_cancel") as interrupt, \
                    patch.object(sa.threading, "Thread") as thread:
                result = timeline.cancel_project(sid)
                self.assertTrue(result["saga"])
                interrupt.assert_not_called()
                self.assertEqual(sa.restore_generation_jobs(start_monitor=False), [])
                thread.assert_not_called()
            self.assertTrue(self.store.load(sid)["cancel"])
            self.assertEqual(self.store.load(sid)["status"], "cancelled")
        self.assertTrue(sa._job("STORY_OTHER")["running"])
        self.assertFalse(sa._job("STORY_OTHER").get("cancel"))

    def test_cancel_active_project_still_interrupts_h3(self):
        from api import timeline
        from models import h3_client
        self.job(phase="render")
        with patch.object(timeline, "_job", return_value={"running": False}), \
                patch.object(h3_client, "request_cancel") as interrupt:
            timeline.cancel_project("STORY_TEST")
            interrupt.assert_called_once()
        self.assertTrue(self.store.load("STORY_TEST")["cancel"])

    def test_known_terminal_render_error_retries_only_failed_operation(self):
        job = self.job()
        render = Mock(side_effect=[RuntimeError("H3 工作流失败：temporary"), {"video": "second.mp4"}])
        result = sa._generation_step(job, "video:2:2", render, phase="render", model=True, external="video:2:2")
        self.assertEqual(result["video"], "second.mp4")
        self.assertEqual(render.call_count, 2)
        self.assertEqual(self.store.load("STORY_TEST")["external_inflight"], "")

    def test_unknown_video_timeout_is_never_resubmitted(self):
        job = self.job()
        render = Mock(side_effect=TimeoutError("H3 生成超时"))
        with self.assertRaises(TimeoutError):
            sa._generation_step(job, "video:2:1", render, phase="render", external="video:2:1")
        render.assert_called_once()
        self.assertEqual(self.store.load("STORY_TEST")["external_inflight"], "video:2:1")

    def test_unknown_h3_submission_becomes_waiting_task(self):
        job = self.job(phase="render")
        with patch.object(sa, "_first_video_chain", side_effect=RuntimeError("H3提交结果待核对，已保存任务编号")), \
                patch.object(sa.story_core, "get_story", return_value={}), \
                patch.object(manual_story, "enabled", return_value=False), patch.object(shot_edit, "approve"):
            sa._run_registered_make("STORY_TEST", job)
        self.assertEqual(job["status"], "waiting_task")
        self.assertEqual(self.store.load("STORY_TEST")["status"], "waiting_task")

    def test_merge_failure_is_bounded_and_never_completed(self):
        job = self.job(phase="merge")
        job["expected_segments"] = 2
        with patch.object(episode_merge, "episode_videos", return_value=[(1, "one.mp4"), (2, "two.mp4")]), \
                patch.object(episode_merge, "merge_episode", side_effect=RuntimeError("ffmpeg 合并失败")) as merge:
            sa._run_registered_make("STORY_TEST", job)
        self.assertEqual(merge.call_count, 3)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.store.load("STORY_TEST")["phase"], "merge")

    def test_merge_resume_uses_saved_clips_without_models(self):
        job = self.job(phase="merge")
        job["expected_segments"] = 2
        with patch.object(episode_merge, "episode_videos", return_value=[(1, "one.mp4"), (2, "two.mp4")]), \
                patch.object(episode_merge, "merge_episode", return_value="movie.mp4"), \
                patch.object(video_continuity, "validate_video", return_value={"duration": 20}), \
                patch.object(sa, "_text_chain_to_shots") as text, patch.object(sa, "_first_video_chain") as video:
            sa._run_registered_make("STORY_TEST", job)
        text.assert_not_called()
        video.assert_not_called()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["merged"], "movie.mp4")

    def chain_mocks(self, timeline):
        self.stack.enter_context(patch.object(saga_core, "ep_timeline", return_value=timeline))
        self.stack.enter_context(patch.object(reference_ready, "ensure_characters"))
        self.stack.enter_context(patch.object(sa, "_ep_scene_images"))
        self.stack.enter_context(patch.object(authoring, "total_segments", return_value=2))
        self.stack.enter_context(patch.object(authoring, "use_new_pipeline", return_value=True))
        self.stack.enter_context(patch.object(authoring, "next_prompts", return_value=([], 2)))
        self.stack.enter_context(patch.object(episode_ledger, "build"))
        self.stack.enter_context(patch.object(segment_api, "render_opening_all", return_value=(0, "")))
        self.stack.enter_context(patch.object(segment_api, "is_segment_current", return_value=True, create=True))

    def test_missing_prompt_blocks_all_downstream_generation(self):
        job = self.job(phase="video")
        self.chain_mocks({"scenes": [{"segments": [{"no": 1, "prompt": "one", "video": "one.mp4"}]}]})
        with patch.object(authoring, "next_prompts", return_value=([], 2)), \
                patch.object(segment_api, "render_segment_checked") as render:
            result = sa._first_video_chain("STORY_TEST", job, ep=2, n=None, approved=True)
        render.assert_not_called()
        self.assertEqual(result, [])
        self.assertEqual(job["missing_prompt_segments"], [2])

    def test_failed_second_clip_preserves_first_and_retries_second(self):
        job = self.job(phase="video")
        self.chain_mocks({"scenes": [{"segments": [{"no": 1, "prompt": "one", "video": "one.mp4"},
                                                   {"no": 2, "prompt": "two"}]}]})
        with patch.object(segment_api, "render_segment_checked", side_effect=[RuntimeError("H3 工作流失败"),
                      {"video": "two.mp4", "size": "100x100"}]) as render:
            result = sa._first_video_chain("STORY_TEST", job, ep=2, n=None, approved=True)
        self.assertEqual(result, ["one.mp4", "two.mp4"])
        self.assertEqual([call.args[3] for call in render.call_args_list], [2, 2])

    def test_nonempty_but_rejected_prompt_cannot_start_render(self):
        job = self.job(phase="video")
        self.chain_mocks({"prompt_failures": {"2": "已保留人工修改的提示词，请处理"},
                          "scenes": [{"segments": [{"no": 1, "prompt": "one", "video": "one.mp4"},
                                                    {"no": 2, "prompt": "bad"}]}]})
        with patch.object(segment_api, "render_segment_checked") as render:
            self.assertEqual(sa._first_video_chain("STORY_TEST", job, ep=2, n=None, approved=True), [])
        render.assert_not_called()
        self.assertEqual(job["missing_prompt_segments"], [2])


if __name__ == "__main__":
    unittest.main()
