"""
Data Extractor AI Web Application Instance
==========================================
Interactive Web Server & API for AI-Powered Multi-Archetype Academic Result Extraction.
Runs on Port 5001.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
import uuid
import webbrowser
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, ".."))
BATCH_DATA_DIR = os.path.join(PROJECT_ROOT, "Batch Data")
INPUT_DIR = os.path.join(APP_DIR, "input")
OUTPUT_DIR = os.path.join(APP_DIR, "output")
KB_DIR = os.path.join(APP_DIR, "knowledge_base")

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(KB_DIR, exist_ok=True)

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.chat_assistant import process_chat_command
from core.checksum_verifier import verify_batch
from core.extractor import extract_pdf
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient
from core.eda_engine import EDAEngine
from core.ai_insights import AIInsightsSynthesizer
from core.semantic_mapper import SemanticMapper
from core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
from exporter import export_ai_extracted_data, write_csv_file, write_excel_file
from models import CourseCatalog, ExamMeta, StudentRecord
from core.ai_cross_checker import run_ai_cross_check, validate_patch_sandbox

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, "templates"),
    static_folder=os.path.join(APP_DIR, "static")
)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024  # 128 MB max upload
app.config["JSON_SORT_KEYS"] = False
app.json.sort_keys = False

# Background Job Management
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()


def update_job_progress(job_id: str, progress: float, message: str) -> None:
    """Thread-safe update of job status and progress bar."""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            jobs[job_id]["message"] = message


def _run_ai_extraction_worker(job_id: str, target_pdf_path: str, cloud_crosscheck: bool = False) -> None:
    """Background extraction thread executing the AI pipeline with optional Cloud AI cross-checking."""
    try:
        client = None
        update_job_progress(job_id, 2.0, "Starting AI extraction pipeline...")
        def on_progress(pct: float, msg: str):
            update_job_progress(job_id, pct, msg)

        def log_audit(entry: Dict[str, Any]):
            with jobs_lock:
                if job_id in jobs:
                    if "audit_logs" not in jobs[job_id]:
                        jobs[job_id]["audit_logs"] = []
                    jobs[job_id]["audit_logs"].append(entry)

        t0 = time.time()
        if cloud_crosscheck:
            # 100% Pure Cloud AI Pipeline: bypass local Ollama neural inference passes entirely
            from core.cloud_client import CloudClient, ORCHESTRATION_CANDIDATES
            cc_probe = CloudClient()
            active_cand = cc_probe.get_best_working_model()

            log_audit({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "cloud_ai_active",
                "badge": "ACTIVE CLOUD MODEL",
                "title": f"Initial Active Cloud LLM: {active_cand['provider'].upper()} ({active_cand['model']})",
                "details": {
                    "provider": active_cand["provider"],
                    "model": active_cand["model"],
                    "auto_orchestrate": True,
                    "failover_candidates": [c["label"] for c in ORCHESTRATION_CANDIDATES[:5]]
                }
            })

            from core.page_ingester import ingest_pdf
            from core.possibility_classifier import classify_document
            from core.possibility_extractor import extract_with_possibility_engine

            update_job_progress(job_id, 5.0, "Ingesting PDF document streams (Pure Cloud AI mode)...")
            pages = ingest_pdf(target_pdf_path)
            archetype, confidence = classify_document(pages)
            log_audit({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "archetype",
                "badge": "ARCHETYPE",
                "title": f"Classified Archetype: {archetype.upper()} ({confidence:.0%} confidence)",
                "details": {"archetype": archetype, "confidence": confidence, "pages": len(pages)}
            })

            update_job_progress(job_id, 20.0, f"Matched Archetype: {archetype} ({confidence:.0%}). Extracting ledger...")
            meta, students = extract_with_possibility_engine(target_pdf_path, archetype, on_progress)
            log_audit({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "structure",
                "badge": "CATALOG BUILT",
                "title": f"Extracted {len(students)} student records with {len(meta.catalog.courses)} courses and complete marks",
                "details": {
                    "students_count": len(students),
                    "courses": [f"{c.code}: {c.name}" for c in meta.catalog.courses[:15]]
                }
            })
        else:
            client = OllamaClient()
            meta, students, archetype = extract_pdf(
                pdf_path=target_pdf_path,
                progress_callback=on_progress,
                client=client,
                audit_callback=log_audit
            )

        flat_records = [s.to_flat_dict(meta.catalog) for s in students]

        ai_crosscheck_result = None
        if cloud_crosscheck and run_ai_cross_check:
            update_job_progress(job_id, 86.0, "Auditing & self-healing records with Cloud AI...")
            try:
                def cc_progress(pct: float, msg: str, card: Optional[Dict[str, Any]] = None):
                    scaled_pct = 86.0 + (pct * 0.06)  # 86% to 92%
                    update_job_progress(job_id, scaled_pct, f"Cloud AI: {msg}")
                    if card:
                        with jobs_lock:
                            if job_id in jobs:
                                if "discrepancies" not in jobs[job_id]:
                                    jobs[job_id]["discrepancies"] = []
                                jobs[job_id]["discrepancies"].append(card)

                ai_crosscheck_result = run_ai_cross_check(
                    meta=meta,
                    students=flat_records,
                    pdf_path=target_pdf_path,
                    progress_cb=cc_progress,
                    auto_apply=True,
                    audit_cb=log_audit
                )
            except Exception as e:
                log_audit({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "ai_error",
                    "badge": "CROSS-CHECK ERROR",
                    "title": f"In-flight Cloud AI cross-check encountered error: {e}",
                    "details": {"error": str(e)}
                })
                print(f"[!] Warning: In-flight Cloud AI cross-check failed: {e}")

        update_job_progress(job_id, 92.0, "Writing CSV, Excel, and JSON metadata...")
        checksum_summary = verify_batch(students, meta.catalog)
        des_dir, csv_path = export_ai_extracted_data(
            meta=meta,
            students=students,
            input_pdf_path=target_pdf_path,
            base_output_dir=OUTPUT_DIR,
            archetype=archetype,
            checksum_data=checksum_summary,
            flat_rows=flat_records
        )

        folder_name = os.path.basename(des_dir)
        csv_filename = os.path.basename(csv_path)
        excel_filename = csv_filename.replace("_extracted_data.csv", "_extracted_data.xlsx")
        json_filename = csv_filename.replace("_extracted_data.csv", "_catalog.json")

        # Save AI cross-check audit JSON if executed
        if ai_crosscheck_result:
            try:
                audit_path = os.path.join(des_dir, f"{folder_name}_crosscheck_audit.json")
                with open(audit_path, "w", encoding="utf-8") as f:
                    json.dump(ai_crosscheck_result, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

        meta_dict = {
            "academic_year": getattr(meta, "academic_year", ""),
            "semester": getattr(meta, "semester", ""),
            "exam_title": getattr(meta, "exam_title", ""),
            "college_name": getattr(meta, "college_name", "")
        }
        academic_filename = get_academic_filename(folder_name, meta_dict)

        duration = round(time.time() - t0, 2)

        if cloud_crosscheck:
            engine_name = "Cloud AI (Multi-Provider Orchestrator)"
        elif client is not None and client.is_available():
            engine_name = f"Local AI ({client.model} on GPU)"
        else:
            engine_name = "Offline Knowledge Base"

        res_payload = {
            "success": True,
            "folder_name": folder_name,
            "csv_file": csv_filename,
            "excel_file": excel_filename,
            "json_file": json_filename,
            "academic_report_file": academic_filename,
            "archetype": archetype,
            "engine_used": engine_name,
            "semester": meta.semester,
            "college_name": meta.college_name,
            "exam_title": meta.exam_title,
            "result_date": meta.result_date,
            "total_students": len(students),
            "checksum_summary": checksum_summary,
            "ai_crosscheck_summary": ai_crosscheck_result,
            "students": flat_records,
            "duration_seconds": duration,
            "catalog": meta.catalog.to_dict() if meta.catalog else [],
            "audit_logs": jobs.get(job_id, {}).get("audit_logs", [])
        }

        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["progress"] = 100.0
                jobs[job_id]["message"] = f"Extraction complete: {len(students)} students processed in {duration}s."
                jobs[job_id]["result"] = res_payload

    except Exception as e:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)
                jobs[job_id]["message"] = f"Extraction failed: {e}"


# --- Routes ---

@app.route("/")
def index_route():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
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


@app.route("/api/inputs", methods=["GET"])
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


@app.route("/api/batches", methods=["GET"])
def list_batches():
    """List batch datasets from Batch Data/."""
    batches = {}
    if os.path.isdir(BATCH_DATA_DIR):
        for root, dirs, files in os.walk(BATCH_DATA_DIR):
            for f in sorted(files):
                if f.lower().endswith(".pdf"):
                    rel = os.path.relpath(os.path.join(root, f), BATCH_DATA_DIR).replace("\\", "/")
                    batch_name = rel.split("/")[0] if "/" in rel else "Default"
                    batches.setdefault(batch_name, []).append({
                        "rel_path": rel,
                        "filename": f,
                        "full_path": os.path.join(root, f)
                    })
    return jsonify({"batches": batches})


@app.route("/api/history", methods=["GET"])
def list_history():
    """List historical extractions."""
    history = []
    if os.path.isdir(OUTPUT_DIR):
        for entry in sorted(os.listdir(OUTPUT_DIR), reverse=True):
            fpath = os.path.join(OUTPUT_DIR, entry)
            if not os.path.isdir(fpath):
                continue
            cat_file = os.path.join(fpath, f"{entry}_catalog.json")
            meta = {}
            if os.path.isfile(cat_file):
                try:
                    with open(cat_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
            history.append({
                "folder_name": entry,
                "archetype": meta.get("archetype", "unknown"),
                "semester": meta.get("semester", "--"),
                "total_students": meta.get("total_students", 0),
                "exam_title": meta.get("exam_title", entry)
            })
    return jsonify({"history": history})


@app.route("/api/history/<folder_name>", methods=["GET"])
def load_history_entry(folder_name):
    """Load extracted CSV records from history."""
    clean_folder = secure_filename(folder_name)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": "History folder not found."}), 404

    csv_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.csv")
    cat_path = os.path.join(folder_path, f"{clean_folder}_catalog.json")

    records = []
    if os.path.isfile(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = list(reader)

    cat_meta = {}
    if os.path.isfile(cat_path):
        try:
            with open(cat_path, "r", encoding="utf-8") as f:
                cat_meta = json.load(f)
        except Exception:
            pass

    academic_filename = get_academic_filename(clean_folder, cat_meta)
    return jsonify({
        "success": True,
        "folder_name": clean_folder,
        "csv_file": f"{clean_folder}_extracted_data.csv",
        "excel_file": f"{clean_folder}_extracted_data.xlsx",
        "json_file": f"{clean_folder}_catalog.json",
        "academic_report_file": academic_filename,
        "total_students": len(records),
        "students": records,
        "meta": cat_meta
    })


@app.route("/api/extract", methods=["POST"])
def start_extraction():
    """Trigger AI extraction job."""
    target_pdf_path = None
    is_sync = request.args.get("sync", "0") in ("1", "true")

    if "pdf_file" in request.files:
        up = request.files["pdf_file"]
        if up and up.filename:
            if not up.filename.lower().endswith(".pdf"):
                return jsonify({"success": False, "error": "Only PDF files (.pdf) are supported."}), 400
            safe_name = secure_filename(up.filename)
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"
            target_pdf_path = os.path.join(INPUT_DIR, safe_name)
            up.save(target_pdf_path)

    json_data = request.get_json(silent=True) or {}

    if not target_pdf_path:
        existing = request.form.get("input_file") or request.form.get("existing_input") or json_data.get("input_file")
        if existing:
            cand = os.path.join(INPUT_DIR, secure_filename(existing))
            if os.path.isfile(cand):
                target_pdf_path = cand

    if not target_pdf_path:
        batch_rel = request.form.get("batch_file") or json_data.get("batch_file") or json_data.get("pdf_path")
        if batch_rel:
            clean_rel = batch_rel.replace("\\", "/").strip().lstrip("/")
            if clean_rel.lower().startswith("batch data/"):
                clean_rel = clean_rel[11:].lstrip("/")
            cand = os.path.abspath(os.path.join(BATCH_DATA_DIR, clean_rel))
            if cand.startswith(BATCH_DATA_DIR) and os.path.isfile(cand):
                target_pdf_path = cand

    if not target_pdf_path or not os.path.isfile(target_pdf_path):
        return jsonify({"success": False, "error": "No valid PDF document provided or found."}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "processing",
            "progress": 0.0,
            "message": "Initializing AI extraction...",
            "created_at": time.time(),
            "audit_logs": [],
            "result": None,
            "error": None
        }

    cloud_crosscheck = (
        request.form.get("cloud_crosscheck", "0") in ("1", "true", "yes")
        or request.args.get("cloud_crosscheck", "0") in ("1", "true", "yes")
        or bool(json_data.get("cloud_crosscheck"))
    )

    if is_sync:
        _run_ai_extraction_worker(job_id, target_pdf_path, cloud_crosscheck)
        res = jobs.get(job_id, {})
        if res.get("status") == "completed":
            return jsonify(res["result"])
        return jsonify({"success": False, "error": res.get("error", "Unknown error")}), 500

    t = threading.Thread(target=_run_ai_extraction_worker, args=(job_id, target_pdf_path, cloud_crosscheck), daemon=True)
    t.start()
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id):
    """Poll job progress."""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404
    return jsonify(job)


@app.route("/api/download/<folder>/<filename>", methods=["GET"])
def download_extracted_file(folder, filename):
    """Download exported CSV, Excel, or JSON."""
    clean_folder = secure_filename(folder)
    clean_filename = secure_filename(filename)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": "Directory not found."}), 404
    return send_from_directory(folder_path, clean_filename, as_attachment=True)


@app.route("/api/export/academic-analysis/<folder>", methods=["GET"])
def export_academic_analysis(folder):
    """
    Generate and stream the publication-grade 6-sheet Academic Analysis Workbook
    (e.g. '21-22 sem 8.xlsx') matching the exact departmental Google Sheet format.
    """
    clean_folder = secure_filename(folder)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": f"Folder '{clean_folder}' not found in output."}), 404

    csv_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.csv")
    cat_path = os.path.join(folder_path, f"{clean_folder}_catalog.json")

    cat_meta = {}
    if os.path.isfile(cat_path):
        try:
            with open(cat_path, "r", encoding="utf-8") as f:
                cat_meta = json.load(f)
        except Exception:
            pass

    records = []
    fieldnames = []
    if os.path.isfile(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            records = list(reader)

    if not records:
        return jsonify({"success": False, "error": "No extracted student records found to generate analysis sheet."}), 400

    academic_filename = get_academic_filename(clean_folder, cat_meta)
    academic_path = os.path.join(folder_path, academic_filename)

    # Always ensure workbook is fresh and generated
    try:
        generator = AcademicExcelReportGenerator(
            students=records,
            catalog=cat_meta,
            meta=cat_meta,
            folder_name=clean_folder,
            fieldnames=fieldnames
        )
        generator.generate_workbook(academic_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to generate academic analysis report: {str(e)}"}), 500

    return send_from_directory(folder_path, academic_filename, as_attachment=True)


@app.route("/api/chat", methods=["POST"])
def chat_with_dataset():
    """Interactive Chat with Hybrid Routing (Ollama + Cloud AI + Persistent Token Cache)."""
    data = request.get_json(silent=True) or {}
    user_prompt = data.get("prompt", "").strip()
    students = data.get("students", [])
    folder_name = data.get("folder_name", "")
    session_id = data.get("session_id", folder_name or "active_session")

    if not user_prompt:
        return jsonify({"success": False, "error": "Prompt cannot be empty."}), 400

    catalog_courses = []
    if folder_name:
        clean_folder = secure_filename(folder_name)
        cat_file = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_catalog.json")
        if os.path.isfile(cat_file):
            try:
                with open(cat_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    catalog_courses = cdata.get("courses", [])
            except Exception:
                pass

    client = OllamaClient()
    cloud_client = CloudClient()
    result = process_chat_command(
        user_prompt=user_prompt,
        students=students,
        catalog=catalog_courses,
        client=client,
        cloud_client=cloud_client,
        session_id=session_id,
        folder_name=folder_name
    )

    # Persist dataset modification to disk if folder_name provided
    if result.get("success") and result.get("action") not in ("answer", "unrecognized", "none"):
        updated = result.get("students", [])
        if folder_name and updated:
            try:
                clean_folder = secure_filename(folder_name)
                folder_path = os.path.join(OUTPUT_DIR, clean_folder)
                if os.path.isdir(folder_path):
                    csv_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.csv")
                    excel_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.xlsx")
                    cat_path = os.path.join(folder_path, f"{clean_folder}_catalog.json")
                    keys = list(updated[0].keys())
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=keys)
                        writer.writeheader()
                        writer.writerows(updated)
                    try:
                        import pandas as pd
                        df = pd.DataFrame(updated)
                        df.to_excel(excel_path, index=False)
                    except Exception:
                        pass
                    # Sync total_students in catalog JSON
                    if os.path.isfile(cat_path):
                        try:
                            with open(cat_path, "r", encoding="utf-8") as f:
                                cdata = json.load(f)
                            cdata["total_students"] = len(updated)
                            with open(cat_path, "w", encoding="utf-8") as f:
                                json.dump(cdata, f, indent=2)
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error persisting modified records: {e}")

    return jsonify(result)


@app.route("/api/cloud/config", methods=["GET"])
def get_cloud_config():
    """Get Cloud AI settings safe for frontend display."""
    client = CloudClient()
    return jsonify(client.get_safe_config())


@app.route("/api/cloud/config", methods=["POST"])
def save_cloud_config():
    """Save Cloud AI API keys and settings."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    ok, msg = client.save_config(data)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400

    # If auto-orchestration is on, pick and lock the best working model immediately
    if client.config.get("auto_orchestrate", True):
        best = client.get_best_working_model(force_refresh=True)
        msg += f" (Locked onto {best['provider'].upper()} - {best['model']})"

    return jsonify({"success": True, "message": msg, "config": client.get_safe_config()})


@app.route("/api/cloud/probe-all", methods=["POST"])
def probe_all_cloud_providers():
    """Probe all configured API keys across providers and return health report."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    keys_dict = data.get("api_keys")
    report = client.probe_all_providers(keys_dict=keys_dict)
    return jsonify({
        "success": True,
        "report": report,
        "config": client.get_safe_config()
    })


@app.route("/api/cloud/test", methods=["POST"])
def test_cloud_connection():
    """Test remote connection to the specified or configured Cloud AI."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    provider = data.get("provider")
    api_key = data.get("api_key")
    model = data.get("model")

    ok, msg = client.test_connection(provider=provider, api_key=api_key, model=model)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/cloud/models", methods=["GET", "POST"])
def get_cloud_models():
    """Fetch live or curated available models for a given provider and API key."""
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = {
            "provider": request.args.get("provider"),
            "api_key": request.args.get("api_key")
        }
    client = CloudClient()
    provider = data.get("provider") or client.config.get("provider", "gemini")
    api_key = (data.get("api_key") or client.config.get("api_key", "")).strip()

    models = client.fetch_live_models(provider=provider, api_key=api_key)
    return jsonify({
        "success": True,
        "provider": provider,
        "models": models
    })


@app.route("/api/models", methods=["GET"])
def get_available_models():
    """List locally downloaded Ollama models and currently active model."""
    client = OllamaClient()
    models = client.list_models() if client.is_available() else []
    return jsonify({
        "success": True,
        "active_model": client.model,
        "models": models
    })


@app.route("/api/model/switch", methods=["POST"])
def switch_model():
    """Switch active Ollama model dynamically."""
    data = request.get_json(silent=True) or {}
    new_model = data.get("model", "").strip()
    if not new_model:
        return jsonify({"success": False, "error": "Model name is required."}), 400
    os.environ["OLLAMA_MODEL"] = new_model
    return jsonify({"success": True, "active_model": new_model})


@app.route("/api/eda", methods=["GET", "POST"])
def run_dataset_eda():
    """Run deterministic statistical EDA on active or saved extraction."""
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or {}

    records = data.get("students", [])
    folder_name = data.get("folder_name") or request.args.get("folder_name", "")

    # If no records provided directly, load from saved folder in output
    if not records and folder_name:
        clean_folder = secure_filename(folder_name)
        csv_path = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_extracted_data.csv")
        if os.path.isfile(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                records = list(csv.DictReader(f))

    if not records:
        return jsonify({"success": False, "error": "No student records provided or found for analysis."}), 400

    ollama = OllamaClient()
    cloud = CloudClient()
    eda_engine = EDAEngine()
    eda_results = eda_engine.run_analysis(
        data=records,
        ollama_client=ollama,
        cloud_client=cloud
    )

    return jsonify({
        "success": True,
        "eda": eda_results
    })


@app.route("/api/eda/insights", methods=["POST"])
def generate_eda_insights():
    """Generate executive AI insights and NAAC recommendations from compact EDA summary."""
    data = request.get_json(silent=True) or {}
    eda_data = data.get("eda", {})
    if not eda_data:
        return jsonify({"success": False, "error": "EDA metrics payload is required."}), 400

    ollama = OllamaClient()
    cloud = CloudClient()
    synthesizer = AIInsightsSynthesizer(ollama_client=ollama, cloud_client=cloud)
    insights = synthesizer.generate_narrative(eda_data)

    return jsonify({
        "success": True,
        "insights": insights
    })





# =================================================================
# AI Cross-Checking & Self-Healing Routes (Dual-Mode)
# =================================================================

ai_cc_jobs: Dict[str, Dict[str, Any]] = {}
ai_cc_jobs_lock = threading.Lock()


def update_ai_cc_progress(job_id: str, progress: float, message: str, card: Optional[Dict[str, Any]] = None) -> None:
    """Thread-safe update of AI cross-check job progress and real-time streaming cards."""
    with ai_cc_jobs_lock:
        if job_id in ai_cc_jobs:
            ai_cc_jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            ai_cc_jobs[job_id]["message"] = message
            if card:
                ai_cc_jobs[job_id]["discrepancies"].append(card)


def _find_pdf_for_folder(folder_name: str) -> Optional[str]:
    """Dynamically locates matching source PDF file across input and Batch Data directories."""
    safe_name = folder_name.lower().replace("_", "-").replace(" ", "-")
    # 1. Search input/
    if os.path.isdir(INPUT_DIR):
        for fname in os.listdir(INPUT_DIR):
            if fname.lower().endswith(".pdf"):
                stem = os.path.splitext(fname)[0].lower().replace("_", "-").replace(" ", "-")
                if stem == safe_name or stem in safe_name or safe_name in stem:
                    return os.path.join(INPUT_DIR, fname)
    # 2. Search Batch Data/ recursively
    if os.path.isdir(BATCH_DATA_DIR):
        for root, dirs, files in os.walk(BATCH_DATA_DIR):
            for fname in files:
                if fname.lower().endswith(".pdf"):
                    stem = os.path.splitext(fname)[0].lower().replace("_", "-").replace(" ", "-")
                    if stem == safe_name or stem in safe_name or safe_name in stem:
                        return os.path.join(root, fname)
    # 3. Check inside designated output dir
    out_dir = os.path.join(OUTPUT_DIR, secure_filename(folder_name))
    if os.path.isdir(out_dir):
        for fname in os.listdir(out_dir):
            if fname.lower().endswith(".pdf"):
                return os.path.join(out_dir, fname)
    return None


def _run_post_crosscheck_worker(job_id: str, folder_name: str, csv_path: str, pdf_path: str) -> None:
    """Background worker for post-extraction AI Cross-Checking & Self-Healing."""
    try:
        if not run_ai_cross_check:
            raise RuntimeError("AI Cross-Checker module is not available.")

        update_ai_cc_progress(job_id, 2.0, "Loading student records from extracted CSV...")
        students = []
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(dict(row))

        if not students:
            with ai_cc_jobs_lock:
                ai_cc_jobs[job_id]["status"] = "failed"
                ai_cc_jobs[job_id]["error"] = "CSV dataset contains no student records."
            return

        # Attempt to load catalog metadata JSON
        folder_dir = os.path.dirname(csv_path)
        catalog_path = os.path.join(folder_dir, f"{folder_name}_catalog.json")
        meta = ExamMeta(exam_title=folder_name, semester="")
        if os.path.isfile(catalog_path):
            try:
                with open(catalog_path, mode="r", encoding="utf-8") as f:
                    cat_data = json.load(f)
                meta.exam_title = cat_data.get("exam_title", folder_name)
                meta.college_name = cat_data.get("college_name", "")
                meta.semester = cat_data.get("semester", "")
                meta.result_date = cat_data.get("result_date", "")
                meta.archetype = cat_data.get("archetype", "")
                if "courses" in cat_data and isinstance(cat_data["courses"], list):
                    cat_obj = CourseCatalog()
                    for c in cat_data["courses"]:
                        cat_obj.add_course(c.get("code", ""), c.get("name", ""), c.get("heads", []))
                    meta.catalog = cat_obj
            except Exception:
                pass

        def log_audit(entry: Dict[str, Any]):
            with ai_cc_jobs_lock:
                if job_id in ai_cc_jobs:
                    if "audit_logs" not in ai_cc_jobs[job_id]:
                        ai_cc_jobs[job_id]["audit_logs"] = []
                    ai_cc_jobs[job_id]["audit_logs"].append(entry)

        from core.cloud_client import CloudClient, ORCHESTRATION_CANDIDATES
        cc_probe = CloudClient()
        active_cand = cc_probe.get_best_working_model()
        log_audit({
            "timestamp": time.strftime("%H:%M:%S"),
            "type": "cloud_ai_active",
            "badge": "ACTIVE CLOUD MODEL",
            "title": f"Initial Active Cloud LLM: {active_cand['provider'].upper()} ({active_cand['model']})",
            "details": {
                "provider": active_cand["provider"],
                "model": active_cand["model"],
                "auto_orchestrate": True,
                "failover_candidates": [c["label"] for c in ORCHESTRATION_CANDIDATES[:5]]
            }
        })

        def cb(pct: float, msg: str, card: Optional[Dict[str, Any]] = None):
            update_ai_cc_progress(job_id, pct, msg, card)

        update_ai_cc_progress(job_id, 5.0, "Initiating Cloud AI cross-checking audit...")
        result = run_ai_cross_check(
            meta=meta,
            students=students,
            pdf_path=pdf_path,
            progress_cb=cb,
            auto_apply=False,
            audit_cb=log_audit
        )

        with ai_cc_jobs_lock:
            ai_cc_jobs[job_id]["status"] = "completed"
            ai_cc_jobs[job_id]["progress"] = 100.0
            ai_cc_jobs[job_id]["message"] = (
                f"Cross-check complete! Verified patches: {result.get('verified_patches', 0)}, "
                f"Rejected: {result.get('rejected_patches', 0)}"
            )
            ai_cc_jobs[job_id]["result"] = result

    except Exception as e:
        with ai_cc_jobs_lock:
            ai_cc_jobs[job_id]["status"] = "failed"
            ai_cc_jobs[job_id]["error"] = str(e)
            ai_cc_jobs[job_id]["message"] = f"Cross-check error: {str(e)}"


@app.route("/api/ai/cross-check/run", methods=["POST"])
def run_ai_crosscheck_endpoint():
    """Start asynchronous on-demand AI Cross-Check on an existing extracted dataset."""
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    pdf_candidate = data.get("pdf_path", "").strip()

    if not folder_name:
        return jsonify({"success": False, "error": "folder_name is required."}), 400

    safe_folder = secure_filename(folder_name)
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
            "error": f"Source PDF file corresponding to '{safe_folder}' could not be located in input/ or Batch Data/."
        }), 404

    job_id = f"cc_{uuid.uuid4()}"
    with ai_cc_jobs_lock:
        ai_cc_jobs[job_id] = {
            "status": "processing",
            "progress": 0.0,
            "message": "Initializing Cloud AI cross-checker...",
            "created_at": time.time(),
            "discrepancies": [],
            "result": None,
            "error": None
        }

    th = threading.Thread(
        target=_run_post_crosscheck_worker,
        args=(job_id, safe_folder, csv_path, pdf_path),
        daemon=True
    )
    th.start()

    return jsonify({"success": True, "job_id": job_id, "pdf_path": pdf_path})


@app.route("/api/ai/cross-check/jobs/<job_id>", methods=["GET"])
def get_ai_crosscheck_job_status(job_id):
    """Poll progress, status, and streaming discrepancy cards for an AI cross-check job."""
    with ai_cc_jobs_lock:
        job = ai_cc_jobs.get(job_id)

    if not job:
        return jsonify({"success": False, "error": "Cross-check job not found."}), 404

    return jsonify({
        "success": True,
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "discrepancies": job.get("discrepancies", []),
        "audit_logs": job.get("audit_logs", []),
        "result": job.get("result"),
        "error": job.get("error")
    })


@app.route("/api/ai/cross-check/apply", methods=["POST"])
def apply_ai_crosscheck_patches():
    """Apply approved AI cross-check patches, update CSV, and regenerate styled Excel."""
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    patches = data.get("patches", [])

    if not folder_name:
        return jsonify({"success": False, "error": "folder_name is required."}), 400

    if not patches:
        return jsonify({"success": False, "error": "No patches provided to apply."}), 400

    safe_folder = secure_filename(folder_name)
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


# =================================================================
# End of AI Cross-Checking Routes
# =================================================================


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Extractor AI Server")
    parser.add_argument("pdf_path", nargs="?", default=None, help="Optional PDF path to extract directly")
    parser.add_argument("--port", type=int, default=5001, help="Server port (default: 5001)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    port = args.port
    print(f"Starting Data Extractor AI on http://127.0.0.1:{port}")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    app.run(host="127.0.0.1", port=port, debug=False)
