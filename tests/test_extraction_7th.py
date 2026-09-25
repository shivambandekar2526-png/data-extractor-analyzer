import os
import sys

# Ensure data_extractor_AI is in sys.path
sys.path.insert(0, os.path.abspath("data_extractor_AI"))

from core.checksum_verifier import verify_batch
from core.extractor import extract_pdf
from core.ollama_client import OllamaClient
from exporter import export_ai_extracted_data

audit_events = []

def audit_logger(entry):
    audit_events.append(entry)
    ts = entry.get("timestamp", "")
    badge = entry.get("badge", "")
    title = entry.get("title", "")
    print(f"[{ts}] [{badge}] {title}")
    if entry.get("type") in ("ai_response", "ai_prompt") and entry.get("details"):
        d = entry["details"]
        if "duration_sec" in d:
            print(f"      --> Latency: {d['duration_sec']}s | Extracted: {d.get('extracted_count')} students")

print("--- STARTING REAL EXTRACTION TEST ON 2122-7th.pdf ---")
client = OllamaClient()
print("Ollama online check:", client.is_available())

meta, students, archetype = extract_pdf(
    pdf_path="Batch Data/21-22 data/2122-7th.pdf",
    progress_callback=lambda pct, msg: print(f"Progress: {pct:.1f}% - {msg}"),
    client=client,
    audit_callback=audit_logger
)

print("\n--- RESULTS ---")
print(f"Archetype: {archetype}")
print(f"Total students: {len(students)}")
print(f"Total courses in catalog: {len(meta.catalog.courses)}")
print(f"Total audit events emitted: {len(audit_events)}")

chk = verify_batch(students, meta.catalog)
print(f"Checksum verification: {chk.get('pass_percentage')}% passed ({chk.get('all_passed')}/{chk.get('total_verified')})")

if students:
    s0 = students[0]
    print(f"\nStudent 0 Details:")
    print(f"  Seat: {s0.seat_no}")
    print(f"  Name: {s0.student_name}")
    print(f"  SGPI: {s0.sgpi}")
    print(f"  Total Marks: {s0.total_marks}")
    print(f"  Course Results Count: {len(s0.course_results)}")
    for cr in s0.course_results:
        print(f"    {cr.course_code} - {cr.course_name}: {cr.marks} (Gr: {cr.grade}, GP: {cr.grade_point}, CR: {cr.credits}, CG: {cr.credit_points})")

des_dir, csv_file = export_ai_extracted_data(
    meta=meta,
    students=students,
    input_pdf_path="Batch Data/21-22 data/2122-7th.pdf",
    base_output_dir="data_extractor_AI/output",
    archetype=archetype,
    checksum_data=chk
)
print(f"\nExport directory: {des_dir}")
print(f"CSV file: {csv_file}")
