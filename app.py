"""
Data Extractor AI Web Application Instance
==========================================
Interactive Web Server & API for AI-Powered Multi-Archetype Academic Result Extraction.
Runs on Port 5001.
"""

import argparse
import csv
import json
import logging
import os
import re

logger = logging.getLogger("app")
import sys
import threading
import time
import uuid
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, ".."))
BATCH_DATA_DIR = os.path.join(PROJECT_ROOT, "Batch Data")
APP_BATCH_DATA_DIR = os.path.join(APP_DIR, "batch_data")
INPUT_DIR = os.path.join(APP_DIR, "input")
OUTPUT_DIR = os.path.join(APP_DIR, "output")
KB_DIR = os.path.join(APP_DIR, "knowledge_base")

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(KB_DIR, exist_ok=True)
os.makedirs(APP_BATCH_DATA_DIR, exist_ok=True)

import shutil

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.chat_assistant import process_chat_command
from core.checksum_verifier import verify_batch
from core.extractor import extract_pdf
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient
from core.eda_engine import EDAEngine
from core.data_cleaning_engine import DataCleaningEngine
from core.ai_insights import AIInsightsSynthesizer
from core.semantic_mapper import SemanticMapper
from core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
from core.ai_cross_checker import run_ai_cross_check, validate_patch_sandbox
from core.excel_csv_ingester import TabularIngester, sanitize_dict_for_json
from core.batch_analyzer import BatchAcademicAnalyzer, detect_semester_number, normalize_batch_slug
from core.batch_excel_generator import BatchExcelReportGenerator
from exporter import export_ai_extracted_data, write_csv_file, write_excel_file
from core.execution_controller import ExtractionController
from models import CourseCatalog, ExamMeta

tabular_ingester = TabularIngester(OUTPUT_DIR)
batch_analyzer = BatchAcademicAnalyzer(OUTPUT_DIR, app_batch_dir=APP_BATCH_DATA_DIR, root_batch_dir=BATCH_DATA_DIR)


def safe_folder_name(name: str) -> str:
    """Safe folder name preserving parentheses for auto-versioning (e.g. sem1(1))."""
    if not name:
        return ""
    clean = os.path.basename(str(name)).replace("..", "").replace("/", "").replace("\\", "")
    return re.sub(r"[^\w\-\(\)\.]", "_", clean)


def auto_sort_into_app_batch_store(
    excel_path: Optional[str] = None,
    meta: Optional[Any] = None,
    source_path: Optional[str] = None,
    students: Optional[List[Dict[str, Any]]] = None,
    catalog: Optional[Any] = None,
    folder_name: Optional[str] = None,
    app_batch_dir: Optional[str] = None
) -> Optional[str]:
    """
    Automatically detects the batch and semester from extraction outputs,
    and sorts/saves a clean Excel file into data_extractor_AI/batch_data/<batch>/Sem_<N>.xlsx.
    """
    try:
        if folder_name and not source_path:
            source_path = folder_name
        if catalog and not meta:
            meta = catalog

        base_batch_store = app_batch_dir or APP_BATCH_DATA_DIR

        # 1. Deduce semester number
        sem_num = None
        if meta:
            sem_val = getattr(meta, "semester", None) if hasattr(meta, "semester") else (meta.get("semester") if isinstance(meta, dict) else None)
            if sem_val:
                m_s = re.search(r"(\d+)", str(sem_val))
                if m_s:
                    sem_num = int(m_s.group(1))
            if not sem_num:
                ex_name = getattr(meta, "exam_name", None) if hasattr(meta, "exam_name") else (meta.get("exam_name") if isinstance(meta, dict) else None)
                if ex_name:
                    sem_num = detect_semester_number(str(ex_name))
        if not sem_num and source_path:
            sem_num = detect_semester_number(source_path)
        if not sem_num and excel_path:
            sem_num = detect_semester_number(excel_path)
        if not sem_num or not (1 <= sem_num <= 8):
            return None

        # 2. Deduce batch slug (e.g. '21-22')
        batch_slug = None
        if source_path:
            parent_dir = os.path.basename(os.path.dirname(os.path.abspath(source_path)))
            m_batch = re.search(r"(\d{2,4})[\s_\-/]+(\d{2,4})", parent_dir)
            if m_batch:
                batch_slug = normalize_batch_slug(parent_dir)
            else:
                m_file = re.search(r"(\d{2,4})[\s_\-/]+(\d{2,4})", os.path.basename(source_path))
                if m_file:
                    batch_slug = normalize_batch_slug(os.path.basename(source_path))

        if not batch_slug and meta:
            acad_yr = str(getattr(meta, "academic_year", "") or "") if hasattr(meta, "academic_year") else (meta.get("academic_year", "") if isinstance(meta, dict) else "")
            ex_title = str(getattr(meta, "exam_title", "") or "") if hasattr(meta, "exam_title") else (meta.get("exam_title", "") if isinstance(meta, dict) else "")
            m_ay = re.search(r"(\d{2,4})[\s_\-/]+(\d{2,4})", acad_yr or ex_title)
            if m_ay:
                batch_slug = normalize_batch_slug(acad_yr or ex_title)

        if not batch_slug and excel_path:
            batch_slug = normalize_batch_slug(os.path.basename(excel_path))

        if not batch_slug or batch_slug == "batch":
            batch_slug = "General"

        dest_dir = os.path.join(base_batch_store, batch_slug)
        os.makedirs(dest_dir, exist_ok=True)
        dest_excel = os.path.join(dest_dir, f"Sem_{sem_num}.xlsx")

        # Copy existing excel or write from students
        if excel_path and os.path.isfile(excel_path):
            shutil.copy2(excel_path, dest_excel)
        elif students:
            analyzer = BatchAcademicAnalyzer(output_dir=OUTPUT_DIR, app_batch_dir=base_batch_store)
            analyzer.save_clean_semester_excel(students, batch_slug, sem_num)

        return dest_excel
    except Exception as e:
        logger.warning(f"auto_sort_into_app_batch_store warning: {e}")
        return None

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

job_controllers: Dict[str, ExtractionController] = {}
job_controllers_lock = threading.Lock()

batch_jobs: Dict[str, Dict[str, Any]] = {}
batch_jobs_lock = threading.Lock()



def update_job_progress(job_id: str, progress: float, message: str) -> None:
    """Thread-safe update of job status and progress bar."""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            jobs[job_id]["message"] = message


def update_batch_job_progress(job_id: str, progress: float, message: str) -> None:
    """Thread-safe update of batch analysis job status and progress bar."""
    with batch_jobs_lock:
        if job_id in batch_jobs:
            batch_jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            batch_jobs[job_id]["message"] = message


def _run_ai_extraction_worker(job_id: str, target_pdf_path: str, cloud_crosscheck: bool = False, pipeline_mode: str = "local") -> None:
    """Background extraction thread executing the AI pipeline (Local, Cloud, or Visual Blueprint)."""
    with job_controllers_lock:
        ctrl = job_controllers.get(job_id)
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
                    if "token_usage" not in jobs[job_id]:
                        jobs[job_id]["token_usage"] = {"input": 0, "output": 0, "total": 0}
                    details = entry.get("details")
                    if isinstance(details, dict):
                        t_data = details.get("tokens")
                        if t_data and isinstance(t_data, dict):
                            in_tok = int(t_data.get("input", 0) or 0)
                            out_tok = int(t_data.get("output", 0) or 0)
                            tot_tok = int(t_data.get("total", in_tok + out_tok) or 0)
                            jobs[job_id]["token_usage"]["input"] += in_tok
                            jobs[job_id]["token_usage"]["output"] += out_tok
                            jobs[job_id]["token_usage"]["total"] += tot_tok

                        # Capture in-flight simultaneous audit healing as verified discrepancy cards
                        if entry.get("type") == "inflight_audit_healed":
                            if "discrepancies" not in jobs[job_id]:
                                jobs[job_id]["discrepancies"] = []
                            disc_card = {
                                "seat_no": str(details.get("seat_no", "")).strip(),
                                "field_name": str(details.get("field", "")).strip(),
                                "extracted_val": details.get("raw_extracted"),
                                "suggested_val": details.get("audited_and_fixed"),
                                "reason": details.get("reason") or details.get("proof_rule") or "Simultaneous in-flight mathematical auto-heal",
                                "evidence_quote": f"Verified in-flight via equation ({details.get('proof_rule', 'Single-pass')})",
                                "sandbox_status": "VERIFIED",
                                "sandbox_reason": "Audited and verified in-flight during single-pass extraction.",
                                "applied": True,
                                "card_type": "data",
                                "page_number": int(details.get("page", 1))
                            }
                            jobs[job_id]["discrepancies"].append(disc_card)

        def on_live_student(student, catalog):
            with jobs_lock:
                if job_id in jobs:
                    flat = student.to_flat_dict(catalog)
                    seat = str(flat.get("Seat_No", flat.get("seat_no", ""))).strip()
                    existing_idx = None
                    if seat:
                        for idx, s in enumerate(jobs[job_id]["live_students"]):
                            if str(s.get("Seat_No", s.get("seat_no", ""))).strip() == seat:
                                existing_idx = idx
                                break
                    if existing_idx is not None:
                        jobs[job_id]["live_students"][existing_idx] = flat
                    else:
                        jobs[job_id]["live_students"].append(flat)
                    if catalog and getattr(catalog, "courses", None):
                        jobs[job_id]["catalog"] = catalog.to_dict()

        def on_live_patch_update(st_dict):
            with jobs_lock:
                if job_id in jobs:
                    seat = str(st_dict.get("Seat_No", st_dict.get("seat_no", ""))).strip()
                    for idx, s in enumerate(jobs[job_id]["live_students"]):
                        if str(s.get("Seat_No", s.get("seat_no", ""))).strip() == seat:
                            jobs[job_id]["live_students"][idx] = dict(st_dict)
                            break

        t0 = time.time()
        if pipeline_mode == "visual_blueprint":
            from core.visual_extractor import run_visual_blueprint_pipeline
            from core.cloud_client import CloudClient, ORCHESTRATION_CANDIDATES
            from core.despooler import is_spooled_pdf, despool_gazette_pdf

            cc_probe = CloudClient()
            active_cand = cc_probe.get_best_working_model()

            working_pdf_path = target_pdf_path
            preloaded_meta = None
            despooled_students = None

            # Detect continuous spool formats (e.g. Mumbai University multi-row candidate blocks with tape stream)
            if is_spooled_pdf(target_pdf_path):
                log_audit({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "spool_detected",
                    "badge": "SPOOL DETECTED",
                    "title": "Continuous Spool Format Detected: Auto-Despooling for Vision AI",
                    "details": {
                        "original_pdf": os.path.basename(target_pdf_path),
                        "reason": "Split multi-row candidate blocks and stream legend detected. Auto-synthesizing unified visual layout..."
                    }
                })
                update_job_progress(job_id, 3.0, "Continuous spool detected. Despooling into unified visual layout...")
                try:
                    despooled_pdf_path, despool_meta, despool_students = despool_gazette_pdf(
                        pdf_path=target_pdf_path,
                        output_dir=OUTPUT_DIR,
                        progress_cb=on_progress,
                        audit_cb=log_audit
                    )
                    working_pdf_path = despooled_pdf_path
                    preloaded_meta = despool_meta
                    despooled_students = despool_students
                    log_audit({
                        "timestamp": time.strftime("%H:%M:%S"),
                        "type": "spool_despooled",
                        "badge": "SPOOL UNIFIED",
                        "title": f"Despooled Gazette Ready for Vision AI: {len(despool_students)} candidates across {len(despool_meta.catalog.courses)} courses",
                        "details": {
                            "despooled_pdf": os.path.basename(despooled_pdf_path),
                            "candidates_count": len(despool_students),
                            "courses_count": len(despool_meta.catalog.courses)
                        }
                    })
                except Exception as spool_err:
                    logger.warning("Despooling warning: %s. Continuing with original PDF.", spool_err)
                    log_audit({
                        "timestamp": time.strftime("%H:%M:%S"),
                        "type": "spool_warning",
                        "badge": "DESPOOL WARNING",
                        "title": f"Despooling warning: {spool_err}. Continuing with direct vision.",
                        "details": {"error": str(spool_err)}
                    })

            log_audit({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "cloud_ai_active",
                "badge": "VISUAL BLUEPRINT",
                "title": f"Visual Blueprint Engine Initialized with {active_cand['provider'].upper()} ({active_cand['model']})",
                "details": {
                    "provider": active_cand["provider"],
                    "model": active_cand["model"],
                    "pipeline": "visual_blueprint",
                    "working_pdf": os.path.basename(working_pdf_path),
                    "auto_failover": True,
                    "failover_candidates": [c["label"] for c in ORCHESTRATION_CANDIDATES[:5]]
                }
            })

            meta, students, archetype = run_visual_blueprint_pipeline(
                pdf_path=working_pdf_path,
                progress_cb=on_progress,
                audit_cb=log_audit,
                client=cc_probe,
                live_cb=on_live_student,
                controller=ctrl,
                preloaded_meta=preloaded_meta,
                despooled_students=despooled_students
            )
        elif pipeline_mode == "cloud" or cloud_crosscheck:
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
            meta, students = extract_with_possibility_engine(
                target_pdf_path, archetype, on_progress, live_callback=on_live_student, controller=ctrl
            )
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
                audit_callback=log_audit,
                live_callback=on_live_student,
                controller=ctrl
            )

        was_stopped = bool(ctrl and ctrl.is_stopped)
        if was_stopped:
            log_audit({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "control",
                "badge": "STOPPED",
                "title": f"Extraction stopped by user. Preserving {len(students)} student records captured.",
                "details": {"records_preserved": len(students)}
            })

        if was_stopped and len(students) == 0:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["status"] = "stopped"
                    jobs[job_id]["progress"] = 0.0
                    jobs[job_id]["message"] = "Extraction stopped before records were captured."
            return

        flat_records = [s.to_flat_dict(meta.catalog) for s in students]

        ai_crosscheck_result = None
        if pipeline_mode == "visual_blueprint":
            with jobs_lock:
                in_flight_cards = list(jobs.get(job_id, {}).get("discrepancies", []))
            ai_crosscheck_result = {
                "success": True,
                "mode": "simultaneous_inflight_audit",
                "total_discrepancies": len(in_flight_cards),
                "verified_patches": len(in_flight_cards),
                "applied_patches": len(in_flight_cards),
                "rejected_patches": 0,
                "discrepancies": in_flight_cards,
                "summary": f"Simultaneous in-flight audit complete! {len(in_flight_cards)} items verified and self-healed in a single pass without extra token overhead."
            }
        elif not was_stopped and (pipeline_mode == "cloud" or cloud_crosscheck) and run_ai_cross_check:
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
                    audit_cb=log_audit,
                    controller=ctrl,
                    live_update_cb=on_live_patch_update
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

        full_excel_path = os.path.join(des_dir, excel_filename)
        # Auto-sort clean Excel into app batch store: data_extractor_AI/batch_data/<batch>/Sem_<N>.xlsx
        auto_sort_into_app_batch_store(
            excel_path=full_excel_path,
            meta=meta,
            source_path=target_pdf_path,
            students=flat_records
        )

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

        if pipeline_mode == "visual_blueprint":
            engine_name = "Visual Blueprint & Sliced Vision Pipeline"
        elif pipeline_mode == "cloud" or cloud_crosscheck:
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
            "stopped_early": was_stopped,
            "checksum_summary": checksum_summary,
            "ai_crosscheck_summary": ai_crosscheck_result,
            "students": flat_records,
            "duration_seconds": duration,
            "catalog": meta.catalog.to_dict() if meta.catalog else [],
            "audit_logs": jobs.get(job_id, {}).get("audit_logs", []),
            "token_usage": jobs.get(job_id, {}).get("token_usage", {"input": 0, "output": 0, "total": 0})
        }

        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["progress"] = 100.0
                if was_stopped:
                    jobs[job_id]["message"] = f"Extraction stopped: {len(students)} student records preserved and saved in {duration}s."
                else:
                    jobs[job_id]["message"] = f"Extraction complete: {len(students)} students processed in {duration}s."
                jobs[job_id]["result"] = res_payload

    except Exception as e:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)
def _run_batch_analysis_worker(
    job_id: str,
    batch_folder: str,
    target_branch: str = "AIDS",
    pdf_pipeline: str = "local",
    excel_mode: str = "direct"
) -> None:
    """Background execution worker for 1st-8th semester multi-source batch analysis."""
    try:
        update_batch_job_progress(job_id, 2.0, "Initiating multi-semester batch analysis...")

        def on_prog(pct: float, msg: str):
            update_batch_job_progress(job_id, pct, msg)

        def log_audit(entry: Dict[str, Any]):
            with batch_jobs_lock:
                if job_id in batch_jobs:
                    if "audit_logs" not in batch_jobs[job_id]:
                        batch_jobs[job_id]["audit_logs"] = []
                    batch_jobs[job_id]["audit_logs"].append(entry)

        analytics = batch_analyzer.run_batch_analysis_job(
            batch_folder=batch_folder,
            target_branch=target_branch,
            pdf_pipeline=pdf_pipeline,
            excel_mode=excel_mode,
            progress_cb=on_prog,
            audit_cb=log_audit
        )

        batch_slug = re.sub(r"[^\w\-]", "_", os.path.basename(batch_folder)).strip("_")
        out_subfolder = f"batch_analysis_{batch_slug}_{job_id[:8]}"
        out_folder_path = os.path.join(OUTPUT_DIR, out_subfolder)
        os.makedirs(out_folder_path, exist_ok=True)

        excel_filename = f"{batch_slug}_Complete_Academic_Analysis.xlsx"
        excel_path = os.path.join(out_folder_path, excel_filename)

        update_batch_job_progress(job_id, 94.0, "Generating official 10-sheet Academic Excel workbook...")
        excel_gen = BatchExcelReportGenerator(analytics)
        excel_gen.generate_workbook(excel_path)

        json_filename = f"{batch_slug}_batch_analysis.json"
        json_path = os.path.join(out_folder_path, json_filename)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(analytics, f, indent=2, ensure_ascii=False)

        total_stds = analytics.get("overall_summary", {}).get("total_students", 0)
        total_sems = len(analytics.get("available_semesters", []))

        with batch_jobs_lock:
            if job_id in batch_jobs:
                batch_jobs[job_id]["status"] = "completed"
                batch_jobs[job_id]["progress"] = 100.0
                batch_jobs[job_id]["message"] = f"Complete batch analysis generated for {total_stds} students across {total_sems} semesters."
                batch_jobs[job_id]["excel_file"] = excel_filename
                batch_jobs[job_id]["json_file"] = json_filename
                batch_jobs[job_id]["output_folder"] = out_subfolder
                batch_jobs[job_id]["result"] = analytics

    except Exception as e:
        with batch_jobs_lock:
            if job_id in batch_jobs:
                batch_jobs[job_id]["status"] = "error"
                batch_jobs[job_id]["error"] = str(e)
                batch_jobs[job_id]["message"] = f"Batch analysis failed: {e}"


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
    """List batch datasets from Batch Data/ including PDF, Excel, and CSV."""
    batches = {}
    if os.path.isdir(BATCH_DATA_DIR):
        for root, dirs, files in os.walk(BATCH_DATA_DIR):
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext in (".pdf", ".xlsx", ".xls", ".csv"):
                    rel = os.path.relpath(os.path.join(root, f), BATCH_DATA_DIR).replace("\\", "/")
                    batch_name = rel.split("/")[0] if "/" in rel else "Default"
                    batches.setdefault(batch_name, []).append({
                        "rel_path": rel,
                        "filename": f,
                        "file_type": "pdf" if ext == ".pdf" else "excel",
                        "full_path": os.path.join(root, f)
                    })
    return jsonify({"batches": batches})


@app.route("/api/batch-analysis/batches", methods=["GET"])
def get_batch_analysis_batches():
    """List all available batch folders across app's designated batch_data and root Batch Data."""
    batch_list = []
    seen_slugs = set()

    candidate_entries = []
    # 1. First check app designated batch store: data_extractor_AI/batch_data/
    if os.path.isdir(APP_BATCH_DATA_DIR):
        for entry in sorted(os.listdir(APP_BATCH_DATA_DIR)):
            dir_path = os.path.join(APP_BATCH_DATA_DIR, entry)
            if os.path.isdir(dir_path):
                candidate_entries.append((entry, dir_path))

    # 2. Check root Batch Data/
    if os.path.isdir(BATCH_DATA_DIR):
        for entry in sorted(os.listdir(BATCH_DATA_DIR)):
            dir_path = os.path.join(BATCH_DATA_DIR, entry)
            if os.path.isdir(dir_path):
                candidate_entries.append((entry, dir_path))

    for entry_name, dir_path in candidate_entries:
        slug = normalize_batch_slug(entry_name)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        try:
            info = batch_analyzer.detect_batch_files(dir_path)
            batch_list.append(info)
        except Exception as e:
            print(f"[!] Error inspecting batch {entry_name}: {e}")

    return jsonify({"success": True, "batches": batch_list})


@app.route("/api/batch-analysis/inspect", methods=["POST"])
def inspect_batch():
    """Inspect a specific batch folder or uploaded files across app and root stores."""
    data = request.get_json(silent=True) or {}
    batch_name = data.get("batch_name", "").strip()
    target_branch = data.get("target_branch", "AIDS").strip()

    slug = normalize_batch_slug(batch_name)
    candidates = [
        os.path.join(APP_BATCH_DATA_DIR, slug),
        os.path.join(APP_BATCH_DATA_DIR, batch_name),
        os.path.join(BATCH_DATA_DIR, batch_name),
        os.path.join(BATCH_DATA_DIR, slug),
        os.path.join(BATCH_DATA_DIR, f"{slug} data"),
        os.path.join(INPUT_DIR, batch_name)
    ]
    folder_path = None
    for cand in candidates:
        if os.path.isdir(cand):
            folder_path = cand
            break

    if not folder_path:
        return jsonify({"success": False, "error": f"Batch folder '{batch_name}' not found."}), 404

    try:
        info = batch_analyzer.detect_batch_files(folder_path, target_branch=target_branch)
        return jsonify(sanitize_dict_for_json({"success": True, **info}))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/batch-analysis/run", methods=["POST"])
def run_batch_analysis():
    """Start asynchronous multi-semester batch analysis."""
    data = request.get_json(silent=True) or {}
    batch_name = data.get("batch_name", "").strip()
    target_branch = data.get("target_branch", "AIDS").strip()
    pdf_pipeline = data.get("pdf_pipeline", "local").strip()
    excel_mode = data.get("excel_mode", "direct").strip()

    slug = normalize_batch_slug(batch_name)
    candidates = [
        os.path.join(APP_BATCH_DATA_DIR, slug),
        os.path.join(APP_BATCH_DATA_DIR, batch_name),
        os.path.join(BATCH_DATA_DIR, batch_name),
        os.path.join(BATCH_DATA_DIR, slug),
        os.path.join(BATCH_DATA_DIR, f"{slug} data"),
        os.path.join(INPUT_DIR, batch_name)
    ]
    folder_path = None
    for cand in candidates:
        if os.path.isdir(cand):
            folder_path = cand
            break

    if not folder_path:
        return jsonify({"success": False, "error": f"Batch folder '{batch_name}' not found."}), 404

    job_id = uuid.uuid4().hex
    with batch_jobs_lock:
        batch_jobs[job_id] = {
            "job_id": job_id,
            "batch_name": batch_name,
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
        args=(job_id, folder_path, target_branch, pdf_pipeline, excel_mode),
        daemon=True
    )
    worker_thread.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/batch-analysis/jobs/<job_id>", methods=["GET"])
def get_batch_job_status(job_id):
    """Poll status and progress of a batch analysis job."""
    with batch_jobs_lock:
        job = batch_jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404
    return jsonify(sanitize_dict_for_json({"success": True, **job}))


@app.route("/api/batch-analysis/download/<job_id>", methods=["GET"])
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


@app.route("/api/batch-analysis/download-json/<job_id>", methods=["GET"])
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


@app.route("/api/history", methods=["GET"])
def list_history():
    """List historical extractions with atomic iteration retention and timestamps."""
    history = []
    if os.path.isdir(OUTPUT_DIR):
        entries = []
        for entry in os.listdir(OUTPUT_DIR):
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
            mtime = os.path.getmtime(fpath)
            created_str = (
                meta.get("created_at")
                or meta.get("timestamp")
                or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            )
            entries.append({
                "folder_name": entry,
                "archetype": meta.get("archetype", "unknown"),
                "semester": meta.get("semester", "--"),
                "total_students": meta.get("total_students", 0),
                "exam_title": meta.get("exam_title", entry),
                "timestamp": created_str,
                "created_at": created_str,
                "_mtime": mtime
            })
        entries.sort(key=lambda x: x["_mtime"], reverse=True)
        for e in entries:
            e.pop("_mtime", None)
        history = entries
    return jsonify({"history": history})


@app.route("/api/history/<folder_name>", methods=["GET"])
def load_history_entry(folder_name):
    """Load extracted CSV records from history."""
    clean_folder = safe_folder_name(folder_name)
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
    target_tabular_path = None

    # Check for direct file uploads
    for key in ["pdf_file", "file", "excel_file", "csv_file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                ext = os.path.splitext(safe_name)[1].lower()
                if ext not in (".pdf", ".xlsx", ".xls", ".csv"):
                    return jsonify({"success": False, "error": "Supported formats: .pdf, .xlsx, .xls, .csv"}), 400
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
        return jsonify({"success": False, "error": "No valid PDF document or tabular file provided or found."}), 400

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
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/jobs/<job_id>", methods=["GET"])
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
    return jsonify(resp)


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
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


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
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


@app.route("/api/jobs/<job_id>/stop", methods=["POST"])
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



# =========================================================================
# Tabular Ingestion Routes (Excel & CSV Inspection, Direct Import, AI Fix)
# =========================================================================

def resolve_tabular_file_path(filename_or_rel: str) -> Optional[Tuple[str, str]]:
    """
    Resolve file path from uploaded input, historical batch directory, or subdirectories.
    Returns (absolute_file_path, display_filename) or None.
    """
    if not filename_or_rel:
        return None

    # 1. Direct INPUT_DIR check
    safe_base = secure_filename(os.path.basename(filename_or_rel))
    cand_input = os.path.join(INPUT_DIR, safe_base)
    if os.path.isfile(cand_input):
        return cand_input, safe_base

    # 2. BATCH_DATA_DIR / APP_BATCH_DATA_DIR relative path check (e.g. '21-22/Sem_8.xlsx')
    clean_rel = str(filename_or_rel).replace("\\", "/").strip().lstrip("/")
    if clean_rel.lower().startswith("batch data/"):
        clean_rel = clean_rel[11:].lstrip("/")

    for base_store in [BATCH_DATA_DIR, APP_BATCH_DATA_DIR]:
        if not os.path.isdir(base_store):
            continue
        cand_batch = os.path.abspath(os.path.join(base_store, clean_rel))
        if cand_batch.startswith(os.path.abspath(base_store)) and os.path.isfile(cand_batch):
            return cand_batch, clean_rel

    # 3. Walk directories to search by filename
    for base_store in [BATCH_DATA_DIR, APP_BATCH_DATA_DIR, INPUT_DIR]:
        if not os.path.isdir(base_store):
            continue
        for root, _, files in os.walk(base_store):
            for f in files:
                if f.lower() == safe_base.lower() or f.lower() == os.path.basename(clean_rel).lower():
                    found_path = os.path.join(root, f)
                    rel_display = os.path.relpath(found_path, base_store).replace("\\", "/")
                    return found_path, rel_display

    return None


@app.route("/api/tabular/inspect", methods=["POST"])
def inspect_tabular_file():
    """Inspect uploaded Excel/CSV file to evaluate formatting and structure."""
    target_path = None
    target_filename = None

    # Check for direct file upload
    for key in ["file", "excel_file", "csv_file", "pdf_file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                target_path = os.path.join(INPUT_DIR, safe_name)
                up.save(target_path)
                target_filename = safe_name
                break

    json_data = request.get_json(silent=True) or {}
    if not target_path:
        fname = (
            request.form.get("filename")
            or json_data.get("filename")
            or request.form.get("batch_file")
            or json_data.get("batch_file")
        )
        if fname:
            resolved = resolve_tabular_file_path(fname)
            if resolved:
                target_path, target_filename = resolved

    if not target_path or not os.path.isfile(target_path):
        return jsonify({"success": False, "error": "No valid tabular file uploaded or specified."}), 400

    sheet_name = request.form.get("sheet_name") or json_data.get("sheet_name")
    assessment = tabular_ingester.inspect_file(target_path, sheet_name=sheet_name)
    if assessment.get("success"):
        assessment["filename"] = target_filename
    return jsonify(sanitize_dict_for_json(assessment))


@app.route("/api/tabular/import", methods=["POST"])
def import_tabular_direct():
    """Directly import Excel or CSV table without invoking AI (0 tokens)."""
    data = request.get_json(silent=True) or request.form.to_dict()
    filename = data.get("filename")
    sheet_name = data.get("sheet_name")
    header_row = data.get("header_row")
    if header_row is not None:
        try:
            header_row = int(header_row)
        except Exception:
            header_row = None

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    resolved = resolve_tabular_file_path(filename)
    if not resolved:
        return jsonify({"success": False, "error": f"Tabular file '{filename}' not found."}), 404
    target_path, target_filename = resolved

    result = tabular_ingester.direct_import(target_path, sheet_name=sheet_name, header_row=header_row)
    if result.get("success") and result.get("students"):
        try:
            auto_sort_into_app_batch_store(source_path=target_path, students=result.get("students"))
        except Exception:
            pass
    return jsonify(sanitize_dict_for_json(result))


@app.route("/api/tabular/ai-fix", methods=["POST"])
def ai_fix_tabular():
    """Extract and restructure messy/unformatted Excel or CSV using AI."""
    data = request.get_json(silent=True) or request.form.to_dict()
    filename = data.get("filename")
    sheet_name = data.get("sheet_name")
    pipeline_mode = data.get("pipeline_mode", "local")
    model = data.get("model")

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    resolved = resolve_tabular_file_path(filename)
    if not resolved:
        return jsonify({"success": False, "error": f"Tabular file '{filename}' not found."}), 404
    target_path, target_filename = resolved

    oc = OllamaClient()
    cc = CloudClient()
    result = tabular_ingester.ai_fix_and_import(
        target_path,
        sheet_name=sheet_name,
        pipeline_mode=pipeline_mode,
        model=model,
        ollama_client=oc,
        cloud_client=cc
    )
    if result.get("success") and result.get("students"):
        try:
            auto_sort_into_app_batch_store(source_path=target_path, students=result.get("students"))
        except Exception:
            pass
    return jsonify(sanitize_dict_for_json(result))


@app.route("/api/tabular/interactive-fix", methods=["POST"])
def interactive_fix_tabular():
    """Auto-repair formulas, totals, SGPI, and formatting for active spreadsheet records."""
    data = request.get_json(silent=True) or {}
    records = data.get("records", [])
    folder_name = data.get("folder_name", "")
    pipeline_mode = data.get("pipeline_mode", "local")

    if not records or not isinstance(records, list):
        return jsonify({"success": False, "error": "No records provided for AI fix."}), 400

    try:
        from core.data_cleaning_engine import DataCleaningEngine
        oc = OllamaClient() if pipeline_mode == "local" else None
        cc = CloudClient() if pipeline_mode == "cloud" else None
        if cc is None:
            inst_cc = CloudClient()
            if inst_cc.is_configured():
                cc = inst_cc
        cleaner = DataCleaningEngine(ollama_client=oc, cloud_client=cc)

        clean_res = cleaner.clean_dataset(records)
        cleaned_records = clean_res.get("cleaned_records", records)
        changes = clean_res.get("changes", [])

        # Re-run normalizer to verify all identifiers & SGPI are clean
        cleaned_records = tabular_ingester._normalize_student_records(cleaned_records)

        # Re-persist if folder exists on server
        if folder_name:
            safe_folder = secure_filename(folder_name)
            folder_path = os.path.join(OUTPUT_DIR, safe_folder)
            if os.path.isdir(folder_path):
                csv_path = os.path.join(folder_path, f"{safe_folder}_extracted_data.csv")
                excel_path = os.path.join(folder_path, f"{safe_folder}_extracted_data.xlsx")
                df = pd.DataFrame(cleaned_records)
                df.to_csv(csv_path, index=False, encoding="utf-8")
                try:
                    df.to_excel(excel_path, index=False)
                except Exception as ex:
                    logger.warning(f"Could not rewrite excel during interactive fix: {ex}")

        return jsonify(sanitize_dict_for_json({
            "success": True,
            "records": cleaned_records,
            "changes": [c.to_dict() if hasattr(c, "to_dict") else c for c in changes],
            "changes_count": len(changes),
            "summary": clean_res.get("summary", {})
        }))
    except Exception as e:
        logger.error(f"Error in interactive fix: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/download/<folder>/<filename>", methods=["GET"])
def download_extracted_file(folder, filename):
    """Download exported CSV, Excel, or JSON."""
    clean_folder = safe_folder_name(folder)
    clean_filename = safe_folder_name(filename)
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
    clean_folder = safe_folder_name(folder)
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


@app.route("/api/dataset/sync", methods=["POST"])
def sync_dataset():
    """
    Save and synchronize user edits from the interactive Excel grid back to
    the server's CSV, Excel (.xlsx), and Course Catalog JSON files.
    """
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    students = data.get("students", [])
    columns = data.get("columns", [])
    catalog_data = data.get("catalog")

    if not folder_name:
        folder_name = "live_edited_results"

    clean_folder = safe_folder_name(folder_name)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    os.makedirs(folder_path, exist_ok=True)

    if not columns and students:
        columns = list(students[0].keys())

    csv_filename = f"{clean_folder}_extracted_data.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    try:
        write_csv_file(students, columns, csv_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed writing CSV: {str(e)}"}), 500

    cat_filename = f"{clean_folder}_catalog.json"
    cat_path = os.path.join(folder_path, cat_filename)
    existing_cat = {}
    if os.path.isfile(cat_path):
        try:
            with open(cat_path, "r", encoding="utf-8") as f:
                existing_cat = json.load(f)
        except Exception:
            pass
    if catalog_data and isinstance(catalog_data, dict):
        existing_cat.update(catalog_data)
    try:
        with open(cat_path, "w", encoding="utf-8") as f:
            json.dump(existing_cat, f, indent=2)
    except Exception as e:
        pass

    excel_filename = f"{clean_folder}_extracted_data.xlsx"
    excel_path = os.path.join(folder_path, excel_filename)
    try:
        meta_obj = ExamMeta(exam_title=clean_folder)
        write_excel_file(students, columns, None, meta_obj, excel_path)
        meta_sem = existing_cat.get("semester")
        meta_sync = ExamMeta(exam_title=clean_folder, semester=meta_sem)
        auto_sort_into_app_batch_store(excel_path=excel_path, meta=meta_sync, source_path=clean_folder, students=students)
    except Exception as e:
        print(f"[!] Note: Excel export sync warning: {e}")

    # Synchronize any matching job in memory
    with jobs_lock:
        for jid, job in jobs.items():
            if job.get("folder_name") == clean_folder:
                job["live_students"] = students
                if "result" in job and isinstance(job["result"], dict):
                    job["result"]["students"] = students
                    job["result"]["total_students"] = len(students)

    return jsonify({
        "success": True,
        "folder_name": clean_folder,
        "csv_file": csv_filename,
        "excel_file": excel_filename,
        "json_file": cat_filename,
        "count": len(students),
        "columns_count": len(columns),
        "message": f"Successfully saved {len(students)} records across {len(columns)} columns."
    })


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
        clean_folder = safe_folder_name(folder_name)
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
                clean_folder = safe_folder_name(folder_name)
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
# In-memory store for pending cleaning sessions
pending_cleaning_sessions: Dict[str, Dict[str, Any]] = {}


@app.route("/api/clean/analyze", methods=["POST"])
def analyze_data_cleaning():
    """
    Run AI-guided parameter discovery and local deterministic cleaning.
    Returns proposed ChangeSet and summary statistics for interactive user review.
    """
    data = request.get_json(silent=True) or {}
    records = data.get("students", [])
    folder_name = data.get("folder_name") or ""
    session_key = folder_name or "active_session"

    fieldnames = []
    if not records and folder_name:
        clean_folder = safe_folder_name(folder_name)
        csv_path = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_extracted_data.csv")
        if os.path.isfile(csv_path):
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                records = list(reader)

    if not records:
        return jsonify({"success": False, "error": "No student records provided or found for cleaning."}), 400

    if not fieldnames and records:
        fieldnames = list(records[0].keys())

    ollama = OllamaClient()
    cloud = CloudClient()
    engine = DataCleaningEngine(ollama_client=ollama, cloud_client=cloud)

    clean_result = engine.clean_dataset(records)

    pending_cleaning_sessions[session_key] = {
        "original_records": records,
        "cleaned_records": clean_result["cleaned_records"],
        "changes": clean_result["changes"],
        "summary": clean_result["summary"],
        "rules": clean_result["rules"],
        "fieldnames": fieldnames,
        "folder_name": folder_name
    }

    return jsonify({
        "success": True,
        "summary": clean_result["summary"],
        "changes": clean_result["changes"],
        "rules": clean_result["rules"],
        "cleaned_records": clean_result["cleaned_records"]
    })


@app.route("/api/clean/apply", methods=["POST"])
def apply_data_cleaning():
    """
    Applies or discards the proposed data cleaning changes to promote the cleaned table to primary.
    """
    try:
        data = request.get_json(silent=True) or {}
        folder_name = data.get("folder_name") or ""
        action = data.get("action", "accept")
        accepted_ids = data.get("accepted_ids")
        session_key = folder_name or "active_session"

        session = pending_cleaning_sessions.get(session_key)

        if action == "discard":
            if session_key in pending_cleaning_sessions:
                del pending_cleaning_sessions[session_key]
            return jsonify({
                "success": True,
                "action": "discard",
                "message": "Data cleaning changes discarded. Primary dataset preserved in original state."
            })

        if not session:
            passed_cleaned = data.get("cleaned_students")
            passed_orig = data.get("original_records")
            passed_changes = data.get("changes")
            if passed_orig and passed_changes and accepted_ids is not None:
                engine = DataCleaningEngine()
                final_records = engine.apply_changes(
                    original_records=passed_orig,
                    changes=passed_changes,
                    accepted_ids=set(str(x) for x in accepted_ids)
                )
            elif passed_cleaned:
                final_records = passed_cleaned
            else:
                return jsonify({"success": False, "error": "No active cleaning session found to apply."}), 400
        else:
            if accepted_ids is not None:
                engine = DataCleaningEngine()
                final_records = engine.apply_changes(
                    original_records=session["original_records"],
                    changes=session["changes"],
                    accepted_ids=set(str(x) for x in accepted_ids)
                )
            else:
                final_records = session["cleaned_records"]

        fieldnames = session.get("fieldnames") if session else (list(final_records[0].keys()) if final_records else [])
        all_fieldnames = list(fieldnames) if fieldnames else []
        for r in final_records:
            for k in r.keys():
                if k not in all_fieldnames:
                    all_fieldnames.append(k)

        if folder_name:
            clean_folder = safe_folder_name(folder_name)
            target_dir = os.path.join(OUTPUT_DIR, clean_folder)
            csv_path = os.path.join(target_dir, f"{clean_folder}_extracted_data.csv")
            excel_path = os.path.join(target_dir, f"{clean_folder}_extracted_data.xlsx")
            cat_path = os.path.join(target_dir, f"{clean_folder}_catalog.json")

            if os.path.isdir(target_dir):
                try:
                    write_csv_file(final_records, all_fieldnames, csv_path)
                except Exception as e:
                    logger.warning(f"Could not overwrite CSV with cleaned data: {e}")

                try:
                    meta = ExamMeta(exam_title=clean_folder, semester="")
                    cat_obj = CourseCatalog()
                    if os.path.isfile(cat_path):
                        with open(cat_path, "r", encoding="utf-8") as f:
                            cat_data = json.load(f)
                        meta.exam_title = cat_data.get("exam_title", clean_folder)
                        meta.semester = cat_data.get("semester", "")
                        for c in cat_data.get("courses", []):
                            cat_obj.add_course(c.get("code", ""), c.get("name", ""), c.get("heads", []))
                        meta.catalog = cat_obj
                    write_excel_file(final_records, all_fieldnames, meta.catalog, meta, excel_path)
                except Exception as e:
                    logger.warning(f"Could not re-generate Excel with cleaned data: {e}")

        if session_key in pending_cleaning_sessions:
            del pending_cleaning_sessions[session_key]

        return jsonify({
            "success": True,
            "action": "accept",
            "message": f"Successfully promoted cleaned data ({len(final_records)} records) as the primary dataset!",
            "records": final_records,
            "total_records": len(final_records)
        })
    except Exception as exc:
        logger.exception("Unexpected error in apply_data_cleaning")
        return jsonify({"success": False, "error": str(exc)}), 500


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
        clean_folder = safe_folder_name(folder_name)
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


@app.route("/api/ai/cross-check/run", methods=["POST"])
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
            "audit_logs": [],
            "token_usage": {"input": 0, "output": 0, "total": 0},
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
        "token_usage": job.get("token_usage", {"input": 0, "output": 0, "total": 0}),
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
