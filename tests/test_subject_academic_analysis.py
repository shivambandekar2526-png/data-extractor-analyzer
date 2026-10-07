"""
Unit and Integration Tests for Phase 4: Subject-wise Academic Analysis
======================================================================
Verifies:
1. Dynamic subject derivation without hardcoding.
2. Deterministic calculation of appeared, passed, failed, pass %, fail %.
3. Institutional classification (Distinction, First Class, Second Class, Pass Class).
4. Adaptive marks distribution calibrated to maximum subject score.
5. Term Work and Oral/Practical detection and unavailable fallback notices.
6. Top 5 student ranking by total subject marks (not SGPI).
7. Data validation (0 marks vs Absent vs Missing vs Fail flags).
8. Full API integration across multiple real historical datasets.
"""

import json
import os
import sys
import unittest
import pandas as pd

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app
from core.academic_classifier import (
    AcademicClassificationRules,
    clean_mark_entry,
    compute_marks_distribution,
    extract_subject_definitions,
    analyze_subject_performance
)


class TestSubjectAcademicAnalysis(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_clean_mark_entry_data_validation(self):
        """Verifies distinct handling of 0 marks, Absent, Missing, and Fail flags."""
        # Valid 0 mark (not absent, not missing)
        st, val, flag = clean_mark_entry(0)
        self.assertEqual(st, "VALID")
        self.assertEqual(val, 0.0)
        self.assertFalse(flag)

        st, val, flag = clean_mark_entry("0.0")
        self.assertEqual(st, "VALID")
        self.assertEqual(val, 0.0)

        # Absent students
        st, val, flag = clean_mark_entry("ABS")
        self.assertEqual(st, "ABSENT")
        self.assertIsNone(val)

        st, val, flag = clean_mark_entry("AB")
        self.assertEqual(st, "ABSENT")

        # Missing values
        st, val, flag = clean_mark_entry(None)
        self.assertEqual(st, "MISSING")

        st, val, flag = clean_mark_entry("--")
        self.assertEqual(st, "MISSING")

        st, val, flag = clean_mark_entry("")
        self.assertEqual(st, "MISSING")

        # Suffix F / fail flagged mark
        st, val, flag = clean_mark_entry("18F")
        self.assertEqual(st, "VALID")
        self.assertEqual(val, 18.0)
        self.assertTrue(flag)

        # Normal passing mark
        st, val, flag = clean_mark_entry(65.5)
        self.assertEqual(st, "VALID")
        self.assertEqual(val, 65.5)
        self.assertFalse(flag)

    def test_classification_rules(self):
        """Verifies standard classification rules without inventing thresholds."""
        self.assertEqual(AcademicClassificationRules.classify_percentage(85.0), "First Class with Distinction")
        self.assertEqual(AcademicClassificationRules.classify_percentage(75.0), "First Class with Distinction")
        self.assertEqual(AcademicClassificationRules.classify_percentage(70.0), "First Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(60.0), "First Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(55.0), "Second Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(50.0), "Second Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(45.0), "Pass Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(40.0), "Pass Class")
        self.assertEqual(AcademicClassificationRules.classify_percentage(35.0), "Fail")

    def test_dynamic_subject_derivation_2122_8th(self):
        """Verifies subjects are dynamically discovered from 2122-8th catalog and CSV."""
        csv_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_extracted_data.csv")
        cat_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_catalog.json")

        self.assertTrue(os.path.isfile(csv_path), "2122-8th CSV must exist")
        df = pd.read_csv(csv_path)
        with open(cat_path, mode="r", encoding="utf-8") as f:
            cat = json.load(f)

        subjects = extract_subject_definitions(df, cat)
        codes = [s["code"] for s in subjects]

        self.assertIn("CSC801", codes)
        self.assertIn("CSL801", codes)
        self.assertIn("CSDO8013", codes)
        self.assertIn("AIDSCSP801", codes)

        csc801 = next(s for s in subjects if s["code"] == "CSC801")
        self.assertEqual(csc801["name"], "Advanced Artificial Intelligence")
        self.assertTrue(csc801["has_theory"])
        self.assertFalse(csc801["has_term_work"])
        self.assertFalse(csc801["has_practical_oral"])

        csl801 = next(s for s in subjects if s["code"] == "CSL801")
        self.assertEqual(csl801["name"], "Advanced Artificial Intelligence Lab")
        self.assertFalse(csl801["has_theory"])
        self.assertTrue(csl801["has_term_work"])
        self.assertTrue(csl801["has_practical_oral"])

    def test_deterministic_theory_metrics_csc801(self):
        """Verifies deterministic pass/fail calculation and ranking for CSC801."""
        csv_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_extracted_data.csv")
        cat_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_catalog.json")

        df = pd.read_csv(csv_path)
        with open(cat_path, mode="r", encoding="utf-8") as f:
            cat = json.load(f)

        res = analyze_subject_performance(df.to_dict(orient="records"), cat, "CSC801")

        self.assertTrue(res["success"])
        self.assertEqual(res["header"]["subject_code"], "CSC801")
        self.assertEqual(res["header"]["subject_name"], "Advanced Artificial Intelligence")

        # Theory stats
        th = res["theory"]
        self.assertTrue(th["available"])
        self.assertEqual(th["appeared"], 65)
        self.assertEqual(th["passed"], 65)
        self.assertEqual(th["failed"], 0)
        self.assertEqual(th["pass_rate"], 100.0)
        self.assertEqual(th["fail_rate"], 0.0)

        # Classification table contains Distinction, First Class, Second Class, Pass Class
        class_table = th["classification"]["table"]
        c_names = [c["short"] for c in class_table]
        self.assertIn("Distinction", c_names)
        self.assertIn("First Class", c_names)
        self.assertIn("Second Class", c_names)
        self.assertIn("Pass Class", c_names)

        # Total classified students must match passed count (65)
        total_classified = sum(c["students"] for c in class_table)
        self.assertEqual(total_classified, 65)

        # Marks distribution has valid intervals
        m_table = th["marks_distribution"]["table"]
        self.assertTrue(len(m_table) > 0)
        self.assertEqual(sum(m["students"] for m in m_table), 65)

        # Unavailable notices for pure theory course
        self.assertFalse(res["term_work"]["available"])
        self.assertEqual(res["term_work"]["message"], "Term work data unavailable in selected dataset.")
        self.assertFalse(res["practical_oral"]["available"])
        self.assertEqual(res["practical_oral"]["message"], "Practical/Oral data unavailable in selected dataset.")

        # Top 5 students
        top_5 = res["top_students"]
        self.assertEqual(len(top_5), 5)
        self.assertEqual(top_5[0]["rank"], 1)
        self.assertTrue(top_5[0]["marks"] >= top_5[1]["marks"])

    def test_lab_analysis_csl801(self):
        """Verifies Term Work and Practical analysis on lab course CSL801."""
        csv_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_extracted_data.csv")
        cat_path = os.path.join(APP_DIR, "output", "2122-8th", "2122-8th_catalog.json")

        df = pd.read_csv(csv_path)
        with open(cat_path, mode="r", encoding="utf-8") as f:
            cat = json.load(f)

        res = analyze_subject_performance(df.to_dict(orient="records"), cat, "CSL801")

        self.assertTrue(res["success"])
        # Theory not applicable
        self.assertFalse(res["theory"]["available"])
        # Term work available
        self.assertTrue(res["term_work"]["available"])
        self.assertEqual(res["term_work"]["appeared"], 65)
        self.assertEqual(res["term_work"]["passed"], 65)
        # Practical available
        self.assertTrue(res["practical_oral"]["available"])
        self.assertEqual(res["practical_oral"]["appeared"], 65)
        self.assertEqual(res["practical_oral"]["passed"], 65)

    def test_multi_dataset_fail_tracking_sem3(self):
        """Verifies real failure tracking and term work on 23-24 Semester 3 Excel dataset."""
        excel_path = os.path.join(APP_DIR, "departments_data", "excel", "Artificial_Intelligence_and_Data_Science", "23-24", "Semester_3.xlsx")
        self.assertTrue(os.path.isfile(excel_path))

        xl = pd.ExcelFile(excel_path)
        df = xl.parse("Extracted Data")
        cat_df = xl.parse("Course Catalog") if "Course Catalog" in xl.sheet_names else None
        courses = []
        if cat_df is not None:
            for _, r in cat_df.iterrows():
                courses.append({"code": r.get("Course Code"), "name": r.get("Official Course Name")})
        cat = {"courses": courses}

        res = analyze_subject_performance(df.to_dict(orient="records"), cat, "CSC301")

        self.assertTrue(res["success"])
        th = res["theory"]
        self.assertTrue(th["available"])
        self.assertEqual(th["appeared"], 86)
        self.assertEqual(th["passed"], 65)
        self.assertEqual(th["failed"], 21)
        self.assertAlmostEqual(th["pass_rate"], 75.58, places=1)
        self.assertAlmostEqual(th["fail_rate"], 24.42, places=1)

        # Term work is also directly present in CSC301
        self.assertTrue(res["term_work"]["available"])
        self.assertEqual(res["term_work"]["appeared"], 87)

    def test_api_options_endpoint(self):
        """Tests GET /api/subject-analysis/options endpoint."""
        res = self.app.get("/api/subject-analysis/options")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("academic_years", data)
        self.assertIn("branches", data)
        self.assertIn("semesters", data)
        self.assertIn("datasets", data)
        self.assertTrue(len(data["datasets"]) > 0)

    def test_api_subjects_endpoint(self):
        """Tests POST /api/subject-analysis/subjects endpoint."""
        res = self.app.post("/api/subject-analysis/subjects", json={"dataset_id": "out_2122-8th"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(len(data["subjects"]) > 0)
        codes = [s["code"] for s in data["subjects"]]
        self.assertIn("CSC801", codes)

    def test_api_analyze_endpoint(self):
        """Tests POST /api/subject-analysis/analyze endpoint."""
        payload = {
            "dataset_id": "out_2122-8th",
            "subject_code": "CSC801",
            "academic_year": "2024–25",
            "branch": "Computer Engineering",
            "semester": "VIII"
        }
        res = self.app.post("/api/subject-analysis/analyze", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["header"]["subject_code"], "CSC801")
        self.assertEqual(data["theory"]["appeared"], 65)
        self.assertEqual(len(data["top_students"]), 5)


if __name__ == "__main__":
    unittest.main()
