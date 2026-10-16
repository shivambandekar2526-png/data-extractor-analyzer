import os
import re
import time
import uuid
import threading
import logging
from typing import Dict, Any, List, Optional
from flask import Blueprint, request, jsonify, send_file, send_from_directory
from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import DEPARTMENTS_DATA_DIR, OUTPUT_DIR, INPUT_DIR, APP_DEPARTMENTS_DATA_DIR
from services.state import (
    batch_jobs, batch_jobs_lock, batch_analyzer
)
from services.batch_service import (
    _run_batch_analysis_worker, build_batch_analysis_excel_hierarchy
)
from services.academic_service import get_dept_excel_base_dir
from core.batch_analyzer import normalize_batch_slug
from core.pdf_classifier import normalize_department, normalize_batch
from core.excel_csv_ingester import sanitize_dict_for_json

batch_bp = Blueprint("batch_api", __name__)

@batch_bp.route("/api/batches", methods=["GET"])
def list_batches():
    """List batch datasets from departments_data/ including PDF, Excel, and CSV with structured hierarchy and backward-compatible batches."""
    batches = {}
    structured = {
        "pdf": {},
        "excel": {}
    }

    if os.path.isdir(DEPARTMENTS_DATA_DIR):
        for root, dirs, files in os.walk(DEPARTMENTS_DATA_DIR):
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext in (".pdf", ".xlsx", ".xls", ".csv"):
                    rel = os.path.relpath(os.path.join(root, f), DEPARTMENTS_DATA_DIR).replace("\\", "/")
                    parts = rel.split("/")
                    
                    # If organized as pdf/<Dept>/<Batch>/<Sem>.pdf or excel/<Dept>/<Batch>/<Sem>.xlsx
                    if len(parts) >= 4 and parts[0] in ("pdf", "excel"):
                        format_type = parts[0]  # "pdf" or "excel"
                        group_type = parts[0].upper()
                        dept = parts[1]
                        batch = parts[2]
                        sem_file = parts[3]
                        sem_name = os.path.splitext(sem_file)[0]
                        dept_label = dept.replace("_", " ")

                        # Flat groups for backward compatibility
                        group_name = f"📁 {dept_label} ({batch}) - {group_type}"
                        item_meta = {
                            "rel_path": rel,
                            "filename": f"{sem_file} ({group_type})",
                            "display_name": f"{sem_name.replace('_', ' ')} ({sem_file})",
                            "file_type": "pdf" if ext == ".pdf" else "excel",
                            "full_path": os.path.join(root, f),
                            "department": dept,
                            "department_label": dept_label,
                            "batch": batch,
                            "semester": sem_name
                        }
                        batches.setdefault(group_name, []).append(item_meta)

                        # Structured hierarchy: format -> dept -> batch -> files
                        structured[format_type].setdefault(dept, {
                            "department": dept,
                            "label": dept_label,
                            "batches": {}
                        })
                        structured[format_type][dept]["batches"].setdefault(batch, []).append({
                            "rel_path": rel,
                            "filename": sem_file,
                            "display_name": f"{sem_name.replace('_', ' ')} ({sem_file})",
                            "semester": sem_name,
                            "batch": batch,
                            "department": dept,
                            "file_type": "pdf" if ext == ".pdf" else "excel"
                        })
                    else:
                        batch_name = parts[0] if len(parts) > 1 else "Root Files"
                        fmt = "pdf" if ext == ".pdf" else "excel"
                        item_meta = {
                            "rel_path": rel,
                            "filename": f,
                            "display_name": f,
                            "file_type": fmt,
                            "full_path": os.path.join(root, f)
                        }
                        batches.setdefault(batch_name, []).append(item_meta)
                        structured[fmt].setdefault("Other", {
                            "department": "Other",
                            "label": "Other Files",
                            "batches": {}
                        })
                        structured[fmt]["Other"]["batches"].setdefault("Default", []).append(item_meta)

    return jsonify({
        "batches": batches,
        "structured": structured
    })




@batch_bp.route("/api/batch-analysis/hierarchy", methods=["GET"])
def get_batch_analysis_hierarchy():
    """Return discovered hierarchical directory structure from departments_data/excel (Branch -> Academic Year -> Semester Excel Sheets)."""
    try:
        data = build_batch_analysis_excel_hierarchy()
        return jsonify({"success": True, **data})
    except Exception as e:
        logger.error(f"Error discovering batch hierarchy: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@batch_bp.route("/api/batch-analysis/batches", methods=["GET"])
def get_batch_analysis_batches():
    """List all available batch folders with hierarchical and flat views from excel store."""
    try:
        data = build_batch_analysis_excel_hierarchy()
        return jsonify({
            "success": True,
            "hierarchy": data.get("branches", []),
            "branches": data.get("branches", []),
            "departments": data.get("departments", []),
            "batches": data.get("batches", [])
        })
    except Exception as e:
        logger.error(f"Error fetching batch list: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@batch_bp.route("/api/batch-analysis/inspect", methods=["POST"])
def inspect_batch():
    """Inspect a specific batch folder or branch/year selection across excel stores."""
    data = request.get_json(silent=True) or {}
    batch_name = data.get("batch_name", "").strip()
    branch = data.get("branch", "").strip()
    academic_year = data.get("academic_year", "").strip()
    semester = data.get("semester", "all")
    target_branch = data.get("target_branch", branch or "AIDS").strip()

    if not batch_name and branch and academic_year:
        batch_name = f"{branch}/{academic_year}"

    clean_batch_name = batch_name.replace("\\", "/").strip("/")
    slug = normalize_batch_slug(clean_batch_name)
    norm_branch = normalize_department(branch) if branch else ""
    norm_yr = normalize_batch(academic_year) if academic_year else ""
    if not norm_branch and "/" in clean_batch_name:
        p_b, p_y = clean_batch_name.split("/", 1)
        norm_branch = normalize_department(p_b)
        norm_yr = normalize_batch(p_y)

    excel_base = get_dept_excel_base_dir()
    candidates = [
        os.path.join(excel_base, branch, academic_year) if branch and academic_year else "",
        os.path.join(excel_base, clean_batch_name),
        os.path.join(excel_base, norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(excel_base, norm_branch, academic_year) if norm_branch and academic_year else "",
        os.path.join(excel_base, branch, norm_yr) if branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", branch, academic_year) if branch and academic_year else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", clean_batch_name),
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", branch, academic_year) if branch and academic_year else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, "pdf", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "pdf", clean_batch_name),
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "pdf", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "pdf", clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, branch, academic_year) if branch and academic_year else "",
        os.path.join(DEPARTMENTS_DATA_DIR, slug),
        os.path.join(DEPARTMENTS_DATA_DIR, batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, f"{slug} data"),
        os.path.join(INPUT_DIR, batch_name)
    ]
    folder_path = None
    for cand in candidates:
        if cand and os.path.isdir(cand):
            folder_path = cand
            break

    if not folder_path:
        return jsonify({"success": False, "error": f"Batch folder '{batch_name}' not found."}), 404

    try:
        info = batch_analyzer.detect_batch_files(folder_path, target_branch=target_branch)
        sel_sem = None
        if semester and str(semester).lower() != "all":
            try:
                m = re.search(r"\d+", str(semester))
                if m:
                    sel_sem = int(m.group(0))
            except Exception:
                sel_sem = None

        return jsonify(sanitize_dict_for_json({
            "success": True,
            **info,
            "selected_semester": sel_sem or "all",
            "branch": branch or info.get("branch"),
            "academic_year": academic_year or info.get("academic_year")
        }))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@batch_bp.route("/api/batch-analysis/run", methods=["POST"])
def run_batch_analysis():
    """Start asynchronous multi-semester batch analysis."""
    data = request.get_json(silent=True) or {}
    batch_name = data.get("batch_name", "").strip()
    branch = data.get("branch", "").strip()
    academic_year = data.get("academic_year", "").strip()
    semester = data.get("semester", "all")
    target_branch = data.get("target_branch", branch or "AIDS").strip()
    pdf_pipeline = data.get("pdf_pipeline", "local").strip()
    excel_mode = data.get("excel_mode", "direct").strip()

    if not batch_name and branch and academic_year:
        batch_name = f"{branch}/{academic_year}"

    clean_batch_name = batch_name.replace("\\", "/").strip("/")
    slug = normalize_batch_slug(clean_batch_name)
    norm_branch = normalize_department(branch) if branch else ""
    norm_yr = normalize_batch(academic_year) if academic_year else ""
    if not norm_branch and "/" in clean_batch_name:
        p_b, p_y = clean_batch_name.split("/", 1)
        norm_branch = normalize_department(p_b)
        norm_yr = normalize_batch(p_y)

    excel_base = get_dept_excel_base_dir()
    candidates = [
        os.path.join(excel_base, branch, academic_year) if branch and academic_year else "",
        os.path.join(excel_base, clean_batch_name),
        os.path.join(excel_base, norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(excel_base, norm_branch, academic_year) if norm_branch and academic_year else "",
        os.path.join(excel_base, branch, norm_yr) if branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", branch, academic_year) if branch and academic_year else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "excel", clean_batch_name),
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", branch, academic_year) if branch and academic_year else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "excel", clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, "pdf", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(DEPARTMENTS_DATA_DIR, "pdf", clean_batch_name),
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "pdf", norm_branch, norm_yr) if norm_branch and norm_yr else "",
        os.path.join(APP_DEPARTMENTS_DATA_DIR, "pdf", clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, clean_batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, branch, academic_year) if branch and academic_year else "",
        os.path.join(DEPARTMENTS_DATA_DIR, slug),
        os.path.join(DEPARTMENTS_DATA_DIR, batch_name),
        os.path.join(DEPARTMENTS_DATA_DIR, f"{slug} data"),
        os.path.join(INPUT_DIR, batch_name)
    ]
    folder_path = None
    for cand in candidates:
        if cand and os.path.isdir(cand):
            folder_path = cand
            break

    if not folder_path:
        return jsonify({"success": False, "error": f"Batch folder '{batch_name}' not found."}), 404

    selected_sems = None
    if semester and str(semester).lower() != "all":
        try:
            m_s = re.search(r"\d+", str(semester))
            if m_s:
                selected_sems = [int(m_s.group(0))]
        except Exception:
            selected_sems = None

    job_id = uuid.uuid4().hex
    with batch_jobs_lock:
        batch_jobs[job_id] = {
            "job_id": job_id,
            "batch_name": clean_batch_name or batch_name,
            "branch": branch,
            "academic_year": academic_year,
            "selected_semesters": selected_sems,
            "status": "running",
            "progress": 0.0,
            "message": "Initializing batch analysis...",
            "created_at": time.time(),
            "audit_logs": [],
            "excel_mode": excel_mode,
            "pdf_pipeline": pdf_pipeline
        }

    worker_thread = threading.Thread(
        target=_run_batch_analysis_worker,
        args=(job_id, folder_path, target_branch, pdf_pipeline, excel_mode, selected_sems),
        daemon=True
    )
    worker_thread.start()

    return jsonify({"success": True, "job_id": job_id})


@batch_bp.route("/api/batch-analysis/jobs/<job_id>", methods=["GET"])
def get_batch_job_status(job_id):
    """Poll status and progress of a batch analysis job."""
    with batch_jobs_lock:
        job = batch_jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404
    return jsonify(sanitize_dict_for_json({"success": True, **job}))


@batch_bp.route("/api/batch-analysis/download/<job_id>", methods=["GET"])
def download_batch_excel(job_id):
    """Download the generated 10-sheet Academic Excel workbook."""
    with batch_jobs_lock:
        job = batch_jobs.get(job_id)

    folder_path = None
    excel_file = None

    if job and job.get("status") == "completed":
        out_folder = job.get("output_folder")
        excel_file = job.get("excel_file")
        folder_path = os.path.join(OUTPUT_DIR, out_folder)
    else:
        # Fallback to scanning OUTPUT_DIR for folder matching job_id or folder slug
        clean_id = secure_filename(job_id)
        if os.path.isdir(OUTPUT_DIR):
            for entry in os.listdir(OUTPUT_DIR):
                if entry == clean_id or entry.endswith(clean_id[:8]) or clean_id in entry:
                    cand_dir = os.path.join(OUTPUT_DIR, entry)
                    if os.path.isdir(cand_dir):
                        for f in os.listdir(cand_dir):
                            if f.endswith(".xlsx"):
                                folder_path = cand_dir
                                excel_file = f
                                break
                    if folder_path:
                        break

    if not folder_path or not excel_file or not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": "Workbook file not found."}), 404

    return send_from_directory(folder_path, excel_file, as_attachment=True)


@batch_bp.route("/api/batch-analysis/download-json/<job_id>", methods=["GET"])
def download_batch_json(job_id):
    """Download the batch analysis JSON payload."""
    with batch_jobs_lock:
        job = batch_jobs.get(job_id)

    folder_path = None
    json_file = None

    if job and job.get("status") == "completed":
        out_folder = job.get("output_folder")
        json_file = job.get("json_file")
        folder_path = os.path.join(OUTPUT_DIR, out_folder)
    else:
        clean_id = secure_filename(job_id)
        if os.path.isdir(OUTPUT_DIR):
            for entry in os.listdir(OUTPUT_DIR):
                if entry == clean_id or entry.endswith(clean_id[:8]) or clean_id in entry:
                    cand_dir = os.path.join(OUTPUT_DIR, entry)
                    if os.path.isdir(cand_dir):
                        for f in os.listdir(cand_dir):
                            if f.endswith(".json") and "analysis" in f:
                                folder_path = cand_dir
                                json_file = f
                                break
                    if folder_path:
                        break

    if not folder_path or not json_file or not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": "Analysis JSON not found."}), 404

    return send_from_directory(folder_path, json_file, as_attachment=True)


