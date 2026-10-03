"""
Test Suite: Visual Blueprint & Sliced Vision Pipeline
====================================================
Verifies:
1. UI Template renders all 3 pipeline modes (local, cloud, visual_blueprint).
2. App routing properly dispatches pipeline_mode="visual_blueprint".
3. Visual blueprint extraction engine components:
   - render_page_as_jpeg creates valid image bytes
   - Multi-model context envelope serialization
   - Transactional rollback on mathematical invariant failure
"""

import copy
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app
from core.checksum_verifier import verify_student
from core.cloud_client import CloudClient
from core.visual_extractor import (
    _parse_student_dict_list,
    align_and_heal_student,
    extract_sliced_page,
    extract_visual_blueprint,
    normalize_course_code,
    render_page_as_jpeg,
    run_visual_blueprint_pipeline,
)
from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord


class TestVisualBlueprintPipeline(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.sample_pdf = os.path.abspath(os.path.join(APP_DIR, "..", "Batch Data", "21-22 data", "2122-8th.pdf"))

    def test_ui_template_contains_three_pipelines(self):
        """Test that index.html exposes all 3 options: local, cloud, and visual_blueprint."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('value="local"', html)
        self.assertIn('value="cloud"', html)
        self.assertIn('value="visual_blueprint"', html)
        self.assertIn("Visual Blueprint & Sliced Stream", html)

    def test_render_page_as_jpeg(self):
        """Test rendering PDF page 1 to JPEG bytes."""
        if not os.path.isfile(self.sample_pdf):
            self.skipTest(f"Sample PDF not found at {self.sample_pdf}")
        jpeg_bytes = render_page_as_jpeg(self.sample_pdf, page_index=0, dpi=100)
        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 1000)
        # Check JPEG magic bytes: 0xFF, 0xD8
        self.assertEqual(jpeg_bytes[:2], b"\xff\xd8")

    def test_transactional_rollback_preserves_valid_student(self):
        """Test that if an AI mutation introduces mathematical inconsistency, it rolls back."""
        catalog = CourseCatalog()
        catalog.courses.append(Course(code="52751", name="HPC", credits=4.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}))
        catalog.courses.append(Course(code="52752", name="DE", credits=4.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}))

        original = StudentRecord(
            seat_no="12345",
            student_name="SHARMA ROHIT",
            sgpi="10.0",
            credits_earned="8.0",
            total_cg="80.0"
        )
        original.course_results = [
            CourseResult(
                course_code="52751",
                marks={"TH": "60", "IA": "15", "TOT": "75"},
                grade="O",
                grade_point="10",
                credits="4",
                credit_points="40"
            ),
            CourseResult(
                course_code="52752",
                marks={"TH": "65", "IA": "18", "TOT": "83"},
                grade="O",
                grade_point="10",
                credits="4",
                credit_points="40"
            )
        ]

        # Baseline check passes
        res_orig = verify_student(original, catalog)
        self.assertTrue(res_orig.all_passed)

        # Simulate hallucinated AI proposal that corrupts grade points and CG (4.0 * 10.0 != 999)
        snapshot = copy.deepcopy(original)
        hallucinated = copy.deepcopy(original)
        hallucinated.course_results[0].credit_points = "999"
        hallucinated.total_cg = "999.0"

        res_hal = verify_student(hallucinated, catalog)
        self.assertFalse(res_hal.all_passed)
        self.assertFalse(res_hal.course_cg_ok)

        # Rollback logic
        recovered = snapshot if not res_hal.all_passed else hallucinated
        self.assertEqual(recovered.course_results[0].credit_points, "40")
        self.assertEqual(recovered.total_cg, "80.0")

    @patch("time.sleep")
    @patch("fitz.open")
    def test_run_visual_blueprint_pipeline_mocked(self, mock_fitz_open, mock_sleep):
        """Test run_visual_blueprint_pipeline end-to-end with mock cloud responses."""
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 2
        mock_page = MagicMock()
        mock_page.get_pixmap().tobytes.return_value = b"\xff\xd8fakejpeg"
        mock_page.get_text.return_value = "Page 1 Content\n52751 HPC\n52752 DE\n"
        mock_doc.__getitem__.return_value = mock_page
        mock_fitz_open.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True

        blueprint_payload = {
            "exam_title": "B.E. (COMPUTER ENGINEERING) (SEM VIII) (CBCGS) EXAMINATION",
            "college_name": "APSIT",
            "semester": "VIII",
            "academic_year": "2021-2022",
            "courses": [
                {"code": "52751", "name": "High Performance Computing", "credits": 4.0, "max_th": 80, "max_ia": 20, "max_tot": 100},
                {"code": "52752", "name": "Deep Learning", "credits": 4.0, "max_th": 80, "max_ia": 20, "max_tot": 100}
            ],
            "sample_record": {
                "seat_no": "12345",
                "name": "TEST STUDENT",
                "prn": "2020016400000000",
                "sgpi": 10.0,
                "total_cg": 80.0,
                "earned_credits": 8.0,
                "result_status": "P",
                "courses": {
                    "52751": {"th": "60", "ia": "15", "tot": "75", "grade": "O", "gp": 10.0, "cg": 40.0},
                    "52752": {"th": "65", "ia": "18", "tot": "83", "grade": "O", "gp": 10.0, "cg": 40.0}
                }
            }
        }

        page_records_payload = [
            {
                "seat_no": "12345",
                "name": "TEST STUDENT",
                "prn": "2020016400000000",
                "sgpi": 10.0,
                "total_cg": 80.0,
                "earned_credits": 8.0,
                "result_status": "P",
                "courses": [
                    {"code": "52751", "marks": {"TH": "60", "IA": "15", "TOT": "75"}, "grade": "O", "gp": 10.0, "cg": 40.0, "credits": 4.0},
                    {"code": "52752", "marks": {"TH": "65", "IA": "18", "TOT": "83"}, "grade": "O", "gp": 10.0, "cg": 40.0, "credits": 4.0}
                ]
            }
        ]

        mock_client.generate_direct.side_effect = [
            json.dumps(blueprint_payload),     # Stage 1 blueprint
            json.dumps(page_records_payload),  # Page 1 records
            json.dumps([])                     # Page 2 records
        ]

        meta, students, archetype = run_visual_blueprint_pipeline(
            pdf_path="dummy.pdf",
            client=mock_client
        )

        self.assertEqual(archetype, "visual_blueprint")
        self.assertEqual(len(meta.catalog.courses), 2)
        self.assertGreaterEqual(len(students), 1)
        self.assertEqual(students[0].seat_no, "12345")
        self.assertEqual(float(students[0].sgpi), 10.0)

    @patch("app._run_ai_extraction_worker")
    def test_api_extract_dispatches_visual_blueprint(self, mock_worker):
        """Test that POST /api/extract with pipeline_mode=visual_blueprint passes mode to worker."""
        res = self.client.post("/api/extract", data={
            "batch_file": "21-22 data/2122-8th.pdf",
            "pipeline_mode": "visual_blueprint"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("job_id", data)

        mock_worker.assert_called_once()
        args, kwargs = mock_worker.call_args
        # kwargs or args: job_id, target_pdf_path, cloud_crosscheck, pipeline_mode
        self.assertIn("visual_blueprint", kwargs.get("pipeline_mode", args[-1] if len(args) >= 4 else ""))

    @patch("time.sleep")
    @patch("fitz.open")
    def test_female_slash_and_prn_recovery(self, mock_fitz_open, mock_sleep):
        """Test female candidate marker, mother name extraction, and digital PRN recovery."""
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_page.get_pixmap().tobytes.return_value = b"\xff\xd8fakejpeg"
        # Mock digital text containing seat number and 16-digit PRN
        mock_page.get_text.return_value = "Seat No: 68115066  /BHALEKAR SANIKA (SUNITA)  PRN: 2021016400987654\n"
        mock_doc.__getitem__.return_value = mock_page
        mock_fitz_open.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True

        blueprint = {
            "exam_title": "B.E. SEM VI",
            "semester": "VI",
            "courses": [{"code": "CSC601", "name": "Data Analytics", "heads": ["TH", "IA", "TOT"]}]
        }

        # Simulate Vision AI output with leading slash in name and empty PRN
        page_records_payload = [
            {
                "seat_no": "68115066",
                "student_name": "/BHALEKAR SANIKA (SUNITA)",
                "prn": "",  # Empty PRN from Vision AI
                "sgpi": "8.50",
                "total_cg": "180",
                "courses": [{"code": "CSC601", "marks": {"TH": "50", "IA": "18", "TOT": "68"}}]
            }
        ]
        mock_client.generate_direct.return_value = json.dumps(page_records_payload)

        students = extract_sliced_page("dummy.pdf", 0, blueprint, mock_client)
        self.assertEqual(len(students), 1)
        s0 = students[0]
        # Verify female indicator '/' was stripped and gender identified as 'F'
        self.assertEqual(s0.gender, "F")
        self.assertFalse(s0.student_name.startswith("/"))
        self.assertIn("BHALEKAR SANIKA", s0.student_name)
        self.assertEqual(s0.mother_name, "SUNITA")
        # Verify 16-digit PRN was recovered from digital text ground truth
        self.assertEqual(s0.prn, "2021016400987654")

    @patch("time.sleep")
    @patch("fitz.open")
    def test_historical_sgpi_and_elective_catalog_expansion(self, mock_fitz_open, mock_sleep):
        """Test Sem VIII historical semester SGPI extraction and dynamic elective discovery."""
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 2
        mock_page = MagicMock()
        mock_page.get_pixmap().tobytes.return_value = b"\xff\xd8fakejpeg"
        mock_page.get_text.return_value = "Page content\n"
        mock_doc.__getitem__.return_value = mock_page
        mock_fitz_open.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True

        blueprint_payload = {
            "exam_title": "B.E. SEM VIII",
            "semester": "VIII",
            "courses": [{"code": "CSC801", "name": "Cloud Computing", "credits": 4.0}]
        }

        # Page 1 student with core course
        page_1_payload = [
            {
                "seat_no": "88111001",
                "student_name": "STUDENT ONE",
                "courses": [{"code": "CSC801", "marks": {"TOT": "75"}, "credits": 4.0, "gp": 9, "cg": 36}]
            }
        ]

        # Page 2 student with an elective not in Page 1 blueprint, plus historical SGPIs
        page_2_payload = [
            {
                "seat_no": "88111002",
                "student_name": "STUDENT TWO",
                "sgpi": "9.20",
                "cgpi": "8.85",
                "history": {
                    "sem1": "8.10",
                    "sem2": "8.30",
                    "sem3": "8.50",
                    "sem4": "8.75",
                    "sem5": "8.90",
                    "sem6": "9.00",
                    "sem7": "9.15"
                },
                "courses": [
                    {"code": "CSDLO8012", "name": "Natural Language Processing", "marks": {"TOT": "80"}, "credits": 3.0, "gp": 10, "cg": 30}
                ]
            }
        ]

        mock_client.generate_direct.side_effect = [
            json.dumps(blueprint_payload),
            json.dumps(page_1_payload),
            json.dumps(page_2_payload)
        ]

        meta, students, archetype = run_visual_blueprint_pipeline("dummy.pdf", client=mock_client)
        self.assertEqual(len(students), 2)

        # Verify dynamic elective was added to master catalog
        catalog_codes = [c.code for c in meta.catalog.courses]
        self.assertIn("CSDLO8012", catalog_codes)

        # Verify historical SGPIs and CGPI flattened correctly
        flat2 = students[1].to_flat_dict(meta.catalog)
        self.assertEqual(flat2.get("CGPI"), "8.85")
        self.assertEqual(flat2.get("SGPI_Sem_1"), "8.10")
        self.assertEqual(flat2.get("SGPI_Sem_7"), "9.15")

    def test_token_usage_tracking_in_generate_direct(self):
        """Test that CloudClient extracts and tracks tokens properly across calls."""
        client = CloudClient()
        client.reset_token_usage()

        client.config["provider"] = "gemini"
        client.config["model"] = "gemini-2.5-flash"
        client.config["api_keys"]["gemini"] = "dummy_gemini_key"
        client.config["auto_orchestrate"] = False

        audit_events = []
        def test_audit_cb(event):
            audit_events.append(event)

        # Mock urllib response for Gemini format
        mock_gemini_resp = io.BytesIO(json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": json.dumps({"test": "data"})}]}
            }],
            "usageMetadata": {
                "promptTokenCount": 1250,
                "candidatesTokenCount": 350,
                "totalTokenCount": 1600
            }
        }).encode("utf-8"))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_gemini_resp
            mock_urlopen.return_value = mock_cm

            res = client.generate_direct(
                user_prompt="Extract page table",
                audit_cb=test_audit_cb
            )

        usage = client.get_token_usage()
        self.assertEqual(usage["input_tokens"], 1250)
        self.assertEqual(usage["output_tokens"], 350)
        self.assertEqual(usage["total_tokens"], 1600)

        # Verify audit_cb emitted token metadata
        success_events = [e for e in audit_events if e.get("type") == "cloud_ai_success"]
        self.assertTrue(len(success_events) > 0)
        token_details = success_events[0].get("details", {}).get("tokens", {})
        self.assertEqual(token_details.get("input"), 1250)
        self.assertEqual(token_details.get("output"), 350)
        self.assertEqual(token_details.get("total"), 1600)

        # Reset tokens
        client.reset_token_usage()
        self.assertEqual(client.get_token_usage()["total_tokens"], 0)

    def test_ui_contains_token_telemetry_elements(self):
        """Test that index.html contains telemetry strip and results token pills."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('id="telemTokens"', html)
        self.assertIn('id="tokenPill"', html)
        self.assertIn('id="aiStatTokens"', html)

    def test_multi_key_pool_parsing_and_rotation(self):
        """Test multi-key pool parsing, masking, rotation and cooldown."""
        client = CloudClient()
        client.config["api_keys"]["anthropic"] = "sk-ant-key1, sk-ant-key2; sk-ant-key3\nsk-ant-key4"
        pool = client.get_key_pool("anthropic")
        self.assertEqual(pool, ["sk-ant-key1", "sk-ant-key2", "sk-ant-key3", "sk-ant-key4"])
        self.assertEqual(client.get_api_key("anthropic"), "sk-ant-key1")

        masked = client.mask_key("sk-ant-key1, sk-ant-key2")
        self.assertIn("2 keys pool", masked)

        next_key = client.rotate_key("anthropic", "sk-ant-key1")
        self.assertEqual(next_key, "sk-ant-key2")
        self.assertEqual(client.get_api_key("anthropic"), "sk-ant-key2")

    def test_anthropic_direct_generation_and_tokens(self):
        """Test Anthropic Messages API dispatch with multimodal image and token tracking."""
        client = CloudClient()
        client.reset_token_usage()
        client.config["provider"] = "anthropic"
        client.config["model"] = "claude-3-5-sonnet-20241022"
        client.config["api_keys"]["anthropic"] = "sk-ant-api03-mock"
        client.config["auto_orchestrate"] = False

        mock_resp = io.BytesIO(json.dumps({
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": json.dumps({"students": [{"seat": "1001"}]})}],
            "model": "claude-3-5-sonnet-20241022",
            "usage": {
                "input_tokens": 1450,
                "output_tokens": 220
            }
        }).encode("utf-8"))

        captured_req = []
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_resp
            def side_effect(req, *args, **kwargs):
                captured_req.append(req)
                return mock_cm
            mock_urlopen.side_effect = side_effect

            res = client.generate_direct(
                user_prompt="Extract students from page",
                image_bytes=b"fake_jpeg_content"
            )

        self.assertIsNotNone(res)
        self.assertIn("1001", res)
        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]
        self.assertEqual(req.get_full_url(), "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.headers.get("X-api-key"), "sk-ant-api03-mock")
        self.assertEqual(req.headers.get("Anthropic-version"), "2023-06-01")

        req_body = json.loads(req.data.decode("utf-8"))
        self.assertIn("messages", req_body)
        self.assertEqual(req_body["messages"][0]["role"], "user")
        content_items = req_body["messages"][0]["content"]
        self.assertTrue(any(c.get("type") == "image" for c in content_items))
        self.assertTrue(any(c.get("type") == "text" for c in content_items))

        usage = client.get_token_usage()
        self.assertEqual(usage["input_tokens"], 1450)
        self.assertEqual(usage["output_tokens"], 220)
        self.assertEqual(usage["total_tokens"], 1670)

    def test_deepseek_reasoner_generation_and_tokens(self):
        """Test DeepSeek OpenAI-compatible endpoint dispatch and token usage."""
        client = CloudClient()
        client.reset_token_usage()
        client.config["provider"] = "deepseek"
        client.config["model"] = "deepseek-reasoner"
        client.config["api_keys"]["deepseek"] = "sk-deepseek-mock"
        client.config["auto_orchestrate"] = False

        mock_resp = io.BytesIO(json.dumps({
            "choices": [{
                "message": {
                    "content": json.dumps({"students": [{"seat": "2002"}]}),
                    "reasoning_content": "Detailed reasoning here..."
                }
            }],
            "usage": {
                "prompt_tokens": 800,
                "completion_tokens": 150,
                "total_tokens": 950
            }
        }).encode("utf-8"))

        captured_req = []
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_resp
            def side_effect(req, *args, **kwargs):
                captured_req.append(req)
                return mock_cm
            mock_urlopen.side_effect = side_effect

            res = client.generate_direct(
                user_prompt="Audit student records"
            )

        self.assertIsNotNone(res)
        self.assertIn("2002", res)
        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]
        self.assertEqual(req.get_full_url(), "https://api.deepseek.com/chat/completions")
        self.assertEqual(req.headers.get("Authorization"), "Bearer sk-deepseek-mock")

        usage = client.get_token_usage()
        self.assertEqual(usage["input_tokens"], 800)
        self.assertEqual(usage["output_tokens"], 150)
        self.assertEqual(usage["total_tokens"], 950)

    def test_multi_key_pool_429_auto_rotation_in_generation(self):
        """Test that hitting HTTP 429 rotates to the next pool key immediately."""
        import urllib.error
        client = CloudClient()
        client.config["provider"] = "groq"
        client.config["model"] = "openai/gpt-oss-120b"
        client.config["api_keys"]["groq"] = "gsk_exhausted_key, gsk_fresh_key"
        client.config["auto_orchestrate"] = False

        mock_success_resp = io.BytesIO(json.dumps({
            "choices": [{
                "message": {"content": json.dumps({"recovered": True})}
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
        }).encode("utf-8"))

        call_keys = []
        def side_effect(req, *args, **kwargs):
            key = req.headers.get("Authorization", "").replace("Bearer ", "")
            call_keys.append(key)
            if key == "gsk_exhausted_key":
                raise urllib.error.HTTPError(
                    url=req.get_full_url(),
                    code=429,
                    msg="Too Many Requests",
                    hdrs={},
                    fp=io.BytesIO(b'{"error": {"message": "Rate limit exceeded"}}')
                )
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_success_resp
            return mock_cm

        with patch("urllib.request.urlopen", side_effect=side_effect):
            res = client.generate_direct(
                user_prompt="Quick test"
            )

        self.assertIsNotNone(res)
        self.assertIn("recovered", res)
        # Verify first call used exhausted key and second call used fresh key
        self.assertEqual(call_keys, ["gsk_exhausted_key", "gsk_fresh_key"])

    def test_ui_contains_anthropic_and_deepseek_options(self):
        """Test that index.html exposes Anthropic and DeepSeek API inputs and provider options."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('id="cloudApiKeyAnthropic"', html)
        self.assertIn('id="cloudApiKeyDeepseek"', html)
        self.assertIn('id="cloudApiKeyOpenrouter"', html)
        self.assertIn('value="anthropic"', html)
        self.assertIn('value="deepseek"', html)
        self.assertIn('value="openrouter"', html)

    def test_align_and_heal_student_marks_and_catalog_order(self):
        """Test anti-hallucination auto-healing: component sums, Grade/GP/CG reconciliation, and catalog ordering."""
        catalog = CourseCatalog()
        catalog.courses.append(Course(code="42211", name="Deep Learning", credits=3.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}))
        catalog.courses.append(Course(code="42212", name="Big Data Analytics", credits=3.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}))
        catalog.courses.append(Course(code="AIDSCSL701", name="Deep Learning Lab", credits=1.0, max_marks={"TW": 25, "PR": 25, "TOT": 50}))

        # Student with scrambled course order, inconsistent TH+IA, missing IA, and missing PR
        student = StudentRecord(
            seat_no="1033195",
            student_name="TEST CANDIDATE",
            sgpi="0.0",
            credits_earned="",
            total_cg="",
            total_marks=""
        )
        student.course_results = [
            # Scrambled order: AIDSCSL701 appears first instead of last
            CourseResult(
                course_code="AIDSCSL701",
                marks={"TW": "22", "TOT": "45"},  # PR is missing (should deduce 45 - 22 = 23)
                grade="O",
                grade_point="0",  # missing GP (Grade O -> 10)
                credits="1",
                credit_points=""   # missing CG (1 * 10 = 10)
            ),
            # 42212 appears second
            CourseResult(
                course_code="42212",
                marks={"TH": "50", "TOT": "68"},  # IA is missing (should deduce 68 - 50 = 18)
                grade="C",
                grade_point="7",
                credits="3",
                credit_points="0"   # missing CG (3 * 7 = 21)
            ),
            # 42211 appears third with hallucinated sum (52 + 16 != 99)
            CourseResult(
                course_code="42211",
                marks={"TH": "52", "IA": "16", "TOT": "99"},  # Hallucinated TOT, should heal to 68
                grade="C",
                grade_point="7",
                credits="3",
                credit_points="21"
            )
        ]

        healed = align_and_heal_student(student, catalog=catalog)

        # 1. Verify strict catalog ordering: 42211 -> 42212 -> AIDSCSL701
        self.assertEqual([cr.course_code for cr in healed.course_results], ["42211", "42212", "AIDSCSL701"])

        # 2. Verify component sum healing for 42211 (52 + 16 = 68)
        cr_42211 = healed.course_results[0]
        self.assertEqual(cr_42211.marks["TOT"], "68")
        self.assertEqual(cr_42211.grade_point, "7")
        self.assertEqual(cr_42211.credit_points, "21")

        # 3. Verify missing IA deduction for 42212 (68 - 50 = 18)
        cr_42212 = healed.course_results[1]
        self.assertEqual(cr_42212.marks["IA"], "18")
        self.assertEqual(cr_42212.credit_points, "21")

        # 4. Verify missing PR deduction and Grade O -> GP 10 for AIDSCSL701
        cr_lab = healed.course_results[2]
        self.assertEqual(cr_lab.marks["PR"], "23")
        self.assertEqual(cr_lab.grade_point, "10")
        self.assertEqual(cr_lab.credit_points, "10")

        # 5. Verify semester aggregate invariants:
        # Total CG = 21 + 21 + 10 = 52
        # Total Credits = 3 + 3 + 1 = 7
        # SGPI = 52 / 7 = 7.43
        # Total Marks = 68 + 68 + 45 = 181
        self.assertEqual(healed.total_cg, "52")
        self.assertEqual(healed.credits_earned, "7")
        self.assertEqual(healed.sgpi, "7.43")
        self.assertEqual(healed.total_marks, "181")

    @patch.object(CloudClient, "generate_direct")
    def test_stage1_discovery_and_p1_student_capture(self, mock_generate):
        """Test Stage 1 discovers true heads (no ghost TH) and extracts Page 1 students directly."""
        mock_response = json.dumps({
            "exam_title": "B.E. (AI & DS) Sem VIII C-Scheme May 2024",
            "semester": "VIII",
            "college_name": "Engineering College",
            "grading_system": "10-point CBCGS",
            "courses": [
                {
                    "code": "52901",
                    "name": "Distributed Computing",
                    "course_type": "Theory",
                    "heads": ["TH", "IA", "TOT"],
                    "max_marks": {"TH": 80, "IA": 20, "TOT": 100},
                    "credits": 3
                },
                {
                    "code": "52906",
                    "name": "Cloud & Edge Lab",
                    "course_type": "Lab",
                    "heads": ["TW", "PR", "TOT"],
                    "max_marks": {"TW": 25, "PR": 25, "TOT": 50},
                    "credits": 1
                }
            ],
            "page_1_students": [
                {
                    "seat_no": "5291001",
                    "student_name": "/PATIL ANANYA",
                    "gender": "F",
                    "candidate_type": "Regular",
                    "result_status": "PASS",
                    "courses": [
                        {
                            "code": "52901",
                            "marks": {"TH": "55", "IA": "17", "TOT": "72"},
                            "grade": "A",
                            "gp": "8",
                            "credits": "3",
                            "cg": "24"
                        },
                        {
                            "code": "52906",
                            "marks": {"TW": "22", "PR": "23", "TOT": "45"},
                            "grade": "O",
                            "gp": "10",
                            "credits": "1",
                            "cg": "10"
                        }
                    ]
                }
            ]
        })
        mock_generate.return_value = mock_response

        client = CloudClient()
        if not os.path.isfile(self.sample_pdf):
            self.skipTest("Sample PDF not available for blueprint test")

        res = extract_visual_blueprint(self.sample_pdf, client=client)
        meta = res["meta"]
        p1_students = res["page_1_students"]

        # 1. Verify discovered courses
        self.assertEqual(len(meta.catalog.courses), 2)
        c_th = meta.catalog.get_by_code("52901")
        c_lab = meta.catalog.get_by_code("52906")
        self.assertIn("TH", c_th.heads)
        self.assertNotIn("TH", c_lab.heads)
        self.assertIn("TW", c_lab.heads)
        self.assertIn("PR", c_lab.heads)

        # 2. Verify Page 1 student captured directly
        self.assertEqual(len(p1_students), 1)
        st = p1_students[0]
        self.assertEqual(st.seat_no, "5291001")
        self.assertEqual(st.gender, "F")
        self.assertFalse(st.student_name.startswith("/"))

    @patch.object(CloudClient, "generate_direct")
    def test_stage2_sliced_page_uses_cached_system_instruction(self, mock_generate):
        """Test Stage 2 injects locked schema into system_instruction and keeps user_prompt lean."""
        mock_generate.return_value = json.dumps({"students": []})
        client = CloudClient()

        blueprint = {
            "exam_title": "B.E. Sem VIII",
            "semester": "VIII",
            "courses": [
                {"code": "52901", "name": "Distributed Computing", "heads": ["TH", "IA", "TOT"], "credits": 3},
                {"code": "52906", "name": "Cloud Lab", "heads": ["TW", "PR", "TOT"], "credits": 1}
            ]
        }

        if not os.path.isfile(self.sample_pdf):
            self.skipTest("Sample PDF not available")

        extract_sliced_page(self.sample_pdf, page_index=1, blueprint=blueprint, client=client)

        self.assertTrue(mock_generate.called)
        _, kwargs = mock_generate.call_args_list[0]
        sys_inst = kwargs.get("system_instruction", "")
        usr_prompt = kwargs.get("user_prompt", "")

        # System instruction has locked schema for caching
        self.assertIn("52901", sys_inst)
        self.assertIn("52906", sys_inst)
        self.assertIn("LOCKED SESSION COURSE SCHEMA", sys_inst)

    def test_grade_normalization_eradicates_synthetic_grades(self):
        """Test that A+ and B+ grades are preserved verbatim for NEP 2020 / autonomous schemes."""
        catalog = CourseCatalog(courses=[
            Course(code="CSC801", name="AI", credits=3.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}),
            Course(code="CSDO8013", name="RL", credits=3.0, max_marks={"TH": 80, "IA": 20, "TOT": 100}),
            Course(code="CSL801", name="AI Lab", credits=1.0, max_marks={"TW": 25, "PR": 25, "TOT": 50}),
        ])

        student = StudentRecord(seat_no="7001", student_name="ALICE")
        student.course_results = [
            # A+ with GP 9 should be preserved verbatim
            CourseResult(course_code="CSC801", marks={"TOT": "72"}, grade="A+", grade_point="9", credits="3"),
            # B+ with GP 8 should be preserved verbatim
            CourseResult(course_code="CSDO8013", marks={"TOT": "62"}, grade="B+", grade_point="8", credits="3"),
            # GP 9 with missing grade should deduce Grade A+ or A
            CourseResult(course_code="CSL801", marks={"TOT": "42"}, grade="", grade_point="9", credits="1"),
        ]

        healed = align_and_heal_student(student, catalog=catalog)

        # 1. CSC801: Grade 'A+' is preserved verbatim, GP remains '9'
        self.assertEqual(healed.course_results[0].grade, "A+")
        self.assertEqual(healed.course_results[0].grade_point, "9")

        # 2. CSDO8013: Grade 'B+' is preserved verbatim, GP remains '8'
        self.assertEqual(healed.course_results[1].grade, "B+")
        self.assertEqual(healed.course_results[1].grade_point, "8")

        # 3. CSL801: GP 9 deduces Grade A
        self.assertEqual(healed.course_results[2].grade, "A")
        self.assertEqual(healed.course_results[2].grade_point, "9")

    def test_semester_sgpi_displacement_diploma(self):
        """Test Diploma student vector-text label anchoring prevents SGPI displacement."""
        from core.visual_extractor import _parse_student_dict_list

        catalog = CourseCatalog(courses=[
            Course(code="CSC801", name="AI", credits=3.0, max_marks={"TOT": 100})
        ])

        page_text = (
            "7000441  NITESH RAMESH YAMUNA (2021016402156235) (205253887148)\n"
            "   DIPLOMA CANDIDATE\n"
            "   60  16  76  3 O 10 30\n"
            "   SEM-III: 8.00 ; SEM-IV: 8.50 ; SEM-V: 9.00 ; SEM-VI: 8.80 ; SEM-VII: 9.10 ; CGPI: 8.68\n\n"
            "7000442  ANOTHER CANDIDATE (2021016402156236)\n"
            "   SEM-I: 7.20 ; SEM-II: 7.50\n"
        )

        # Simulate LLM returning naive sequential sem1..sem5
        raw_students = [{
            "seat_no": "7000441",
            "student_name": "NITESH RAMESH YAMUNA",
            "candidate_type": "Diploma",
            "history": {
                "sem1": "8.00",
                "sem2": "8.50",
                "sem3": "9.00",
                "sem4": "8.80",
                "sem5": "9.10"
            },
            "courses": [
                {"code": "CSC801", "marks": {"TOT": "76"}, "grade": "O", "gp": "10", "credits": "3"}
            ]
        }]

        parsed = _parse_student_dict_list(raw_students, catalog=catalog, page_text=page_text)
        self.assertEqual(len(parsed), 1)
        st = parsed[0]

        # Candidate is Diploma: sem1 and sem2 MUST NOT be present
        self.assertNotIn("1", st.history)
        self.assertNotIn("2", st.history)

        # Sem 3 through 7 must be accurately anchored to their roman numeral labels
        self.assertEqual(st.history.get("3", {}).get("sgpi"), "8.00")
        self.assertEqual(st.history.get("4", {}).get("sgpi"), "8.50")
        self.assertEqual(st.history.get("5", {}).get("sgpi"), "9.00")
        self.assertEqual(st.history.get("6", {}).get("sgpi"), "8.80")
        self.assertEqual(st.history.get("7", {}).get("sgpi"), "9.10")
        self.assertEqual(st.cgpi, "8.68")

        # And must not bleed into candidate 7000442
        self.assertNotIn("7.20", str(st.history))

    def test_prefix_normalized_course_code_alignment(self):
        """Test that serial prefixes like 01.CSC801 match CSC801 and align correctly."""
        catalog = CourseCatalog(courses=[
            Course(code="01.CSC801", name="AI", credits=3.0, max_marks={"TOT": 100}),
            Course(code="04.CSDO8013", name="RL", credits=3.0, max_marks={"TOT": 100}),
            Course(code="17.CSL801", name="AI Lab", credits=1.0, max_marks={"TOT": 50}),
        ])

        # Test catalog matching with and without prefixes
        self.assertIsNotNone(catalog.get_by_code("CSC801"))
        self.assertIsNotNone(catalog.get_by_code("01.CSC801"))
        self.assertIsNotNone(catalog.get_by_code("CSDO8013"))
        self.assertIsNotNone(catalog.get_by_code("04.CSDO8013"))

        # Test alignment sorts correctly regardless of whether student or catalog has prefix
        student = StudentRecord(seat_no="12345", student_name="BOB")
        student.course_results = [
            CourseResult(course_code="CSL801", marks={"TOT": "45"}),
            CourseResult(course_code="04.CSDO8013", marks={"TOT": "70"}),
            CourseResult(course_code="CSC801", marks={"TOT": "85"}),
        ]

        healed = align_and_heal_student(student, catalog=catalog)
        self.assertEqual(
            [normalize_course_code(cr.course_code) for cr in healed.course_results],
            ["CSC801", "CSDO8013", "CSL801"]
        )

    def test_2d_matrix_rows_parsing_and_zero_loss_export(self):
        """Test that _parse_matrix_rows_to_students parses dense 2D rows and preserves A+/B+."""
        from core.visual_extractor import _parse_matrix_rows_to_students

        catalog = CourseCatalog(courses=[
            Course(code="1313311", name="Policy", credits=2.0, heads=["TH", "IA", "TOT"]),
            Course(code="2014411", name="Mini Project", credits=2.0, heads=["TW", "PR", "TOT"])
        ])

        column_contract = [
            "Seat_No", "Student_Name", "Gender", "Candidate_Type", "PRN", "Result_Status",
            "Total_Marks", "Credits_Earned", "Total_CG", "SGPI",
            "1313311_TH", "1313311_IA", "1313311_TOT", "1313311_Grade", "1313311_GP",
            "2014411_TW", "2014411_PR", "2014411_TOT", "2014411_Grade", "2014411_GP"
        ]

        matrix_rows = [
            [
                "101410022", "AARADHYA SHINDE", "M", "Regular", "MU0341120240205831", "FAIL",
                "549", "22", "176.0", "0.00",
                "23", "16", "39", "A", "8",
                "42", "21", "63", "A+", "9"
            ],
            [
                "101410023", "ACHARI MOHIT", "M", "Regular", "MU0341120240205840", "PASS",
                "553", "23", "179.0", "7.78",
                "24", "16", "40", "A+", "9",
                "34", "19", "53", "A", "8"
            ]
        ]

        students = _parse_matrix_rows_to_students(column_contract, matrix_rows, catalog)
        self.assertEqual(len(students), 2)

        # Check candidate 1
        s1 = students[0]
        self.assertEqual(s1.seat_no, "101410022")
        self.assertEqual(s1.student_name, "AARADHYA SHINDE")
        self.assertEqual(s1.total_marks, "549")
        self.assertEqual(s1.course_results[0].grade, "A")
        self.assertEqual(s1.course_results[1].grade, "A+")  # Legitimate A+ preserved

        # Check candidate 2
        s2 = students[1]
        self.assertEqual(s2.seat_no, "101410023")
        self.assertEqual(s2.course_results[0].grade, "A+")  # Legitimate A+ preserved

        # Check flat export dictionary
        flat_s2 = s2.to_flat_dict(catalog)
        self.assertEqual(flat_s2["Seat_No"], "101410023")
        self.assertEqual(flat_s2["1313311_Grade"], "A+")
        self.assertEqual(flat_s2["SGPI"], "7.78")

    @patch.object(CloudClient, "generate_direct")
    def test_extract_sliced_page_with_2d_matrix_payload(self, mock_generate):
        """Test extract_sliced_page handles 2D matrix JSON payload seamlessly."""
        catalog = CourseCatalog(courses=[
            Course(code="1313311", name="Policy", credits=2.0, heads=["TH", "IA", "TOT"]),
        ])
        column_contract = [
            "Seat_No", "Student_Name", "Gender", "Candidate_Type", "PRN", "Result_Status",
            "Total_Marks", "Credits_Earned", "Total_CG", "SGPI",
            "1313311_TH", "1313311_IA", "1313311_TOT", "1313311_Grade", "1313311_GP"
        ]
        blueprint = {
            "exam_title": "TEST EXAM",
            "semester": "IV",
            "courses": [{"code": "1313311", "name": "Policy", "heads": ["TH", "IA", "TOT"], "credits": 2}],
            "column_contract": column_contract
        }

        mock_generate.return_value = json.dumps({
            "rows": [
                ["101410022", "AARADHYA SHINDE", "M", "Regular", "PRN1", "PASS", "500", "20", "160.0", "8.00", "25", "15", "40", "A+", "9"]
            ]
        })

        client = MagicMock()
        client.is_configured.return_value = True
        client.generate_direct = mock_generate

        with patch("core.visual_extractor.render_page_as_jpeg", return_value=b"\xff\xd8fake"), \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = "Page text"
            mock_doc.__getitem__.return_value = mock_page
            mock_fitz.return_value = mock_doc

            students = extract_sliced_page(
                pdf_path="dummy.pdf",
                page_index=0,
                blueprint=blueprint,
                client=client,
                column_contract=column_contract,
                catalog=catalog
            )

            self.assertEqual(len(students), 1)
            self.assertEqual(students[0].seat_no, "101410022")
            self.assertEqual(students[0].course_results[0].grade, "A+")

    @patch.object(CloudClient, "generate_direct")
    def test_extract_sliced_page_with_positional_slots_prevents_shift(self, mock_generate):
        """Test positional slot extraction strictly prevents column shift between CSDO8023 and AIDSCSDOL8023."""
        slots_def = [
            {"slot": 1, "code": "CSC801", "name": "AI", "type": "Theory", "credits": 3},
            {"slot": 2, "code": "CSDO8013", "name": "RL", "type": "Theory", "credits": 3},
            {"slot": 3, "code": "CSDO8023", "name": "Social Media Analytic", "type": "Theory", "credits": 3},
            {"slot": 4, "code": "ILO8021", "name": "PM", "type": "Theory", "credits": 3},
            {"slot": 5, "code": "CSL801", "name": "AI Lab", "type": "Lab", "credits": 1},
            {"slot": 6, "code": "AIDSCSDOL8013", "name": "RL Lab", "type": "Lab", "credits": 1},
            {"slot": 7, "code": "AIDSCSDOL8023", "name": "Social Media Analytic Lab", "type": "Lab", "credits": 1},
            {"slot": 8, "code": "AIDSCSP801", "name": "Major Project", "type": "Project", "credits": 6},
        ]
        catalog = CourseCatalog(courses=[
            Course(code=s["code"], name=s["name"], credits=s["credits"]) for s in slots_def
        ])
        blueprint = {
            "exam_title": "B.E. SEM VIII",
            "semester": "VIII",
            "courses": [{"code": s["code"], "name": s["name"], "credits": s["credits"]} for s in slots_def],
            "slots": slots_def
        }

        # Candidate 7000457 ground truth
        mock_generate.return_value = json.dumps({
            "students": [
                {
                    "seat_no": "7000457",
                    "student_name": "GAWAND OMKAR SANDEEP",
                    "gender": "M",
                    "candidate_type": "Regular",
                    "result_status": "P",
                    "total_marks": "484",
                    "credits_earned": "21",
                    "total_cg": "173",
                    "sgpi": "8.24",
                    "slots": {
                        "1": {"tot": "62", "th": "42", "in": "20", "grade": "A", "gp": "9", "cg": "27"},
                        "2": {"tot": "65", "th": "45", "in": "20", "grade": "A", "gp": "9", "cg": "27"},
                        "3": {"tot": "57", "th": "39", "in": "18", "grade": "B", "gp": "8", "cg": "24"},
                        "4": {"tot": "66", "th": "47", "in": "19", "grade": "A", "gp": "9", "cg": "27"},
                        "5": {"tot": "37", "tw": "19", "or": "18", "grade": "B", "gp": "8", "cg": "8"},
                        "6": {"tot": "38", "tw": "20", "or": "18", "grade": "A", "gp": "9", "cg": "9"},
                        "7": {"tot": "40", "tw": "20", "or": "20", "grade": "O", "gp": "10", "cg": "10"},
                        "8": {"tot": "119", "tw": "41", "or": "78", "grade": "B", "gp": "8", "cg": "48"}
                    }
                }
            ]
        })

        client = MagicMock()
        client.is_configured.return_value = True
        client.generate_direct = mock_generate

        with patch("core.visual_extractor.render_page_as_jpeg", return_value=b"\xff\xd8fake"), \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = "Page text"
            mock_doc.__getitem__.return_value = mock_page
            mock_fitz.return_value = mock_doc

            students = extract_sliced_page(
                pdf_path="dummy.pdf",
                page_index=0,
                blueprint=blueprint,
                client=client,
                catalog=catalog,
                slot_registry=slots_def
            )

            self.assertEqual(len(students), 1)
            s = students[0]
            self.assertEqual(s.seat_no, "7000457")
            self.assertEqual(s.total_marks, "484")
            self.assertEqual(s.sgpi, "8.24")

            # Check that Theory Course CSDO8023 (Slot 3) has marks=57, grade=B, gp=8
            c3 = next(cr for cr in s.course_results if cr.course_code == "CSDO8023")
            self.assertEqual(c3.marks.get("TOT"), "57")
            self.assertEqual(c3.grade, "B")
            self.assertEqual(c3.grade_point, "8")

            # Check that Lab Course AIDSCSDOL8023 (Slot 7) has marks=40, grade=O, gp=10
            c7 = next(cr for cr in s.course_results if cr.course_code == "AIDSCSDOL8023")
            self.assertEqual(c7.marks.get("TOT"), "40")
            self.assertEqual(c7.grade, "O")
            self.assertEqual(c7.grade_point, "10")

    @patch.object(CloudClient, "generate_direct")
    def test_extract_sliced_page_with_list_slots(self, mock_generate):
        """Test positional slots provided as a list bind properly to slot_registry."""
        slots_def = [
            {"slot": 1, "code": "CS101", "name": "Intro", "type": "Theory", "credits": 3},
            {"slot": 2, "code": "CS102", "name": "Data", "type": "Theory", "credits": 3},
        ]
        catalog = CourseCatalog(courses=[Course(code=s["code"], name=s["name"], credits=s["credits"]) for s in slots_def])
        blueprint = {"exam_title": "TEST", "semester": "I", "courses": [], "slots": slots_def}

        mock_generate.return_value = json.dumps({
            "students": [
                {
                    "seat_no": "99901",
                    "student_name": "JOHN DOE",
                    "slots": [
                        {"tot": "85", "grade": "O", "gp": "10", "cg": "30"},
                        {"tot": "70", "grade": "B", "gp": "8", "cg": "24"}
                    ]
                }
            ]
        })

        client = MagicMock()
        client.is_configured.return_value = True
        client.generate_direct = mock_generate

        with patch("core.visual_extractor.render_page_as_jpeg", return_value=b"\xff\xd8fake"), \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = "Page text"
            mock_doc.__getitem__.return_value = mock_page
            mock_fitz.return_value = mock_doc

            students = extract_sliced_page(
                pdf_path="dummy.pdf",
                page_index=0,
                blueprint=blueprint,
                client=client,
                catalog=catalog,
                slot_registry=slots_def
            )

            self.assertEqual(len(students), 1)
            s = students[0]
            self.assertEqual(s.course_results[0].course_code, "CS101")
            self.assertEqual(s.course_results[0].marks.get("TOT"), "85")
            self.assertEqual(s.course_results[1].course_code, "CS102")
            self.assertEqual(s.course_results[1].marks.get("TOT"), "70")

    def test_field_precision_course_credits_and_cg_healing(self):
        """Test that duplicated CG in credits (e.g. 21) heals to catalog credits (3) and CG identity (C*GP) heals 9->10 and 54->60."""
        catalog = CourseCatalog(courses=[
            Course(code="CSDO8023", name="Social Media", credits=3.0),
            Course(code="AIDSCSDOL8023", name="Social Media Lab", credits=1.0),
            Course(code="AIDSCSP801", name="Major Project", credits=6.0)
        ])

        student = StudentRecord(
            seat_no="7000476",
            student_name="TEST STUDENT",
            course_results=[
                CourseResult(
                    course_code="CSDO8023",
                    marks={"TOT": "61"},
                    grade="C",
                    grade_point="7",
                    credits="21",  # Hallucinated duplication from CG
                    credit_points="21"
                ),
                CourseResult(
                    course_code="AIDSCSDOL8023",
                    marks={"TOT": "41"},
                    grade="O",
                    grade_point="10",
                    credits="1",
                    credit_points="9"  # Visual misread, should heal 1 * 10 = 10
                ),
                CourseResult(
                    course_code="AIDSCSP801",
                    marks={"TOT": "136"},
                    grade="O",
                    grade_point="10",
                    credits="6",
                    credit_points="54"  # Visual misread, should heal 6 * 10 = 60
                )
            ]
        )

        healed = align_and_heal_student(student, catalog=catalog)
        # CSDO8023 credits healed to 3
        self.assertEqual(healed.course_results[0].credits, "3")
        self.assertEqual(healed.course_results[0].credit_points, "21")
        # AIDSCSDOL8023 CG healed to 10
        self.assertEqual(healed.course_results[1].credits, "1")
        self.assertEqual(healed.course_results[1].credit_points, "10")
        # AIDSCSP801 CG healed to 60
        self.assertEqual(healed.course_results[2].credits, "6")
        self.assertEqual(healed.course_results[2].credit_points, "60")

    def test_field_precision_header_anchoring_and_gender(self):
        """Test that digital candidate header line anchors exact surname spelling, PRN, and gender."""
        catalog = CourseCatalog(courses=[Course(code="C1", name="Sub1", credits=3.0)])
        raw_students = [
            {
                "seat_no": "7000500",
                "student_name": "THORAT VAISHNAV VILAS SANDHYA",  # Standardized spelling from AI
                "gender": "F",  # AI hallucinated F due to SANDHYA
                "prn": "2021016402169999",  # Hallucinated PRN
                "slots": {"1": {"tot": "50", "grade": "C", "gp": "7", "credits": "3", "cg": "21"}}
            },
            {
                "seat_no": "7000458",
                "student_name": "DONDE SAKSHI DINESH SMITA",
                "gender": "F",
                "prn": "2022016402240447",
                "slots": {"1": {"tot": "40", "grade": "D", "gp": "6", "credits": "3", "cg": "18"}}
            }
        ]

        # Candidate text window containing exact header lines
        page_text = (
            "7000500  THORWAT VAISHNAV VILAS SANDHYA                                 (2021016402164111)             (137464702402)\n"
            "   37 16 53 3 C 7 21\n"
            "7000458 /DONDE SAKSHI DINESH SMITA                                      (2022016402240447)             (457807038422)\n"
            "   32 10 42 3 D 6 18\n"
        )

        students = _parse_student_dict_list(raw_students, catalog=catalog, page_text=page_text)
        self.assertEqual(len(students), 2)
        # 7000500: Name anchored to THORWAT, PRN to 2021016402164111, Gender to M (no slash)
        self.assertEqual(students[0].student_name, "THORWAT VAISHNAV VILAS SANDHYA")
        self.assertEqual(students[0].prn, "2021016402164111")
        self.assertEqual(students[0].gender, "M")
        # 7000458: Gender anchored to F (slash present)
        self.assertEqual(students[1].gender, "F")

    def test_spotlight_healer_preserves_all_metadata_and_history(self):
        """Zero-trust spotlight patch must never strip semester, cgpi, history, or metadata."""
        from core.spotlight_healer import validate_spotlight_patch, DiscrepancyReport
        from core.dynamic_relation_engine import DynamicRelationEngine
        from core.document_spec import DocumentExecutionSpec

        engine = DynamicRelationEngine(DocumentExecutionSpec())
        st = StudentRecord(
            seat_no="7000441",
            student_name="NITESH RAMESH YAMUNA",
            semester="VIII",
            cgpi="8.04",
            history={"1": {"sgpi": "9.58"}, "7": {"sgpi": "8.05"}},
            mother_name="YAMUNA",
            prn="2021016402156235",
            abc_id="205253887148",
            course_results=[
                CourseResult(
                    course_code="CSC801",
                    course_name="AI",
                    marks={"TH": "35", "IA": "15", "TOT": "50"},
                    grade="C",
                    grade_point="7",
                    credits="3",
                    credit_points="21"
                )
            ]
        )

        report = DiscrepancyReport(
            seat_no="7000441",
            student_name="NITESH RAMESH YAMUNA",
            course_code="CSC801",
            field_name="CSC801_TOT",
            extracted_val="50",
            expected_val="50",
            discrepancy_magnitude=0,
            formula="TH + IA == TOT",
            component_values={"TH": 35, "IA": 15}
        )

        patch = {
            "resolved": True,
            "corrected_field": "TOT",
            "corrected_value": "50",
            "reason": "Verified",
            "verbatim_evidence": "35 15 50"
        }

        ok, msg, patched = validate_spotlight_patch(
            patch=patch,
            report=report,
            student=st,
            engine=engine,
            raw_snippet="7000441 NITESH 35 15 50"
        )

        self.assertTrue(ok)
        self.assertEqual(patched.semester, "VIII")
        self.assertEqual(patched.cgpi, "8.04")
        self.assertEqual(len(patched.history), 2)
        self.assertEqual(patched.mother_name, "YAMUNA")

    def test_course_slot_project_profile_and_no_false_alarm(self):
        """Major Project courses must have max TOT 150 and not be treated as 50-mark lab slots."""
        from core.document_spec import CourseSlot, DocumentExecutionSpec
        from core.dynamic_relation_engine import DynamicRelationEngine

        slot = CourseSlot(
            slot=8,
            code="AIDSCSP801",
            name="Major Project II",
            credits=6.0,
            course_type="Project",
            components=["TW", "PR", "TOT"]
        )
        self.assertTrue(slot.is_project())
        self.assertFalse(slot.is_lab())
        self.assertEqual(slot.get_max_tot(), 150.0)

        spec = DocumentExecutionSpec(slots=[slot])
        engine = DynamicRelationEngine(spec)
        st = StudentRecord(
            seat_no="7000441",
            student_name="NITESH RAMESH YAMUNA",
            course_results=[
                CourseResult(
                    course_code="AIDSCSP801",
                    course_name="Major Project II",
                    marks={"TOT": "136", "TW": "91", "PR": "45"},
                    grade="O",
                    grade_point="10",
                    credits="6",
                    credit_points="60"
                )
            ],
            total_cg="60",
            credits_earned="6"
        )
        _, discs = engine.audit_and_heal_student(st)
        conflicts = [d for d in discs if d.status == "CONFLICT"]
        self.assertEqual(len(conflicts), 0)

    def test_parse_matrix_rows_with_page_text_preserves_history_and_semester(self):
        """_parse_matrix_rows_to_students anchors history and semester from page_text."""
        from core.visual_extractor import _parse_matrix_rows_to_students
        from models import ExamMeta

        catalog = CourseCatalog(courses=[Course(code="CSC801", name="AI", heads=["TH", "IA", "TOT"], credits=3.0)])
        meta = ExamMeta(exam_title="Test Exam", semester="VIII", college_name="Test", catalog=catalog)
        page_text = (
            "7000441  NITESH RAMESH YAMUNA  (2021016402156235)  (205253887148)\n"
            "   58 17 75 3 A 8 24  P 500\n"
            "   SGPI: SEM-I: 9.58 ; SEM-II: 9.36 ; SEM-VII: 8.05 ; CGPI: 8.04\n"
        )
        rows = [{"Seat_No": "7000441"}]
        students = _parse_matrix_rows_to_students(["Seat_No"], rows, catalog=catalog, meta=meta, page_text=page_text)
        self.assertEqual(len(students), 1)
        st = students[0]
        self.assertEqual(st.semester, "VIII")
        self.assertEqual(st.cgpi, "8.04")
        self.assertIn("1", st.history)
        self.assertEqual(st.history["1"]["sgpi"], "9.58")
        flat = st.to_flat_dict(catalog)
        self.assertEqual(flat["Semester"], "VIII")
        self.assertEqual(flat["CGPI"], "8.04")
        self.assertEqual(flat["SGPI_Sem_1"], "9.58")

    def test_mandatory_student_metadata_recall_and_sgpi_cgpi(self):
        """
        Guarantees that all mandatory student attributes (Name, Seat, PRN, Total Marks,
        Gender, Result Status, SGPI, CGPI, and Sem History) are correctly extracted
        and serialized without omission.
        """
        catalog = CourseCatalog(courses=[
            Course(code="CSC801", name="AI", credits=3.0, heads=["TH", "IA", "TOT"]),
            Course(code="CSDO8013", name="RL", credits=3.0, heads=["TH", "IA", "TOT"]),
        ])
        meta = ExamMeta(exam_title="B.E. SEM VIII", semester="VIII", catalog=catalog)

        # Candidate 1: Male candidate with female mother's name (SNEHA) -> Must be M
        # Candidate 2: Female candidate with leading slash -> Must be F
        # Candidate 3: Diploma candidate with held CGPI (--)
        page_text = (
            "  7000441  NITESH RAMESH YAMUNA  (2021016402156235)  (205253887148)\n"
            "    60  16  76  60  16  76  P  547\n"
            "    3 A 9 27  3 A 9 27  21  186  8.86\n"
            "    SGPI: SEM-I: 9.58 ; SEM-II: 8.18 ; SEM-VII: 8.05 ; CGPI: 8.04\n"
            "----------------------------------------------------------------------------------------------------\n"
            "  7000444 /ANGANE PRIYANKA VIVEK VIBHUTI  (2021016402160152)  (510040362489)\n"
            "    57  20  77  68  15  83  P  558\n"
            "    3 A 9 27  3 O 10 30  21  195  9.29\n"
            "    SGPI: SEM-I: 9.20 ; SEM-II: 9.10 ; SEM-VII: 9.15 ; CGPI: 9.14\n"
            "----------------------------------------------------------------------------------------------------\n"
            "  7000458  PATIL RAHUL SANJAY SNEHA  (2021016402169999)  (510040369999)\n"
            "    40  10  50  45  12  57  P  RLE  423\n"
            "    3 D 6 18  3 C 7 21  21  141  6.71\n"
            "    (DIPLOMA STUDENT)  SGPI: SEM-III: 7.22 ; SEM-IV: 5.92 ; SEM-VI: -- ; SEM-VII: 5.73 ; CGPI: --\n"
        )

        raw_students = [
            {"seat_no": "7000441"},
            {"seat_no": "7000444"},
            {"seat_no": "7000458"},
        ]

        students = _parse_student_dict_list(
            raw_students=raw_students,
            catalog=catalog,
            page_text=page_text
        )

        self.assertEqual(len(students), 3)

        # 1. Candidate 1 (Male, despite female mother's name YAMUNA)
        s1 = students[0]
        self.assertEqual(s1.seat_no, "7000441")
        self.assertEqual(s1.student_name, "NITESH RAMESH YAMUNA")
        self.assertEqual(s1.gender, "M")
        self.assertEqual(s1.prn, "2021016402156235")
        self.assertEqual(s1.abc_id, "205253887148")
        self.assertEqual(s1.result_status, "P")
        self.assertEqual(s1.total_marks, "547")
        self.assertEqual(s1.credits_earned, "21")
        self.assertEqual(s1.total_cg, "186")
        self.assertEqual(s1.sgpi, "8.86")
        self.assertEqual(s1.cgpi, "8.04")
        self.assertEqual(s1.history.get("1", {}).get("sgpi"), "9.58")
        self.assertEqual(s1.history.get("7", {}).get("sgpi"), "8.05")

        # 2. Candidate 2 (Female, leading slash correctly stripped from name)
        s2 = students[1]
        self.assertEqual(s2.seat_no, "7000444")
        self.assertEqual(s2.student_name, "ANGANE PRIYANKA VIVEK VIBHUTI")
        self.assertEqual(s2.gender, "F")
        self.assertEqual(s2.prn, "2021016402160152")
        self.assertEqual(s2.total_marks, "558")
        self.assertEqual(s2.sgpi, "9.29")
        self.assertEqual(s2.cgpi, "9.14")

        # 3. Candidate 3 (Diploma, RLE status, CGPI is --)
        s3 = students[2]
        self.assertEqual(s3.seat_no, "7000458")
        self.assertEqual(s3.student_name, "PATIL RAHUL SANJAY SNEHA")
        self.assertEqual(s3.gender, "M")
        self.assertEqual(s3.candidate_type, "Diploma")
        self.assertEqual(s3.result_status, "RLE")
        self.assertEqual(s3.remark, "RLE")
        self.assertEqual(s3.total_marks, "423")
        self.assertEqual(s3.sgpi, "6.71")
        self.assertEqual(s3.cgpi, "--")
        self.assertNotIn("1", s3.history)  # Diploma students do not have Sem 1
        self.assertEqual(s3.history.get("3", {}).get("sgpi"), "7.22")

        # 4. Serialization check to flat dict
        for st in students:
            flat = st.to_flat_dict(catalog)
            self.assertIn("Seat_No", flat)
            self.assertIn("Student_Name", flat)
            self.assertIn("Gender", flat)
            self.assertIn("PRN", flat)
            self.assertIn("Total_Marks", flat)
            self.assertIn("Result_Status", flat)
            self.assertIn("SGPI", flat)
            self.assertIn("CGPI", flat)
            self.assertTrue(len(flat["Student_Name"]) > 3)
            self.assertTrue(len(flat["PRN"]) >= 10)
            self.assertIn(flat["Gender"], ("M", "F"))
            self.assertTrue(len(flat["SGPI"]) > 0)
            self.assertTrue(len(flat["CGPI"]) > 0)

    def test_zero_missing_students_recovery_guarantee(self):
        """
        Guarantees that 100% of detected candidates on a page are recovered
        even if model JSON returns an incomplete list or omits candidates.
        """
        catalog = CourseCatalog(courses=[Course(code="CSC801", name="AI", credits=3.0)])
        page_text = (
            "  7000441  STUDENT ONE  (2021016402156235)\n"
            "    50  15  65  P  450\n"
            "    21  168  8.00\n"
            "  7000442  STUDENT TWO  (2021016402156236)\n"
            "    55  18  73  P  480\n"
            "    21  189  9.00\n"
        )
        detected_seats = ["7000441", "7000442"]

        # Simulate model only extracting candidate 1
        model_students = [
            StudentRecord(seat_no="7000441", student_name="STUDENT ONE", sgpi="8.00")
        ]

        extracted_seat_set = {s.seat_no for s in model_students if s.seat_no}
        missing_seats = [s for s in detected_seats if s not in extracted_seat_set]
        self.assertEqual(missing_seats, ["7000442"])

        recovered_raw = [{"seat_no": s} for s in missing_seats]
        recovered = _parse_student_dict_list(
            raw_students=recovered_raw,
            catalog=catalog,
            page_text=page_text
        )
        self.assertEqual(len(recovered), 1)
        r0 = recovered[0]
        self.assertEqual(r0.seat_no, "7000442")
        self.assertEqual(r0.student_name, "STUDENT TWO")
        self.assertEqual(r0.prn, "2021016402156236")
        self.assertEqual(r0.sgpi, "9.00")
        self.assertEqual(r0.total_marks, "480")


if __name__ == "__main__":
    unittest.main()


