"""
Unit Test Suite for Universal Semantic Normalizer & Adaptive 3-Tier Checksums
=============================================================================
Tests:
1. Canonical Assessment Head Aliases (ESE, ISE, CA, PO, TW, etc.).
2. Field Alias Equivalence (CSC301_TH == CSC301_SE).
3. Student Name Parsing (female prefix '/', mother's name '()', whitespace/newline sanitization).
4. Identifier String Fingerprinting (leading zeros preserved, float decimals removed).
5. Ledger Boilerplate Stripping (token minimization).
6. Adaptive 3-Tier Mathematical Checksums:
   - Standard TH + IA == TOT.
   - Lab TW + PR == TOT.
   - Missing IA auto-deduction from Total and Theory.
   - Combinatorial subset sum fallback.
   - Invariant Grace Rule (Tier 2 & Tier 3 math proofs preserve validity).
7. Sandbox Alias-Aware Matching.
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

from core.normalizer import (
    normalize_head,
    are_fields_alias_equivalent,
    normalize_student_name,
    normalize_identifier,
    normalize_result_status,
    strip_ledger_boilerplate,
)
from core.checksum_verifier import verify_student
from core.ai_cross_checker import validate_patch_sandbox, ContextEnvelope
from models import CourseResult, StudentRecord


class TestUniversalNormalizer(unittest.TestCase):
    """Tests for normalizer.py functions."""

    def test_normalize_head_aliases(self):
        self.assertEqual(normalize_head("ESE"), "TH")
        self.assertEqual(normalize_head("SE"), "TH")
        self.assertEqual(normalize_head("Theory"), "TH")
        self.assertEqual(normalize_head("E1"), "TH")

        self.assertEqual(normalize_head("ISE"), "IA")
        self.assertEqual(normalize_head("IN"), "IA")
        self.assertEqual(normalize_head("CA"), "IA")
        self.assertEqual(normalize_head("Test"), "IA")
        self.assertEqual(normalize_head("I1"), "IA")

        self.assertEqual(normalize_head("PO"), "PR")
        self.assertEqual(normalize_head("PRA"), "PR")
        self.assertEqual(normalize_head("Oral"), "PR")
        self.assertEqual(normalize_head("OR"), "PR")

        self.assertEqual(normalize_head("TW"), "TW")
        self.assertEqual(normalize_head("Termwork"), "TW")

        self.assertEqual(normalize_head("TOT"), "TOT")
        self.assertEqual(normalize_head("Grand_Total"), "TOT")

    def test_are_fields_alias_equivalent(self):
        self.assertTrue(are_fields_alias_equivalent("CSC301_TH", "CSC301_SE"))
        self.assertTrue(are_fields_alias_equivalent("CSC301_ESE", "CSC301_Theory"))
        self.assertTrue(are_fields_alias_equivalent("CSC302_ISE", "CSC302_IA"))
        self.assertTrue(are_fields_alias_equivalent("CSL301_PO", "CSL301_PR"))
        # Different courses should NOT match
        self.assertFalse(are_fields_alias_equivalent("CSC301_TH", "CSC302_TH"))
        # Different heads should NOT match
        self.assertFalse(are_fields_alias_equivalent("CSC301_TH", "CSC301_IA"))

    def test_normalize_student_name(self):
        # Female student with leading slash and mother name in parens
        raw = "/ PATIL SHRUTI\nASHOK (SANGEETA)"
        res = normalize_student_name(raw)
        self.assertEqual(res["gender"], "F")
        self.assertEqual(res["student_name"], "PATIL SHRUTI ASHOK")
        self.assertEqual(res["mother_name"], "SANGEETA")

        # Male student with comma and extra spaces
        raw_m = "CHOUDHARY,  MOHAMMED ZEESHAN  AHMED"
        res_m = normalize_student_name(raw_m)
        self.assertEqual(res_m["gender"], "M")
        self.assertEqual(res_m["student_name"], "CHOUDHARY MOHAMMED ZEESHAN AHMED")
        self.assertEqual(res_m["mother_name"], "")

    def test_normalize_identifier(self):
        # Preserve leading zeros
        self.assertEqual(normalize_identifier("0058215001"), "0058215001")
        # Strip float conversions
        self.assertEqual(normalize_identifier("58215001.0"), "58215001")
        # Strip special punctuation
        self.assertEqual(normalize_identifier("D-21/AIDS#01"), "D21AIDS01")

    def test_normalize_result_status(self):
        self.assertEqual(normalize_result_status("Successful"), "PASS")
        self.assertEqual(normalize_result_status("P"), "PASS")
        self.assertEqual(normalize_result_status("Unsuccessful"), "FAIL")
        self.assertEqual(normalize_result_status("F*"), "FAIL")
        self.assertEqual(normalize_result_status("ATKT"), "KT")
        self.assertEqual(normalize_result_status("ABSENT"), "ABS")

    def test_strip_ledger_boilerplate(self):
        raw_text = (
            "UNIVERSITY OF MUMBAI\n"
            "Page No. : 1\n"
            "DATE OF DECLARATION : 15/01/2024\n"
            "CREDIT SYSTEM FOR BE GRADUATION\n"
            "58215001 PATIL SHRUTI ASHOK\n"
            "CSC301 48 16 64 8 32\n"
            "äC : sum of credit points\n"
            "RPV - PROVISIONAL\n"
        )
        pruned = strip_ledger_boilerplate(raw_text)
        self.assertNotIn("UNIVERSITY OF MUMBAI", pruned)
        self.assertNotIn("Page No.", pruned)
        self.assertNotIn("äC : sum", pruned)
        self.assertIn("58215001 PATIL SHRUTI ASHOK", pruned)
        self.assertIn("CSC301 48 16 64 8 32", pruned)


class TestAdaptive3TierChecksum(unittest.TestCase):
    """Tests for adaptive checksum logic in checksum_verifier.py."""

    def test_standard_th_ia_sum(self):
        student = StudentRecord(seat_no="58215001", student_name="Alice", total_cg="32.0", credits_earned="4.0", sgpi="8.00")
        student.course_results = [
            CourseResult(course_code="CSC301", marks={"TH": "45", "IA": "15", "TOT": "60"}, credits="4", grade_point="8", credit_points="32")
        ]
        res = verify_student(student)
        self.assertTrue(res.all_passed)
        self.assertTrue(res.component_sum_ok)
        self.assertTrue(res.course_cg_ok)
        self.assertTrue(res.sem_total_cg_ok)
        self.assertTrue(res.sgpi_ok)

    def test_lab_tw_pr_sum(self):
        student = StudentRecord(seat_no="58215002", student_name="Bob", total_cg="18.0", credits_earned="2.0", sgpi="9.00")
        student.course_results = [
            CourseResult(course_code="CSL301", marks={"TW": "22", "PR": "23", "TOT": "45"}, credits="2", grade_point="9", credit_points="18")
        ]
        res = verify_student(student)
        self.assertTrue(res.all_passed)
        self.assertTrue(res.component_sum_ok)

    def test_missing_ia_column_auto_deduction(self):
        student = StudentRecord(seat_no="58215003", student_name="Charlie", total_cg="32.0", credits_earned="4.0", sgpi="8.00")
        # IA column missing from PDF extraction, but ESE is 48 and TOT is 64
        student.course_results = [
            CourseResult(course_code="CSC301", marks={"ESE": "48", "TOT": "64"}, credits="4", grade_point="8", credit_points="32")
        ]
        res = verify_student(student)
        self.assertTrue(res.all_passed)
        # IA must have been deduced as 16 (64 - 48)
        self.assertEqual(student.course_results[0].marks.get("IA"), "16")

    def test_invariant_grace_rule(self):
        # A gazette where internal component was absent or non-standard, but student Tier 2 & Tier 3 math is 100% verified
        student = StudentRecord(seat_no="58215004", student_name="Diana", total_cg="40.0", credits_earned="5.0", sgpi="8.00")
        student.course_results = [
            CourseResult(course_code="CSC301", marks={"TOT": "75"}, credits="5", grade_point="8", credit_points="40")
        ]
        res = verify_student(student)
        # Tier 2 (5 * 8 = 40) and Tier 3 (40 / 5 = 8.00) pass
        self.assertTrue(res.course_cg_ok)
        self.assertTrue(res.sem_total_cg_ok)
        self.assertTrue(res.sgpi_ok)
        self.assertTrue(res.all_passed)


class TestSandboxAliasMatching(unittest.TestCase):
    """Tests for alias-aware sandbox validation in ai_cross_checker.py."""

    def test_sandbox_accepts_alias_field(self):
        envelope = ContextEnvelope(
            valid_columns=["Seat_No", "Student_Name", "CSC301_SE", "CSC301_IA", "CSC301_TOT", "SGPI"],
            courses=[{"code": "CSC301", "name": "Operating Systems", "heads": ["SE", "IA", "TOT"]}]
        )
        students_by_seat = {
            "58215001": {"Seat_No": "58215001", "Student_Name": "Alice", "CSC301_SE": "35", "CSC301_IA": "15"}
        }
        # Cloud AI proposes a patch using "CSC301_TH" instead of table's "CSC301_SE"
        candidate = {
            "seat_no": "58215001",
            "field": "CSC301_TH",
            "suggested_val": "42",
            "evidence_quote": "CSC301 42 15 57"
        }
        raw_page_texts = ["58215001 Alice CSC301 42 15 57"]

        valid, reason = validate_patch_sandbox(candidate, raw_page_texts, envelope, students_by_seat)
        self.assertTrue(valid, f"Sandbox rejected valid alias: {reason}")
        # Candidate target field must have been mapped to actual column name "CSC301_SE"
        self.assertEqual(candidate["field"], "CSC301_SE")


if __name__ == "__main__":
    unittest.main()
