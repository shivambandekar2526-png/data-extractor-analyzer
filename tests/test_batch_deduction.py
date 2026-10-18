import unittest
from core.pdf_classifier import (
    extract_prn_cohort_year,
    invert_academic_progression,
    normalize_batch,
    classify_pdf_first_page,
    classify_tabular_metadata,
)

class TestBatchDeduction(unittest.TestCase):
    def test_prn_cohort_extraction(self):
        sample_text = """
        UNIVERSITY OF MUMBAI - RESULT LEDGER
        SEAT NO: 101  PRN: 2022016402248621  NAME: SHARMA AAKASH
        SEAT NO: 102  PRN: 2022016402248622  NAME: PATIL SNEHA
        SEAT NO: 103  PRN: 2022016402248623  NAME: VERMA ROHIT
        SEAT NO: 104  PRN: 2023016402248650  NAME: DSE LATERAL STUDENT
        """
        cohort_year = extract_prn_cohort_year(sample_text)
        self.assertEqual(cohort_year, 2022)
        batch = normalize_batch("", exam_text=sample_text)
        self.assertEqual(batch, "22-23")

    def test_inversion_formula_summer_session(self):
        # User specified: "the 6th sem of 22-23 batch exam was held in may 2026"
        admit_year = invert_academic_progression(exam_year=2026, is_summer=True, sem_num=6)
        self.assertEqual(admit_year, 2022)

        sample_text = """
        EXAMINATION: B.TECH (COMPUTER ENGINEERING) SEMESTER VI
        EXAMINATION HELD IN: MAY 2026
        """
        batch = normalize_batch("", exam_text=sample_text, sem_num=6)
        self.assertEqual(batch, "22-23")

    def test_inversion_formula_winter_session(self):
        # 7th sem held in December 2025 -> Batch 22-23 (admitted in 2022)
        admit_year = invert_academic_progression(exam_year=2025, is_summer=False, sem_num=7)
        self.assertEqual(admit_year, 2022)

        sample_text = """
        FOURTH YEAR B.TECH SEMESTER VII (CBCS)
        HELD IN DECEMBER 2025
        """
        batch = normalize_batch("", exam_text=sample_text, sem_num=7)
        self.assertEqual(batch, "22-23")

    def test_normalize_batch_variants(self):
        self.assertEqual(normalize_batch("2022-2023"), "22-23")
        self.assertEqual(normalize_batch("2022-23"), "22-23")
        self.assertEqual(normalize_batch("22-23"), "22-23")
        self.assertEqual(normalize_batch("2022_2023"), "22-23")

if __name__ == "__main__":
    unittest.main()
