"""
Batch Multi-Semester Academic Analysis Service
=============================================
Background worker execution, progress reporting, and hierarchy generation for
cross-semester academic analytics.
"""

import os
import re
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.state import (
    batch_jobs, batch_jobs_lock, batch_analyzer
)
from services.academic_service import (
    get_departments_data_excel_hierarchy,
    get_dept_excel_base_dir
)
from core.batch_analyzer import detect_semester_number
from core.batch_excel_generator import BatchExcelReportGenerator


def update_batch_job_progress(job_id: str, progress: float, message: str) -> None:
    """Thread-safe update of batch analysis job status and progress bar."""
    with batch_jobs_lock:
        if job_id in batch_jobs:
            batch_jobs[job_id]["progress"] = round(min(max(float(progress), 0.0), 100.0), 1)
            batch_jobs[job_id]["message"] = message


def _run_batch_analysis_worker(
    job_id: str,
    batch_folder: str,
    target_branch: str = "AIDS",
    pdf_pipeline: str = "local",
    excel_mode: str = "direct",
    selected_semesters: Optional[List[int]] = None
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
            audit_cb=log_audit,
            selected_semesters=selected_semesters
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


def build_batch_analysis_excel_hierarchy() -> Dict[str, Any]:
    """Build hierarchy specifically from departments_data/excel for Batch-Wise Analysis."""
    dept_hierarchy = get_departments_data_excel_hierarchy()
    branches = []
    batch_list = []
    base_excel_dir = get_dept_excel_base_dir()

    for d in dept_hierarchy:
        branch_years = []
        for b in d.get("batches", []):
            sem_detected = []
            sem_numbers = []
            for s in b.get("semesters", []):
                fname = s.get("file_name", "")
                sem_num = detect_semester_number(fname)
                if sem_num:
                    sem_numbers.append(sem_num)
                fpath = os.path.join(base_excel_dir, s.get("rel_path", ""))
                size = os.path.getsize(fpath) if os.path.isfile(fpath) else 0
                sem_detected.append({
                    "semester": sem_num,
                    "filename": fname,
                    "file_name": fname,
                    "display_name": s.get("display_name"),
                    "semester_name": s.get("semester_name"),
                    "rel_path": s.get("rel_path"),
                    "dataset_id": s.get("dataset_id"),
                    "full_path": fpath,
                    "file_type": "excel",
                    "ext": os.path.splitext(fname)[1].lower(),
                    "size_bytes": size,
                    "detected_sem": sem_num,
                    "is_app_store": True,
                    "needs_auto_extract": False
                })

            sem_detected.sort(key=lambda x: (x["semester"] or 99, x["filename"]))

            yr_item = {
                "year": b["folder"],
                "folder": b["folder"],
                "id": b["folder"],
                "batch_folder": f"{d['folder']}/{b['folder']}",
                "display_name": b["name"],
                "name": b["name"],
                "academic_year": b.get("academic_year", b["folder"]),
                "branch": d["folder"],
                "total_semesters": len(sem_detected),
                "excel_count": len(sem_detected),
                "pdf_count": 0,
                "has_excel": True,
                "semesters": sem_detected,
                "semesters_detected": sem_detected,
                "semester_numbers": sem_numbers
            }
            branch_years.append(yr_item)
            batch_list.append({
                "batch_name": yr_item["batch_folder"],
                "display_name": f"{d['name']} - {b['name']}",
                "branch": d["id"],
                "branch_display": d["name"],
                "year": b["folder"],
                **yr_item
            })

        dept_item = {
            "id": d["id"],
            "folder": d["folder"],
            "name": d["name"],
            "display_name": d["name"],
            "batches": branch_years,
            "years": branch_years
        }
        branches.append(dept_item)

    return {
        "departments": branches,
        "branches": branches,
        "batches": batch_list,
        "total_branches": len(branches),
        "total_batches": len(batch_list)
    }
