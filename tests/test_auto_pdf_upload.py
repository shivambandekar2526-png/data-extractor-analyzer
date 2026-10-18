"""
Unit Tests for Requirement 4: Automatic PDF Upload to Supabase upon Extraction
=============================================================================
Asserts single, non-redundant upload of PDF to Supabase Storage strictly when
the user triggers extraction.
"""

import os
import unittest
from unittest.mock import MagicMock, patch
from services.supabase_service import get_supabase_service
import app as flask_app


class TestAutoPdfUpload(unittest.TestCase):

    def setUp(self):
        self.app = flask_app.app.test_client()
        self.app.testing = True
        self.svc = get_supabase_service()

    @patch("routes.extract_api.classify_pdf_first_page")
    @patch("routes.extract_api._run_ai_extraction_worker")
    def test_direct_upload_archives_to_supabase_on_extract(self, mock_worker, mock_classify):
        """A direct PDF upload must be archived to Supabase Storage when extraction starts."""
        mock_classify.return_value = {
            "department": "Artificial_Intelligence_and_Data_Science",
            "batch": "24-25",
            "semester": "Semester_3"
        }

        # Mock Supabase upload_local_file and upsert_dataset_record
        with patch.object(self.svc, "upload_local_file", return_value=True) as mock_upload, \
             patch.object(self.svc, "upsert_dataset_record", return_value=True) as mock_upsert:

            # Create a dummy test PDF
            test_pdf = os.path.join(flask_app.INPUT_DIR, "test_req4_sample.pdf")
            os.makedirs(flask_app.INPUT_DIR, exist_ok=True)
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 50), "SAVITRIBAI PHULE PUNE UNIVERSITY - TEST")
            doc.save(test_pdf)
            doc.close()

            with open(test_pdf, "rb") as fp:
                resp = self.app.post("/api/extract", data={
                    "file": fp,
                    "department": "Artificial_Intelligence_and_Data_Science",
                    "batch": "24-25",
                    "semester": "Semester_3",
                    "overwrite": "1"
                }, content_type="multipart/form-data")

            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertIn("job_id", data)
            meta = data.get("dataset_meta", {})
            self.assertEqual(meta.get("pdf_path"), "pdf/Artificial_Intelligence_and_Data_Science/24-25/Semester_3.pdf")

            # Assert upload was called exactly once
            mock_upload.assert_called_once()
            args, _ = mock_upload.call_args
            self.assertEqual(args[1], "pdf/Artificial_Intelligence_and_Data_Science/24-25/Semester_3.pdf")

            # Clean up
            if os.path.isfile(test_pdf):
                try: os.remove(test_pdf)
                except Exception: pass

    @patch("routes.extract_api._run_ai_extraction_worker")
    def test_library_selection_does_not_reupload_pdf(self, mock_worker):
        """When selecting a file from the Department Library, it must NOT re-upload."""
        with patch.object(self.svc, "upload_local_file") as mock_upload:
            resp = self.app.post("/api/extract", json={
                "batch_file": "pdf/Artificial_Intelligence_and_Data_Science/24-25/Semester_3.pdf",
                "department": "Artificial_Intelligence_and_Data_Science",
                "batch": "24-25",
                "semester": "Semester_3",
                "overwrite": True
            })

            # mock_upload should NOT be called for a library batch_file
            mock_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
