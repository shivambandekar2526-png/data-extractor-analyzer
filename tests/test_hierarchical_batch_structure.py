"""
Unit Tests for Hierarchical Batch Directory Structure & Cascading Dropdowns
=============================================================================
Update 4.1 Verification:
1. Format branch label helper
2. Hierarchical Batch Directory Discovery (Branch -> Academic Year -> Semesters 1-8)
3. Detection of Computer Engineering (Computer_Engineering/2024-2025/Sem_3.pdf, Sem_4.pdf)
4. Detection of AIDS (AIDS/2024-2025/Sem_3.pdf, Sem_4.pdf)
5. Flask Endpoints: /api/batch-analysis/hierarchy, batches, inspect, and run
6. Saving clean Excel to hierarchical target paths
"""

import os
import shutil
import tempfile
import unittest

from core.batch_analyzer import (
    BatchAcademicAnalyzer,
    format_branch_label,
    normalize_batch_slug,
)
from app import app


class TestHierarchicalBatchStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.real_batch_dir = os.path.join(cls.app_dir, "departments_data")

    def setUp(self):
        self.client = app.test_client()
        self.temp_dir = tempfile.mkdtemp(prefix="test_hier_batch_")
        self.app_batch_dir = os.path.join(self.temp_dir, "departments_data")
        self.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(self.app_batch_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

        self.analyzer = BatchAcademicAnalyzer(
            output_dir=self.output_dir,
            app_batch_dir=self.app_batch_dir
        )

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_branch_label(self):
        self.assertEqual(format_branch_label("Computer_Engineering"), "Computer Engineering")
        self.assertEqual(format_branch_label("AIDS"), "Artificial Intelligence & Data Science (AIDS)")
        self.assertEqual(format_branch_label("AI_DS"), "Artificial Intelligence & Data Science (AIDS)")
        self.assertEqual(format_branch_label("Information_Technology"), "Information Technology")
        self.assertEqual(format_branch_label("Mechanical_Engineering"), "Mechanical Engineering")

    def test_synthetic_hierarchical_directory_discovery(self):
        # Create hierarchy:
        # departments_data/
        # ├── Computer_Engineering/
        # │   └── 2024-2025/
        # │       ├── Sem_3.pdf
        # │       └── Sem_4.pdf
        # └── AIDS/
        #     └── 2024-2025/
        #         ├── Sem_3.pdf
        #         └── Sem_4.pdf
        ce_dir = os.path.join(self.app_batch_dir, "Computer_Engineering", "2024-2025")
        aids_dir = os.path.join(self.app_batch_dir, "AIDS", "2024-2025")
        os.makedirs(ce_dir, exist_ok=True)
        os.makedirs(aids_dir, exist_ok=True)

        for folder in [ce_dir, aids_dir]:
            with open(os.path.join(folder, "Sem_3.pdf"), "wb") as f:
                f.write(b"%PDF-1.4 test")
            with open(os.path.join(folder, "Sem_4.pdf"), "wb") as f:
                f.write(b"%PDF-1.4 test")

        hierarchy = self.analyzer.discover_batch_hierarchy()
        branches = {b["name"]: b for b in hierarchy["branches"]}

        self.assertIn("Computer_Engineering", branches)
        self.assertIn("AIDS", branches)

        ce_years = {y["year"]: y for y in branches["Computer_Engineering"]["years"]}
        self.assertIn("2024-2025", ce_years)
        self.assertEqual(ce_years["2024-2025"]["total_semesters"], 2)
        self.assertEqual(ce_years["2024-2025"]["semesters"], [3, 4])
        self.assertEqual(ce_years["2024-2025"]["pdf_count"], 2)
        self.assertEqual(ce_years["2024-2025"]["excel_count"], 0)

        aids_years = {y["year"]: y for y in branches["AIDS"]["years"]}
        self.assertIn("2024-2025", aids_years)
        self.assertEqual(aids_years["2024-2025"]["total_semesters"], 2)
        self.assertEqual(aids_years["2024-2025"]["semesters"], [3, 4])

    def test_detect_batch_files_hierarchical(self):
        ce_dir = os.path.join(self.app_batch_dir, "Computer_Engineering", "2024-2025")
        os.makedirs(ce_dir, exist_ok=True)
        with open(os.path.join(ce_dir, "Sem_3.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 sem3")
        with open(os.path.join(ce_dir, "Sem_4.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 sem4")

        # Test detection with relative path "Computer_Engineering/2024-2025"
        meta = self.analyzer.detect_batch_files("Computer_Engineering/2024-2025")
        self.assertEqual(meta["total_semesters"], 2)
        self.assertEqual(meta["branch"], "Computer_Engineering")
        self.assertEqual(meta["academic_year"], "2024-2025")
        sems = [s["semester"] for s in meta["semesters_detected"]]
        self.assertEqual(sems, [3, 4])

    def test_save_clean_semester_excel_hierarchical_target(self):
        records = [
            {"Seat_No": "C201", "Name": "ANAND VIKRAM", "Result": "PASS", "SGPI": 9.15, "KT_Count": 0}
        ]
        target_dir = os.path.join(self.app_batch_dir, "Computer_Engineering", "2024-2025")
        os.makedirs(target_dir, exist_ok=True)

        saved = self.analyzer.save_clean_semester_excel(
            records=records,
            batch_slug="Computer_Engineering/2024-2025",
            sem_num=3,
            target_dir=target_dir
        )
        self.assertTrue(os.path.isfile(saved))
        self.assertEqual(os.path.normpath(saved), os.path.normpath(os.path.join(target_dir, "Sem_3.xlsx")))

    def test_real_workspace_hierarchy_endpoint(self):
        # Hits the live Flask app on data_extractor_AI/departments_data
        res = self.client.get("/api/batch-analysis/hierarchy")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))

        branches = {b["name"]: b for b in data.get("branches", [])}
        self.assertIn("Computer_Engineering", branches)
        aids_branch = branches.get("Artificial_Intelligence_and_Data_Science") or branches.get("AIDS")
        self.assertIsNotNone(aids_branch)

        ce_years = {y["year"]: y for y in branches["Computer_Engineering"]["years"]}
        ce_yr = ce_years.get("24-25") or ce_years.get("2024-2025") or ce_years.get("2425")
        self.assertIsNotNone(ce_yr)
        self.assertEqual(ce_yr["total_semesters"], 2)
        self.assertIn(3, ce_yr["semesters"])
        self.assertIn(4, ce_yr["semesters"])

        aids_years = {y["year"]: y for y in aids_branch["years"]}
        self.assertTrue(any(y in aids_years for y in ["24-25", "2024-2025", "2425"]))

    def test_inspect_endpoint_hierarchical_cascading(self):
        # Inspect Computer Engineering 2024-2025
        res = self.client.post("/api/batch-analysis/inspect", json={
            "branch": "Computer_Engineering",
            "academic_year": "2024-2025",
            "target_branch": "Computer_Engineering",
            "semester": "all"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("total_semesters"), 2)
        sems = [s["semester"] for s in data.get("semesters_detected", [])]
        self.assertEqual(sems, [3, 4])

        # Inspect specific semester filter
        res_sem3 = self.client.post("/api/batch-analysis/inspect", json={
            "branch": "Computer_Engineering",
            "academic_year": "2024-2025",
            "semester": "3"
        })
        self.assertEqual(res_sem3.status_code, 200)
        data_sem3 = res_sem3.get_json()
        self.assertEqual(data_sem3.get("selected_semester"), 3)


if __name__ == "__main__":
    unittest.main()
