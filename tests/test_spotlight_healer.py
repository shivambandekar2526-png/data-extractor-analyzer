"""
Unit Tests for SpotlightHealer & Zero-Trust Sandbox Gate
=========================================================
"""

import unittest
from core.document_spec import CourseSlot, DocumentExecutionSpec, get_default_relations
from core.dynamic_relation_engine import DiscrepancyReport, DynamicRelationEngine
from core.spotlight_healer import SpotlightHealer, validate_spotlight_patch
from models import CourseResult, StudentRecord


class TestSpotlightHealer(unittest.TestCase):

    def setUp(self):
        self.spec = DocumentExecutionSpec(
            exam_title="Test Exam",
            slots=[CourseSlot(slot=1, code="CSC401", credits=3.0, components=["TH", "IA", "TOT"])],
            algebraic_relations=get_default_relations()
        )
        self.engine = DynamicRelationEngine(self.spec)
        self.healer = SpotlightHealer(client=None, engine=self.engine)

    def test_sandbox_rejects_hallucinated_patch(self):
        """Test sandbox rejection when AI returns a patch that still fails math."""
        st = StudentRecord(
            seat_no="1001",
            student_name="Alice",
            course_results=[CourseResult(course_code="CSC401", marks={"TH": "40", "IA": "15", "TOT": "65"})]
        )
        report = DiscrepancyReport(
            seat_no="1001",
            student_name="Alice",
            course_code="CSC401",
            field_name="CSC401_TOT",
            extracted_val="65",
            expected_val=55,
            discrepancy_magnitude=10.0,
            formula="TH + IA",
            component_values={"TH": 40, "IA": 15},
            status="CONFLICT"
        )
        # AI hallucinates that TOT is 99 (which still doesn't equal 40 + 15 = 55)
        bad_patch = {
            "resolved": True,
            "corrected_field": "TOT",
            "corrected_value": "99",
            "verbatim_evidence": "40 15 65"
        }
        raw_snippet = "1001 Alice 40 15 65"
        ok, msg, _ = validate_spotlight_patch(bad_patch, report, st, self.engine, raw_snippet)
        self.assertFalse(ok)
        self.assertIn("Simulated patch failed to resolve math conflict", msg)

    def test_sandbox_accepts_valid_patch(self):
        """Test sandbox acceptance when AI returns a valid, verified patch."""
        st = StudentRecord(
            seat_no="1001",
            student_name="Alice",
            course_results=[CourseResult(course_code="CSC401", marks={"TH": "40", "IA": "15", "TOT": "65"})]
        )
        report = DiscrepancyReport(
            seat_no="1001",
            student_name="Alice",
            course_code="CSC401",
            field_name="CSC401_TOT",
            extracted_val="65",
            expected_val=55,
            discrepancy_magnitude=10.0,
            formula="TH + IA",
            component_values={"TH": 40, "IA": 15},
            status="CONFLICT"
        )
        # AI correctly identifies that TOT printed in document was 55
        good_patch = {
            "resolved": True,
            "corrected_field": "TOT",
            "corrected_value": "55",
            "verbatim_evidence": "40 15 55"
        }
        raw_snippet = "1001 Alice 40 15 55"
        ok, msg, patched_st = validate_spotlight_patch(good_patch, report, st, self.engine, raw_snippet)
        self.assertTrue(ok)
        self.assertEqual(patched_st.course_results[0].marks.get("TOT"), "55")

    def test_deterministic_fallback_when_offline(self):
        """Test that deterministic fallback resolves the conflict safely when AI client is unconfigured."""
        class DummyOfflineClient:
            def is_configured(self):
                return False

        offline_healer = SpotlightHealer(client=DummyOfflineClient(), engine=self.engine)

        st = StudentRecord(
            seat_no="1001",
            student_name="Alice",
            course_results=[CourseResult(course_code="CSC401", marks={"TH": "40", "IA": "15", "TOT": "65"})]
        )
        report = DiscrepancyReport(
            seat_no="1001",
            student_name="Alice",
            course_code="CSC401",
            field_name="CSC401_TOT",
            extracted_val="65",
            expected_val=55,
            discrepancy_magnitude=10.0,
            formula="TH + IA",
            component_values={"TH": 40, "IA": 15},
            status="CONFLICT"
        )
        patched_st, success, msg = offline_healer.heal_discrepancy(report, st, "1001 Alice 40 15 55")
        self.assertTrue(success)
        self.assertEqual(patched_st.course_results[0].marks.get("TOT"), "55")
        self.assertIn("Deterministic formula fallback applied", msg)


if __name__ == "__main__":
    unittest.main()
