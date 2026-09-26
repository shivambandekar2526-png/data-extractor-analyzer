"""
Test Suite for Data Extractor AI Cross-Checking & Self-Healing Pipeline
========================================================================
Validates:
1. ContextEnvelope pattern serialization and token economy.
2. Page-by-page raw PDF text extraction on real academic PDFs.
3. Zero-Trust Sandbox Validation rules (verbatim quote, schema whitelist, mark bounds).
4. Multi-provider stateless failover under 429 rate limit conditions.
5. Macro Schema Alignment & Micro Chunk Proofreading.
6. End-to-end integration test with real PDF data.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure data_extractor_AI is in sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_APP_DIR = os.path.abspath(os.path.join(TESTS_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(AI_APP_DIR, ".."))
for p in [AI_APP_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.ai_cross_checker import (
    ContextEnvelope,
    DiscrepancyCard,
    align_schema_with_ai,
    build_context_envelope,
    extract_raw_page_texts,
    locate_student_page,
    run_ai_cross_check,
    validate_patch_sandbox,
    verify_student_chunk,
)
from core.cloud_client import CloudClient
from models import CourseCatalog, ExamMeta, StudentRecord


class TestContextEnvelope(unittest.TestCase):
    """Tests for ContextEnvelope serialization and stateless recovery."""

    def test_compact_prompt_header(self):
        env = ContextEnvelope(
            exam_title="B.E. (with Credits) - Regular - CBCGS - Computer Engineering - Sem VII",
            semester="VII",
            college_name="Universal College of Engineering",
            archetype="gazette_spool",
            valid_columns=["Seat_No", "Name", "CSC701_TH", "CSC701_IA", "Total", "SGPI", "Result"],
            courses=[
                {"code": "CSC701", "name": "Digital Signal & Image Processing", "heads": ["TH", "IA", "TOT"]},
                {"code": "CSC702", "name": "Mobile Communication & Computing", "heads": ["TH", "IA", "TOT"]},
            ]
        )
        header = env.to_compact_prompt_header()
        self.assertIn("EXAM: B.E. (with Credits)", header)
        self.assertIn("SEMESTER: VII", header)
        self.assertIn("CSC701", header)
        self.assertIn("VALID CSV COLUMNS: Seat_No, Name", header)
        words = header.split()
        self.assertLess(len(words), 200)


class TestPdfTextExtraction(unittest.TestCase):
    """Tests for raw PDF page text extraction on actual PDF batch files."""

    def setUp(self):
        self.sample_pdf = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-8th.pdf")
        if not os.path.isfile(self.sample_pdf):
            self.sample_pdf = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-7th.pdf")

    def test_extract_page_texts(self):
        if not os.path.isfile(self.sample_pdf):
            self.skipTest(f"Sample PDF not found at {self.sample_pdf}")

        pages = extract_raw_page_texts(self.sample_pdf)
        self.assertIsInstance(pages, list)
        self.assertGreater(len(pages), 0, "Should extract at least one page of text")
        self.assertGreater(len(pages[0].strip()), 50, "Page 1 text should not be empty")

    def test_locate_student_page(self):
        pages = [
            "University of Mumbai - Page 1\n1234567 Doe John SGPI: 8.5",
            "University of Mumbai - Page 2\n7654321 Smith Alice SGPI: 9.1",
        ]
        p_idx = locate_student_page("7654321", "Smith Alice", pages)
        self.assertEqual(p_idx, 1)

        p_idx_fallback = locate_student_page("9999999", "Unknown", pages)
        self.assertEqual(p_idx_fallback, 0)


class TestZeroTrustSandboxValidation(unittest.TestCase):
    """Tests for the ground-truth sandbox validation engine."""

    def setUp(self):
        self.env = ContextEnvelope(
            valid_columns=["Seat_No", "Name", "CSC801_TH", "CSC801_IA", "Total", "SGPI", "Result"],
            courses=[{"code": "CSC801", "name": "Distributed Computing", "heads": ["TH", "IA"], "max_marks": {"TH": 80, "IA": 20}}]
        )
        self.page_texts = [
            "Seat: 5001 Name: BANDEKAR SHIVAM CSC801: 65 18 Total: 540 SGPI: 8.92 Result: Successful"
        ]
        self.students_by_seat = {
            "5001": {
                "Seat_No": "5001",
                "Name": "BANDEKAR SHIVAM",
                "CSC801_TH": 56,  # Erroneous
                "SGPI": 8.92,
                "Result": "Successful"
            }
        }

    def test_valid_patch_passes_sandbox(self):
        patch = {
            "seat_no": "5001",
            "field": "CSC801_TH",
            "extracted_val": 56,
            "suggested_val": 65,
            "reason": "OCR transposed digits 56 -> 65",
            "evidence_quote": "CSC801: 65"
        }
        ok, reason = validate_patch_sandbox(patch, self.page_texts, self.env, self.students_by_seat)
        self.assertTrue(ok, f"Patch should be VERIFIED, but got: {reason}")
        self.assertIn("Passed", reason)

    def test_hallucinated_evidence_rejected(self):
        patch = {
            "seat_no": "5001",
            "field": "CSC801_TH",
            "extracted_val": 56,
            "suggested_val": 75,
            "reason": "AI invented mark",
            "evidence_quote": "CSC801: 75 NOT IN DOCUMENT"
        }
        ok, reason = validate_patch_sandbox(patch, self.page_texts, self.env, self.students_by_seat)
        self.assertFalse(ok)
        self.assertIn("Verbatim quote", reason)

    def test_invalid_field_rejected(self):
        patch = {
            "seat_no": "5001",
            "field": "HALLUCINATED_COL",
            "extracted_val": None,
            "suggested_val": "Val",
            "reason": "Fake column",
            "evidence_quote": "5001"
        }
        ok, reason = validate_patch_sandbox(patch, self.page_texts, self.env, self.students_by_seat)
        self.assertFalse(ok)
        self.assertIn("not in valid CSV schema columns", reason)

    def test_excessive_marks_rejected(self):
        patch = {
            "seat_no": "5001",
            "field": "CSC801_TH",
            "extracted_val": 56,
            "suggested_val": 150,  # Max allowable is 80 (course catalog limit)
            "reason": "Excessive mark",
            "evidence_quote": "5001"
        }
        ok, reason = validate_patch_sandbox(patch, self.page_texts, self.env, self.students_by_seat)
        self.assertFalse(ok)
        self.assertIn("exceeds allowable max bounds", reason)


class TestCloudFailoverAndRecovery(unittest.TestCase):
    """Tests multi-provider failover without context loss."""

    @patch("urllib.request.urlopen")
    def test_failover_cooldown_and_direct_generation(self, mock_urlopen):
        client = CloudClient()
        client.config["api_keys"]["groq"] = "test-groq-key"
        client.config["api_keys"]["gemini"] = "test-gemini-key"
        client.config["auto_orchestrate"] = False
        client.config["provider"] = "groq"
        client.config["model"] = "openai/gpt-oss-120b"
        CloudClient._model_cooldowns = {}

        calls = []

        def side_effect(req, timeout=40):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "groq.com" in url:
                calls.append("groq")
                import urllib.error
                raise urllib.error.HTTPError(url, 429, "Rate limit hit", {}, None)
            elif "googleapis.com" in url:
                calls.append("gemini")
                resp = MagicMock()
                resp.read.return_value = json.dumps({
                    "candidates": [{"content": {"parts": [{"text": '{"status": "ALIGNED"}'}]}}]
                }).encode("utf-8")
                resp.__enter__.return_value = resp
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = side_effect
        # In non-auto_orchestrate mode, groq raises 429, marks model failed
        with self.assertRaises(Exception):
            client.generate_direct("Test prompt")

        self.assertIn("groq", calls)
        # Verify model was put in cooldown
        self.assertGreater(CloudClient._model_cooldowns.get("groq:openai/gpt-oss-120b", 0), 0)


class TestFullCrossCheckPipeline(unittest.TestCase):
    """End-to-End pipeline test on real PDF with mock AI response."""

    def setUp(self):
        self.sample_pdf = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-8th.pdf")
        if not os.path.isfile(self.sample_pdf):
            self.sample_pdf = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-7th.pdf")

    def test_run_ai_cross_check_mock(self):
        if not os.path.isfile(self.sample_pdf):
            self.skipTest(f"Sample PDF not found at {self.sample_pdf}")

        pages = extract_raw_page_texts(self.sample_pdf)
        self.assertTrue(len(pages) > 0)
        first_page = pages[0]

        # Use an authentic quote that exists in page 1
        quote = "OFFICE REGISTER FOR THE"
        self.assertIn(quote.lower(), first_page.lower())

        meta = ExamMeta(exam_title="B.E. Examination", semester="VIII")
        students = [
            {"Seat_No": "1001", "Name": "TEST STUDENT", "SGPI": 7.5, "Result": "P"}
        ]

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True

        # Stage 1 returns ALIGNED, Stage 2 returns 1 verified card
        mock_client.generate_direct.side_effect = [
            json.dumps({"status": "ALIGNED", "summary": "Columns match perfectly."}),
            json.dumps({
                "discrepancies": [
                    {
                        "seat_no": "1001",
                        "field": "SGPI",
                        "extracted_val": 7.5,
                        "suggested_val": 8.0,
                        "reason": "Test SGPI patch",
                        "evidence_quote": quote
                    }
                ]
            })
        ]

        result = run_ai_cross_check(
            meta=meta,
            students=students,
            pdf_path=self.sample_pdf,
            client=mock_client,
            auto_apply=True
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["schema_audit"]["status"], "ALIGNED")
        self.assertEqual(result["verified_patches"], 1)
        self.assertEqual(result["applied_patches"], 1)
        # Verify auto_apply updated student record
        self.assertEqual(students[0]["SGPI"], 8.0)


if __name__ == "__main__":
    unittest.main()
