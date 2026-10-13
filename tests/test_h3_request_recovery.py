"""H3 request recovery with fake HTTP/GPU/model and retained sandbox files."""
import os
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from cores import store
from models import h3_client as h3, gpu_manager


class H3RequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="h3_request_recovery_test_"))
        self.output = self.root / "h3_output"
        self.output.mkdir()
        self.movie = self.output / "finished.mp4"
        self.movie.write_bytes(b"fake-model-result")
        self.key = "STORY_TEST:episode2:segment1"
        self.meta = {"mode": "ref2va", "frames": 81, "seed": 42, "size_tier": "0.4", "width": 64, "height": 64}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, "DATA", self.root / "data"))
        self.stack.enter_context(patch.object(h3, "H3_OUTPUT", self.output))
        self.stack.enter_context(patch.object(h3, "OUTPUT_ROOT", self.root / "copies"))
        self.up = self.stack.enter_context(patch.object(h3, "up", return_value=True))
        self.start = self.stack.enter_context(patch.object(h3, "start_h3", side_effect=AssertionError("real model must never start")))
        self.acquire = self.stack.enter_context(patch.object(gpu_manager, "acquire"))
        self.stack.enter_context(patch.object(gpu_manager, "guard"))
        self.stack.enter_context(patch.object(gpu_manager, "assert_local_run_allowed"))
        self.build = self.stack.enter_context(patch.object(h3, "build_workflow", return_value=({}, self.meta)))
        self.stack.enter_context(patch.object(h3, "_graph_steps", return_value=2))
        self.stack.enter_context(patch.object(h3, "_record_timing"))
        self.stack.enter_context(patch.object(h3.time, "sleep"))
        self.http = self.stack.enter_context(patch.object(h3, "_http", side_effect=AssertionError("unexpected HTTP call")))
        self.stack.enter_context(patch.object(h3, "CANCEL", {"flag": False}))
        # generate itself is under test; every route to network/model startup is
        # replaced above before bypassing its blanket offline-test sentinel.
        self.stack.enter_context(patch.dict(os.environ, {"V41_NO_MODEL": ""}))

    def receipt(self, status, **extra):
        value = {"status": status, "prompt_id": "old-id", "meta": self.meta,
                 "steps": 2, "started_at": 1, "filename_prefix": "MiniMaxH3/old-id"}
        value.update(extra)
        h3._save_receipt(self.key, value)
        return value

    def success(self, pid):
        return {pid: {"status": {"status_str": "success"},
                      "outputs": {"video": {"videos": [{"filename": self.movie.name, "subfolder": ""}]}}}}

    def generate(self):
        return h3.generate("ref2va", "test-prompt", images=["fake.png"], recovery_key=self.key, timeout=1)

    def test_existing_pid_recovers_without_post_or_new_graph(self):
        self.receipt("submitted")
        def http(method, path, *args, **kwargs):
            self.assertEqual(method, "GET")
            if path == "/queue":
                return {"queue_running": [[0, "old-id"]], "queue_pending": []}
            self.assertEqual(path, "/history/old-id")
            return self.success("old-id")
        self.http.side_effect = http
        result = self.generate()
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "completed")
        self.build.assert_not_called()
        self.assertFalse(any(call.args[0] == "POST" for call in self.http.call_args_list))

    def test_unknown_submission_is_queried_without_resubmitting(self):
        self.receipt("submitting")
        self.http.side_effect = lambda method, path, **kwargs: {"queue_running": [], "queue_pending": []} if path == "/queue" else {}
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "提交结果待核对"):
                self.generate()
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "submitting")
        self.assertFalse(any(call.args[0] == "POST" for call in self.http.call_args_list))
        self.build.assert_not_called()

    def test_acknowledged_request_lost_from_queue_and_history_resubmits(self):
        self.receipt("submitted")
        new_ids = []
        def http(method, path, data=None, **kwargs):
            if path == "/queue":
                return {"queue_running": [], "queue_pending": []}
            if path == "/history/old-id":
                return {}
            if method == "POST" and path == "/prompt":
                self.assertNotEqual(data["prompt_id"], "old-id")
                self.assertEqual(h3.recovery_receipt(self.key)["status"], "submitting")
                self.assertEqual(h3.recovery_receipt(self.key)["prompt_id"], data["prompt_id"])
                new_ids.append(data["prompt_id"])
                return {"prompt_id": data["prompt_id"]}
            return self.success(new_ids[-1])
        self.http.side_effect = http
        self.generate()
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "completed")

    def test_http_500_is_unknown_submission_and_preserves_receipt(self):
        self.http.side_effect = urllib.error.HTTPError("http://fake/prompt", 500, "failure", {}, None)
        with self.assertRaisesRegex(RuntimeError, "提交结果待核对"):
            self.generate()
        receipt = h3.recovery_receipt(self.key)
        self.assertEqual(receipt["status"], "submitting")
        self.assertTrue(receipt["prompt_id"])
        self.assertEqual(self.http.call_count, 1)

    def test_cancelled_receipt_never_restarts_or_queries(self):
        self.receipt("cancelled")
        with self.assertRaisesRegex(RuntimeError, "已取消"):
            self.generate()
        self.http.assert_not_called()
        self.up.assert_not_called()
        self.acquire.assert_not_called()

    def test_completed_receipt_reuses_saved_result(self):
        saved = {"output_path": str(self.movie), "seed": 42}
        self.receipt("completed", result=saved)
        self.assertEqual(self.generate(), saved)
        self.http.assert_not_called()
        self.up.assert_not_called()
        self.build.assert_not_called()

    def test_failed_terminal_receipt_can_create_one_new_request(self):
        self.receipt("failed", error="H3 工作流失败")
        submitted = []
        def http(method, path, data=None, **kwargs):
            if method == "POST":
                self.assertEqual(path, "/prompt")
                submitted.append(data["prompt_id"])
                return {"prompt_id": data["prompt_id"]}
            return self.success(submitted[0])
        self.http.side_effect = http
        self.generate()
        self.assertEqual(len(submitted), 1)
        self.assertNotEqual(submitted[0], "old-id")
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "completed")

    def test_cancel_during_model_preparation_prevents_submission(self):
        def cancel_while_preparing(*args, **kwargs):
            h3.request_cancel()
            return {}, self.meta
        self.build.side_effect = cancel_while_preparing
        with self.assertRaisesRegex(RuntimeError, "已取消"):
            self.generate()
        self.http.assert_not_called()
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "cancelled")

    def test_cancel_during_post_interrupts_before_polling(self):
        calls = []
        def http(method, path, data=None, **kwargs):
            calls.append((method, path))
            if path == "/prompt":
                h3.request_cancel()
                return {"prompt_id": data["prompt_id"]}
            self.assertEqual((method, path), ("POST", "/interrupt"))
            return {}
        self.http.side_effect = http
        with self.assertRaisesRegex(RuntimeError, "已取消"):
            self.generate()
        self.assertEqual(calls, [("POST", "/prompt"), ("POST", "/interrupt")])
        self.assertEqual(h3.recovery_receipt(self.key)["status"], "cancelled")

    def test_lost_history_prefers_verified_audio_mux_over_raw_video(self):
        self.receipt("submitted")  # Legacy receipt intentionally has no expect_audio.
        folder = self.output / "MiniMaxH3"
        folder.mkdir()
        raw = folder / "old-id_00001.mp4"
        final = folder / "old-id_00001-audio.mp4"
        raw.write_bytes(b"raw-without-audio")
        final.write_bytes(b"finished-with-audio")
        self.http.side_effect = lambda method, path, **kwargs: {"queue_running": [], "queue_pending": []} if path == "/queue" else {}
        with patch.object(h3, "_has_audio_stream", side_effect=lambda path: Path(path) == final):
            result = self.generate()
        self.assertEqual(result["output_file"], final.name)
        self.assertEqual(Path(result["output_path"]).read_bytes(), b"finished-with-audio")
        self.assertTrue(result["expect_audio"])
        self.assertFalse(any(call.args[0] == "POST" for call in self.http.call_args_list))
        self.build.assert_not_called()

    def test_only_raw_output_does_not_complete_expected_audio_request(self):
        folder = self.output / "MiniMaxH3"
        folder.mkdir()
        raw = folder / "old-id_00001.mp4"
        raw.write_bytes(b"raw-without-audio")
        for status in ("submitting", "submitted"):
            with self.subTest(status=status):
                self.receipt(status)
                self.http.reset_mock()
                new_ids = []
                def http(method, path, data=None, **kwargs):
                    if path == "/queue":
                        return {"queue_running": [], "queue_pending": []}
                    if path == "/history/old-id":
                        return {}
                    if path == "/prompt":
                        new_ids.append(data["prompt_id"])
                        return {"prompt_id": data["prompt_id"]}
                    return self.success(new_ids[-1])
                self.http.side_effect = http
                with patch.object(h3, "_has_audio_stream", return_value=False):
                    if status == "submitting":
                        with self.assertRaisesRegex(RuntimeError, "提交结果待核对"):
                            self.generate()
                        self.assertEqual(new_ids, [])
                        self.assertEqual(h3.recovery_receipt(self.key)["status"], "submitting")
                    else:
                        result = self.generate()
                        self.assertEqual(len(new_ids), 1)
                        self.assertNotEqual(result["output_file"], raw.name)


if __name__ == "__main__":
    unittest.main()
