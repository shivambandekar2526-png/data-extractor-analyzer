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
from exporter import export_ai_extracted_data
from models import CourseCatalog, ExamMeta, StudentRecord

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


def _run_ai_extraction_worker(job_id: str, target_pdf_path: str) -> None:
    """Background extraction thread executing the AI pipeline."""
    try:
        update_job_progress(job_id, 2.0, "Starting AI extraction pipeline...")
        client = OllamaClient()

        def on_progress(pct: float, msg: str):
            update_job_progress(job_id, pct, msg)

        def log_audit(entry: Dict[str, Any]):
            with jobs_lock:
                if job_id in jobs:
                    if "audit_logs" not in jobs[job_id]:
                        jobs[job_id]["audit_logs"] = []
                    jobs[job_id]["audit_logs"].append(entry)

        t0 = time.time()
        meta, students, archetype = extract_pdf(
            pdf_path=target_pdf_path,
            progress_callback=on_progress,
            client=client,
            audit_callback=log_audit
        )

        update_job_progress(job_id, 92.0, "Writing CSV, Excel, and JSON metadata...")
        checksum_summary = verify_batch(students, meta.catalog)
        des_dir, csv_path = export_ai_extracted_data(
            meta=meta,
            students=students,
            input_pdf_path=target_pdf_path,
            base_output_dir=OUTPUT_DIR,
            archetype=archetype,
            checksum_data=checksum_summary
        )

        folder_name = os.path.basename(des_dir)
        csv_filename = os.path.basename(csv_path)
        excel_filename = csv_filename.replace("_extracted_data.csv", "_extracted_data.xlsx")
        json_filename = csv_filename.replace("_extracted_data.csv", "_catalog.json")

        duration = round(time.time() - t0, 2)
        flat_records = [s.to_flat_dict(meta.catalog) for s in students]

        res_payload = {
            "success": True,
            "folder_name": folder_name,
            "csv_file": csv_filename,
            "excel_file": excel_filename,
            "json_file": json_filename,
            "archetype": archetype,
            "engine_used": f"Local AI ({client.model} on GPU)" if client.is_available() else "Offline Knowledge Base",
            "semester": meta.semester,
            "college_name": meta.college_name,
            "exam_title": meta.exam_title,
            "result_date": meta.result_date,
            "total_students": len(students),
            "checksum_summary": checksum_summary,
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

    return jsonify({
        "success": True,
        "folder_name": clean_folder,
        "csv_file": f"{clean_folder}_extracted_data.csv",
        "excel_file": f"{clean_folder}_extracted_data.xlsx",
        "json_file": f"{clean_folder}_catalog.json",
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

    if not target_pdf_path:
        existing = request.form.get("input_file") or request.form.get("existing_input")
        if existing:
            cand = os.path.join(INPUT_DIR, secure_filename(existing))
            if os.path.isfile(cand):
                target_pdf_path = cand

    if not target_pdf_path:
        batch_rel = request.form.get("batch_file")
        if batch_rel:
            clean_rel = batch_rel.replace("\\", "/").strip().lstrip("/")
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

    if is_sync:
        _run_ai_extraction_worker(job_id, target_pdf_path)
        res = jobs.get(job_id, {})
        if res.get("status") == "completed":
            return jsonify(res["result"])
        return jsonify({"success": False, "error": res.get("error", "Unknown error")}), 500

    t = threading.Thread(target=_run_ai_extraction_worker, args=(job_id, target_pdf_path), daemon=True)
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

    client = OllamaClient()
    cloud_client = CloudClient()
    result = process_chat_command(
        user_prompt=user_prompt,
        students=students,
        client=client,
        cloud_client=cloud_client,
        session_id=session_id
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
    """Save Cloud AI API key and settings."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    ok, msg = client.save_config(data)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "message": msg, "config": client.get_safe_config()})


@app.route("/api/cloud/test", methods=["POST"])
def test_cloud_connection():
    """Test remote connection to the configured Cloud AI."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    if data.get("api_key"):
        client.config["api_key"] = data["api_key"].strip()
    if data.get("provider"):
        client.config["provider"] = data["provider"]
    if data.get("model"):
        client.config["model"] = data["model"]
    if data.get("custom_base_url"):
        client.config["custom_base_url"] = data["custom_base_url"]

    ok, msg = client.test_connection()
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
