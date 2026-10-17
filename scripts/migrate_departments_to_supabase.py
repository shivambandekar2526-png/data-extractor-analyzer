"""
Automated Migration Script: departments_data -> Supabase Cloud
==============================================================
Scans local departments_data/ (PDFs, Excel files, and SQLite catalog)
and migrates all files directly into the Supabase Storage bucket
and catalog database.
"""

import os
import sys
import json
import sqlite3
import argparse
from typing import Dict, List, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.supabase_service import get_supabase_service


def migrate_all_to_supabase(dry_run: bool = False):
    print("=" * 70)
    print("MIGRATION: departments_data -> Supabase Cloud Storage")
    print("=" * 70)

    svc = get_supabase_service()
    if not svc.is_configured:
        print("[ERROR] Supabase is not configured. Check .env or config/supabase_config.json")
        return False

    dept_dir = os.path.join(PROJECT_ROOT, "departments_data")
    if not os.path.isdir(dept_dir):
        print(f"[ERROR] Source departments_data directory not found at: {dept_dir}")
        return False

    svc.ensure_bucket_exists()

    # 1. Read existing SQLite records if available
    db_path = os.path.join(dept_dir, "academic_records.db")
    db_metadata: Dict[str, Dict[str, Any]] = {}
    if os.path.isfile(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT department, batch, semester, total_students, meta_json FROM datasets;")
            for row in cur.fetchall():
                key = f"{row['department']}/{row['batch']}/{row['semester']}".lower()
                meta_parsed = {}
                if row['meta_json']:
                    try:
                        meta_parsed = json.loads(row['meta_json'])
                    except Exception:
                        pass
                db_metadata[key] = {
                    "department": row["department"],
                    "batch": row["batch"],
                    "semester": row["semester"],
                    "total_students": row["total_students"] or 0,
                    "meta_json": meta_parsed
                }
            conn.close()
            print(f"[INFO] Loaded {len(db_metadata)} dataset metadata entries from academic_records.db")
        except Exception as e:
            print(f"[WARN] Could not inspect academic_records.db: {e}")

    # 2. Upload Excel files
    excel_dir = os.path.join(dept_dir, "excel")
    excel_uploads = []
    if os.path.isdir(excel_dir):
        for root, _, files in os.walk(excel_dir):
            for f in files:
                if not (f.endswith(".xlsx") or f.endswith(".xls")) or f.startswith("~$"):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, excel_dir).replace("\\", "/")
                remote_path = f"excel/{rel_path}"
                excel_uploads.append((full_path, remote_path, rel_path))

    print(f"\n[INFO] Found {len(excel_uploads)} Excel files to migrate.")

    # 3. Upload PDF files
    pdf_dir = os.path.join(dept_dir, "pdf")
    pdf_uploads = []
    if os.path.isdir(pdf_dir):
        for root, _, files in os.walk(pdf_dir):
            for f in files:
                if not f.endswith(".pdf") or f.startswith("."):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, pdf_dir).replace("\\", "/")
                remote_path = f"pdf/{rel_path}"
                pdf_uploads.append((full_path, remote_path, rel_path))

    print(f"[INFO] Found {len(pdf_uploads)} PDF files to migrate.")

    if dry_run:
        print("\n[DRY RUN] No files were uploaded.")
        return True

    # Perform actual uploads
    success_count = 0
    fail_count = 0

    print("\n---> Uploading Excel Files...")
    for local_p, remote_p, rel_p in excel_uploads:
        file_size_kb = os.path.getsize(local_p) / 1024
        print(f"  Uploading {remote_p} ({file_size_kb:.1f} KB)...", end=" ", flush=True)
        if svc.upload_local_file(local_p, remote_p):
            print("[OK]")
            success_count += 1
        else:
            print("[FAILED]")
            fail_count += 1

    print("\n---> Uploading PDF Archives...")
    for local_p, remote_p, rel_p in pdf_uploads:
        file_size_kb = os.path.getsize(local_p) / 1024
        print(f"  Uploading {remote_p} ({file_size_kb:.1f} KB)...", end=" ", flush=True)
        if svc.upload_local_file(local_p, remote_p):
            print("[OK]")
            success_count += 1
        else:
            print("[FAILED]")
            fail_count += 1

    # 4. Upsert dataset records into Supabase catalog table
    print("\n---> Updating Supabase Dataset Catalog...")
    catalog_count = 0
    for local_p, remote_p, rel_p in excel_uploads:
        parts = rel_p.split("/")
        if len(parts) >= 3:
            dept, batch, filename = parts[0], parts[1], parts[2]
            sem = os.path.splitext(filename)[0]
            lookup_key = f"{dept}/{batch}/{sem}".lower()
            meta_entry = db_metadata.get(lookup_key, {})
            matching_pdf_rel = f"pdf/{dept}/{batch}/{sem}.pdf"

            svc.upsert_dataset_record(
                department=dept,
                batch=batch,
                semester=sem,
                excel_path=remote_p,
                pdf_path=matching_pdf_rel,
                total_students=meta_entry.get("total_students", 0),
                meta_json=meta_entry.get("meta_json", {})
            )
            catalog_count += 1

    print(f"\n" + "=" * 70)
    print(f"MIGRATION COMPLETE:")
    print(f"  Total Uploaded: {success_count} files")
    print(f"  Failed:         {fail_count} files")
    print(f"  Catalog Rows:   {catalog_count} datasets registered")
    print("=" * 70)
    return fail_count == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate departments_data to Supabase Cloud")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without uploading")
    args = parser.parse_args()

    success = migrate_all_to_supabase(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
