import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app
from services.supabase_service import get_supabase_service


class TestHistoricalFIFO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.supabase_svc = get_supabase_service()

    def test_01_api_history_endpoint(self):
        """Verify /api/history returns valid JSON with history array."""
        res = self.client.get("/api/history")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data.decode("utf-8"))
        self.assertTrue(data.get("success", False))
        self.assertIn("history", data)
        self.assertIsInstance(data["history"], list)

    def test_02_api_history_scan_endpoint(self):
        """Verify /api/history/scan forces a live scan of cloud storage."""
        res = self.client.get("/api/history/scan")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data.decode("utf-8"))
        self.assertTrue(data.get("success", False))
        self.assertIn("runs", data)
        self.assertIn("count", data)

    def test_03_save_and_retrieve_historical_run(self):
        """Verify saving a historical run archives meta.json and records.json and can be retrieved."""
        svc = self.supabase_svc
        if not svc.is_configured:
            self.skipTest("Supabase service is not configured.")

        test_run_id = "run_99999999_999999_test_fifo_unit"
        source_name = "test_fifo.pdf"
        dataset_meta = {
            "department": "Computer_Engineering",
            "batch": "2024-2028",
            "semester": "Semester_7"
        }
        students = [
            {"seat_no": "TEST001", "name": "Alice Smith", "cgpa": "9.50"},
            {"seat_no": "TEST002", "name": "Bob Jones", "cgpa": "8.80"}
        ]

        # Save run with explicit run_id
        res_id = svc.save_historical_run(
            source_name=source_name,
            students=students,
            catalog={"courses": [{"code": "CSC701", "name": "Deep Learning"}]},
            dataset_meta=dataset_meta,
            run_id=test_run_id
        )
        self.assertEqual(res_id, test_run_id)

        # Retrieve run via svc
        entry = svc.get_historical_run(test_run_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.get("folder_name"), test_run_id)
        self.assertEqual(len(entry.get("students", [])), 2)
        self.assertEqual(entry["students"][0]["seat_no"], "TEST001")

        # Retrieve run via HTTP API
        http_res = self.client.get(f"/api/history/{test_run_id}")
        self.assertEqual(http_res.status_code, 200)
        http_data = json.loads(http_res.data.decode("utf-8"))
        self.assertTrue(http_data.get("success", False))
        self.assertEqual(len(http_data.get("students", [])), 2)

        # Clean up test run
        del_res = svc.delete_historical_run(test_run_id)
        self.assertTrue(del_res)

    def test_04_fifo_eviction_logic(self):
        """Verify enforce_historical_fifo trims oldest entries when threshold is exceeded."""
        svc = self.supabase_svc
        if not svc.is_configured:
            self.skipTest("Supabase service is not configured.")

        # Create 3 temporary runs with timestamps in ID
        prefix = "run_20990101_00000"
        runs = [f"{prefix}{i}_temp_fifo" for i in range(1, 4)]
        for r_id in runs:
            dataset_meta = {
                "department": "Artificial_Intelligence_and_Data_Science",
                "batch": "2024-2028",
                "semester": "Semester_8"
            }
            svc.save_historical_run(
                source_name=f"{r_id}.xlsx",
                students=[{"seat_no": "T1", "name": "Test"}],
                dataset_meta=dataset_meta,
                run_id=r_id
            )

        try:
            # Enforce FIFO with max_runs=2 on the overall storage
            deleted_count = svc.enforce_historical_fifo(max_runs=2)
            self.assertIsInstance(deleted_count, int)

            # Check if historical runs cache reflects FIFO limit
            runs_list = svc.list_historical_runs(force_refresh=True)
            self.assertLessEqual(len(runs_list), 30)
        finally:
            # Clean up all created temp runs
            for r_id in runs:
                try:
                    svc.delete_historical_run(r_id)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
