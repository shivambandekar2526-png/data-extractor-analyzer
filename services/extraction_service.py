"""
AI Extraction Service
=====================
Handles background worker execution for PDF extraction, status updates,
and auto-sorting into the canonical departmental store.
"""

import os
import re
import sys
import time
import json
import shutil
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app")

from services.config import (
    APP_DIR, DEPARTMENTS_DATA_DIR, APP_DEPARTMENTS_DATA_DIR,
    OUTPUT_DIR, INPUT_DIR
)
from services.state import (
    DB_PATH, jobs, jobs_lock, job_controllers, job_controllers_lock
)
from core.extractor import extract_pdf
from core.checksum_verifier import verify_batch
from core.ai_cross_checker import run_ai_cross_check
from core.batch_analyzer import BatchAcademicAnalyzer, normalize_batch_slug
from core.pdf_classifier import (
    normalize_department, normalize_batch, normalize_semester
)
from core.dataset_database import save_dataset_to_db
from core.excel_report_generator import get_academic_filename
from exporter import export_ai_extracted_data
from models import ExamMeta, CourseCatalog

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
    app_batch_dir: Optional[str] = None,
    dataset_meta: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Stores a clean Excel file into canonical structure:
      departments_data/excel/<Department>/<Batch>/<Semester>.xlsx
    and persists records into SQLite academic_records.db.
    """
    try:
        if folder_name and not source_path:
            source_path = folder_name
        if catalog and not meta:
            meta = catalog

        # Extract or resolve canonical department, batch, semester
        dept_name = None
        batch_slug = None
        sem_canonical = None

        if dataset_meta and isinstance(dataset_meta, dict):
            dept_name = dataset_meta.get("department")
            batch_slug = dataset_meta.get("batch")
            sem_canonical = dataset_meta.get("semester")

        # 1. Resolve Semester
        if not sem_canonical:
            sem_val = None
            if meta:
                sem_val = getattr(meta, "semester", None) if hasattr(meta, "semester") else (meta.get("semester") if isinstance(meta, dict) else None)
                if not sem_val:
                    ex_name = getattr(meta, "exam_name", None) if hasattr(meta, "exam_name") else (meta.get("exam_name") if isinstance(meta, dict) else None)
                    if ex_name:
                        sem_val = ex_name
            if not sem_val and source_path:
                sem_val = source_path
            if not sem_val and excel_path:
                sem_val = excel_path
            sem_canonical = normalize_semester(sem_val)

        sem_num_match = re.search(r"\d+", sem_canonical)
        sem_num = int(sem_num_match.group(0)) if sem_num_match else 1

        # 2. Resolve Department
        if not dept_name:
            cand_text = f"{source_path or ''} {excel_path or ''}"
            if meta:
                c_name = getattr(meta, "college_name", "") if hasattr(meta, "college_name") else (meta.get("college_name", "") if isinstance(meta, dict) else "")
                e_title = getattr(meta, "exam_title", "") if hasattr(meta, "exam_title") else (meta.get("exam_title", "") if isinstance(meta, dict) else "")
                cand_text += f" {c_name} {e_title}"
            dept_name = normalize_department(cand_text)

        # 3. Resolve Batch
        if not batch_slug:
            cand_batch = None
            if meta:
                cand_batch = getattr(meta, "academic_year", None) if hasattr(meta, "academic_year") else (meta.get("academic_year") if isinstance(meta, dict) else None)
                if not cand_batch:
                    cand_batch = getattr(meta, "exam_title", None) if hasattr(meta, "exam_title") else (meta.get("exam_title") if isinstance(meta, dict) else None)
            if not cand_batch and source_path:
                cand_batch = source_path
            if not cand_batch and excel_path:
                cand_batch = excel_path
            batch_slug = normalize_batch(cand_batch, sem_num=sem_num)

        # Target directory: only write locally if Supabase is NOT configured
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()

        dest_dirs = []
        if not supabase_svc.is_configured:
            dest_dirs.append(os.path.join(DEPARTMENTS_DATA_DIR, "excel", dept_name, batch_slug))

        legacy_dirs = []
        if app_batch_dir and app_batch_dir not in (APP_DEPARTMENTS_DATA_DIR, DEPARTMENTS_DATA_DIR):
            dest_dirs.append(os.path.join(app_batch_dir, "excel", dept_name, batch_slug))
            legacy_slug = normalize_batch_slug(batch_slug)
            raw_source_slug = normalize_batch_slug(cand_batch) if cand_batch else legacy_slug
            for s in set([legacy_slug, raw_source_slug]):
                legacy_dirs.append(os.path.join(app_batch_dir, s))
                legacy_dirs.append(os.path.join(app_batch_dir, dept_name, s))

        canonical_excel = excel_path if (excel_path and os.path.isfile(excel_path)) else None
        for d_dir in dest_dirs:
            os.makedirs(d_dir, exist_ok=True)
            target_f = os.path.join(d_dir, f"{sem_canonical}.xlsx")
            if excel_path and os.path.isfile(excel_path):
                shutil.copy2(excel_path, target_f)
            elif students:
                analyzer = BatchAcademicAnalyzer(output_dir=OUTPUT_DIR, app_batch_dir=APP_DEPARTMENTS_DATA_DIR, root_batch_dir=DEPARTMENTS_DATA_DIR)
                analyzer.save_clean_semester_excel(students, batch_slug, sem_num, target_dir=d_dir)
            if not canonical_excel:
                canonical_excel = target_f

        # Save legacy Sem_<N>.xlsx if custom app_batch_dir was provided
        for l_dir in legacy_dirs:
            os.makedirs(l_dir, exist_ok=True)
            target_l = os.path.join(l_dir, f"Sem_{sem_num}.xlsx")
            if excel_path and os.path.isfile(excel_path):
                shutil.copy2(excel_path, target_l)
            elif students:
                analyzer = BatchAcademicAnalyzer(output_dir=OUTPUT_DIR, app_batch_dir=APP_DEPARTMENTS_DATA_DIR, root_batch_dir=DEPARTMENTS_DATA_DIR)
                analyzer.save_clean_semester_excel(students, normalize_batch_slug(batch_slug), sem_num, target_dir=l_dir)

        # Save to SQLite database (optional local cache)
        if DB_PATH:
            try:
                pdf_path = os.path.join(DEPARTMENTS_DATA_DIR, "pdf", dept_name, batch_slug, f"{sem_canonical}.pdf")
                if not os.path.isfile(pdf_path):
                    pdf_path = None
                cat_payload = catalog if isinstance(catalog, dict) else (catalog.to_dict() if hasattr(catalog, "to_dict") else {})
                save_dataset_to_db(
                    db_path=DB_PATH,
                    department=dept_name,
                    batch=batch_slug,
                    semester=sem_canonical,
                    excel_path=canonical_excel,
                    pdf_path=pdf_path,
                    students=students or [],
                    catalog=cat_payload
                )
            except Exception as db_err:
                logger.warning(f"Failed to persist dataset to SQLite: {db_err}")

        # Supabase Cloud Sync (Primary Cloud Storage & Catalog)
        try:
            from services.supabase_service import get_supabase_service
            supabase_svc = get_supabase_service()
            if supabase_svc.is_configured:
                supabase_rel_excel = f"excel/{dept_name}/{batch_slug}/{sem_canonical}.xlsx"
                source_is_excel = bool(source_path and str(source_path).lower().endswith((".xlsx", ".xls", ".csv")) and os.path.isfile(str(source_path)))
                src_to_upload = canonical_excel if (canonical_excel and os.path.isfile(canonical_excel)) else (excel_path if (excel_path and os.path.isfile(str(excel_path))) else (source_path if source_is_excel else None))
                if src_to_upload and os.path.isfile(src_to_upload):
                    supabase_svc.upload_local_file(src_to_upload, supabase_rel_excel)

                supabase_rel_pdf = f"pdf/{dept_name}/{batch_slug}/{sem_canonical}.pdf"
                pdf_uploaded = None

                # Only upload local PDF if it was NOT already uploaded to Supabase Storage before extraction
                meta_pdf = str(dataset_meta.get("pdf_path") or "") if dataset_meta else ""
                already_in_cloud = meta_pdf.startswith("pdf/") or meta_pdf.startswith("http")
                if already_in_cloud:
                    pdf_uploaded = meta_pdf
                else:
                    cand_pdfs = [
                        source_path if (source_path and str(source_path).lower().endswith(".pdf") and os.path.isfile(str(source_path))) else None,
                        dataset_meta.get("pdf_path") if (dataset_meta and dataset_meta.get("pdf_path") and str(dataset_meta["pdf_path"]).lower().endswith(".pdf") and os.path.isfile(str(dataset_meta["pdf_path"]))) else None,
                        os.path.join(DEPARTMENTS_DATA_DIR, "pdf", dept_name, batch_slug, f"{sem_canonical}.pdf"),
                        os.path.join(INPUT_DIR, f"{source_path}.pdf") if (source_path and not str(source_path).lower().endswith((".xlsx", ".xls", ".csv", ".pdf"))) else None,
                        os.path.join(INPUT_DIR, os.path.basename(str(source_path))) if (source_path and str(source_path).lower().endswith(".pdf")) else None
                    ]
                    for cp in cand_pdfs:
                        if cp and os.path.isfile(cp):
                            supabase_svc.upload_local_file(cp, supabase_rel_pdf)
                            pdf_uploaded = supabase_rel_pdf
                            break

                cat_payload = catalog if isinstance(catalog, dict) else (catalog.to_dict() if hasattr(catalog, "to_dict") else {})
                supabase_svc.upsert_dataset_record(
                    department=dept_name,
                    batch=batch_slug,
                    semester=sem_canonical,
                    excel_path=supabase_rel_excel,
                    pdf_path=pdf_uploaded,
                    total_students=len(students) if students else 0,
                    meta_json={"courses": (cat_payload or {}).get("courses", [])} if isinstance(cat_payload, dict) else {}
                )
        except Exception as sup_err:
            logger.warning(f"Failed to sync dataset to Supabase: {sup_err}")

        return canonical_excel
    except Exception as e:
        logger.warning(f"auto_sort_into_app_batch_store warning: {e}")
        return None


def update_job_progress(job_id: str, progress: float, message: str) -> None:
    """Thread-safe update of job status and progress bar."""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            jobs[job_id]["message"] = message
            m = re.search(r"[Pp]age\s+(\d+)\s*(?:/|of)\s*(\d+)", message)
            if m:
                curr_p = int(m.group(1))
                tot_p = int(m.group(2))
                jobs[job_id]["current_page"] = curr_p
                jobs[job_id]["total_pages"] = tot_p
                jobs[job_id]["pages_processed"] = curr_p
                jobs[job_id]["pages_remaining"] = max(0, tot_p - curr_p)


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
        with jobs_lock:
            ds_meta = jobs.get(job_id, {}).get("dataset_meta")

        # Auto-sort clean Excel into departments_data/excel/<Department>/<Batch>/<Semester>.xlsx and SQLite DB
        auto_sort_into_app_batch_store(
            excel_path=full_excel_path,
            meta=meta,
            source_path=target_pdf_path,
            students=flat_records,
            catalog=meta.catalog if meta else None,
            dataset_meta=ds_meta
        )

        # Persist dataset_meta into catalog.json as persistent single source of truth
        if ds_meta:
            try:
                cat_full = os.path.join(des_dir, json_filename)
                if os.path.isfile(cat_full):
                    with open(cat_full, "r", encoding="utf-8") as f:
                        cat_content = json.load(f)
                    cat_content["dataset_meta"] = ds_meta
                    with open(cat_full, "w", encoding="utf-8") as f:
                        json.dump(cat_content, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to append dataset_meta to catalog.json: {e}")

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
            "dataset_meta": ds_meta,
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

        # Auto-archive to dedicated historical_data/ in Supabase Storage (FIFO 30)
        try:
            import threading
            from services.supabase_service import get_supabase_service
            supabase_svc = get_supabase_service()
            if supabase_svc.is_configured and flat_records:
                threading.Thread(
                    target=supabase_svc.save_historical_run,
                    kwargs={
                        "source_name": os.path.basename(str(pdf_path)),
                        "students": flat_records,
                        "catalog": meta.catalog.to_dict() if (meta and hasattr(meta, "catalog") and meta.catalog) else {},
                        "meta": meta,
                        "dataset_meta": ds_meta,
                        "run_id": f"run_{time.strftime('%Y%m%d_%H%M%S')}_{safe_folder_name(folder_name)}"
                    },
                    daemon=True,
                    name=f"HistoricalArchive-{job_id}"
                ).start()
        except Exception as arc_e:
            logger.debug(f"Historical archive notice: {arc_e}")

    except Exception as e:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)
