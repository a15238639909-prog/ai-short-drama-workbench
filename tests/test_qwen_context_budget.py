"""Exact request budgeting and useful HTTP failures, with every model call mocked."""
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from cores import runtime_reset
from models import gpu_manager, krea_client, qwen_client as client


class QwenContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.capacity = 16384
        self.count = 7000
        self.requests = []
        self.count_error = None
        self.generation_error = None
        self.reply = {"choices": [{"message": {"content": "{\"ok\":true}"},
                                  "finish_reason": "stop"}]}
        patches = [
            patch.dict(os.environ, {"V41_NO_MODEL": ""}),
            patch.object(client, "QWEN_URL", "http://127.0.0.1:8080"),
            patch.object(client, "_pubg_guard"),
            patch.object(client, "_budget_take"),
            patch.object(client, "up", return_value=True),
            patch.object(client, "start", side_effect=AssertionError("no model startup")),
            patch.object(client, "_free_idle_gpu"),
            patch.object(client, "_trace"),
            patch.object(client, "_REASONING_OVERRIDE", None),
            patch.object(client, "SAMPLING_OVERRIDE", {}),
            patch.object(gpu_manager, "acquire"),
            patch.object(krea_client, "up", return_value=False),
            patch.object(runtime_reset, "assert_current"),
            patch.object(client.urllib.request, "urlopen", side_effect=self.respond),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def respond(self, request, **kwargs):
        self.requests.append(request)
        if request.full_url.endswith("/props"):
            payload = {"default_generation_settings": {"n_ctx": self.capacity}}
        elif request.full_url.endswith("/input_tokens"):
            if self.count_error:
                raise self.count_error
            payload = {"input_tokens": self.count}
        else:
            if self.generation_error:
                raise self.generation_error
            payload = self.reply(json.loads(request.data)) if callable(self.reply) else self.reply
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    def http_error(self, status, message):
        body = json.dumps({"error": {"message": message}}).encode("utf-8")
        return urllib.error.HTTPError("http://127.0.0.1:8080/fake", status,
                                      "failure", {}, io.BytesIO(body))

    def assert_no_generation(self):
        self.assertFalse(any(request.full_url.endswith("/chat/completions")
                             for request in self.requests))

    def test_checked_request_counts_the_exact_schema_and_thinking_payload(self):
        schema = {"type": "json_object", "schema": {"type": "object"}}
        content, meta = client.chat("格式", "当前原文", max_tokens=5000,
                                    reasoning=False, response_format=schema,
                                    return_meta=True, check_context=True)
        self.assertEqual(content, '{"ok":true}')
        self.assertEqual(meta["context_window"], 16384)
        self.assertEqual(meta["context_output_limit"], 9352)
        self.assertEqual(meta["context_input_tokens"], 7000)
        counted = json.loads(self.requests[1].data)
        generated = json.loads(self.requests[2].data)
        self.assertEqual(counted, generated)
        self.assertEqual(counted["response_format"], schema)
        self.assertEqual(counted["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(counted["max_tokens"], 5000)
        self.assertEqual(counted["messages"][1]["content"], "当前原文")

    def test_prompt_near_capacity_reserves_output_and_never_generates(self):
        self.count = 16292
        with self.assertRaises(client.ModelContextError) as caught:
            client.chat("规则", "原文", max_tokens=5000, check_context=True)
        self.assertEqual(caught.exception.code, "model_context_limit")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.input_tokens, 16292)
        self.assertEqual(caught.exception.requested_output_tokens, 5000)
        self.assertEqual(caught.exception.context_output_limit, 60)
        self.assert_no_generation()

    def test_prompt_already_exceeding_context_never_generates(self):
        self.count = 17381
        with self.assertRaises(client.ModelContextError) as caught:
            client.chat("规则", "原文", max_tokens=5000, check_context=True)
        self.assertEqual(caught.exception.context_output_limit, 0)
        self.assert_no_generation()

    def test_exact_boundary_passes_without_lowering_requested_output(self):
        self.count = self.capacity - 5000 - 32
        client.chat("规则", "完整原文", max_tokens=5000, check_context=True)
        self.assertEqual(json.loads(self.requests[-1].data)["max_tokens"], 5000)

    def test_cancellation_during_preflight_does_not_send_generation(self):
        with patch.object(runtime_reset, "assert_current", side_effect=[None, RuntimeError("已取消")]):
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                client.chat("规则", "原文", check_context=True)
        self.assertEqual(len(self.requests), 2)
        self.assert_no_generation()

    def test_release_authoring_and_client_use_compact_checked_requests(self):
        from cores import authoring, workbench_story_director as release
        from tests.test_workbench_story_director_release import fixture, response
        slices, cards, settings = fixture()
        submitted = []

        def reply(body):
            payload = json.loads(body["messages"][1]["content"])
            submitted.append(payload)
            return {"choices": [{"message": {"content": json.dumps(response(payload), ensure_ascii=False)},
                                  "finish_reason": "stop"}]}

        self.reply = reply
        with patch.object(authoring, "_log_slow_call"):
            rows = release.make_prompts(slices, cards, ["竹庭"], authoring._q, settings=settings)
        self.assertEqual([request.full_url.rsplit("/", 1)[-1] for request in self.requests],
                         ["props", "input_tokens", "completions"] * 2)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(len(rows), 2)
        for payload, row, text in zip(submitted, rows, ("慢一点。", "这回稳了。")):
            self.assertNotIn("source_contract", payload["beat"])
            self.assertNotIn("source_contract", json.dumps(payload["story_plan"]))
            self.assertIn(text, row["prompt"])
            self.assertEqual(row["director_profile"], release.PROFILE)
            self.assertFalse(row["source_fallback"])

    def test_disabled_check_preserves_the_existing_multimodal_request(self):
        user = [{"type": "text", "text": "当前图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,fake"}}]
        _, meta = client.chat("规则", user, return_meta=True)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads(self.requests[0].data)["messages"][1]["content"], user)
        self.assertNotIn("context_window", meta)

    def test_unavailable_counter_stops_without_guessing_or_generating(self):
        self.count_error = self.http_error(404, "Unknown endpoint")
        with self.assertRaises(client.ContextCheckError) as caught:
            client.chat("规则", "原文", check_context=True)
        self.assertEqual(caught.exception.code, "model_context_check_unavailable")
        self.assertFalse(caught.exception.retryable)
        self.assertIn("Unknown endpoint", str(caught.exception))
        self.assertIn("文字模型额度检查不可用", str(caught.exception))
        self.assertNotIn("无法确认", str(caught.exception))
        self.assert_no_generation()

    def test_invalid_actual_capacity_or_token_count_stops(self):
        for capacity, count in ((None, 10), (True, 10), (16384, None), (16384, -1)):
            with self.subTest(capacity=capacity, count=count):
                self.capacity, self.count = capacity, count
                with self.assertRaises(client.ContextCheckError):
                    client.chat("规则", "原文", check_context=True)
        self.assert_no_generation()

    def test_server_context_error_preserves_body_and_numeric_evidence(self):
        message = ("request (17381 tokens) exceeds the available context size "
                   "(16384 tokens), try increasing it")
        self.generation_error = self.http_error(400, message)
        with self.assertRaises(client.ModelContextError) as caught:
            client.chat("规则", "原文")
        self.assertIn(message, str(caught.exception))
        self.assertEqual(caught.exception.input_tokens, 17381)
        self.assertEqual(caught.exception.context_window, 16384)
        self.assertEqual(caught.exception.http_status, 400)
        self.assertEqual(json.loads(caught.exception.response_body)["error"]["message"], message)
        self.assertFalse(caught.exception.retryable)

    def test_other_http_error_keeps_server_detail_and_retry_policy(self):
        for status, retryable in ((400, False), (503, True)):
            with self.subTest(status=status):
                self.generation_error = self.http_error(status, "具体服务错误")
                with self.assertRaises(client.ModelHTTPError) as caught:
                    client.chat("规则", "原文")
                self.assertIn("具体服务错误", str(caught.exception))
                self.assertEqual(caught.exception.http_status, status)
                self.assertEqual(caught.exception.retryable, retryable)


if __name__ == "__main__":
    unittest.main()
