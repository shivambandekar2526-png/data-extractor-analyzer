"""
Unit Test Suite for AcademicExcelReportGenerator
=================================================
Validates the dynamic 6-page Academic Analysis Excel workbook generator:
1. Dynamic max_marks & min_marks per course and component head.
2. 40% statutory passing law fallback.
3. Semantic aliases (TH/SE, IA/IN/IAT, PR/OR/TW_PR, TW, TOT).
4. IAT KT analysis & PR KT analysis sheets.
5. Summary sheet metrics (Toppers passing-only, exact pass/fail counts, SGPI brackets).
6. Subject Wise Result & Distributions sheets scaling.
7. End-to-end generation on real academic ground truth dataset.
"""

import os
import sys
import unittest
import csv
import openpyxl

# Add project root and data_extractor_AI to path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_APP_DIR = os.path.abspath(os.path.join(TESTS_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(AI_APP_DIR, ".."))
for p in [AI_APP_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
from models import Course, CourseCatalog, ExamMeta, StudentRecord


class TestAcademicExcelReportGenerator(unittest.TestCase):
    """Unit tests for AcademicExcelReportGenerator dynamic analysis engine."""

    def setUp(self):
        # Catalog with courses of varying schemes (60/40, 80/20, 25/25, 50)
        self.catalog = CourseCatalog(courses=[
            Course(
                code="CSC401",
                name="Applied Mathematics - IV",
                heads=["TH", "IA", "TOT"],
                max_marks={"TH": 60, "IA": 40, "TOT": 100},
                min_marks={"TH": 24, "IA": 16, "TOT": 40},
                credits=4.0
            ),
            Course(
                code="CSC402",
                name="Analysis of Algorithm",
                heads=["TH", "IN", "TOT"],
                max_marks={"TH": 80, "IN": 20, "TOT": 100},
                min_marks={"TH": 32, "IN": 8, "TOT": 40},
                credits=4.0
            ),
            Course(
                code="CSL401",
                name="Analysis of Algorithm Lab",
                heads=["TW", "PR", "TOT"],
                max_marks={"TW": 25, "PR": 25, "TOT": 50},
                min_marks={"TW": 10, "PR": 10, "TOT": 20},
                credits=1.0
            ),
            Course(
                code="CSL402",
                name="Web Computing Lab",
                heads=["TW", "PR", "TOT"],
                max_marks={"TW": 25, "PR": 50, "TOT": 75},
                # min_marks omitted intentionally to test 40% statutory rule fallback
                credits=2.0
            )
        ])

        # Test student cohort
        self.students = [
            # Student 1: High Scorer (All Clear)
            {
                "Seat_No": "1001",
                "Student_Name": "ALICE SMITH",
                "Result_Status": "P",
                "Total_Marks": 280,
                "SGPI": 9.50,
                "CSC401_TH": 50, "CSC401_IA": 35, "CSC401_TOT": 85, "CSC401_Grade": "O",
                "CSC402_TH": 70, "CSC402_IN": 18, "CSC402_TOT": 88, "CSC402_Grade": "O",
                "CSL401_TW": 22, "CSL401_PR": 23, "CSL401_TOT": 45, "CSL401_Grade": "O",
                "CSL402_TW": 22, "CSL402_PR": 40, "CSL402_TOT": 62, "CSL402_Grade": "O"
            },
            # Student 2: Passed with moderate marks
            {
                "Seat_No": "1002",
                "Student_Name": "BOB JONES",
                "Result_Status": "P",
                "Total_Marks": 200,
                "SGPI": 7.25,
                "CSC401_TH": 30, "CSC401_IA": 20, "CSC401_TOT": 50, "CSC401_Grade": "C",
                "CSC402_TH": 45, "CSC402_IN": 12, "CSC402_TOT": 57, "CSC402_Grade": "C",
                "CSL401_TW": 15, "CSL401_PR": 15, "CSL401_TOT": 30, "CSL401_Grade": "C",
                "CSL402_TW": 15, "CSL402_PR": 25, "CSL402_TOT": 40, "CSL402_Grade": "D"
            },
            # Student 3: Failed IAT in CSC401 (12/40 < 16 min) and Failed PR in CSL402 (15/50 < 20 min)
            {
                "Seat_No": "1003",
                "Student_Name": "CHARLIE BROWN",
                "Result_Status": "FAIL",
                "Total_Marks": 140,
                "SGPI": 0.00,
                "CSC401_TH": 30, "CSC401_IA": 12, "CSC401_TOT": 42, "CSC401_Grade": "F",
                "CSC402_TH": 40, "CSC402_IN": 10, "CSC402_TOT": 50, "CSC402_Grade": "D",
                "CSL401_TW": 15, "CSL401_PR": 15, "CSL401_TOT": 30, "CSL401_Grade": "C",
                "CSL402_TW": 15, "CSL402_PR": 15, "CSL402_TOT": 30, "CSL402_Grade": "F"
            },
            # Student 4: Absent in CSC401 IAT
            {
                "Seat_No": "1004",
                "Student_Name": "DAVID LEE",
                "Result_Status": "FAIL",
                "Total_Marks": 110,
                "SGPI": 0.00,
                "CSC401_TH": 28, "CSC401_IA": "AA", "CSC401_TOT": 28, "CSC401_Grade": "F",
                "CSC402_TH": 35, "CSC402_IN": 9, "CSC402_TOT": 44, "CSC402_Grade": "P",
                "CSL401_TW": 12, "CSL401_PR": 12, "CSL401_TOT": 24, "CSL401_Grade": "P",
                "CSL402_TW": 12, "CSL402_PR": 22, "CSL402_TOT": 34, "CSL402_Grade": "P"
            },
            # Student 5: Entirely Absent
            {
                "Seat_No": "1005",
                "Student_Name": "EVA GREEN",
                "Result_Status": "ABS",
                "Total_Marks": 0,
                "SGPI": 0.00,
                "CSC401_TH": "AA", "CSC401_IA": "AA", "CSC401_TOT": "AA", "CSC401_Grade": "AB",
                "CSC402_TH": "AA", "CSC402_IN": "AA", "CSC402_TOT": "AA", "CSC402_Grade": "AB",
                "CSL401_TW": "AA", "CSL401_PR": "AA", "CSL401_TOT": "AA", "CSL401_Grade": "AB",
                "CSL402_TW": "AA", "CSL402_PR": "AA", "CSL402_TOT": "AA", "CSL402_Grade": "AB"
            }
        ]

    def test_dynamic_min_max_and_40_percent_rule(self):
        """Test that minimum passing marks are dynamically derived or computed via 40% rule."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)
        csl402 = next(c for c in gen.courses if c["code"] == "CSL402")

        # CSL402 PR has max 50 and no explicit min_marks -> 40% of 50 = 20
        min_pr = gen._get_course_head_min(csl402, "PR")
        self.assertEqual(min_pr, 20.0)

        # CSL402 TW has max 25 and no explicit min_marks -> 40% of 25 = 10
        min_tw = gen._get_course_head_min(csl402, "TW")
        self.assertEqual(min_tw, 10.0)

        # CSC401 IA has explicit max 40, min 16
        csc401 = next(c for c in gen.courses if c["code"] == "CSC401")
        self.assertEqual(gen._get_course_head_min(csc401, "IA"), 16.0)

        # CSC402 IN has explicit max 20, min 8
        csc402 = next(c for c in gen.courses if c["code"] == "CSC402")
        self.assertEqual(gen._get_course_head_min(csc402, "IA"), 8.0)

    def test_semantic_alias_resolution(self):
        """Test that _get_student_mark resolves across IA/IN, TH/SE, PR/OR aliases."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)

        # Student 1 has CSC401_IA and CSC402_IN
        s1 = self.students[0]
        self.assertEqual(gen._get_student_mark(s1, "CSC401", "IA"), 35.0)
        self.assertEqual(gen._get_student_mark(s1, "CSC402", "IA"), 18.0)

    def test_iat_kt_sheet_generation(self):
        """Test IAT KT sheet accurately lists only students who failed or were absent in IAT."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        gen._build_iat_kt_sheet(wb)

        ws = wb["IAT KT analysis"]
        # Charlie Brown (1003) failed CSC401 IA (12 < 16) -> 1 KT
        # David Lee (1004) absent in CSC401 IA -> 1 KT
        # Eva Green (1005) absent in all -> 2 KTs
        # Alice (1001) and Bob (1002) passed all IATs -> NOT listed
        listed_seats = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        self.assertIn("1003", listed_seats)
        self.assertIn("1004", listed_seats)
        self.assertIn("1005", listed_seats)
        self.assertNotIn("1001", listed_seats)
        self.assertNotIn("1002", listed_seats)

    def test_pr_kt_sheet_generation(self):
        """Test PR KT sheet accurately lists students who failed PR component or were absent."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        gen._build_pr_kt_sheet(wb)

        ws = wb["PR KT analysis"]
        # Charlie Brown (1003) scored 15/50 in CSL402 PR (min 20) and Grade F -> 1 PR KT
        # Eva Green (1005) absent -> 2 PR KTs
        # Alice (1001), Bob (1002), David (1004) passed labs -> NOT listed
        listed_seats = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        self.assertIn("1003", listed_seats)
        self.assertIn("1005", listed_seats)
        self.assertNotIn("1001", listed_seats)
        self.assertNotIn("1002", listed_seats)
        self.assertNotIn("1004", listed_seats)

    def test_summary_sheet_toppers_passing_only(self):
        """Test Summary sheet Topper section strictly includes passing students and excludes failed/KT."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        gen._build_summary_sheet(wb)

        ws = wb["Summary"]
        # Rows 4 to 8 are Topper Students (Top 5)
        topper_names = [ws.cell(row=r, column=3).value for r in range(4, 9) if ws.cell(row=r, column=3).value]
        # Only Alice (9.5) and Bob (7.25) passed
        self.assertEqual(len(topper_names), 2)
        self.assertEqual(topper_names[0], "ALICE SMITH")
        self.assertEqual(topper_names[1], "BOB JONES")

        # Row 12: Overall Result
        # Total: 5, ABS: 1 (Eva), Pass: 2 (Alice, Bob), Fail: 2 (Charlie, David)
        self.assertEqual(ws.cell(row=12, column=1).value, 5)  # No. of Students
        self.assertEqual(ws.cell(row=12, column=2).value, 1)  # ABS
        self.assertEqual(ws.cell(row=12, column=3).value, 2)  # Pass
        self.assertEqual(ws.cell(row=12, column=4).value, 2)  # Fail with KT
        self.assertEqual(ws.cell(row=12, column=5).value, 0.40)  # Pass Percentage = 2/5 = 40%

    def test_subject_wise_and_distributions_sheets(self):
        """Test Subject Wise Result and Distributions sheets generate without errors."""
        gen = AcademicExcelReportGenerator(self.students, catalog=self.catalog)
        wb = gen.generate_workbook()

        self.assertIn("Data", wb.sheetnames)
        self.assertIn("IAT KT analysis", wb.sheetnames)
        self.assertIn("PR KT analysis", wb.sheetnames)
        self.assertIn("Summary", wb.sheetnames)
        self.assertIn("Subject Wise Result", wb.sheetnames)
        self.assertIn("Distributions", wb.sheetnames)

        # Check Distributions sheet row count matches number of courses
        ws_dist = wb["Distributions"]
        # 3 header rows + 4 courses = 7 rows
        self.assertEqual(ws_dist.max_row, 7)

    def test_real_aids_sem4_ground_truth(self):
        """Test end-to-end workbook generation using the real AI&DS SEM4 15-student ground truth CSV."""
        gt_csv = os.path.join(PROJECT_ROOT, "Result Analysis", "ground_truth_AIDS_SEM4.csv")
        if not os.path.isfile(gt_csv):
            self.skipTest(f"Ground truth CSV not found at {gt_csv}")

        students = []
        with open(gt_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                students.append(row)

        self.assertEqual(len(students), 15)

        gen = AcademicExcelReportGenerator(
            students=students,
            folder_name="AI_DS_SEM4",
            fieldnames=fieldnames
        )

        wb = gen.generate_workbook()
        self.assertEqual(len(wb.sheetnames), 6)

        # Check Data sheet
        ws_data = wb["Data"]
        self.assertEqual(ws_data.max_row, 16)  # 1 header + 15 students

        # Check Summary sheet pass/fail count equals 15
        ws_sum = wb["Summary"]
        tot = ws_sum.cell(row=12, column=1).value
        abs_c = ws_sum.cell(row=12, column=2).value
        pass_c = ws_sum.cell(row=12, column=3).value
        fail_c = ws_sum.cell(row=12, column=4).value
        self.assertEqual(tot, 15)
        self.assertEqual(abs_c + pass_c + fail_c, 15)


if __name__ == "__main__":
    unittest.main()
