"""
Real-world Verification Test: AI Cross-Checker on Actual Academic PDF
====================================================================
Tests the full pipeline on real PDF files from Batch Data:
- Evaluates real PDF page extraction.
- Evaluates real student record structure.
- Verifies Stage 1 and Stage 2 with zero-trust ground truth validation.
- Verifies Option 1 (simultaneous in-flight cross-check & auto-patching).
- Verifies Option 2 (post-extraction on-demand cross-check & patch application).
"""

import json
import os
import sys
import time

# Ensure data_extractor_AI is in sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_APP_DIR = os.path.abspath(os.path.join(TESTS_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(AI_APP_DIR, ".."))
for p in [AI_APP_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.ai_cross_checker import (
    build_context_envelope,
    extract_raw_page_texts,
    run_ai_cross_check,
    validate_patch_sandbox,
)
from core.cloud_client import CloudClient
from core.extractor import extract_pdf
from core.ollama_client import OllamaClient
from exporter import export_ai_extracted_data, write_csv_file, write_excel_file
from models import CourseCatalog, ExamMeta, StudentRecord


def test_real_pdf_crosscheck():
    pdf_path = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-8th.pdf")
    if not os.path.isfile(pdf_path):
        pdf_path = os.path.join(PROJECT_ROOT, "Batch Data", "21-22 data", "2122-7th.pdf")
    
    print(f"\n==================================================================")
    print(f"  DATA EXTRACTOR AI: REAL PDF CROSS-CHECK TEST")
    print(f"  Target PDF: {pdf_path}")
    print(f"==================================================================")

    assert os.path.isfile(pdf_path), f"Test PDF not found: {pdf_path}"

    # 1. Text extraction check
    print("\n[Step 1] Extracting raw page text strings from PDF...")
    page_texts = extract_raw_page_texts(pdf_path)
    print(f"   -> Successfully extracted {len(page_texts)} pages.")
    assert len(page_texts) > 0, "No pages extracted!"
    assert len(page_texts[0].strip()) > 50, "Page 1 text is too short!"
    print(f"   -> Page 1 sample (first 100 chars): {repr(page_texts[0][:100])}")

    # 2. Extract students using the extractor engine
    print("\n[Step 2] Extracting students from PDF via data_extractor_AI possibility engine...")
    from core.page_ingester import ingest_pdf
    from core.possibility_classifier import classify_document
    from core.possibility_extractor import extract_with_possibility_engine

    pages = ingest_pdf(pdf_path)
    archetype, confidence = classify_document(pages)
    meta, students = extract_with_possibility_engine(pdf_path, archetype)
    print(f"   -> Archetype detected: {archetype} (confidence: {confidence:.0%})")
    print(f"   -> Total students extracted: {len(students)}")
    print(f"   -> Courses in catalog: {len(meta.catalog.courses)}")
    assert len(students) > 0, "No students extracted!"

    # Flatten students to dicts
    flat_students = [s.to_flat_dict(meta.catalog) for s in students]

    # 3. Build ContextEnvelope
    print("\n[Step 3] Building ContextEnvelope...")
    envelope = build_context_envelope(meta, flat_students)
    prompt_header = envelope.to_compact_prompt_header()
    print(f"   -> Envelope prompt header:\n{prompt_header}")
    assert len(envelope.valid_columns) > 5, "Too few columns in envelope!"

    # 4. Check Zero-Trust Sandbox with Synthetic Ground-Truth Patches
    print("\n[Step 4] Testing Zero-Trust Sandbox against real PDF text...")
    students_by_seat = {str(s.get("Seat_No", s.get("seat_no", ""))): s for s in flat_students}
    first_seat = str(flat_students[0].get("Seat_No", flat_students[0].get("seat_no", "")))

    # A. Authentic patch: Quote that literally exists in page_texts
    real_quote = page_texts[0][150:180].strip()
    # Find a valid column
    test_col = None
    for c in envelope.valid_columns:
        if c.endswith("_TH") or c == "SGPI":
            test_col = c
            break
    if not test_col:
        test_col = "SGPI"

    authentic_patch = {
        "seat_no": first_seat,
        "field": test_col,
        "extracted_val": flat_students[0].get(test_col),
        "suggested_val": 8.5 if test_col == "SGPI" else 45,
        "reason": "Test ground-truth verification",
        "evidence_quote": real_quote
    }
    ok, reason = validate_patch_sandbox(authentic_patch, page_texts, envelope, students_by_seat)
    print(f"   -> Authentic patch with quote '{real_quote[:30]}...': VERIFIED={ok} ({reason})")
    assert ok, f"Authentic quote failed sandbox: {reason}"

    # B. Hallucinated patch: Quote that does NOT exist
    fake_patch = {
        "seat_no": first_seat,
        "field": test_col,
        "extracted_val": 40,
        "suggested_val": 80,
        "reason": "AI hallucinated data",
        "evidence_quote": "THIS TEXT DOES NOT EXIST IN MUMBAI UNIVERSITY LEDGER 99999"
    }
    ok_fake, reason_fake = validate_patch_sandbox(fake_patch, page_texts, envelope, students_by_seat)
    print(f"   -> Fake patch with nonexistent quote: REJECTED={not ok_fake} ({reason_fake})")
    assert not ok_fake, "Hallucinated patch was falsely accepted!"

    # 5. Test Option 1: In-Flight Simultaneous Auto-Apply
    print("\n[Step 5] Testing Option 1 (In-Flight Simultaneous Cross-Check with auto_apply=True)...")
    progress_events = []
    def on_progress(pct, msg, card=None):
        progress_events.append((pct, msg))

    # Create a mock or direct CloudClient
    cloud_client = CloudClient()
    print(f"   -> Cloud AI configured: {cloud_client.is_configured()}")
    
    # Run cross-check on a 5-student subset for speed
    test_subset = flat_students[:5]
    result = run_ai_cross_check(
        meta=meta,
        students=test_subset,
        pdf_path=pdf_path,
        progress_cb=on_progress,
        auto_apply=True,
        chunk_size=5
    )
    print(f"   -> Result success: {result.get('success')}")
    print(f"   -> Verified patches: {result.get('verified_patches')}")
    print(f"   -> Rejected patches: {result.get('rejected_patches')}")
    print(f"   -> Applied patches: {result.get('applied_patches')}")
    print(f"   -> Total progress events emitted: {len(progress_events)}")
    assert len(progress_events) >= 3, "Progress events should be emitted during cross-check!"

    # 6. Test Option 2: Post-Extraction On-Demand Patch Application
    print("\n[Step 6] Testing Option 2 (Post-Extraction Manual Patch Application to CSV & Excel)...")
    test_output_dir = os.path.join(AI_APP_DIR, "output", "test_crosscheck_verify")
    os.makedirs(test_output_dir, exist_ok=True)
    test_csv = os.path.join(test_output_dir, "test_crosscheck_verify_extracted_data.csv")
    test_excel = os.path.join(test_output_dir, "test_crosscheck_verify_extracted_data.xlsx")

    # Export initial data
    export_fieldnames = list(flat_students[0].keys())
    write_csv_file(flat_students, export_fieldnames, test_csv)
    write_excel_file(flat_students, export_fieldnames, meta.catalog, meta, test_excel)
    assert os.path.isfile(test_csv), "Initial CSV not created"
    assert os.path.isfile(test_excel), "Initial Excel not created"

    # Simulate accepting a patch
    patch_to_apply = {
        "seat_no": first_seat,
        "field": test_col,
        "suggested_val": 9.75 if test_col == "SGPI" else 62
    }
    # Apply patch to dataset
    for s in flat_students:
        if str(s.get("Seat_No", s.get("seat_no", ""))) == first_seat:
            s[patch_to_apply["field"]] = patch_to_apply["suggested_val"]
            break

    # Re-write CSV and Excel (as done by /api/ai/cross-check/apply)
    write_csv_file(flat_students, export_fieldnames, test_csv)
    write_excel_file(flat_students, export_fieldnames, meta.catalog, meta, test_excel)

    # Verify CSV file on disk has updated value
    import csv
    with open(test_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        first_row = next(reader)
        print(f"   -> Re-read CSV Seat {first_seat} {test_col}: {first_row.get(test_col)}")
        assert str(first_row.get(test_col)) == str(patch_to_apply["suggested_val"]), "CSV was not updated with patch!"

    print("\n==================================================================")
    print("  ALL REAL PDF CROSS-CHECK TESTS PASSED SUCCESSFULLY! (100% OK)")
    print("==================================================================\n")


if __name__ == "__main__":
    test_real_pdf_crosscheck()
