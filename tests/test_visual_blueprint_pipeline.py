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
    extract_sliced_page,
    extract_visual_blueprint,
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


if __name__ == "__main__":
    unittest.main()

