import os
import re
import time
import uuid
import threading
import logging
import fitz
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import INPUT_DIR, OUTPUT_DIR, KB_DIR, PROJECT_ROOT, DEPARTMENTS_DATA_DIR
from services.state import (
    jobs, jobs_lock, job_controllers, job_controllers_lock,
    tabular_ingester
)
from services.extraction_service import _run_ai_extraction_worker
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient
from core.execution_controller import ExtractionController
from core.pdf_classifier import (
    classify_pdf_first_page, check_duplicate_dataset, store_classified_pdf,
    get_canonical_excel_path, normalize_department, normalize_batch, normalize_semester
)

extract_bp = Blueprint("extract_api", __name__)

@extract_bp.route("/api/status", methods=["GET"])
def get_ai_status():
    """Check Ollama availability, active model, Cloud AI readiness, and knowledge base."""
    client = OllamaClient()
    ollama_ok = client.is_available()
    models = client.list_models() if ollama_ok else []

    cloud_client = CloudClient()
    cloud_info = cloud_client.get_safe_config()

    kb_ready = os.path.isfile(os.path.join(KB_DIR, "archetypes.json"))
    ex_count = len(os.listdir(os.path.join(KB_DIR, "exemplars"))) if os.path.isdir(os.path.join(KB_DIR, "exemplars")) else 0

    return jsonify({
        "ollama_available": ollama_ok,
        "active_model": client.model,
        "available_models": models,
        "cloud_ai": cloud_info,
        "knowledge_base_ready": kb_ready,
        "exemplars_count": ex_count,
        "port": 5001
    })


@extract_bp.route("/api/inputs", methods=["GET"])
def list_inputs():
    """List uploaded PDFs in input/."""
    files = []
    if os.path.isdir(INPUT_DIR):
        for f in sorted(os.listdir(INPUT_DIR)):
            if f.lower().endswith(".pdf"):
                fpath = os.path.join(INPUT_DIR, f)
                files.append({
                    "filename": f,
                    "size_bytes": os.path.getsize(fpath),
                    "modified": os.path.getmtime(fpath)
                })
    return jsonify({"files": files})



@extract_bp.route("/api/extract", methods=["POST"])
def start_extraction():
    """Trigger AI extraction job."""
    target_pdf_path = None
    target_tabular_path = None

    # Check for direct file uploads across all keys
    keys_to_check = list(request.files.keys()) + ["pdf_file", "file", "excel_file", "csv_file", "tabular_file"]
    for key in keys_to_check:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                if not safe_name or not os.path.splitext(safe_name)[1]:
                    _, orig_ext = os.path.splitext(up.filename)
                    safe_name = f"uploaded_file_{int(time.time())}{orig_ext if orig_ext else '.xlsx'}"
                ext = os.path.splitext(safe_name)[1].lower()
                if ext not in (".pdf", ".xlsx", ".xls", ".csv"):
                    return jsonify({"success": False, "error": "Supported formats: .pdf, .xlsx, .xls, .csv"}), 400
                os.makedirs(INPUT_DIR, exist_ok=True)
                saved_path = os.path.join(INPUT_DIR, safe_name)
                up.save(saved_path)
                if ext == ".pdf":
                    target_pdf_path = saved_path
                else:
                    target_tabular_path = saved_path
                break

    json_data = request.get_json(silent=True) or {}

    if not target_pdf_path and not target_tabular_path:
        existing = request.form.get("input_file") or request.form.get("existing_input") or json_data.get("input_file")
        if existing:
            safe_name = secure_filename(existing)
            ext = os.path.splitext(safe_name)[1].lower()
            cand = os.path.join(INPUT_DIR, safe_name)
            if os.path.isfile(cand):
                if ext == ".pdf":
                    target_pdf_path = cand
                elif ext in (".xlsx", ".xls", ".csv"):
                    target_tabular_path = cand

    pipeline_mode = (
        request.form.get("pipeline_mode")
        or json_data.get("pipeline_mode")
        or request.args.get("pipeline_mode")
        or "local"
    )

    if target_tabular_path:
        # Route tabular file according to formatting inspection or explicit pipeline
        sheet_name = request.form.get("sheet_name") or json_data.get("sheet_name")
        insp = tabular_ingester.inspect_file(target_tabular_path, sheet_name=sheet_name)
        if insp.get("is_clean") and pipeline_mode not in ("cloud", "visual_blueprint"):
            res = tabular_ingester.direct_import(target_tabular_path, sheet_name=sheet_name)
        else:
            oc = OllamaClient()
            cc = CloudClient()
            res = tabular_ingester.ai_fix_and_import(
                target_tabular_path,
                sheet_name=sheet_name,
                pipeline_mode=pipeline_mode,
                ollama_client=oc,
                cloud_client=cc
            )
        return jsonify(res)

    if not target_pdf_path and not target_tabular_path:
        batch_rel = request.form.get("batch_file") or json_data.get("batch_file") or json_data.get("pdf_path")
        if batch_rel:
            clean_rel = batch_rel.replace("\\", "/").strip().lstrip("/")
            b_ext = os.path.splitext(clean_rel)[1].lower()
            if b_ext in (".xlsx", ".xls", ".csv"):
                resolved = resolve_tabular_file_path(batch_rel)
                if resolved:
                    target_tabular_path = resolved[0]
                    sheet_name = request.form.get("sheet_name") or json_data.get("sheet_name")
                    insp = tabular_ingester.inspect_file(target_tabular_path, sheet_name=sheet_name)
                    if insp.get("is_clean") and pipeline_mode not in ("cloud", "visual_blueprint"):
                        res = tabular_ingester.direct_import(target_tabular_path, sheet_name=sheet_name)
                    else:
                        oc = OllamaClient()
                        cc = CloudClient()
                        res = tabular_ingester.ai_fix_and_import(
                            target_tabular_path,
                            sheet_name=sheet_name,
                            pipeline_mode=pipeline_mode,
                            ollama_client=oc,
                            cloud_client=cc
                        )
                    return jsonify(res)

            if clean_rel.lower().startswith("departments data/") or clean_rel.lower().startswith("departments_data/"):
                clean_rel = clean_rel.split("/", 1)[1].lstrip("/")
            elif clean_rel.lower().startswith("batch data/") or clean_rel.lower().startswith("batch_data/"):
                clean_rel = clean_rel.split("/", 1)[1].lstrip("/")
            candidate_dirs = [
                DEPARTMENTS_DATA_DIR,
                os.path.join(DEPARTMENTS_DATA_DIR, "pdf"),
                os.path.join(PROJECT_ROOT, "Batch Data")
            ]
            for base_dir in candidate_dirs:
                if not os.path.isdir(base_dir):
                    continue
                cand = os.path.abspath(os.path.join(base_dir, clean_rel))
                if os.path.isfile(cand):
                    target_pdf_path = cand
                    break

            if not target_pdf_path:
                try:
                    from services.supabase_service import get_supabase_service
                    svc = get_supabase_service()
                    if svc.is_configured:
                        remote_pdf_cand = f"pdf/{clean_rel}" if not clean_rel.startswith("pdf/") else clean_rel
                        pdf_bytes = svc.download_file_bytes(remote_pdf_cand)
                        if pdf_bytes:
                            import tempfile
                            tmp_pdf = os.path.join(tempfile.gettempdir(), os.path.basename(clean_rel))
                            with open(tmp_pdf, "wb") as pf:
                                pf.write(pdf_bytes)
                            target_pdf_path = tmp_pdf
                except Exception as e:
                    logger.warning(f"Error fetching PDF from Supabase in extract_api: {e}")

    if not target_pdf_path or not os.path.isfile(target_pdf_path):
        return jsonify({"success": False, "error": "No valid PDF document or tabular file provided or found."}), 400

    if os.path.getsize(target_pdf_path) == 0:
        return jsonify({"success": False, "error": "PDF file is empty (0 bytes)."}), 400

    try:
        import fitz
        test_doc = fitz.open(target_pdf_path)
        if len(test_doc) == 0:
            test_doc.close()
            return jsonify({"success": False, "error": "PDF file contains 0 pages or is empty."}), 400
        test_doc.close()
    except Exception:
        return jsonify({"success": False, "error": "Malformed or corrupted PDF file header."}), 400

    # 1. Run single Vision AI classification step on Page 1 if dataset metadata not already provided
    overwrite = (
        request.form.get("overwrite") in ("1", "true", "yes")
        or request.args.get("overwrite") in ("1", "true", "yes")
        or json_data.get("overwrite") in (True, "1", 1)
    )

    dept = request.form.get("department") or json_data.get("department")
    batch = request.form.get("batch") or json_data.get("batch")
    sem = request.form.get("semester") or json_data.get("semester")

    if not (dept and batch and sem):
        try:
            clf_res = classify_pdf_first_page(target_pdf_path)
            dept = dept or clf_res["department"]
            batch = batch or clf_res["batch"]
            sem = sem or clf_res["semester"]
        except Exception as clf_err:
            logger.warning(f"Classification warning: {clf_err}")
            dept = dept or "Artificial_Intelligence_and_Data_Science"
            batch = batch or "2223"
            sem = sem or "Semester_1"

    # Normalize canonical names
    dept = normalize_department(dept)
    sem = normalize_semester(sem)
    sem_num_val = int(re.search(r"\d+", sem).group(0)) if re.search(r"\d+", sem) else 1
    batch = normalize_batch(batch, sem_num=sem_num_val)

    # 2. Duplicate Detection: Check if <Department>/<Batch>/<Semester> already exists (for direct uploads)
    is_direct_upload = bool(not request.form.get("batch_file") and not json_data.get("batch_file"))
    if is_direct_upload:
        dup_info = check_duplicate_dataset(dept, batch, sem, DEPARTMENTS_DATA_DIR)
        if dup_info["duplicate"] and not overwrite:
            return jsonify({
                "success": False,
                "duplicate": True,
                "department": dept,
                "batch": batch,
                "semester": sem,
                "pdf_exists": dup_info["pdf_exists"],
                "excel_exists": dup_info["excel_exists"],
                "message": f"Dataset for '{dept} / {batch} / {sem}' already exists. Please confirm replacement."
            }), 409

    should_archive_pdf = (
        request.form.get("archive_pdf") in ("1", "true", "yes")
        or request.args.get("archive_pdf") in ("1", "true", "yes")
        or json_data.get("archive_pdf") in (True, "1", 1)
    )

    # 3. Add to Supabase Storage directly (or local departments_data/pdf if Supabase unconfigured)
    canonical_excel = get_canonical_excel_path(dept, batch, sem, DEPARTMENTS_DATA_DIR)
    canonical_pdf = os.path.join(DEPARTMENTS_DATA_DIR, "pdf", dept, batch, f"{sem}.pdf").replace("\\", "/")
    pdf_url = ""

    try:
        from services.supabase_service import get_supabase_service
        svc = get_supabase_service()
        if svc.is_configured:
            remote_pdf = f"pdf/{dept}/{batch}/{sem}.pdf"
            # Only upload if this is a direct user file upload (not already selected from Department Library)
            if is_direct_upload and target_pdf_path and os.path.isfile(target_pdf_path):
                logger.info(f"[ExtractAPI] Archiving uploaded PDF directly to Supabase Storage: {remote_pdf}")
                svc.upload_local_file(target_pdf_path, remote_pdf)
                svc.upsert_dataset_record(
                    department=dept,
                    batch=batch,
                    semester=sem,
                    excel_path=None,
                    pdf_path=remote_pdf
                )
                canonical_pdf = remote_pdf
            else:
                # If chosen from library, it is already in Supabase Storage
                canonical_pdf = remote_pdf

            pdf_url = svc.get_public_url(remote_pdf) or svc.get_signed_url(remote_pdf) or ""
    except Exception as sup_err:
        logger.warning(f"Could not auto-archive PDF to Supabase: {sup_err}")

    if should_archive_pdf and not (canonical_pdf and canonical_pdf.startswith("pdf/")):
        try:
            stored_pdf = store_classified_pdf(
                target_pdf_path, dept, batch, sem, DEPARTMENTS_DATA_DIR, overwrite=True
            )
            canonical_pdf = stored_pdf
            target_pdf_path = canonical_pdf
        except Exception as store_err:
            logger.warning(f"Could not store classified PDF to departments_data: {store_err}")

    dataset_meta = {
        "department": dept,
        "batch": batch,
        "semester": sem,
        "pdf_path": canonical_pdf,
        "excel_path": canonical_excel,
        "pdf_url": pdf_url
    }

    job_id = str(uuid.uuid4())
    ctrl = ExtractionController(job_id)
    with job_controllers_lock:
        job_controllers[job_id] = ctrl

    with jobs_lock:
        jobs[job_id] = {
            "status": "processing",
            "progress": 0.0,
            "message": "Initializing AI extraction...",
            "created_at": time.time(),
            "audit_logs": [],
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "live_students": [],
            "catalog": [],
            "dataset_meta": dataset_meta,
            "is_paused": False,
            "is_stopped": False,
            "result": None,
            "error": None
        }

    pipeline_mode = (
        request.form.get("pipeline_mode")
        or json_data.get("pipeline_mode")
        or request.args.get("pipeline_mode")
    )
    cloud_crosscheck = (
        request.form.get("cloud_crosscheck", "0") in ("1", "true", "yes")
        or request.args.get("cloud_crosscheck", "0") in ("1", "true", "yes")
        or bool(json_data.get("cloud_crosscheck"))
    )
    if not pipeline_mode:
        if cloud_crosscheck:
            pipeline_mode = "cloud"
        else:
            pipeline_mode = "local"

    is_sync = (
        request.args.get("sync", "0") in ("1", "true")
        or request.form.get("sync", "0") in ("1", "true")
        or bool(json_data.get("sync"))
    )

    if is_sync:
        _run_ai_extraction_worker(job_id, target_pdf_path, cloud_crosscheck, pipeline_mode=pipeline_mode)
        res = jobs.get(job_id, {})
        if res.get("status") == "completed":
            return jsonify(res["result"])
        return jsonify({"success": False, "error": res.get("error", "Unknown error")}), 500

    t = threading.Thread(
        target=_run_ai_extraction_worker,
        args=(job_id, target_pdf_path, cloud_crosscheck, pipeline_mode),
        daemon=True
    )
    t.start()
    return jsonify({"success": True, "job_id": job_id, "dataset_meta": dataset_meta})


@extract_bp.route("/api/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id):
    """Poll job progress, live streaming student records, and control status."""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404

    with job_controllers_lock:
        ctrl = job_controllers.get(job_id)

    resp = dict(job)
    if ctrl:
        resp["is_paused"] = ctrl.is_paused
        resp["is_stopped"] = ctrl.is_stopped
    resp["live_count"] = len(job.get("live_students", []))
    resp["current_page"] = job.get("current_page")
    resp["total_pages"] = job.get("total_pages")
    resp["pages_processed"] = job.get("pages_processed")
    resp["pages_remaining"] = job.get("pages_remaining")
    return jsonify(resp)


@extract_bp.route("/api/jobs/<job_id>/pause", methods=["POST"])
def pause_extraction_job(job_id):
    """Pause background extraction job."""
    with job_controllers_lock:
        ctrl = job_controllers.get(job_id)
    if not ctrl:
        return jsonify({"success": False, "error": "Job not found or already terminated."}), 404

    ctrl.pause()
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["is_paused"] = True
            jobs[job_id]["status"] = "paused"
            jobs[job_id]["message"] = "Extraction paused by user. Inspect the live table or click Resume."
            if "audit_logs" in jobs[job_id]:
                jobs[job_id]["audit_logs"].append({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "control",
                    "badge": "PAUSED",
                    "title": "Extraction execution paused by user.",
                    "details": {"action": "pause"}
                })
    return jsonify({"success": True, "message": "Extraction paused."})


@extract_bp.route("/api/jobs/<job_id>/resume", methods=["POST"])
def resume_extraction_job(job_id):
    """Resume background extraction job."""
    with job_controllers_lock:
        ctrl = job_controllers.get(job_id)
    if not ctrl:
        return jsonify({"success": False, "error": "Job not found or already terminated."}), 404

    ctrl.resume()
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["is_paused"] = False
            jobs[job_id]["status"] = "processing"
            jobs[job_id]["message"] = "Resuming extraction..."
            if "audit_logs" in jobs[job_id]:
                jobs[job_id]["audit_logs"].append({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "control",
                    "badge": "RESUMED",
                    "title": "Extraction execution resumed by user.",
                    "details": {"action": "resume"}
                })
    return jsonify({"success": True, "message": "Extraction resumed."})


@extract_bp.route("/api/jobs/<job_id>/stop", methods=["POST"])
def stop_extraction_job(job_id):
    """Stop background extraction job and finalize whatever records are captured."""
    with job_controllers_lock:
        ctrl = job_controllers.get(job_id)
    if not ctrl:
        return jsonify({"success": False, "error": "Job not found or already terminated."}), 404

    ctrl.stop()
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["is_stopped"] = True
            jobs[job_id]["message"] = "Stopping extraction and finalizing records..."
            if "audit_logs" in jobs[job_id]:
                jobs[job_id]["audit_logs"].append({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "control",
                    "badge": "STOPPING",
                    "title": "Stop signal received. Preserving all records fetched so far...",
                    "details": {"action": "stop"}
                })
    return jsonify({"success": True, "message": "Stop requested. Finalizing..."})



