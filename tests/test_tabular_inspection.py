import os
import io
import unittest
import pandas as pd
from app import create_app

class TestTabularInspection(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def _create_sample_excel(self):
        df = pd.DataFrame({
            "Seat_No": ["101", "102"],
            "Student_Name": ["Student A", "Student B"],
            "CSC701_TH": ["70", "80"],
            "CSC701_IA": ["15", "18"],
            "CSC701_TOT": ["85", "98"],
            "CSC701_Credits": ["3", "3"],
            "CSC701_GP": ["10", "10"],
            "CSC701_CG": ["30", "30"],
            "Total_Marks": ["85", "98"],
            "Total_CG": ["30", "30"],
            "Credits_Earned": ["3", "3"],
            "SGPI": ["10.00", "10.00"],
            "Result_Status": ["PASS", "PASS"]
        })
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)
        return buf

    def test_inspect_with_file_field(self):
        excel_buf = self._create_sample_excel()
        res = self.client.post(
            "/api/tabular/inspect",
            data={"file": (excel_buf, "student_ledger.xlsx")},
            content_type="multipart/form-data"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("file_type"), "excel")
        self.assertEqual(data.get("filename"), "student_ledger.xlsx")

    def test_inspect_with_tabular_file_field(self):
        excel_buf = self._create_sample_excel()
        res = self.client.post(
            "/api/tabular/inspect",
            data={"tabular_file": (excel_buf, "ledger_tabular.xlsx")},
            content_type="multipart/form-data"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("filename"), "ledger_tabular.xlsx")

    def test_classify_with_tabular_file(self):
        excel_buf = self._create_sample_excel()
        res = self.client.post(
            "/api/classify",
            data={"tabular_file": (excel_buf, "Computer_Engineering_2122_Sem7.xlsx")},
            content_type="multipart/form-data"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))

    def test_inspect_with_batch_file_from_supabase_mock(self):
        from unittest.mock import patch, MagicMock
        mock_svc = MagicMock()
        mock_svc.is_configured = True
        mock_svc.download_file_bytes.return_value = self._create_sample_excel().getvalue()

        with patch("services.supabase_service.get_supabase_service", return_value=mock_svc):
            res = self.client.post(
                "/api/tabular/inspect",
                json={"batch_file": "excel/Artificial_Intelligence_and_Data_Science/21-22/Semester_7.xlsx"}
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("file_type"), "excel")

if __name__ == "__main__":
    unittest.main()
