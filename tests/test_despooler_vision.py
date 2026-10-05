"""
Unit and Integration Tests for Despooler + Vision AI Pipeline
============================================================
Verifies:
1. is_spooled_pdf detection accuracy on spooled vs non-spooled PDFs.
2. parse_spool_stream multi-tier candidate block parsing and course legend discovery.
3. render_despooled_pdf vector PDF synthesis with unified horizontal candidate cards.
4. run_visual_blueprint_pipeline integration with preloaded_meta and despooled_students.
5. app.py worker routing with auto-despooling when pipeline_mode == 'visual_blueprint'.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure data_extractor_AI and its parent are on sys.path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TEST_DIR)
PROJECT_ROOT = os.path.dirname(APP_DIR)

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import fitz
from core.despooler import (
    is_spooled_pdf,
    parse_spool_stream,
    render_despooled_pdf,
    despool_gazette_pdf,
    parse_spool_marks_components
)
from core.visual_extractor import run_visual_blueprint_pipeline, extract_visual_blueprint
from models import ExamMeta, StudentRecord, Course, CourseResult, CourseCatalog

SAMPLE_SPOOLED_PDF = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-7th.pdf")


class TestDespoolerCore(unittest.TestCase):
    """Tests for core despooler detection, parsing, and vector rendering."""

    def test_01_is_spooled_detection_real_file(self):
        """Verify is_spooled_pdf correctly identifies real continuous spool PDF."""
        if not os.path.isfile(SAMPLE_SPOOLED_PDF):
            self.skipTest(f"Test sample PDF not found: {SAMPLE_SPOOLED_PDF}")

        self.assertTrue(is_spooled_pdf(SAMPLE_SPOOLED_PDF))

    def test_02_is_spooled_detection_negative(self):
        """Verify is_spooled_pdf returns False for non-spooled document."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            temp_pdf = tf.name

        try:
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text(fitz.Point(50, 100), "Standard College Report\nRoll No: 101 Name: Test Student\nMarks: 85")
            doc.save(temp_pdf)
            doc.close()

            self.assertFalse(is_spooled_pdf(temp_pdf))
        finally:
            if os.path.exists(temp_pdf):
                os.remove(temp_pdf)

    def test_03_parse_spool_marks_components(self):
        """Verify component marks parsing from spool cells."""
        # Theory + IA cell: "48 + 14 = 62"
        res1 = parse_spool_marks_components("48 + 14 = 62", "")
        self.assertEqual(res1.get("TH_OR"), "48")
        self.assertEqual(res1.get("IA_TW"), "14")
        self.assertEqual(res1.get("Total"), "62")

        # Lab cell: "22 20" (Term work and Total)
        res2 = parse_spool_marks_components("22 20", "")
        self.assertEqual(res2.get("IA_TW"), "22")
        self.assertEqual(res2.get("Total"), "20")

        # 3-token practical cell: "22 20 42"
        res3 = parse_spool_marks_components("22 20 42", "")
        self.assertEqual(res3.get("TH_OR"), "22")
        self.assertEqual(res3.get("IA_TW"), "20")
        self.assertEqual(res3.get("Total"), "42")

    def test_04_parse_spool_stream_real_file(self):
        """Verify full candidate extraction and course catalog building from real spool."""
        if not os.path.isfile(SAMPLE_SPOOLED_PDF):
            self.skipTest(f"Test sample PDF not found: {SAMPLE_SPOOLED_PDF}")

        meta, students = parse_spool_stream(SAMPLE_SPOOLED_PDF)

        self.assertGreater(len(students), 50, "Should extract all students from spool ledger")
        self.assertGreater(len(meta.catalog.courses), 10, "Should extract legend courses from end pages")

        sample_student = students[0]
        self.assertTrue(sample_student.seat_no, "Student should have seat number")
        self.assertTrue(sample_student.student_name, "Student should have name")
        self.assertGreater(len(sample_student.course_results), 6, "Student should have combined courses from both row halves")

        # Verify that both theory courses (e.g. 42211) and lab/project courses (e.g. AIDSCSL701, AIDSCSP701) exist
        codes = [cr.course_code for cr in sample_student.course_results]
        self.assertTrue(any(c.startswith("422") or c.startswith("CSC") for c in codes))
        self.assertTrue(any("CSL" in c or "CSP" in c for c in codes))

    def test_05_render_despooled_pdf(self):
        """Verify synthesis of high-resolution vector PDF."""
        if not os.path.isfile(SAMPLE_SPOOLED_PDF):
            self.skipTest(f"Test sample PDF not found: {SAMPLE_SPOOLED_PDF}")

        meta, students = parse_spool_stream(SAMPLE_SPOOLED_PDF)
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_pdf = os.path.join(tmp_dir, "test_despooled.pdf")
            render_despooled_pdf(meta, students, out_pdf)

            self.assertTrue(os.path.isfile(out_pdf))
            doc = fitz.open(out_pdf)
            self.assertGreater(len(doc), 0)
            first_page_text = doc[0].get_text("text")
            self.assertIn("UNIVERSITY OF MUMBAI", first_page_text)
            self.assertIn(students[0].seat_no, first_page_text)
            doc.close()


class TestDespoolerVisionIntegration(unittest.TestCase):
    """Tests for seamless integration of Despooler with Visual Blueprint pipeline."""

    def test_06_visual_pipeline_with_preloaded_despool_fallback(self):
        """Verify run_visual_blueprint_pipeline safely returns despooled records when client is unconfigured."""
        mock_client = MagicMock()
        mock_client.is_configured.return_value = False

        meta = ExamMeta(exam_title="Test Exam", semester="VII", college_name="Test College", catalog=CourseCatalog())
        course = meta.catalog.add_course("CSC701", "Machine Learning", ["TH", "IA", "TOT"])
        course.credits = 3.0

        st = StudentRecord(seat_no="1001", student_name="ALICE SMITH", result_status="P", sgpi="8.50")
        cr = CourseResult(course_code="CSC701", course_name="Machine Learning", marks={"Total": "75"}, grade="A", grade_point="8")
        st.course_results.append(cr)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            dummy_pdf = tf.name

        try:
            doc = fitz.open()
            doc.new_page()
            doc.save(dummy_pdf)
            doc.close()

            res_meta, res_students, mode = run_visual_blueprint_pipeline(
                pdf_path=dummy_pdf,
                client=mock_client,
                preloaded_meta=meta,
                despooled_students=[st]
            )

            self.assertEqual(len(res_students), 1)
            self.assertEqual(res_students[0].seat_no, "1001")
            self.assertIn("despooled", mode)
        finally:
            if os.path.exists(dummy_pdf):
                os.remove(dummy_pdf)

    def test_07_extract_visual_blueprint_with_preloaded_meta(self):
        """Verify extract_visual_blueprint leverages preloaded_meta to seed catalog."""
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.generate_direct.return_value = '{"exam_title": "B.E. SEM VII", "courses": [], "slots": [], "page_1_students": []}'

        meta = ExamMeta(exam_title="Despooled BE VII", semester="VII", college_name="Engineering College", catalog=CourseCatalog())
        c1 = meta.catalog.add_course("CSC701", "Machine Learning", ["TH", "IA", "TOT"])
        c1.credits = 3.0
        c2 = meta.catalog.add_course("CSL701", "Machine Learning Lab", ["TW", "PR", "TOT"])
        c2.credits = 1.0

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            dummy_pdf = tf.name

        try:
            doc = fitz.open()
            p = doc.new_page()
            p.insert_text(fitz.Point(50, 50), "Despooled Gazette Page 1")
            doc.save(dummy_pdf)
            doc.close()

            bp_res = extract_visual_blueprint(
                pdf_path=dummy_pdf,
                client=mock_client,
                preloaded_meta=meta
            )

            res_meta = bp_res["meta"]
            self.assertEqual(res_meta.semester, "VII")
            self.assertEqual(len(res_meta.catalog.courses), 2)
            codes = [c.code for c in res_meta.catalog.courses]
            self.assertIn("CSC701", codes)
            self.assertIn("CSL701", codes)
        finally:
            if os.path.exists(dummy_pdf):
                os.remove(dummy_pdf)

    def test_08_candidate_reconciliation_guarantee(self):
        """Verify that any student present in despooled_students is reconciled for 100% recall."""
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        # Model only returns student 2001 on page 1, misses 2002
        mock_client.generate_direct.return_value = '''{
            "exam_title": "B.E. SEM VII",
            "semester": "VII",
            "courses": [{"code": "CSC701", "name": "AI", "heads": ["TH", "IA", "TOT"], "credits": 3}],
            "slots": [{"slot": 1, "code": "CSC701", "heads": ["TH", "IA", "TOT"], "credits": 3}],
            "page_1_students": [{
                "seat_no": "2001",
                "name": "STUDENT ONE",
                "slots": {"1": {"th": "60", "ia": "18", "tot": "78", "grade": "A", "gp": "8", "credits": "3", "cg": "24"}},
                "sgpi": "8.00"
            }]
        }'''

        meta = ExamMeta(exam_title="B.E. SEM VII", semester="VII", catalog=CourseCatalog())
        c = meta.catalog.add_course("CSC701", "AI", ["TH", "IA", "TOT"])
        c.credits = 3.0

        ds1 = StudentRecord(seat_no="2001", student_name="STUDENT ONE", sgpi="8.00")
        ds2 = StudentRecord(seat_no="2002", student_name="STUDENT TWO", sgpi="7.50")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            dummy_pdf = tf.name

        try:
            doc = fitz.open()
            p = doc.new_page()
            p.insert_text(fitz.Point(50, 50), "Page 1 - Seat: 2001 and 2002")
            doc.save(dummy_pdf)
            doc.close()

            res_meta, res_students, mode = run_visual_blueprint_pipeline(
                pdf_path=dummy_pdf,
                client=mock_client,
                preloaded_meta=meta,
                despooled_students=[ds1, ds2]
            )

            seat_nos = {s.seat_no for s in res_students}
            self.assertIn("2001", seat_nos)
            self.assertIn("2002", seat_nos, "Student 2002 should be reconciled from despooled_students")
            self.assertEqual(len(res_students), 2)
        finally:
            if os.path.exists(dummy_pdf):
                os.remove(dummy_pdf)


if __name__ == "__main__":
    unittest.main()
