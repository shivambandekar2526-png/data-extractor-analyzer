"""
AI Cross-Check & Patch Sandbox Routes
=====================================
Live verification against original PDF tokens, sandboxed patch validation, and auto-repair.
"""

import os
import csv
import json
import time
import uuid
import threading
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.state import ai_cc_jobs, ai_cc_jobs_lock
from services.extraction_service import safe_folder_name
from services.crosscheck_service import _find_pdf_for_folder, _run_post_crosscheck_worker
from core.checksum_verifier import verify_batch
from exporter import write_csv_file, write_excel_file
from models import ExamMeta, CourseCatalog

crosscheck_bp = Blueprint("crosscheck_api", __name__)


@crosscheck_bp.route("/api/ai/cross-check/run", methods=["POST"])
def run_ai_crosscheck_endpoint():
    """Start asynchronous on-demand AI Cross-Check on an existing extracted dataset."""
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    pdf_candidate = data.get("pdf_path", "").strip()

    if not folder_name:
        return jsonify({"success": False, "error": "folder_name is required."}), 400

    safe_folder = safe_folder_name(folder_name)
    target_dir = os.path.join(OUTPUT_DIR, safe_folder)
    if not os.path.isdir(target_dir):
        return jsonify({"success": False, "error": f"Extracted output folder '{safe_folder}' not found."}), 404

    csv_path = os.path.join(target_dir, f"{safe_folder}_extracted_data.csv")
    if not os.path.isfile(csv_path):
        candidates = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.lower().endswith(".csv")]
        if candidates:
            csv_path = candidates[0]
        else:
            return jsonify({"success": False, "error": "No CSV file found in designated folder."}), 404

    # Resolve PDF path
    pdf_path = None
    if pdf_candidate and os.path.isfile(pdf_candidate):
        pdf_path = pdf_candidate
    else:
        pdf_path = _find_pdf_for_folder(safe_folder)

    if not pdf_path or not os.path.isfile(pdf_path):
        return jsonify({
            "success": False,
            "error": f"Could not find matching source PDF for '{safe_folder}'. Please ensure PDF exists in input/ or departments_data/."
        }), 404

    job_id = f"crosscheck_{uuid.uuid4().hex[:10]}"
    with ai_cc_jobs_lock:
        ai_cc_jobs[job_id] = {
            "job_id": job_id,
            "folder_name": safe_folder,
            "status": "processing",
            "progress": 0.0,
            "message": "Initializing AI Cross-Check job...",
            "discrepancies": [],
            "error": None,
            "result": None,
            "start_time": time.time(),
            "pdf_path": pdf_path,
            "csv_path": csv_path,
            "audit_logs": [],
            "token_usage": {"input": 0, "output": 0, "total": 0}
        }

    t = threading.Thread(
        target=_run_post_crosscheck_worker,
        args=(job_id, safe_folder, csv_path, pdf_path),
        daemon=True
    )
    t.start()
    return jsonify({"success": True, "job_id": job_id, "folder_name": safe_folder, "pdf_path": pdf_path})


@crosscheck_bp.route("/api/ai/cross-check/jobs/<job_id>", methods=["GET"])
def get_ai_crosscheck_job_status(job_id):
    """Poll progress, streaming discrepancies, token analytics, and results of an AI Cross-Check job."""
    with ai_cc_jobs_lock:
        job = ai_cc_jobs.get(job_id)

    if not job:
        return jsonify({"success": False, "error": f"Cross-check job '{job_id}' not found."}), 404

    return jsonify({
        "success": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "discrepancies_count": len(job.get("discrepancies", [])),
        "discrepancies": job.get("discrepancies", []),
        "result": job.get("result"),
        "error": job.get("error"),
        "audit_logs": job.get("audit_logs", []),
        "token_usage": job.get("token_usage", {"input": 0, "output": 0, "total": 0})
    })


@crosscheck_bp.route("/api/ai/cross-check/apply", methods=["POST"])
def apply_ai_crosscheck_patches():
    """Apply approved AI cross-check patches, update CSV, and regenerate styled Excel."""
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    patches = data.get("patches", [])

    if not folder_name:
        return jsonify({"success": False, "error": "folder_name is required."}), 400

    if not patches:
        return jsonify({"success": False, "error": "No patches provided to apply."}), 400

    safe_folder = safe_folder_name(folder_name)
    target_dir = os.path.join(OUTPUT_DIR, safe_folder)
    if not os.path.isdir(target_dir):
        return jsonify({"success": False, "error": "Target folder not found."}), 404

    csv_path = os.path.join(target_dir, f"{safe_folder}_extracted_data.csv")
    if not os.path.isfile(csv_path):
        candidates = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.lower().endswith(".csv")]
        if candidates:
            csv_path = candidates[0]
        else:
            return jsonify({"success": False, "error": "Target CSV file not found."}), 404

    # Load existing CSV
    students = []
    fieldnames = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            students.append(dict(row))

    students_by_seat = {str(s.get("Seat_No", s.get("seat_no", ""))): s for s in students}

    # Apply patches
    applied_count = 0
    applied_audit = []
    for patch in patches:
        seat_no = str(patch.get("seat_no", "")).strip()
        field_name = str(patch.get("field", patch.get("field_name", ""))).strip()
        new_val = patch.get("suggested_val")

        st = students_by_seat.get(seat_no)
        if st is not None and field_name:
            st[field_name] = new_val
            applied_count += 1
            applied_audit.append(patch)

    # Re-write updated CSV
    write_csv_file(students, fieldnames, csv_path)

    # Load metadata and catalog if present
    catalog_path = os.path.join(target_dir, f"{safe_folder}_catalog.json")
    meta = ExamMeta(exam_title=safe_folder, semester="")
    if os.path.isfile(catalog_path):
        try:
            with open(catalog_path, mode="r", encoding="utf-8") as f:
                cat_data = json.load(f)
            meta.exam_title = cat_data.get("exam_title", safe_folder)
            meta.college_name = cat_data.get("college_name", "")
            meta.semester = cat_data.get("semester", "")
            meta.result_date = cat_data.get("result_date", "")
            meta.archetype = cat_data.get("archetype", "")
            if "courses" in cat_data:
                cat_obj = CourseCatalog()
                for c in cat_data["courses"]:
                    cat_obj.add_course(c.get("code", ""), c.get("name", ""), c.get("heads", []))
                meta.catalog = cat_obj
        except Exception:
            pass

    # Re-verify checksums
    verification_summary = {}
    try:
        verification_summary = verify_batch(students, meta.catalog)
    except Exception:
        pass

    # Re-generate styled Excel file
    excel_path = os.path.join(target_dir, f"{safe_folder}_extracted_data.xlsx")
    write_excel_file(
        data=students,
        fieldnames=fieldnames,
        catalog=meta.catalog,
        meta=meta,
        output_path=excel_path
    )

    # Update catalog JSON with new checksum data
    if os.path.isfile(catalog_path):
        try:
            with open(catalog_path, mode="r", encoding="utf-8") as f:
                cat_data = json.load(f)
            cat_data["checksum_verification"] = verification_summary
            with open(catalog_path, mode="w", encoding="utf-8") as f:
                json.dump(cat_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # Save applied patches audit log
    audit_log_path = os.path.join(target_dir, f"{safe_folder}_crosscheck_audit.json")
    try:
        existing_audit = {}
        if os.path.isfile(audit_log_path):
            with open(audit_log_path, mode="r", encoding="utf-8") as f:
                existing_audit = json.load(f)
        existing_audit["last_applied_patches"] = applied_audit
        existing_audit["last_applied_timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(audit_log_path, mode="w", encoding="utf-8") as f:
            json.dump(existing_audit, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    return jsonify({
        "success": True,
        "applied_count": applied_count,
        "message": f"Successfully applied {applied_count} patches and regenerated CSV & Excel files.",
        "folder_name": safe_folder
    })
