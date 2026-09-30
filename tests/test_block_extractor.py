"""
Unit Tests for Universal Block-by-Block Gazette Extraction Pipeline.
Tests each stage independently with mocked Vision AI responses.
"""

import io
import json
import os
import re
import sys
import unittest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
test_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(test_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord, normalize_course_code
from core.block_extractor import (
    DocumentContract,
    PageData,
    StudentBlock,
    ComposedBlock,
    Conflict,
    prepare_pages,
    discover_document_contract,
    capture_header_band,
    segment_page_by_text,
    segment_page_fixed,
    compose_block_image,
    build_block_system_instruction,
    build_block_user_prompt,
    parse_block_response,
    validate_record,
    validate_all,
    build_exam_meta,
    _parse_num,
)


# ── Helpers ──

def _make_contract(**overrides):
    """Create a DocumentContract with sensible defaults."""
    defaults = {
        "institution": "Test University",
        "exam_title": "B.E. Semester VIII Examination",
        "semester": "VIII",
        "grading_scale": {"O": 10.0, "A": 9.0, "B": 8.0, "C": 7.0, "D": 6.0, "E": 5.0, "P": 4.0, "F": 0.0},
        "max_gpa": 10.0,
        "candidate_id_label": "Seat_No",
        "candidate_id_regex": r"\b([0-9]{7})\b",
        "courses": [
            {"code": "CSC801", "name": "AI", "type": "Theory", "heads": ["TH", "IA", "TOT"], "credits": 3},
            {"code": "CSL801", "name": "AI Lab", "type": "Lab", "heads": ["TW", "PR", "TOT"], "credits": 1},
        ],
        "header_y_start_pct": 10.0,
        "header_y_end_pct": 30.0,
        "has_history": True,
        "history_labels": ["SEM-I", "SEM-II", "SEM-III", "SEM-IV", "SEM-V", "SEM-VI", "SEM-VII"],
        "students_per_page": 3,
    }
    defaults.update(overrides)
    return DocumentContract(**defaults)


def _make_student(seat="1033195", courses=None, sgpi="8.50"):
    """Create a StudentRecord with courses."""
    if courses is None:
        courses = [
            CourseResult(
                course_code="CSC801", marks={"TH": "50", "IA": "18", "TOT": "68"},
                grade="C", grade_point="7", credits="3", credit_points="21"
            ),
            CourseResult(
                course_code="CSL801", marks={"TW": "22", "PR": "23", "TOT": "45"},
                grade="O", grade_point="10", credits="1", credit_points="10"
            ),
        ]
    return StudentRecord(
        seat_no=seat,
        student_name="TEST STUDENT",
        sgpi=sgpi,
        course_results=courses,
    )


# ── Tests ──

class TestBlockExtractorStage0(unittest.TestCase):
    """Stage 0: Page Preparation"""

    def test_prepare_pages_returns_list(self):
        """Smoke test: prepare_pages on a sample PDF returns PageData objects."""
        sample_pdf = os.path.join(test_dir, "sample_gazette.pdf")
        if not os.path.exists(sample_pdf):
            self.skipTest("sample_gazette.pdf not found in tests/")

        pages = prepare_pages(sample_pdf, dpi=72)
        self.assertGreater(len(pages), 0)
        self.assertIsInstance(pages[0].image_bytes, bytes)
        self.assertIsInstance(pages[0].text_layer, str)
        self.assertEqual(pages[0].page_index, 0)


class TestBlockExtractorStage1(unittest.TestCase):
    """Stage 1: Document Fingerprint Discovery"""

    def test_discover_contract_parses_response(self):
        """Test that discover_document_contract correctly parses AI JSON response."""
        mock_response = json.dumps({
            "institution": "VTU Belagavi",
            "exam_title": "B.E. 8th Semester Exam June 2024",
            "semester": "VIII",
            "grading_scale": {
                "grade_points": {"O": 10, "A+": 9, "A": 8, "B+": 7, "B": 6, "C": 5, "P": 4, "F": 0},
                "max_gpa": 10
            },
            "candidate_id": {
                "label": "USN",
                "format": "alphanumeric",
                "example": "1MS21CS045",
                "regex_hint": "[A-Z0-9]{10}"
            },
            "secondary_id": {"label": "null", "regex_hint": "null"},
            "courses": [
                {"code": "21CS81", "name": "Internet of Things", "type": "Theory",
                 "heads": ["IA", "ESE", "TOT"], "max_marks": {"IA": 50, "ESE": 100, "TOT": 150}, "credits": 4}
            ],
            "header_band": {"y_start_percent": 12, "y_end_percent": 28},
            "conventions": {"female_indicator": "none", "absent_tokens": ["AB"], "grace_tokens": []},
            "has_history": False,
            "history_labels": [],
            "students_per_page": 5
        })

        mock_client = MagicMock()
        mock_client.generate_direct.return_value = mock_response

        # Create a dummy PageData
        from PIL import Image
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        page = PageData(page_index=0, image_bytes=buf.getvalue(), text_layer="dummy text")

        contract = discover_document_contract(page, mock_client)

        self.assertEqual(contract.institution, "VTU Belagavi")
        self.assertEqual(contract.semester, "VIII")
        self.assertEqual(contract.candidate_id_label, "USN")
        self.assertIn("A+", contract.grading_scale)
        self.assertEqual(contract.grading_scale["A+"], 9.0)
        self.assertEqual(contract.grading_scale["O"], 10.0)
        self.assertEqual(len(contract.courses), 1)
        self.assertEqual(contract.courses[0]["code"], "21CS81")
        self.assertEqual(contract.courses[0]["heads"], ["IA", "ESE", "TOT"])
        self.assertFalse(contract.has_history)
        self.assertEqual(contract.students_per_page, 5)

    def test_discover_contract_handles_bad_response(self):
        """Test that discover_document_contract returns safe defaults on bad JSON."""
        mock_client = MagicMock()
        mock_client.generate_direct.return_value = "not valid json!!!"

        from PIL import Image
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        page = PageData(page_index=0, image_bytes=buf.getvalue(), text_layer="")

        contract = discover_document_contract(page, mock_client)
        # Should return defaults without crashing
        self.assertEqual(contract.institution, "")
        self.assertEqual(len(contract.courses), 0)


class TestBlockExtractorStage2(unittest.TestCase):
    """Stage 2: Header Band Capture"""

    def test_capture_header_band_crops_correctly(self):
        """Test that header band is cropped at the contract's Y percentages."""
        from PIL import Image
        img = Image.new("RGB", (1000, 2000), (200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")

        contract = _make_contract(header_y_start_pct=10.0, header_y_end_pct=30.0)
        header = capture_header_band(buf.getvalue(), contract)

        header_img = Image.open(io.BytesIO(header))
        # Expected: 10% of 2000 = 200, 30% of 2000 = 600, height = 400
        self.assertEqual(header_img.height, 400)
        self.assertEqual(header_img.width, 1000)

    def test_capture_header_band_minimum_height(self):
        """Test that header band enforces minimum 80px height."""
        from PIL import Image
        img = Image.new("RGB", (500, 500), (200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")

        # 1% of 500 = 5px, 2% = 10px → too small, should enforce 80px
        contract = _make_contract(header_y_start_pct=1.0, header_y_end_pct=2.0)
        header = capture_header_band(buf.getvalue(), contract)

        header_img = Image.open(io.BytesIO(header))
        self.assertGreaterEqual(header_img.height, 75)  # At least 0.15 * 500 = 75


class TestBlockExtractorStage3(unittest.TestCase):
    """Stage 3: Page Segmentation"""

    def test_segment_by_text_finds_seats(self):
        """Test text-layer segmentation with numeric seat numbers."""
        page_text = """
UNIVERSITY GAZETTE
1033195  STUDENT A   50 18 68  22 23 45   PASS  7.82
1033196  STUDENT B   40 15 55  20 20 40   PASS  6.50
1033197  STUDENT C   60 20 80  25 25 50   PASS  9.00
"""
        contract = _make_contract(candidate_id_regex=r"\b(103\d{4})\b")
        blocks = segment_page_by_text(page_text, 2000, 0, contract)

        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0].seat_hint, "1033195")
        self.assertEqual(blocks[1].seat_hint, "1033196")
        self.assertEqual(blocks[2].seat_hint, "1033197")

    def test_segment_by_text_alphanumeric_usn(self):
        """Test text-layer segmentation with alphanumeric USN patterns."""
        page_text = """
VTU GAZETTE
1MS21CS045  ADITI SHARMA    78 65 143  45 50 95   PASS  8.75
1MS21CS089  BHARATH K       60 55 115  40 45 85   PASS  7.90
"""
        contract = _make_contract(candidate_id_regex=r"\b(1MS21CS\d{3})\b")
        blocks = segment_page_by_text(page_text, 1500, 0, contract)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].seat_hint, "1MS21CS045")
        self.assertEqual(blocks[1].seat_hint, "1MS21CS089")

    def test_segment_fixed_height(self):
        """Test fixed-height fallback segmentation."""
        blocks = segment_page_fixed(2000, 400, page_index=0, students_per_page=4)

        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0].y_start, 400)
        expected_height = (2000 - 400) // 4
        self.assertEqual(blocks[0].y_end, 400 + expected_height)
        self.assertEqual(blocks[3].y_end, 400 + 4 * expected_height)


class TestBlockExtractorStage4(unittest.TestCase):
    """Stage 4: Block Extraction"""

    def test_compose_block_image_splices_correctly(self):
        """Test that compose_block_image creates a vertically spliced image."""
        from PIL import Image

        # Create a fake page image (1000 x 2000)
        page_img = Image.new("RGB", (1000, 2000), (100, 100, 100))
        buf = io.BytesIO()
        page_img.save(buf, format="JPEG")
        page_bytes = buf.getvalue()

        # Create a header band (1000 x 300)
        header_img = Image.new("RGB", (1000, 300), (200, 200, 200))
        buf2 = io.BytesIO()
        header_img.save(buf2, format="JPEG")
        header_bytes = buf2.getvalue()

        block = StudentBlock(page_index=0, seat_hint="1033195", y_start=500, y_end=900)
        composed = compose_block_image(page_bytes, header_bytes, block)

        result_img = Image.open(io.BytesIO(composed))
        self.assertEqual(result_img.width, 1000)
        self.assertEqual(result_img.height, 300 + (900 - 500))  # header + student row

    def test_build_system_instruction_compact(self):
        """Test that system instruction is compact and contains course list."""
        contract = _make_contract()
        instr = build_block_system_instruction(contract)

        self.assertIn("CSC801", instr)
        self.assertIn("CSL801", instr)
        self.assertIn("TH,IA,TOT", instr)
        self.assertIn("TW,PR,TOT", instr)
        self.assertIn("O=10", instr)

    def test_build_system_instruction_with_autonomous_grades(self):
        """Test system instruction with A+/B+ grading scale."""
        contract = _make_contract(
            grading_scale={"O": 10.0, "A+": 9.0, "A": 8.0, "B+": 7.0, "B": 6.0, "C": 5.0, "P": 4.0, "F": 0.0}
        )
        instr = build_block_system_instruction(contract)
        self.assertIn("A+=9", instr)
        self.assertIn("B+=7", instr)

    def test_parse_block_response_valid(self):
        """Test parsing a well-formed block response."""
        contract = _make_contract()
        resp = json.dumps({
            "id": "1033195",
            "name": "SHEIKH MOHD NAYEF",
            "gender": "M",
            "type": "Regular",
            "status": "PASS",
            "total": "113",
            "credits": "4",
            "total_cg": "31",
            "sgpi": "7.75",
            "courses": [
                {"code": "CSC801", "marks": {"TH": "50", "IA": "18", "TOT": "68"},
                 "grade": "C", "gp": "7", "cr": "3", "cg": "21"},
                {"code": "CSL801", "marks": {"TW": "22", "PR": "23", "TOT": "45"},
                 "grade": "O", "gp": "10", "cr": "1", "cg": "10"}
            ],
            "history": {"SEM-I": "6.50", "SEM-II": "7.00"}
        })

        student = parse_block_response(resp, contract)

        self.assertIsNotNone(student)
        self.assertEqual(student.seat_no, "1033195")
        self.assertEqual(student.student_name, "SHEIKH MOHD NAYEF")
        self.assertEqual(len(student.course_results), 2)
        self.assertEqual(student.course_results[0].marks["TH"], "50")
        self.assertEqual(student.course_results[1].marks["TW"], "22")
        self.assertEqual(student.sgpi, "7.75")
        # History: SEM-I -> key "1", SEM-II -> key "2"
        self.assertIn("1", student.history)
        self.assertEqual(student.history["1"]["sgpi"], "6.50")

    def test_parse_block_response_empty(self):
        """Test that empty/invalid response returns None."""
        contract = _make_contract()
        self.assertIsNone(parse_block_response("", contract))
        self.assertIsNone(parse_block_response("{}", contract))
        self.assertIsNone(parse_block_response("not json", contract))


class TestBlockExtractorStage5(unittest.TestCase):
    """Stage 5: Math Validation"""

    def test_parse_num_basic(self):
        self.assertEqual(_parse_num("50"), 50.0)
        self.assertEqual(_parse_num("0"), 0.0)
        self.assertIsNone(_parse_num(""))
        self.assertIsNone(_parse_num("AB"))
        self.assertIsNone(_parse_num(None))

    def test_parse_num_grace_marks(self):
        self.assertEqual(_parse_num("40#"), 40.0)
        self.assertEqual(_parse_num("36+4#"), 40.0)
        self.assertEqual(_parse_num("45*"), 45.0)

    def test_validate_clean_record(self):
        """A mathematically correct record should have zero conflicts."""
        contract = _make_contract()
        student = _make_student(sgpi="7.75")
        # Fix sgpi to match: CG = 21 + 10 = 31, Credits = 3 + 1 = 4, SGPI = 31/4 = 7.75
        conflicts = validate_record(student, contract)
        self.assertEqual(len(conflicts), 0)

    def test_validate_component_sum_error(self):
        """Detect TH + IA != TOT."""
        contract = _make_contract()
        student = _make_student()
        # Break the sum: TH=50, IA=18, TOT=70 (should be 68)
        student.course_results[0].marks["TOT"] = "70"
        conflicts = validate_record(student, contract)
        comp_conflicts = [c for c in conflicts if c.conflict_type == "COMPONENT_SUM"]
        self.assertGreater(len(comp_conflicts), 0)

    def test_validate_grade_gp_conflict(self):
        """Detect Grade 'C' with GP 9 (should be 7)."""
        contract = _make_contract()
        student = _make_student()
        student.course_results[0].grade_point = "9"  # C should be 7
        conflicts = validate_record(student, contract)
        gp_conflicts = [c for c in conflicts if c.conflict_type == "GRADE_GP"]
        self.assertGreater(len(gp_conflicts), 0)

    def test_validate_cg_product_error(self):
        """Detect Credits × GP != CG."""
        contract = _make_contract()
        student = _make_student()
        student.course_results[0].credit_points = "30"  # Should be 3 × 7 = 21
        conflicts = validate_record(student, contract)
        cg_conflicts = [c for c in conflicts if c.conflict_type == "CG_PRODUCT"]
        self.assertGreater(len(cg_conflicts), 0)

    def test_validate_sgpi_mismatch(self):
        """Detect SGPI calculation mismatch."""
        contract = _make_contract()
        student = _make_student(sgpi="9.99")  # Real SGPI = (21+10) / (3+1) = 7.75
        conflicts = validate_record(student, contract)
        sgpi_conflicts = [c for c in conflicts if c.conflict_type == "SGPI_MISMATCH"]
        self.assertGreater(len(sgpi_conflicts), 0)

    def test_validate_autonomous_grading_scale(self):
        """A+ with GP 9 should NOT conflict under UGC/autonomous scale."""
        contract = _make_contract(
            grading_scale={"O": 10.0, "A+": 9.0, "A": 8.0, "B+": 7.0, "B": 6.0, "C": 5.0, "P": 4.0, "F": 0.0}
        )
        student = _make_student()
        student.course_results[0].grade = "A+"
        student.course_results[0].grade_point = "9"
        student.course_results[0].credit_points = "27"  # 3 × 9 = 27
        # Fix SGPI: CG = 27 + 10 = 37, Credits = 4, SGPI = 9.25
        student.sgpi = "9.25"
        conflicts = validate_record(student, contract)
        gp_conflicts = [c for c in conflicts if c.conflict_type == "GRADE_GP"]
        self.assertEqual(len(gp_conflicts), 0)

    def test_validate_preserves_numeric_zero(self):
        """Grade F with GP 0 should NOT flag as conflict."""
        contract = _make_contract()
        student = _make_student()
        student.course_results[0].grade = "F"
        student.course_results[0].grade_point = "0"
        student.course_results[0].credit_points = "0"  # 3 × 0 = 0
        # SGPI: CG = 0 + 10 = 10, Credits = 4, SGPI = 2.50
        student.sgpi = "2.50"
        conflicts = validate_record(student, contract)
        gp_conflicts = [c for c in conflicts if c.conflict_type == "GRADE_GP"]
        self.assertEqual(len(gp_conflicts), 0)


class TestBlockExtractorMetaBuilder(unittest.TestCase):
    """Test build_exam_meta."""

    def test_build_exam_meta_from_contract(self):
        contract = _make_contract()
        meta = build_exam_meta(contract)

        self.assertEqual(meta.exam_title, "B.E. Semester VIII Examination")
        self.assertEqual(meta.college_name, "Test University")
        self.assertEqual(meta.semester, "VIII")
        self.assertEqual(meta.semester_num, 8)
        self.assertEqual(len(meta.catalog.courses), 2)
        self.assertEqual(meta.catalog.courses[0].code, "CSC801")
        self.assertEqual(meta.archetype, "block_extraction")

    def test_build_exam_meta_numeric_semester(self):
        contract = _make_contract(semester="4th Semester")
        meta = build_exam_meta(contract)
        self.assertEqual(meta.semester_num, 4)

    def test_build_exam_meta_roman_semester(self):
        contract = _make_contract(semester="III")
        meta = build_exam_meta(contract)
        self.assertEqual(meta.semester_num, 3)


class TestBlockExtractorCourseCodeNormalization(unittest.TestCase):
    """Test universal course code normalization."""

    def test_strip_serial_prefix(self):
        self.assertEqual(normalize_course_code("01.CSC801"), "CSC801")
        self.assertEqual(normalize_course_code("17.CSL801"), "CSL801")
        self.assertEqual(normalize_course_code("1.CSL801"), "CSL801")

    def test_preserve_clean_codes(self):
        self.assertEqual(normalize_course_code("CSC801"), "CSC801")
        self.assertEqual(normalize_course_code("42211"), "42211")
        self.assertEqual(normalize_course_code("21CS81"), "21CS81")
        self.assertEqual(normalize_course_code("AIDSCSL701"), "AIDSCSL701")


if __name__ == "__main__":
    unittest.main()
