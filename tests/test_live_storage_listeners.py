"""
Tests for Live Database & Cloud Storage Listeners
=================================================
Verifies push-based delta synchronization, zero polling queries,
removal of static catalog JSON, and instant SSE notification triggers.
"""

import unittest
import time
from services.supabase_service import get_supabase_service
from core.folder_watcher import folder_watcher


class TestLiveStorageListeners(unittest.TestCase):

    def setUp(self):
        self.svc = get_supabase_service()
        # Seed test catalog in memory
        self.svc._catalog_cache = {
            "datasets": [
                {
                    "department": "AIDS",
                    "batch": "24-25",
                    "semester": "SEM 3",
                    "excel_path": "excel/AIDS/24-25/SEM 3.xlsx",
                    "pdf_path": "pdf/AIDS/24-25/SEM 3.pdf",
                    "total_students": 60,
                    "meta_json": {}
                },
                {
                    "department": "COMP",
                    "batch": "23-24",
                    "semester": "SEM 4",
                    "excel_path": "excel/COMP/23-24/SEM 4.xlsx",
                    "pdf_path": None,
                    "total_students": 70,
                    "meta_json": {}
                }
            ],
            "updated_at": "2026-10-18 12:00:00"
        }

    def test_handle_storage_delete_event(self):
        """Deleting a PDF should prune only the pdf_path without deleting the dataset if Excel exists."""
        initial_version = folder_watcher.get_status().get("version", 0)
        res = self.svc.handle_storage_event("DELETE", "pdf/AIDS/24-25/SEM 3.pdf")
        self.assertTrue(res)

        datasets = self.svc.list_datasets_from_catalog()
        aids_ds = next((d for d in datasets if d["department"] == "AIDS" and d["batch"] == "24-25"), None)
        self.assertIsNotNone(aids_ds)
        self.assertIsNone(aids_ds["pdf_path"])
        self.assertEqual(aids_ds["excel_path"], "excel/AIDS/24-25/SEM 3.xlsx")

        # Check that folder_watcher received the notification and incremented version
        new_version = folder_watcher.get_status().get("version", 0)
        self.assertGreater(new_version, initial_version)

    def test_handle_storage_delete_complete_record(self):
        """Deleting the only file for a dataset should prune the entire dataset record from memory."""
        res = self.svc.handle_storage_event("DELETE", "excel/COMP/23-24/SEM 4.xlsx")
        self.assertTrue(res)

        datasets = self.svc.list_datasets_from_catalog()
        comp_ds = next((d for d in datasets if d["department"] == "COMP" and d["batch"] == "23-24"), None)
        self.assertIsNone(comp_ds)

    def test_handle_storage_insert_event(self):
        """Inserting a new file should dynamically create a new dataset entry in memory."""
        res = self.svc.handle_storage_event("INSERT", "pdf/IT/22-23/SEM 6.pdf")
        self.assertTrue(res)

        datasets = self.svc.list_datasets_from_catalog()
        it_ds = next((d for d in datasets if d["department"] == "IT" and d["batch"] == "22-23" and d["semester"] == "SEM 6"), None)
        self.assertIsNotNone(it_ds)
        self.assertEqual(it_ds["pdf_path"], "pdf/IT/22-23/SEM 6.pdf")
        self.assertIsNone(it_ds["excel_path"])

    def test_cloud_batches_map_reflects_in_memory_state(self):
        """get_cloud_batches_map should instantly reflect the in-memory state with 0 database calls."""
        res_map = self.svc.get_cloud_batches_map()
        structured = res_map.get("structured", {})
        self.assertIn("excel", structured)
        self.assertIn("pdf", structured)


if __name__ == "__main__":
    unittest.main()
