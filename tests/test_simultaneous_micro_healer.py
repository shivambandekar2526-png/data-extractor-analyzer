"""
Unit Tests for Exhaustive Recall, Complete Field Filling & Simultaneous Micro-Healing
=====================================================================================
Verifies:
1. detect_candidate_seat_numbers matches 6, 7, 8, 9, 10-digit seat numbers while filtering PRNs/ABC IDs.
2. Missing candidates omitted by LLM JSON are autonomously recovered from vector text (100% recall).
3. Simultaneous micro-healer runs in-flight during page extraction (saving secondary pass tokens).
4. Every value (components, aggregates, attributes) is properly filled with zero blank cells.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.cloud_client import CloudClient
from core.document_spec import CourseSlot, DocumentExecutionSpec, get_default_relations
from core.dynamic_relation_engine import DynamicRelationEngine
from core.visual_extractor import (
    align_and_heal_student,
    detect_candidate_seat_numbers,
    extract_sliced_page,
    run_visual_blueprint_pipeline,
)
from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord


class TestSimultaneousMicroHealerAndRecall(unittest.TestCase):

    def test_detect_candidate_seat_numbers_multi_format(self):
        """Test exhaustive recall for 6, 7, 8, 9, and 10 digit seat numbers excluding PRNs & ABC IDs."""
        sample_ledger_text = (
            "UNIVERSITY EXAMINATION LEDGER - MAY 2024\n"
            "DATE: 2024-05-15  PAGE: 1  TOTAL CANDIDATES: 5\n"
            "\n"
            "582150  KULKARNI SOHAM SANJAY (2021016402156235) (137464702402)\n"
            "   55 18 73 3 A 8 24\n"
            "\n"
            "7000457  /DONDE SAKSHI DINESH (2022016402240447)\n"
            "   60 19 79 3 A 9 27\n"
            "\n"
            "52910010  DESHMUKH ARYAN ANIL (2020016401987654)\n"
            "   45 15 60 3 B 7 21\n"
            "\n"
            "101410022  /SHINDE AARADHYA SANTOSH (2021016402159876) (457807038422)\n"
            "   58 17 75 3 A 8 24\n"
            "\n"
            "2021012345  PATIL TANMAY PRASAD (2021016402151122)\n"
            "   50 16 66 3 C 7 21\n"
        )

        seats = detect_candidate_seat_numbers(sample_ledger_text)
        self.assertIn("582150", seats)       # 6 digits
        self.assertIn("7000457", seats)      # 7 digits
        self.assertIn("52910010", seats)     # 8 digits
        self.assertIn("101410022", seats)    # 9 digits
        self.assertIn("2021012345", seats)   # 10 digits

        # Ensure 16-digit PRNs and 12-digit ABC IDs are NOT included in seat numbers
        self.assertNotIn("2021016402156235", seats)
        self.assertNotIn("137464702402", seats)
        self.assertNotIn("2022016402240447", seats)
        self.assertEqual(len(seats), 5)

    def test_missing_candidate_recovery_prevents_dropped_students(self):
        """Test that if the LLM JSON response omits a candidate, vector text recovery captures it (100% recall)."""
        slots_def = [
            {"slot": 1, "code": "CSC401", "name": "Applied Math", "heads": ["TH", "IA", "TOT"], "credits": 3},
            {"slot": 2, "code": "CSC402", "name": "Data Structures", "heads": ["TH", "IA", "TOT"], "credits": 3}
        ]
        catalog = CourseCatalog(courses=[Course(code=s["code"], name=s["name"], heads=s["heads"], credits=s["credits"]) for s in slots_def])
        blueprint = {
            "exam_title": "TEST EXAMINATION",
            "semester": "IV",
            "courses": slots_def,
            "slots": slots_def,
            "column_contract": ["Seat_No", "Student_Name", "CSC401_TOT", "CSC402_TOT", "Total_Marks", "SGPI"]
        }

        # Page text has 2 candidates: 101410022 and 101410023
        page_text = (
            "101410022 /SHINDE AARADHYA SANTOSH (2021016402159876) (457807038422)\n"
            "   58  17  75  3 A 8 24\n"
            "   50  16  66  3 C 7 21\n"
            "   P  141  6.00  45  7.50\n"
            "\n"
            "101410023 JADHAV ADITYA RAJESH (2021016402159877) (457807038423)\n"
            "   60  18  78  3 A 9 27\n"
            "   55  17  72  3 B 8 24\n"
            "   P  150  6.00  51  8.50\n"
        )

        # Vision model JSON returns ONLY candidate 101410022 (omits 101410023)
        mock_response = json.dumps({
            "students": [
                {
                    "seat_no": "101410022",
                    "student_name": "/SHINDE AARADHYA SANTOSH",
                    "gender": "F",
                    "candidate_type": "Regular",
                    "slots": {
                        "1": {"th": "58", "ia": "17", "tot": "75", "grade": "A", "gp": "8", "credits": "3", "cg": "24"},
                        "2": {"th": "50", "ia": "16", "tot": "66", "grade": "C", "gp": "7", "credits": "3", "cg": "21"}
                    }
                }
            ]
        })

        client = MagicMock()
        client.is_configured.return_value = True
        client.generate_direct.return_value = mock_response

        with patch("core.visual_extractor.render_page_as_jpeg", return_value=b"\xff\xd8fake"), \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = page_text
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

            # Both candidates MUST be present (Zero dropped students)
            self.assertEqual(len(students), 2)
            seat_set = {s.seat_no for s in students}
            self.assertIn("101410022", seat_set)
            self.assertIn("101410023", seat_set)

            # Check candidate 101410023 recovered from text has complete metadata
            s2 = next(s for s in students if s.seat_no == "101410023")
            self.assertEqual(s2.student_name, "JADHAV ADITYA RAJESH")
            self.assertEqual(s2.prn, "2021016402159877")
            self.assertEqual(s2.abc_id, "457807038423")
            self.assertEqual(s2.gender, "M")
            self.assertEqual(len(s2.course_results), 2)

    def test_simultaneous_micro_healing_fills_all_values_in_flight(self):
        """Test that in-flight micro-healing automatically resolves missing components and aggregates in real time."""
        slots_def = [
            {"slot": 1, "code": "CSC501", "name": "TCS", "heads": ["TH", "IA", "TOT"], "credits": 3},
            {"slot": 2, "code": "CSC502", "name": "SE", "heads": ["TH", "IA", "TOT"], "credits": 3}
        ]
        catalog = CourseCatalog(courses=[Course(code=s["code"], name=s["name"], heads=s["heads"], credits=s["credits"]) for s in slots_def])
        blueprint = {
            "exam_title": "B.E. SEM V",
            "semester": "V",
            "courses": slots_def,
            "slots": slots_def
        }

        # Model extracts student with missing TOT in course 1, missing TH in course 2, and missing aggregate totals
        mock_response = json.dumps({
            "students": [
                {
                    "seat_no": "5001234",
                    "student_name": "/KALE ANUSHKA RAJENDRA",
                    "slots": {
                        # Course 1 has TH=52, IA=18, but TOT is missing
                        "1": {"th": "52", "ia": "18", "tot": "", "grade": "A", "gp": "8", "credits": "3", "cg": "24"},
                        # Course 2 has TOT=70, IA=18, but TH is missing (should deduce 70 - 18 = 52)
                        "2": {"th": "", "ia": "18", "tot": "70", "grade": "A", "gp": "8", "credits": "3", "cg": "24"}
                    }
                    # Notice: total_marks, total_cg, credits_earned, sgpi are completely blank!
                }
            ]
        })

        client = MagicMock()
        client.is_configured.return_value = True
        client.generate_direct.return_value = mock_response

        audit_events = []
        def mock_audit_cb(evt):
            audit_events.append(evt)

        with patch("core.visual_extractor.render_page_as_jpeg", return_value=b"\xff\xd8fake"), \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = "5001234 /KALE ANUSHKA RAJENDRA"
            mock_doc.__getitem__.return_value = mock_page
            mock_fitz.return_value = mock_doc

            students = extract_sliced_page(
                pdf_path="dummy.pdf",
                page_index=0,
                blueprint=blueprint,
                client=client,
                audit_cb=mock_audit_cb,
                catalog=catalog,
                slot_registry=slots_def
            )

            self.assertEqual(len(students), 1)
            st = students[0]

            # 1. Course 1: TOT auto-healed to 70 (52 + 18)
            c1 = st.course_results[0]
            self.assertEqual(c1.marks.get("TOT"), "70")

            # 2. Course 2: TH auto-derived to 52 (70 - 18)
            c2 = st.course_results[1]
            self.assertEqual(c2.marks.get("TH"), "52")

            # 3. Student metadata and attributes completed
            self.assertEqual(st.gender, "F")
            self.assertEqual(st.candidate_type, "Regular")
            self.assertEqual(st.result_status, "PASS")

            # 4. Aggregates completely populated with ZERO blank cells
            # Total Marks = 70 + 70 = 140
            # Total CG = 24 + 24 = 48
            # Credits Earned = 3 + 3 = 6
            # SGPI = 48 / 6 = 8.00
            self.assertEqual(st.total_marks, "140")
            self.assertEqual(st.total_cg, "48")
            self.assertEqual(st.credits_earned, "6")
            self.assertEqual(st.sgpi, "8.00")

            # 5. In-flight audit telemetry was emitted simultaneously
            healed_events = [e for e in audit_events if e.get("type") == "inflight_audit_healed"]
            self.assertGreaterEqual(len(healed_events), 1)

    def test_live_callback_receives_fully_healed_records(self):
        """Test that live_cb receives records that are ALREADY healed in real time."""
        slots_def = [
            {"slot": 1, "code": "CS101", "name": "Programming", "heads": ["TH", "IA", "TOT"], "credits": 4}
        ]
        catalog = CourseCatalog(courses=[Course(code="CS101", name="Programming", heads=["TH", "IA", "TOT"], credits=4.0)])
        blueprint = {
            "exam_title": "TEST",
            "semester": "I",
            "courses": slots_def,
            "slots": slots_def,
            "page_1_students": [
                {
                    "seat_no": "1001",
                    "student_name": "BOB",
                    "slots": {"1": {"th": "60", "ia": "20", "tot": "", "grade": "O", "gp": "10", "credits": "4", "cg": "40"}}
                }
            ]
        }

        streamed_students = []
        def live_cb(st, cat):
            streamed_students.append(st)

        client = MagicMock()
        client.is_configured.return_value = True

        with patch("core.visual_extractor.extract_visual_blueprint") as mock_bp, \
             patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__.return_value = 1
            mock_page = MagicMock()
            mock_page.get_text.return_value = "1001 BOB"
            mock_doc.__getitem__.return_value = mock_page
            mock_fitz.return_value = mock_doc

            meta = ExamMeta(exam_title="TEST", semester="I", catalog=catalog)
            spec = DocumentExecutionSpec(exam_title="TEST", semester="I", slots=[CourseSlot(slot=1, code="CS101", credits=4.0)], algebraic_relations=get_default_relations())
            raw_p1_student = StudentRecord(
                seat_no="1001",
                student_name="BOB",
                course_results=[CourseResult(course_code="CS101", marks={"TH": "60", "IA": "20"}, grade="O", grade_point="10", credits="4", credit_points="40")]
            )
            mock_bp.return_value = {
                "meta": meta,
                "blueprint": blueprint,
                "spec": spec,
                "slot_registry": slots_def,
                "valid_grades": ["O", "A", "B", "C", "D", "P", "F"],
                "detected_p1_seats": ["1001"],
                "page_1_students": [raw_p1_student]
            }

            run_visual_blueprint_pipeline(
                pdf_path="dummy.pdf",
                client=client,
                live_cb=live_cb
            )

            self.assertEqual(len(streamed_students), 1)
            streamed = streamed_students[0]
            # Streamed student MUST be fully healed with zero blanks
            self.assertEqual(streamed.course_results[0].marks.get("TOT"), "80")
            self.assertEqual(streamed.total_marks, "80")
            self.assertEqual(streamed.total_cg, "40")
            self.assertEqual(streamed.credits_earned, "4")
            self.assertEqual(streamed.sgpi, "10.00")
            self.assertEqual(streamed.gender, "M")
            self.assertEqual(streamed.result_status, "PASS")


if __name__ == "__main__":
    unittest.main()
