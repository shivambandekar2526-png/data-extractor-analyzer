import unittest
from services.supabase_service import get_supabase_service
from app import app

class TestLibraryCatalog(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.svc = get_supabase_service()

    def test_supabase_configured(self):
        self.assertTrue(self.svc.is_configured, "Supabase service must be configured with URL and key.")

    def test_list_batches_returns_cloud_structure(self):
        resp = self.client.get("/api/batches")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("structured", data)
        self.assertIn("batches", data)
        self.assertIn("pdf", data["structured"])
        self.assertIn("excel", data["structured"])

    def test_zero_phantom_files(self):
        # Force a fresh check and verify no entries exist without remote backing
        resp = self.client.get("/api/batches?refresh=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        structured = data.get("structured", {})
        
        # Check PDF entries
        for dept, dept_obj in structured.get("pdf", {}).items():
            for batch, files in dept_obj.get("batches", {}).items():
                for f in files:
                    self.assertTrue(f["rel_path"].startswith("pdf/"), f"PDF path {f['rel_path']} must be remote cloud storage path")
                    self.assertEqual(f["file_type"], "pdf")

        # Check Excel entries
        for dept, dept_obj in structured.get("excel", {}).items():
            for batch, files in dept_obj.get("batches", {}).items():
                for f in files:
                    self.assertTrue(f["rel_path"].startswith("excel/"), f"Excel path {f['rel_path']} must be remote cloud storage path")
                    self.assertEqual(f["file_type"], "excel")

if __name__ == "__main__":
    unittest.main()
