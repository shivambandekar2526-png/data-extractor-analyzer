import os
import re
import json
import logging
import importlib
from typing import Dict, Any, List
import pandas as pd
from flask import Blueprint, request, jsonify, send_file

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.state import DB_PATH
from services.extraction_service import safe_folder_name
from services.academic_service import (
    get_departments_data_excel_hierarchy,
    resolve_subject_dataset,
    _standardize_academic_year,
    _standardize_semester_label
)
from core.dataset_database import list_datasets_from_db
from core.academic_classifier import (
    AcademicClassificationRules,
    analyze_subject_performance,
    extract_subject_definitions
)
from core.semester_analyzer import analyze_semester_performance
from core.subject_docx_exporter import generate_subject_analysis_docx
import core.semester_docx_exporter

analysis_bp = Blueprint("analysis_api", __name__)

@analysis_bp.route("/api/subject-analysis/options", methods=["GET"])
def get_subject_analysis_options():
    """
    Returns available Department -> Batch -> Semester hierarchy
    from department_data/excel along with cascading options.
    """
    # 1. Hierarchical department structure from department_data/excel
    dept_hierarchy = get_departments_data_excel_hierarchy()

    dataset_list = []
    seen_ids = set()

    # Flatten department hierarchy into dataset_list for backward compatibility
    for d in dept_hierarchy:
        for b in d.get("batches", []):
            for s in b.get("semesters", []):
                item_id = s["dataset_id"]
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                dataset_list.append({
                    "id": item_id,
                    "display_name": f"{s['file_name']} ({d['name']} • {b['folder']})",
                    "academic_year": b["academic_year"],
                    "branch": d["name"],
                    "semester": s["semester_name"],
                    "source_type": "Excel Spreadsheet",
                    "total_students": 0,
                    "rel_path": s["rel_path"]
                })

    # Output directory gazettes (for historical PDF viewing)
    if os.path.isdir(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            fpath = os.path.join(OUTPUT_DIR, entry)
            if not os.path.isdir(fpath):
                continue
            f_clean = safe_folder_name(entry)
            cat_p = os.path.join(fpath, f"{f_clean}_catalog.json")
            cat_meta = {}
            if os.path.isfile(cat_p):
                try:
                    with open(cat_p, mode="r", encoding="utf-8") as f:
                        cat_meta = json.load(f)
                except Exception:
                    pass

            total_st = cat_meta.get("total_students", 0)
            if not total_st:
                csv_p = os.path.join(fpath, f"{f_clean}_extracted_data.csv")
                if os.path.isfile(csv_p):
                    try:
                        with open(csv_p, "r", encoding="utf-8-sig") as cf:
                            total_st = max(0, sum(1 for _ in cf) - 1)
                    except Exception:
                        pass

            is_8th = "8th" in f_clean or "VIII" in str(cat_meta.get("semester", "")).upper()
            branches_for_entry = ["Computer Engineering", "Artificial Intelligence and Data Science"] if is_8th else ["Artificial Intelligence and Data Science"]
            ay = "2024–2025" if is_8th else "2021–2022"
            sem = "Semester VIII" if is_8th else "Semester I"

            for b_name in branches_for_entry:
                item_id = f"out_{f_clean}_{b_name.replace(' ', '_')}"
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                dataset_list.append({
                    "id": f"out_{f_clean}",
                    "display_name": f"{f_clean} (May 2025 Gazette • {total_st} students)" if is_8th else f"{f_clean} ({total_st} students)",
                    "academic_year": ay,
                    "branch": b_name,
                    "semester": sem,
                    "source_type": "PDF Gazette",
                    "total_students": total_st,
                    "folder_name": f_clean
                })

    # Database Datasets
    try:
        db_entries = list_datasets_from_db(DB_PATH)
        for ds in db_entries:
            b_label = ds["department"].replace("_", " ")
            ay_label = _standardize_academic_year(ds["batch"])
            sem_label = _standardize_semester_label(ds["semester"])
            item_id = f"db_{ds['department']}_{ds['batch']}_{ds['semester']}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            dataset_list.append({
                "id": item_id,
                "display_name": f"{ds['department']} - {ds['semester']} ({ds['total_students']} students)",
                "academic_year": ay_label,
                "branch": b_label,
                "semester": sem_label,
                "source_type": "Database Saved",
                "total_students": ds.get("total_students", 0)
            })
    except Exception as e:
        logger.warning(f"Error fetching DB datasets for options: {e}")

    # Extract distinct cascading values
    unique_years = sorted(list({d["academic_year"] for d in dataset_list}), reverse=True)
    unique_branches = sorted(list({d["branch"] for d in dataset_list}))

    sem_order = [
        "Semester I", "Semester II", "Semester III", "Semester IV",
        "Semester V", "Semester VI", "Semester VII", "Semester VIII"
    ]
    discovered_sems = {d["semester"] for d in dataset_list}
    unique_semesters = [s for s in sem_order if s in discovered_sems] + sorted(list(discovered_sems - set(sem_order)))

    return jsonify({
        "success": True,
        "departments": dept_hierarchy,
        "academic_years": unique_years,
        "branches": unique_branches,
        "semesters": unique_semesters,
        "datasets": dataset_list
    })


@analysis_bp.route("/api/subject-analysis/subjects", methods=["POST"])
def get_subject_analysis_subjects():
    """
    Dynamically returns ONLY the subjects that exist in the selected Excel sheet.
    Accepts dataset_id, rel_path, or (department, batch, semester_file).
    List subjects smartly using _Subject columns.
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get("dataset_id", "").strip()
    rel_path = data.get("rel_path", "").strip()

    if not dataset_id and rel_path:
        dataset_id = f"excel_{rel_path}"

    dept = data.get("department", "").strip()
    batch = data.get("batch", "").strip()
    sem_file = (data.get("semester_file") or data.get("file_name") or "").strip()
    if not dataset_id and dept and batch and sem_file:
        dataset_id = f"excel_{dept}/{batch}/{sem_file}"

    if not dataset_id:
        return jsonify({"success": False, "error": "dataset_id or relative path is required."}), 400

    records, catalog, meta = resolve_subject_dataset(dataset_id)
    if not records:
        return jsonify({
            "success": False,
            "error": f"No student records found in dataset '{dataset_id}'."
        }), 404

    df = pd.DataFrame(records)
    subjects = extract_subject_definitions(df, catalog)

    return jsonify({
        "success": True,
        "total_records": len(df),
        "dataset_meta": meta,
        "subjects": subjects
    })


@analysis_bp.route("/api/subject-analysis/analyze", methods=["POST"])
def run_subject_analysis():
    """
    Executes complete deterministic subject-wise academic analysis.
    Calculates Theory summary, Pass/Fail table & graph, Classifications,
    Adaptive Marks Distribution, Term Work, Practical/Oral, and Top 5 students.
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get("dataset_id", "").strip()
    rel_path = data.get("rel_path", "").strip()
    if not dataset_id and rel_path:
        dataset_id = f"excel_{rel_path}"

    dept = data.get("department", "").strip()
    batch = data.get("batch", "").strip()
    sem_file = (data.get("semester_file") or data.get("file_name") or "").strip()
    if not dataset_id and dept and batch and sem_file:
        dataset_id = f"excel_{dept}/{batch}/{sem_file}"

    subject_code = data.get("subject_code", "").strip()

    if not dataset_id or not subject_code:
        return jsonify({
            "success": False,
            "error": "Both 'dataset_id' (or Excel path) and 'subject_code' are required."
        }), 400

    records, catalog, meta = resolve_subject_dataset(dataset_id)
    if not records:
        return jsonify({
            "success": False,
            "error": f"No data found for dataset '{dataset_id}'."
        }), 404

    # Allow frontend overrides for display header
    if data.get("academic_year"):
        meta["academic_year"] = data["academic_year"]
    if data.get("branch"):
        meta["branch"] = data["branch"]
    if data.get("semester"):
        meta["semester"] = data["semester"]

    try:
        analysis_result = analyze_subject_performance(
            records=records,
            catalog=catalog,
            subject_code=subject_code,
            dataset_meta=meta
        )
        return jsonify(analysis_result)
    except Exception as e:
        logger.error(f"Subject analysis computation failed: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Analysis calculation failed: {str(e)}"
        }), 500


@analysis_bp.route("/api/subject-analysis/export-docx", methods=["POST"])
def export_subject_analysis_docx():
    """
    Exports a 1-page institutional Microsoft Word document (.docx)
    for Terna Engineering College, including Theory summary, Term Work,
    Practical/Oral analysis, and Top 5 subject merit leaders.
    """
    from core.subject_docx_exporter import generate_subject_analysis_docx

    data = request.get_json(silent=True) or {}
    analysis_data = data.get("analysis_data")
    department = data.get("department", "").strip()
    batch = data.get("batch", "").strip()
    semester = data.get("semester", "").strip()

    if not analysis_data:
        # Resolve via dataset_id & subject_code if analysis_data not directly posted
        dataset_id = data.get("dataset_id", "").strip()
        rel_path = data.get("rel_path", "").strip()
        if not dataset_id and rel_path:
            dataset_id = f"excel_{rel_path}"

        dept = data.get("department", "").strip()
        b_val = data.get("batch", "").strip()
        sem_file = (data.get("semester_file") or data.get("file_name") or "").strip()
        if not dataset_id and dept and b_val and sem_file:
            dataset_id = f"excel_{dept}/{b_val}/{sem_file}"

        subject_code = data.get("subject_code", "").strip()

        if not dataset_id or not subject_code:
            return jsonify({
                "success": False,
                "error": "Either 'analysis_data' or ('dataset_id' and 'subject_code') is required."
            }), 400

        records, catalog, meta = resolve_subject_dataset(dataset_id)
        if not records:
            return jsonify({
                "success": False,
                "error": f"Dataset '{dataset_id}' not found."
            }), 404

        if department:
            meta["department"] = department
            meta["branch"] = department
        if batch:
            meta["batch"] = batch
        if semester:
            meta["semester"] = semester

        try:
            analysis_data = analyze_subject_performance(records, catalog, subject_code, dataset_meta=meta)
        except Exception as e:
            return jsonify({"success": False, "error": f"Failed to compute analysis for docx export: {e}"}), 500

    try:
        hdr = analysis_data.get("header", {})
        dept_val = department or hdr.get("department") or hdr.get("branch") or "Engineering"
        batch_val = batch or hdr.get("batch") or hdr.get("academic_year") or "Batch"
        sem_val = semester or hdr.get("semester") or "Semester"

        buf = generate_subject_analysis_docx(
            analysis_data=analysis_data,
            department_name=dept_val,
            batch_name=batch_val,
            semester_name=sem_val
        )

        safe_subj = "".join(c for c in (hdr.get("theory_code") or hdr.get("subject_code") or "Subject") if c.isalnum() or c in ("_", "-"))
        safe_batch = "".join(c for c in batch_val if c.isalnum() or c in ("_", "-"))
        filename = f"Terna_Subject_Analysis_{safe_subj}_{safe_batch}.docx"

        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Docx export failed: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Docx generation failed: {str(e)}"
        }), 500



# =================================================================
# PHASE 5: SEMESTER-WISE ACADEMIC ANALYSIS ROUTES
# =================================================================

@analysis_bp.route("/api/semester-analysis/options", methods=["GET"])
def get_semester_analysis_options():
    """
    Returns available Department -> Batch -> Semester hierarchy
    for Semester-Wise Analysis.
    """
    dept_hierarchy = get_departments_data_excel_hierarchy()
    return jsonify({
        "success": True,
        "departments": dept_hierarchy
    })


@analysis_bp.route("/api/semester-analysis/analyze", methods=["POST"])
def run_semester_analysis():
    """
    Executes complete deterministic semester-wise academic analysis.
    Calculates Appeared, Passed, Failed, Pass %, Fail %, Semester Success Rate,
    Institutional Classifications, SGPI Distribution, and Top 5 Semester Toppers.
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get("dataset_id", "").strip()

    if not dataset_id:
        return jsonify({"success": False, "error": "'dataset_id' is required."}), 400

    records, catalog, meta = resolve_subject_dataset(dataset_id)
    if not records:
        return jsonify({
            "success": False,
            "error": f"No student records found in dataset '{dataset_id}'."
        }), 404

    # Inject overrides if provided from client selection
    if data.get("branch"):
        meta["branch"] = data["branch"]
    if data.get("academic_year"):
        meta["academic_year"] = data["academic_year"]
    if data.get("semester"):
        meta["semester"] = data["semester"]

    try:
        from core.semester_analyzer import analyze_semester_performance
        analysis_result = analyze_semester_performance(
            records=records,
            dataset_meta=meta,
            catalog=catalog
        )
        return jsonify(analysis_result)
    except Exception as e:
        logger.error(f"Semester analysis computation failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@analysis_bp.route("/api/semester-analysis/export-docx", methods=["POST"])
def export_semester_analysis_docx():
    """
    Generates and downloads publication-grade, 1-page Microsoft Word (.docx)
    academic performance report for the entire semester.
    """
    import importlib
    import core.semester_docx_exporter
    importlib.reload(core.semester_docx_exporter)
    generate_semester_analysis_docx = core.semester_docx_exporter.generate_semester_analysis_docx
    data = request.get_json(silent=True) or {}

    analysis_data = data.get("analysis_data")
    department = data.get("department", "").strip()
    batch = data.get("batch", "").strip()
    semester = data.get("semester", "").strip()

    if not analysis_data:
        dataset_id = data.get("dataset_id", "").strip()
        if not dataset_id:
            return jsonify({
                "success": False,
                "error": "Either 'analysis_data' or 'dataset_id' is required."
            }), 400

        records, catalog, meta = resolve_subject_dataset(dataset_id)
        if not records:
            return jsonify({
                "success": False,
                "error": f"No student records found in dataset '{dataset_id}'."
            }), 404

        if department:
            meta["branch"] = department
        if batch:
            meta["academic_year"] = batch
        if semester:
            meta["semester"] = semester

        try:
            from core.semester_analyzer import analyze_semester_performance
            analysis_data = analyze_semester_performance(records, dataset_meta=meta, catalog=catalog)
        except Exception as e:
            return jsonify({"success": False, "error": f"Failed to compute analysis: {str(e)}"}), 500

    try:
        hdr = analysis_data.get("header", {})
        dept_val = department or hdr.get("department") or hdr.get("branch") or "Department"
        batch_val = batch or hdr.get("batch") or hdr.get("academic_year") or "Batch"
        sem_val = semester or hdr.get("semester") or "Semester"

        buf = generate_semester_analysis_docx(
            analysis_data=analysis_data,
            department_name=dept_val,
            batch_name=batch_val,
            semester_name=sem_val
        )

        safe_sem = "".join(c for c in sem_val if c.isalnum() or c in ("_", "-"))
        safe_batch = "".join(c for c in batch_val if c.isalnum() or c in ("_", "-"))
        filename = f"Terna_Semester_Analysis_{safe_sem}_{safe_batch}.docx"

        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Semester Docx export failed: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Docx generation failed: {str(e)}"
        }), 500



# =================================================================
# LIVE FOLDER WATCHER & ACTION LISTENER ROUTES
# =================================================================

