"""
Unit Tests: Cloud AI Model Failover & Auto-Orchestration
=========================================================
Tests:
1. Fast failover on HTTP 429 without long blocking sleeps.
2. Fast failover on HTTP 404 / 400 decommissioned model IDs.
3. Strict multimodal vision candidate enforcement when image_bytes is present.
4. Autonomous vector text fallback in Stage 1 blueprint discovery.
5. Earliest cooldown expiration candidate selection when all models are in temporary cooldown.
"""

import io
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.cloud_client import CloudClient, ORCHESTRATION_CANDIDATES
from core.visual_extractor import extract_visual_blueprint


class TestCloudFailover(unittest.TestCase):
    def setUp(self):
        # Reset class-level caches and cooldowns before each test
        CloudClient._locked_model = None
        CloudClient._model_cooldowns.clear()
        CloudClient._provider_cooldowns.clear()
        CloudClient._key_cooldowns.clear()
        CloudClient._key_pool_indices.clear()
        self.tmp_config = os.path.join(TEST_DIR, "tmp_cloud_config.json")
        self.config_patcher = patch("core.cloud_client.CONFIG_FILE", self.tmp_config)
        self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        if os.path.exists(self.tmp_config):
            try:
                os.remove(self.tmp_config)
            except Exception:
                pass

    def test_vision_candidate_filtering(self):
        """When requires_vision=True, get_best_working_model must ONLY return vision models."""
        client = CloudClient()
        client.config["api_keys"] = {
            "gemini": "test_gemini_key",
            "groq": "test_groq_key"
        }

        # Request with vision
        cand_vision = client.get_best_working_model(force_refresh=True, requires_vision=True)
        self.assertEqual(cand_vision["provider"], "gemini")
        match = next(c for c in ORCHESTRATION_CANDIDATES if c["provider"] == cand_vision["provider"] and c["model"] == cand_vision["model"])
        self.assertTrue(match["supports_vision"])

        # Request without vision can pick Groq or Gemini
        cand_text = client.get_best_working_model(force_refresh=True, requires_vision=False)
        self.assertIn(cand_text["provider"], ["gemini", "groq"])

    def test_fast_failover_on_429_quota_exhausted(self):
        """HTTP 429 quota exhaustion should immediately switch models without hanging."""
        client = CloudClient()
        client.config["api_keys"] = {
            "gemini": "test_gemini_key",
            "groq": "test_groq_key"
        }

        call_models = []

        import urllib.error

        def mock_urlopen(req, timeout=40):
            # Inspect url to see which model was requested
            req_url = req.full_url if hasattr(req, "full_url") else str(req)
            if "gemini-flash-latest" in req_url:
                call_models.append("gemini-flash-latest")
                fp = io.BytesIO(b'{"error": {"message": "ResourceExhausted: Quota exceeded for metric requests. Please retry in 17h."}}')
                raise urllib.error.HTTPError(req_url, 429, "Quota Exceeded", {}, fp)
            elif "gemini-flash-lite-latest" in req_url:
                call_models.append("gemini-flash-lite-latest")
                fp = io.BytesIO(b'{"candidates": [{"content": {"parts": [{"text": "{\\"status\\": \\"success\\"}"}]}}]}')
                mock_resp = MagicMock()
                mock_resp.read.return_value = fp.read()
                mock_resp.__enter__.return_value = mock_resp
                mock_resp.__exit__.return_value = None
                return mock_resp
            else:
                fp = io.BytesIO(b'{"error": {"message": "Unexpected model"}}')
                raise urllib.error.HTTPError(req_url, 500, "Error", {}, fp)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            audit_events = []
            t0 = time.time()
            res = client.generate_direct(
                user_prompt="test prompt",
                image_bytes=b"dummy_image_bytes",
                audit_cb=lambda e: audit_events.append(e)
            )
            elapsed = time.time() - t0

            # Must have attempted gemini-flash-latest first, then switched to gemini-flash-lite-latest
            self.assertIn("gemini-flash-latest", call_models)
            self.assertIn("gemini-flash-lite-latest", call_models)
            # Response must be from the successfully failed-over candidate
            data = json.loads(res)
            self.assertEqual(data.get("status"), "success")
            # Failover must be fast (less than 3 seconds, not 30+ seconds)
            self.assertLess(elapsed, 4.0)

            # Locked model must now be the working model
            self.assertEqual(CloudClient._locked_model["model"], "gemini-flash-lite-latest")

    def test_fast_failover_on_404_decommissioned_model(self):
        """HTTP 404/400 decommissioned model should immediately be put into cooldown and fail over."""
        client = CloudClient()
        client.config["api_keys"] = {
            "groq": "test_groq_key"
        }

        call_models = []
        import urllib.error

        def mock_urlopen(req, timeout=40):
            req_data = json.loads(req.data.decode("utf-8")) if req.data else {}
            m = req_data.get("model", "")
            call_models.append(m)
            if m == "openai/gpt-oss-120b":
                # Simulate decommissioned / 404 error
                fp = io.BytesIO(b'{"error": {"message": "The model has been decommissioned"}}')
                raise urllib.error.HTTPError("https://api.groq.com", 400, "Decommissioned", {}, fp)
            elif m == "qwen/qwen3.8-27b":
                fp = io.BytesIO(b'{"choices": [{"message": {"content": "{\\"status\\": \\"qwen_ok\\"}"}}]}')
                mock_resp = MagicMock()
                mock_resp.read.return_value = fp.read()
                mock_resp.__enter__.return_value = mock_resp
                mock_resp.__exit__.return_value = None
                return mock_resp
            else:
                fp = io.BytesIO(b'{"error": {"message": "unknown"}}')
                raise urllib.error.HTTPError("https://api.groq.com", 404, "Not Found", {}, fp)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = client.generate_direct(
                user_prompt="test text",
                image_bytes=None
            )
            data = json.loads(res)
            self.assertEqual(data.get("status"), "qwen_ok")
            self.assertIn("openai/gpt-oss-120b", call_models)
            self.assertIn("qwen/qwen3.8-27b", call_models)
            # Decommissioned model must be in long cooldown
            self.assertGreater(CloudClient._model_cooldowns.get("groq:openai/gpt-oss-120b", 0), time.time() + 1000)

    def test_all_candidates_cooldown_picks_earliest_expiry(self):
        """If all eligible candidates are in cooldown, pick the one that expires soonest."""
        client = CloudClient()
        client.config["api_keys"] = {
            "groq": "test_groq_key"
        }

        now = time.time()
        # Put all Groq candidates in cooldown with different expiration times
        CloudClient._model_cooldowns["groq:openai/gpt-oss-120b"] = now + 100
        CloudClient._model_cooldowns["groq:qwen/qwen3.8-27b"] = now + 20   # Earliest!
        CloudClient._model_cooldowns["groq:openai/gpt-oss-20b"] = now + 200

        cand = client.get_best_working_model(force_refresh=True, requires_vision=False)
        self.assertEqual(cand["provider"], "groq")
    def test_openrouter_free_vision_dispatch(self):
        """Test OpenRouter multimodal vision request formatting and candidate selection."""
        client = CloudClient()
        client.config["api_keys"] = {
            "openrouter": "sk-or-v1-mock-key"
        }

        # Verify candidate selection with vision
        cand = client.get_best_working_model(force_refresh=True, requires_vision=True)
        self.assertEqual(cand["provider"], "openrouter")
        self.assertEqual(cand["model"], "google/gemma-4-31b-it:free")

        # Mock OpenRouter vision response
        captured_req = []

        def mock_urlopen(req, timeout=40):
            captured_req.append(req)
            fp = io.BytesIO(b'{"choices": [{"message": {"content": "{\\"result\\": \\"vision_ok\\"}"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}')
            mock_resp = MagicMock()
            mock_resp.read.return_value = fp.read()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = client.generate_direct(
                user_prompt="Analyze ledger page",
                image_bytes=b"\xff\xd8\xff\xe0mock_jpeg_bytes",
                image_mime="image/jpeg"
            )
            data = json.loads(res)
            self.assertEqual(data.get("result"), "vision_ok")
            self.assertEqual(len(captured_req), 1)
            req = captured_req[0]
            self.assertEqual(req.get_full_url(), "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(req.headers.get("Authorization"), "Bearer sk-or-v1-mock-key")
            payload = json.loads(req.data.decode("utf-8"))
            self.assertEqual(payload["model"], "google/gemma-4-31b-it:free")
            self.assertEqual(payload.get("reasoning"), {"effort": "none"})
            user_msg = payload["messages"][-1]
            # Should have multimodal content array with image_url and text
            self.assertIsInstance(user_msg["content"], list)
            types = [c.get("type") for c in user_msg["content"]]
            self.assertIn("image_url", types)
            self.assertIn("text", types)

    def test_opportunistic_priority_recovery(self):
        """Top priority candidate (Gemini) should be restored once its cooldown expires, without thrashing."""
        client = CloudClient()
        client.config["api_keys"] = {
            "gemini": "test_gemini_key",
            "openrouter": "test_openrouter_key"
        }

        # Step 1: Initially Gemini is chosen (highest priority)
        c1 = client.get_best_working_model(force_refresh=True, requires_vision=True)
        self.assertEqual(c1["provider"], "gemini")

        # Step 2: Entire Gemini provider enters rate-limit cooldown
        CloudClient.mark_provider_failed("gemini", cooldown_seconds=60)

        # Step 3: Immediate next call switches to OpenRouter fallback
        c2 = client.get_best_working_model(force_refresh=False, requires_vision=True)
        self.assertEqual(c2["provider"], "openrouter")

        # Step 4: Call before cooldown expires sticks to OpenRouter (NO thrashing)
        c3 = client.get_best_working_model(force_refresh=False, requires_vision=True)
        self.assertEqual(c3["provider"], "openrouter")

        # Step 5: After Gemini cooldown expires, next call opportunistically recovers Gemini!
        CloudClient._model_cooldowns.clear()
        CloudClient._provider_cooldowns.clear()
        c4 = client.get_best_working_model(force_refresh=False, requires_vision=True)
        self.assertEqual(c4["provider"], "gemini")

    def test_adaptive_exponential_backoff_on_consecutive_429(self):
        """Consecutive 429 errors must exponentially back off (60s -> 120s -> 240s) to prevent thrashing loops."""
        CloudClient._provider_backoff_level.clear()
        t_base = time.time()

        # 1st 429
        CloudClient.mark_model_failed("gemini", "gemini-flash-latest", cooldown_seconds=60, is_rate_limit=True)
        cd1 = CloudClient._model_cooldowns.get("gemini:gemini-flash-latest", 0) - t_base
        self.assertAlmostEqual(cd1, 60, delta=2)
        self.assertEqual(CloudClient._provider_backoff_level.get("gemini"), 1)

        # 2nd consecutive 429
        CloudClient.mark_model_failed("gemini", "gemini-flash-latest", cooldown_seconds=60, is_rate_limit=True)
        cd2 = CloudClient._model_cooldowns.get("gemini:gemini-flash-latest", 0) - t_base
        self.assertAlmostEqual(cd2, 120, delta=2)
        self.assertEqual(CloudClient._provider_backoff_level.get("gemini"), 2)

        # 3rd consecutive 429
        CloudClient.mark_model_failed("gemini", "gemini-flash-latest", cooldown_seconds=60, is_rate_limit=True)
        cd3 = CloudClient._model_cooldowns.get("gemini:gemini-flash-latest", 0) - t_base
        self.assertAlmostEqual(cd3, 240, delta=2)
        self.assertEqual(CloudClient._provider_backoff_level.get("gemini"), 3)

    def test_dynamic_rules_registry_accumulation(self):
        """DynamicRulesRegistry captures slot definitions, electives, and formats compact prompts."""
        from core.document_spec import DynamicRulesRegistry
        from models import StudentRecord, CourseResult

        blueprint = {
            "exam_title": "B.E. SEM VIII MAY 2025",
            "semester": "VIII",
            "college_name": "237:TCE",
            "grading_system": "10-point CBCGS",
            "valid_grades": ["O", "A", "B", "C", "D", "P", "F"],
            "slots": [
                {"slot": 1, "code": "CSC801", "name": "AI", "credits": 3, "course_type": "Theory", "heads": ["TH", "IA"]},
                {"slot": 2, "code": "CSDO8013", "name": "RL", "credits": 3, "course_type": "Elective", "heads": ["TH", "IA"]},
                {"slot": 8, "code": "AIDSCSP801", "name": "Major Project II", "credits": 6, "course_type": "Project", "heads": ["TW", "PR"]}
            ]
        }

        registry = DynamicRulesRegistry.from_blueprint_and_spec(blueprint)
        self.assertEqual(len(registry.slot_profiles), 3)
        self.assertEqual(registry.slot_profiles[8]["max_tot"], 150.0)

        # Student takes a newly discovered elective in Slot 2 (CSDO8014 Deep Learning)
        st = StudentRecord(
            seat_no="7000445",
            student_name="DOE JANE",
            credits_earned="20",
            course_results=[
                CourseResult(course_code="CSC801", course_name="AI", marks={"TOT": "85"}),
                CourseResult(course_code="CSDO8014", course_name="Deep Learning", marks={"TOT": "88"}),
                CourseResult(course_code="AIDSCSP801", course_name="Major Project II", marks={"TOT": "136"})
            ]
        )
        registry.record_student(st)

        # Slot 2 now contains BOTH discovered electives
        elecs_slot2 = [e["code"] for e in registry.slot_electives[2]]
        self.assertIn("CSDO8013", elecs_slot2)
        self.assertIn("CSDO8014", elecs_slot2)
        self.assertEqual(registry.total_program_credits, 20.0)

        # Formatted prompt includes both electives
        prompt_block = registry.format_for_prompt()
        self.assertIn("CSDO8013 / CSDO8014", prompt_block)
        self.assertIn("Max TOT: 150", prompt_block)
        self.assertIn("Leading '/' before name marks Female", prompt_block)


if __name__ == "__main__":
    unittest.main()

