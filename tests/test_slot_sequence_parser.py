"""
Unit Test Suite for Sequential Token Slot Engine
=================================================
Validates:
1. Grammar compilation from CourseCatalog.
2. Sequential token parsing into exact CourseResult objects.
3. Heartbeat Anchor resynchronization on missing marks.
4. Auto-reconciliation of GP, CG, SGPI, and Result.
5. End-to-end verification against authentic 11-course AI&DS candidate data.
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_APP_DIR = os.path.abspath(os.path.join(TESTS_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(AI_APP_DIR, ".."))
for p in [AI_APP_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.slot_sequence_parser import (
    CourseSlotGroup,
    SequentialSlotMachine,
    compile_course_slot_groups,
    is_grade_token,
    is_status_token
)
from models import Course, CourseCatalog, StudentRecord


class TestSlotSequenceParser(unittest.TestCase):
    """Test suite for Sequential Token Slot Engine."""

    def setUp(self):
        # 4-course test catalog
        self.catalog = CourseCatalog(courses=[
            Course(
                code="CSC401",
                name="Applied Mathematics - IV",
                heads=["TH", "IA", "TOT"],
                credits=4.0
            ),
            Course(
                code="CSC402",
                name="Analysis of Algorithm",
                heads=["TH", "IA", "TOT"],
                credits=4.0
            ),
            Course(
                code="CSL401",
                name="Analysis of Algorithm Lab",
                heads=["TW", "PR", "TOT"],
                credits=1.0
            ),
            Course(
                code="CSL402",
                name="Web Computing Lab",
                heads=["TW", "TOT"],
                credits=2.0
            )
        ])

    def test_compile_slot_groups(self):
        """Test that catalog compiles into correct ordered slot groups."""
        groups = compile_course_slot_groups(self.catalog)
        self.assertEqual(len(groups), 4)

        # CSC401 has TH, IA, TOT
        self.assertEqual(groups[0].course_code, "CSC401")
        self.assertEqual(groups[0].mark_heads, ["TH", "IA", "TOT"])
        self.assertEqual(groups[0].credits, 4.0)

        # CSL401 has TW, PR, TOT
        self.assertEqual(groups[2].course_code, "CSL401")
        self.assertEqual(groups[2].mark_heads, ["TW", "PR", "TOT"])

        # CSL402 has TW, TOT
        self.assertEqual(groups[3].course_code, "CSL402")
        self.assertEqual(groups[3].mark_heads, ["TW", "TOT"])

    def test_parse_perfect_sequential_stream(self):
        """Test consuming a clean sequential token stream."""
        sm = SequentialSlotMachine(self.catalog)

        # Tokens:
        # CSC401: 50, 30, 80, O, 10, 4, 40.0
        # CSC402: 60, 20, 80, O, 10, 4, 40.0
        # CSL401: 22, 23, 45, O, 10, 1, 10.0
        # CSL402: 45, 45, O, 10, 2, 20.0
        # Summary: 250, 11, 110.0, 10.00, PASS
        tokens = [
            "50", "30", "80", "O", "10", "4", "40.0",
            "60", "20", "80", "O", "10", "4", "40.0",
            "22", "23", "45", "O", "10", "1", "10.0",
            "45", "45", "O", "10", "2", "20.0",
            "250", "11", "110.0", "10.00", "PASS"
        ]
        meta = {"seat_no": "1001", "student_name": "JOHN DOE"}

        st = sm.parse_student_tokens(tokens, meta=meta)

        self.assertEqual(st.seat_no, "1001")
        self.assertEqual(st.student_name, "JOHN DOE")
        self.assertEqual(len(st.course_results), 4)

        # Verify Course 1
        cr1 = st.course_results[0]
        self.assertEqual(cr1.course_code, "CSC401")
        self.assertEqual(cr1.marks.get("TH"), "50")
        self.assertEqual(cr1.marks.get("IA"), "30")
        self.assertEqual(cr1.marks.get("TOT"), "80")
        self.assertEqual(cr1.grade, "O")
        self.assertEqual(cr1.grade_point, "10")

        # Verify Course 3 (Lab)
        cr3 = st.course_results[2]
        self.assertEqual(cr3.course_code, "CSL401")
        self.assertEqual(cr3.marks.get("TW"), "22")
        self.assertEqual(cr3.marks.get("PR"), "23")
        self.assertEqual(cr3.marks.get("TOT"), "45")

        # Verify Summary
        self.assertEqual(st.total_marks, "250")
        self.assertEqual(st.sgpi, "10.00")
        self.assertEqual(st.result_status, "PASS")

    def test_heartbeat_anchor_prevents_drift_on_missing_mark(self):
        """Test that a missing mark token does NOT drift columns because Grade acts as anchor."""
        sm = SequentialSlotMachine(self.catalog)

        # In CSC401, suppose the total mark '80' was faint and skipped by OCR:
        # Stream has: "50", "30", "O", "10", "4", "40.0" (only 2 marks before Grade 'O'!)
        tokens = [
            "50", "30", "O", "10", "4", "40.0",  # missing TOT, Grade 'O' catches it!
            "60", "20", "80", "O", "10", "4", "40.0",
            "22", "23", "45", "O", "10", "1", "10.0",
            "45", "45", "O", "10", "2", "20.0",
            "250", "11", "110.0", "10.00", "PASS"
        ]

        st = sm.parse_student_tokens(tokens)

        # Check CSC401: TH=50, IA=30, TOT auto-deduced as 80!
        cr1 = st.course_results[0]
        self.assertEqual(cr1.marks.get("TH"), "50")
        self.assertEqual(cr1.marks.get("IA"), "30")
        self.assertEqual(cr1.marks.get("TOT"), "80")
        self.assertEqual(cr1.grade, "O")

        # Check CSC402: Did NOT shift! TH is still 60, not 40 or O!
        cr2 = st.course_results[1]
        self.assertEqual(cr2.course_code, "CSC402")
        self.assertEqual(cr2.marks.get("TH"), "60")
        self.assertEqual(cr2.marks.get("IA"), "20")
        self.assertEqual(cr2.marks.get("TOT"), "80")

    def test_authentic_11_course_candidate(self):
        """Test against full 11-course AI&DS SEM4 student sequence."""
        catalog_aids = CourseCatalog(courses=[
            Course(code="1313311", name="Policy", heads=["TH", "IA", "TOT"], credits=2.0),
            Course(code="2014411", name="Mini Project", heads=["TW", "PR", "TOT"], credits=2.0),
            Course(code="2114111", name="Computational Theory", heads=["TH", "IA", "TOT"], credits=3.0),
            Course(code="2114112", name="DBMS", heads=["TH", "IA", "TOT"], credits=3.0),
            Course(code="2114113", name="OS", heads=["TH", "IA", "TOT"], credits=3.0),
            Course(code="2114114", name="DBMS Lab", heads=["TW", "PR", "TOT"], credits=1.0),
            Course(code="2114115", name="OS Lab", heads=["TW", "PR", "TOT"], credits=1.0),
            Course(code="2304211", name="Telecom", heads=["TH", "IA", "TOT"], credits=3.0),
            Course(code="2304212", name="Telecom Lab", heads=["TW", "PR", "TOT"], credits=1.0),
            Course(code="2994511", name="Business Model", heads=["TW", "TOT"], credits=2.0),
            Course(code="2994512", name="Design Thinking", heads=["TW", "TOT"], credits=2.0),
        ])

        sm = SequentialSlotMachine(catalog_aids)

        # Real tokens for Candidate 101410022
        tokens = [
            "101410022", "AARADHYA VISHAL SHINDE", "MU0341120240205831",
            "23", "16", "39", "A", "8", "2", "16.0",       # 1313311
            "42", "21", "63", "A+", "9", "2", "18.0",      # 2014411
            "41", "30", "71", "A", "8", "3", "24.0",       # 2114111
            "48", "22", "70", "A", "8", "3", "24.0",       # 2114112
            "34", "26", "60", "B+", "7", "3", "21.0",      # 2114113
            "3", "16", "19", "F", "0", "1", "0.0",         # 2114114 (FAILED)
            "21", "21", "42", "A+", "9", "1", "9.0",       # 2114115
            "41", "25", "66", "B+", "7", "3", "21.0",      # 2304211
            "21", "20", "41", "A+", "9", "1", "9.0",       # 2304212
            "40", "40", "A+", "9", "2", "18.0",            # 2994511
            "38", "38", "A", "8", "2", "16.0",             # 2994512
            "549", "22", "176.0", "0.00", "FAIL"           # Summary
        ]

        st = sm.parse_student_tokens(tokens)

        self.assertEqual(st.seat_no, "101410022")
        self.assertEqual(st.student_name, "AARADHYA VISHAL SHINDE")
        self.assertEqual(st.prn, "MU0341120240205831")
        self.assertEqual(len(st.course_results), 11)

        # Check Course 6 (DBMS Lab, failed)
        c6 = st.course_results[5]
        self.assertEqual(c6.course_code, "2114114")
        self.assertEqual(c6.marks.get("TW"), "3")
        self.assertEqual(c6.marks.get("PR"), "16")
        self.assertEqual(c6.marks.get("TOT"), "19")
        self.assertEqual(c6.grade, "F")
        self.assertEqual(c6.grade_point, "0")

        # Check Summary
        self.assertEqual(st.total_marks, "549")
        self.assertEqual(st.credits_earned, "22")
        self.assertEqual(float(st.total_cg), 176.0)
        self.assertEqual(st.sgpi, "0.00")
        self.assertEqual(st.result_status, "FAIL")


if __name__ == "__main__":
    unittest.main()
