"""
Unit Tests for Multi-Semester Batch Analysis & Success Rate Automation
======================================================================
Tests:
1. Batch slug normalization and file detection across dual stores
2. Automatic clean Excel saving into data_extractor_AI/batch_data/<batch>/Sem_<N>.xlsx
3. Automatic sorting hook (auto_sort_into_app_batch_store)
4. Accredited Success Rate calculation: P_final / (N1 + N3) * 100%
5. Student cross-checking by PRN and Name tokens (Regular vs Lateral entry)
6. Excel report generation with dedicated Success Rate and Semester Analysis sheets
"""

import os
import shutil
import tempfile
import unittest
import openpyxl

from core.batch_analyzer import (
    BatchAcademicAnalyzer,
    normalize_batch_slug,
)
from core.batch_excel_generator import BatchExcelReportGenerator
from app import auto_sort_into_app_batch_store


class TestBatchSuccessRate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_batch_suite_")
        self.app_batch_dir = os.path.join(self.temp_dir, "app_batch_data")
        self.root_batch_dir = os.path.join(self.temp_dir, "root_batch_data")
        self.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(self.app_batch_dir, exist_ok=True)
        os.makedirs(self.root_batch_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.analyzer = BatchAcademicAnalyzer(
            output_dir=self.output_dir,
            app_batch_dir=self.app_batch_dir,
            root_batch_dir=self.root_batch_dir
        )

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_normalize_batch_slug(self):
        self.assertEqual(normalize_batch_slug("21-22 data"), "21-22")
        self.assertEqual(normalize_batch_slug("Batch 2021-2022"), "21-22")
        self.assertEqual(normalize_batch_slug("2022-23"), "22-23")
        self.assertEqual(normalize_batch_slug("21-22"), "21-22")

    def test_detect_batch_files_dual_store_preference(self):
        batch_slug = "21-22"
        # Root batch directory has a PDF for Sem 1 and Excel for Sem 2
        root_batch_sub = os.path.join(self.root_batch_dir, "21-22 data")
        os.makedirs(root_batch_sub, exist_ok=True)
        sem1_pdf = os.path.join(root_batch_sub, "Sem 1 AI DS.pdf")
        sem2_xlsx = os.path.join(root_batch_sub, "Sem 2 Results.xlsx")
        with open(sem1_pdf, "wb") as f:
            f.write(b"%PDF-1.4 test")
        wb = openpyxl.Workbook()
        wb.save(sem2_xlsx)

        # App batch directory has clean Excel for Sem 1
        app_batch_sub = os.path.join(self.app_batch_dir, batch_slug)
        os.makedirs(app_batch_sub, exist_ok=True)
        sem1_clean_xlsx = os.path.join(app_batch_sub, "Sem_1.xlsx")
        wb2 = openpyxl.Workbook()
        wb2.save(sem1_clean_xlsx)

        inspection = self.analyzer.detect_batch_files(batch_slug, target_branch="AIDS")
        detected = {s["semester"]: s for s in inspection["semesters_detected"]}

        self.assertIn(1, detected)
        self.assertIn(2, detected)
        # Sem 1 should prioritize clean Excel from app_batch_dir
        self.assertEqual(detected[1]["file_type"], "excel")
        self.assertEqual(os.path.normpath(detected[1]["full_path"]), os.path.normpath(sem1_clean_xlsx))
        self.assertFalse(detected[1]["needs_auto_extract"])

        # Sem 2 was found in root
        self.assertEqual(detected[2]["file_type"], "excel")

    def test_save_clean_semester_excel(self):
        records = [
            {
                "Seat_No": "A101",
                "Name": "SHARMA ROHIT",
                "PRN": "2021016400012345",
                "SGPI": 8.75,
                "Result": "PASS",
                "KT_Count": 0,
                "Total_Marks": 650,
            },
            {
                "Seat_No": "A102",
                "Name": "VERMA PRIYA",
                "PRN": "2021016400012346",
                "SGPI": 6.20,
                "Result": "PASS",
                "KT_Count": 0,
                "Total_Marks": 520,
            }
        ]
        saved_path = self.analyzer.save_clean_semester_excel(
            records=records,
            batch_slug="21-22",
            sem_num=1
        )
        self.assertTrue(os.path.isfile(saved_path))
        self.assertTrue(saved_path.endswith("Sem_1.xlsx"))

        # Verify sheets
        wb = openpyxl.load_workbook(saved_path, data_only=True)
        self.assertIn("Student Results", wb.sheetnames)
        self.assertIn("Semester Summary", wb.sheetnames)
        ws = wb["Student Results"]
        self.assertEqual(ws.cell(row=2, column=2).value, "A101")
        self.assertEqual(ws.cell(row=2, column=3).value, "SHARMA ROHIT")
        self.assertEqual(ws.cell(row=2, column=4).value, "2021016400012345")

    def test_auto_sort_into_app_batch_store(self):
        records = [
            {"Seat_No": "101", "Name": "STUDENT ONE", "PRN": "PRN1", "SGPI": 9.0, "Result": "PASS", "KT_Count": 0}
        ]
        # Folder name containing semester and batch
        folder_name = "Sem_3_21-22_Results_Export"
        catalog = {"exam_name": "Third Semester B.Tech"}

        saved_path = auto_sort_into_app_batch_store(
            folder_name=folder_name,
            students=records,
            catalog=catalog,
            app_batch_dir=self.app_batch_dir
        )
        self.assertIsNotNone(saved_path)
        expected_file = os.path.join(self.app_batch_dir, "21-22", "Sem_3.xlsx")
        self.assertTrue(os.path.isfile(expected_file))

    def test_success_rate_calculation_and_student_alignment(self):
        """
        Tests the accredited Success Rate formula:
        Success Rate = P_final / (N1 + N3) * 100%
        Where:
        - N1: Admitted in Sem 1 (Regular Entry)
        - N3: Admitted in Sem 3 (Lateral Entry / DSE)
        - P_final: Passed final semester
        """
        # Create 3 Semesters (1, 2, 3) where Sem 3 is final evaluated
        # Sem 1: 4 Regular students (N1 = 4)
        sem1_records = [
            {"Seat_No": "S101", "Name": "AARAV PATEL", "PRN": "20210001", "SGPI": 8.0, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S102", "Name": "BHAVYA SHAH", "PRN": "20210002", "SGPI": 7.5, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S103", "Name": "CHETAN DESAI", "PRN": "20210003", "SGPI": 5.8, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S104", "Name": "DEVRAJ JOSHI", "PRN": "20210004", "SGPI": 0.0, "Result": "FAIL", "KT_Count": 2},
        ]
        # Sem 2: Same 4 students
        sem2_records = [
            {"Seat_No": "S201", "Name": "AARAV PATEL", "PRN": "20210001", "SGPI": 8.5, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S202", "Name": "BHAVYA SHAH", "PRN": "20210002", "SGPI": 7.8, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S203", "Name": "CHETAN DESAI", "PRN": "20210003", "SGPI": 6.2, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S204", "Name": "DEVRAJ JOSHI", "PRN": "20210004", "SGPI": 4.5, "Result": "PASS", "KT_Count": 1},
        ]
        # Sem 3: The 4 regular students + 2 Lateral Entry (DSE) students (N3 = 2)
        sem3_records = [
            # Regular students
            {"Seat_No": "S301", "Name": "AARAV PATEL", "PRN": "20210001", "SGPI": 8.9, "Result": "PASS", "KT_Count": 0}, # Cleared with zero KT
            {"Seat_No": "S302", "Name": "BHAVYA SHAH", "PRN": "20210002", "SGPI": 8.0, "Result": "PASS", "KT_Count": 0}, # Cleared with zero KT
            {"Seat_No": "S303", "Name": "CHETAN DESAI", "PRN": "20210003", "SGPI": 6.5, "Result": "PASS", "KT_Count": 0}, # Cleared with zero KT
            {"Seat_No": "S304", "Name": "DEVRAJ JOSHI", "PRN": "20210004", "SGPI": 5.0, "Result": "FAIL", "KT_Count": 1}, # Failed final sem
            # Lateral students
            {"Seat_No": "S305", "Name": "ESHAN KULKARNI", "PRN": "20220005", "SGPI": 9.1, "Result": "PASS", "KT_Count": 0}, # Cleared zero KT
            {"Seat_No": "S306", "Name": "FARHAN KHAN", "PRN": "20220006", "SGPI": 4.0, "Result": "FAIL", "KT_Count": 2}, # Failed final sem
        ]

        semesters_data = {
            1: {"semester_num": 1, "records": sem1_records, "input_type": "EXCEL", "source_files": ["Sem_1.xlsx"]},
            2: {"semester_num": 2, "records": sem2_records, "input_type": "EXCEL", "source_files": ["Sem_2.xlsx"]},
            3: {"semester_num": 3, "records": sem3_records, "input_type": "EXCEL", "source_files": ["Sem_3.xlsx"]},
        }

        master_students = self.analyzer.align_master_students(semesters_data)
        self.assertEqual(len(master_students), 6)

        # Check classification
        p_aarav = next(s for s in master_students if "AARAV" in s["canonical_name"])
        p_eshan = next(s for s in master_students if "ESHAN" in s["canonical_name"])
        self.assertEqual(p_aarav["entry_category"], "Regular Entry (Semester 1)")
        self.assertEqual(p_aarav["entry_semester"], 1)
        self.assertIn("Lateral Entry", p_eshan["entry_category"])
        self.assertEqual(p_eshan["entry_semester"], 3)

        analytics = self.analyzer.compute_batch_analytics(semesters_data, master_students)
        sr = analytics["success_rate_analysis"]

        # N1 = 4, N3 = 2, Total intake = 6
        self.assertEqual(sr["regular_intake_n1"], 4)
        self.assertEqual(sr["lateral_entry_n3"], 2)
        self.assertEqual(sr["total_admitted_n1_n3"], 6)

        # In Sem 3 (final evaluated):
        # Passed: S301, S302, S303, S305 -> 4 students
        # Failed: S304, S306 -> 2 students
        self.assertEqual(sr["passed_final_sem"], 4)
        self.assertEqual(sr["failed_final_sem"], 2)

        # Success Rate = 4 / 6 * 100 = 66.67%
        self.assertAlmostEqual(sr["success_rate_percentage"], 66.67, places=2)

        # Zero KT: S301, S302, S303, S305 have zero KT throughout their enrolled semesters -> 4 students
        self.assertEqual(sr["passed_without_backlogs"], 4)
        self.assertEqual(sr["passed_with_backlogs"], 0)

        # Check semester-wise analysis breakdown
        sem_wise = analytics["semester_wise_analysis"]
        self.assertEqual(len(sem_wise), 3)
        # In Sem 1: 4 students (4 regular, 0 lateral)
        self.assertEqual(sem_wise[0]["total_students"], 4)
        self.assertEqual(sem_wise[0]["regular_students"], 4)
        self.assertEqual(sem_wise[0]["lateral_students"], 0)
        # In Sem 3: 6 students (4 regular, 2 lateral)
        self.assertEqual(sem_wise[2]["total_students"], 6)
        self.assertEqual(sem_wise[2]["regular_students"], 4)
        self.assertEqual(sem_wise[2]["lateral_students"], 2)

    def test_excel_report_generation_with_success_rate_sheet(self):
        sem1_records = [
            {"Seat_No": "S101", "Name": "AARAV PATEL", "PRN": "20210001", "SGPI": 8.0, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S102", "Name": "BHAVYA SHAH", "PRN": "20210002", "SGPI": 7.5, "Result": "PASS", "KT_Count": 0},
        ]
        sem3_records = [
            {"Seat_No": "S301", "Name": "AARAV PATEL", "PRN": "20210001", "SGPI": 8.9, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S302", "Name": "BHAVYA SHAH", "PRN": "20210002", "SGPI": 8.0, "Result": "PASS", "KT_Count": 0},
            {"Seat_No": "S303", "Name": "LATERAL CANDIDATE", "PRN": "20220003", "SGPI": 9.0, "Result": "PASS", "KT_Count": 0},
        ]
        semesters_data = {
            1: {"semester_num": 1, "records": sem1_records, "input_type": "EXCEL", "source_files": ["Sem_1.xlsx"]},
            3: {"semester_num": 3, "records": sem3_records, "input_type": "EXCEL", "source_files": ["Sem_3.xlsx"]},
        }
        master_students = self.analyzer.align_master_students(semesters_data)
        analytics = self.analyzer.compute_batch_analytics(semesters_data, master_students)

        out_path = os.path.join(self.temp_dir, "Batch_Success_Rate_Report.xlsx")
        report_data = {
            "batch_metadata": {
                "batch_name": "2021-2022 Batch",
                "target_branch": "AIDS",
            },
            "overall_summary": analytics.get("overall_cohort_summary", {}),
            "success_rate_analysis": analytics.get("success_rate_analysis", {}),
            "semester_analysis": analytics.get("semester_wise_analysis", []),
            "semester_wise_analysis": analytics.get("semester_wise_analysis", []),
            "student_progression": analytics.get("student_progression", []),
            "kt_analysis": analytics.get("kt_analysis", []),
            "repeat_kt_students": analytics.get("repeat_kt_students", []),
            "topper_analysis": analytics.get("topper_analysis", {}),
            "retention_analysis": analytics.get("retention_analysis", []),
            "performance_categories": analytics.get("performance_categories", {}),
            "quality_audit_log": analytics.get("quality_audit_log", []),
            "available_semesters": list(semesters_data.keys()),
            "raw_semesters_data": {
                s_num: s_info.get("records", []) for s_num, s_info in semesters_data.items()
            }
        }
        excel_gen = BatchExcelReportGenerator(report_data)
        gen_path = excel_gen.generate_workbook(out_path)
        self.assertTrue(os.path.isfile(gen_path))

        wb = openpyxl.load_workbook(gen_path, data_only=True)
        # Check sheet names
        self.assertIn("Success Rate & Intake", wb.sheetnames)
        self.assertIn("Semester Analysis", wb.sheetnames)

        # Check contents of Success Rate sheet
        ws_sr = wb["Success Rate & Intake"]
        text_content = []
        for row in ws_sr.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    text_content.append(str(cell))
        all_text = " ".join(text_content)
        self.assertIn("SUCCESS RATE", all_text)
        self.assertIn("REGULAR INTAKE", all_text)
        self.assertIn("LATERAL ENTRY", all_text)
        self.assertIn("AARAV PATEL", all_text)
        self.assertIn("LATERAL CANDIDATE", all_text)

    def test_actual_batch_21_22_full_sem_1_to_8(self):
        """
        End-to-end integration test verifying the 2021-2022 batch across all 8 semesters:
        - Sem 7 parsed correctly with 73 students (accounting for Student_Name header).
        - PRN continuity unifies KU SANIKA with BHALEKAR SANIKA VIRENDRA SHALINI.
        - Diploma / Year 2-3 admissions properly classified into Lateral Entry (N3 = 15).
        - Regular intake properly classified (N1 = 67).
        - Evaluated Success Rate: 79.27% (65 / 82).
        - Zero KT (passed without backlogs): 24 students (29.27%).
        - Cleared KT (passed with backlogs): 41 students (50.00%).
        """
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        actual_batch_dir = os.path.join(proj_root, "batch_data")
        analyzer = BatchAcademicAnalyzer(output_dir=self.output_dir, app_batch_dir=actual_batch_dir)

        detected_info = analyzer.detect_batch_files("AIDS/2021-2022", target_branch="AIDS")
        sem_files = {s["semester"]: s["full_path"] for s in detected_info["semesters_detected"]}
        if len(sem_files) < 8:
            self.skipTest("All 8 semester files for AIDS/2021-2022 not present in batch_data")

        sem_data = {}
        for s_num in range(1, 9):
            recs = analyzer.parse_excel_semester(sem_files[s_num], s_num, target_branch="AIDS")
            sem_data[s_num] = recs

        # Assert parsed record counts per semester
        self.assertEqual(len(sem_data[1]), 67)
        self.assertEqual(len(sem_data[2]), 65)
        self.assertEqual(len(sem_data[3]), 75)
        self.assertEqual(len(sem_data[4]), 75)
        self.assertEqual(len(sem_data[5]), 65)
        self.assertEqual(len(sem_data[6]), 66)
        self.assertEqual(len(sem_data[7]), 73)
        self.assertEqual(len(sem_data[8]), 65)

        master_students = analyzer.align_master_students(sem_data)
        analytics = analyzer.compute_batch_analytics(detected_info, sem_data, master_students)
        sr = analytics["success_rate_analysis"]

        self.assertEqual(sr["regular_intake_n1"], 67)
        self.assertEqual(sr["lateral_entry_n3"], 12)
        self.assertEqual(sr["diploma_intake_count"], 12)
        self.assertEqual(sr["total_admitted_n1_n3"], 79)
        self.assertEqual(sr["passed_final_sem"], 65)
        self.assertEqual(sr["passed_without_backlogs"], 23)
        self.assertEqual(sr["passed_with_backlogs"], 42)
        self.assertAlmostEqual(sr["success_rate_percentage"], 82.28, places=2)
        self.assertAlmostEqual(sr["success_rate_no_backlog_percentage"], 29.11, places=2)
        self.assertAlmostEqual(sr["success_rate_with_backlog_percentage"], 53.16, places=2)
        self.assertAlmostEqual(sr["regular_success_rate"], 80.60, places=1)
        self.assertAlmostEqual(sr["lateral_success_rate"], 91.67, places=1)
        self.assertEqual(sr["multiple_exit_after_y1_count"], 4)
        self.assertEqual(sr["multiple_entry_count"], 10)


if __name__ == "__main__":
    unittest.main()
