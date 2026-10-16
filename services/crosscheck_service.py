"""
AI Cross-Check & Post-Extraction Audit Service
=============================================
Locating source PDFs, background execution of AI Cross-Checking against
original documents, and discrepancy tracking.
"""

import os
import csv
import json
import time
import logging
from typing import Any, Dict, Optional

from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import INPUT_DIR, OUTPUT_DIR, DEPARTMENTS_DATA_DIR
from services.state import ai_cc_jobs, ai_cc_jobs_lock
from models import ExamMeta, CourseCatalog
from core.ai_cross_checker import run_ai_cross_check


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
    # 2. Search departments_data recursively
    if os.path.isdir(DEPARTMENTS_DATA_DIR):
        for root, dirs, files in os.walk(DEPARTMENTS_DATA_DIR):
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
                    if "token_usage" not in ai_cc_jobs[job_id]:
                        ai_cc_jobs[job_id]["token_usage"] = {"input": 0, "output": 0, "total": 0}
                    details = entry.get("details")
                    if isinstance(details, dict):
                        t_data = details.get("tokens")
                        if t_data and isinstance(t_data, dict):
                            in_tok = int(t_data.get("input", 0) or 0)
                            out_tok = int(t_data.get("output", 0) or 0)
                            tot_tok = int(t_data.get("total", in_tok + out_tok) or 0)
                            ai_cc_jobs[job_id]["token_usage"]["input"] += in_tok
                            ai_cc_jobs[job_id]["token_usage"]["output"] += out_tok
                            ai_cc_jobs[job_id]["token_usage"]["total"] += tot_tok

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
