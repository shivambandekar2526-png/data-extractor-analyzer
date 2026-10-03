"""
Unit Tests for DocumentExecutionSpec & DynamicRelationEngine
=============================================================
"""

import unittest
from core.document_spec import (
    AlgebraicRelation, CourseSlot, DocumentExecutionSpec, GradingContract, get_default_relations
)
from core.dynamic_relation_engine import DynamicRelationEngine
from models import Course, CourseCatalog, CourseResult, StudentRecord


class TestDynamicRelationEngine(unittest.TestCase):

    def setUp(self):
        self.spec = DocumentExecutionSpec(
            exam_title="Test B.E. Examination",
            semester="IV",
            slots=[
                CourseSlot(slot=1, code="CSC401", name="Maths IV", credits=3.0, components=["TH", "IA", "TOT"]),
                CourseSlot(slot=2, code="CSL401", name="Network Lab", credits=1.0, components=["TW", "PR", "TOT"])
            ],
            algebraic_relations=get_default_relations(),
            grading_contract=GradingContract(
                scale={"O": 10.0, "A": 9.0, "B": 8.0, "C": 7.0, "D": 6.0, "P": 4.0, "F": 0.0},
                valid_grades=["O", "A", "B", "C", "D", "P", "F"]
            )
        )
        self.engine = DynamicRelationEngine(self.spec)

    def test_auto_fill_missing_tot(self):
        """Test auto-fill of missing TOT when TH and IA exist."""
        st = StudentRecord(
            seat_no="1001",
            student_name="Alice Smith",
            course_results=[
                CourseResult(
                    course_code="CSC401",
                    marks={"TH": "52", "IA": "18"},  # TOT is missing!
                    grade="A",
                    grade_point="9",
                    credits="3"
                )
            ]
        )
        healed, discs = self.engine.audit_and_heal_student(st)
        self.assertEqual(healed.course_results[0].marks.get("TOT"), "70")
        resolved_discs = [d for d in discs if d.status == "MISSING_AUTO_RESOLVED" and "TOT" in d.field_name]
        self.assertEqual(len(resolved_discs), 1)
        self.assertEqual(resolved_discs[0].expected_val, "70")

    def test_auto_fill_missing_cg_and_sgpi(self):
        """Test auto-calculation of CG and aggregate SGPI."""
        st = StudentRecord(
            seat_no="1002",
            student_name="Bob Jones",
            course_results=[
                CourseResult(
                    course_code="CSC401",
                    marks={"TH": "50", "IA": "20", "TOT": "70"},
                    grade="A",
                    grade_point="9",
                    credits="3"  # Expected CG = 3 * 9 = 27
                ),
                CourseResult(
                    course_code="CSL401",
                    marks={"TW": "22", "PR": "23", "TOT": "45"},
                    grade="O",
                    grade_point="10",
                    credits="1"  # Expected CG = 1 * 10 = 10
                )
            ]
        )
        healed, discs = self.engine.audit_and_heal_student(st)
        self.assertEqual(healed.course_results[0].credit_points, "27")
        self.assertEqual(healed.course_results[1].credit_points, "10")
        # Total Marks = 70 + 45 = 115
        self.assertEqual(healed.total_marks, "115")
        # Total CG = 27 + 10 = 37
        self.assertEqual(healed.total_cg, "37")
        # Total Credits = 4
        self.assertEqual(healed.credits_earned, "4")
        # SGPI = 37 / 4 = 9.25
        self.assertEqual(healed.sgpi, "9.25")

    def test_detect_discrepancy_conflict(self):
        """Test detection of mathematical conflict when extracted mark disagrees with formula."""
        st = StudentRecord(
            seat_no="1003",
            student_name="Charlie Brown",
            course_results=[
                CourseResult(
                    course_code="CSC401",
                    marks={"TH": "45", "IA": "12", "TOT": "67"},  # 45 + 12 = 57 != 67!
                    grade="D",
                    grade_point="6",
                    credits="3",
                    credit_points="18"
                )
            ]
        )
        _, discs = self.engine.audit_and_heal_student(st)
        conflicts = [d for d in discs if d.status == "CONFLICT"]
        self.assertTrue(any("TOT" in c.field_name for c in conflicts))
        conflict = next(c for c in conflicts if "TOT" in c.field_name)
        self.assertEqual(conflict.extracted_val, "67")
        self.assertEqual(conflict.expected_val, 57)
        self.assertEqual(conflict.discrepancy_magnitude, 10.0)

    def test_column_shift_algebraic_permutation_healing(self):
        """Test algebraic permutation healing when an omitted elective causes off-by-one shift."""
        multi_spec = DocumentExecutionSpec(
            exam_title="Sem VI Exam",
            semester="VI",
            slots=[
                CourseSlot(slot=1, code="CSC601", name="Theory 1", credits=3.0, course_type="Theory", components=["TH", "IA", "TOT"], max_marks={"TOT": 100}),
                CourseSlot(slot=2, code="CSDLO601", name="Elective", credits=3.0, course_type="Elective", components=["TH", "IA", "TOT"], max_marks={"TOT": 100}),
                CourseSlot(slot=3, code="CSL601", name="Lab 1", credits=1.0, course_type="Lab", components=["TW", "PR", "TOT"], max_marks={"TOT": 50}),
                CourseSlot(slot=4, code="CSL602", name="Lab 2", credits=1.0, course_type="Lab", components=["TW", "TOT"], max_marks={"TOT": 25})
            ],
            algebraic_relations=get_default_relations(),
            grading_contract=GradingContract(
                scale={"O": 10.0, "A": 9.0, "B": 8.0, "C": 7.0, "P": 4.0, "F": 0.0},
                valid_grades=["O", "A", "B", "C", "P", "F"]
            )
        )
        engine = DynamicRelationEngine(multi_spec)

        # Student was absent in Elective (Slot 2). Extractor shifted Lab 1 into Slot 2 and Lab 2 into Slot 3!
        # Printed Total CG is 3*9 (CSC601) + 1*10 (CSL601) + 1*8 (CSL602) = 27 + 10 + 8 = 45.
        st = StudentRecord(
            seat_no="1004",
            student_name="Diana Prince",
            total_cg="45",
            course_results=[
                CourseResult(course_code="CSC601", marks={"TH": "55", "IA": "17", "TOT": "72"}, grade="A", grade_point="9", credits="3", credit_points="27"),
                CourseResult(course_code="CSDLO601", marks={"TW": "22", "PR": "23", "TOT": "45"}, grade="O", grade_point="10", credits="3", credit_points="30"),
                CourseResult(course_code="CSL601", marks={"TW": "20", "TOT": "20"}, grade="B", grade_point="8", credits="1", credit_points="8")
            ]
        )

        healed, discs = engine.audit_and_heal_student(st)
        # Should be healed to 4 slots!
        self.assertEqual(len(healed.course_results), 4)
        # Slot 1 is CSC601
        self.assertEqual(healed.course_results[0].course_code, "CSC601")
        # Slot 2 is CSDLO601 and marked ABS
        self.assertEqual(healed.course_results[1].course_code, "CSDLO601")
        self.assertEqual(healed.course_results[1].grade, "ABS")
        # Slot 3 is CSL601 with 1 credit and 10 CG
        self.assertEqual(healed.course_results[2].course_code, "CSL601")
        self.assertEqual(healed.course_results[2].credits, "1")
        self.assertEqual(healed.course_results[2].credit_points, "10")
        # Slot 4 is CSL602 with 1 credit and 8 CG
        self.assertEqual(healed.course_results[3].course_code, "CSL602")
        self.assertEqual(healed.course_results[3].credits, "1")
        self.assertEqual(healed.course_results[3].credit_points, "8")
        # Sum of CG = 27 + 0 + 10 + 8 = 45!
        self.assertEqual(healed.total_cg, "45")

    def test_column_shift_profile_violation_detection(self):
        """Test detection and flagging of structural profile violations."""
        spec = DocumentExecutionSpec(
            exam_title="Test Exam",
            semester="VI",
            slots=[
                CourseSlot(slot=1, code="CSL601", name="Lab 1", credits=1.0, course_type="Lab", components=["TW", "PR", "TOT"], max_marks={"TOT": 50})
            ]
        )
        engine = DynamicRelationEngine(spec)
        # Student has TOT=78 in Lab slot with max 50
        st = StudentRecord(
            seat_no="1005",
            student_name="Evan Wright",
            course_results=[
                CourseResult(course_code="CSL601", marks={"TH": "60", "IA": "18", "TOT": "78"}, grade="O", grade_point="10", credits="1", credit_points="10")
            ]
        )
        _, discs = engine.audit_and_heal_student(st)
        violations = [d for d in discs if "Profile_Violation" in d.field_name]
        self.assertEqual(len(violations), 1)
        self.assertIn("Lab slot CSL601 received Theory mark", violations[0].reason)


if __name__ == "__main__":
    unittest.main()

