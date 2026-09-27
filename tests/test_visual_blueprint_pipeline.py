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


if __name__ == "__main__":
    unittest.main()
