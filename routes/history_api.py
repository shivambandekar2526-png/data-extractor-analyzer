import os
import io
import re
import csv
import json
import time
import stat
import shutil
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, send_from_directory
import pandas as pd

logger = logging.getLogger("app")

from services.config import (
    OUTPUT_DIR, DEPARTMENTS_DATA_DIR, APP_DEPARTMENTS_DATA_DIR
)
from services.state import DB_PATH, jobs, jobs_lock
from services.extraction_service import safe_folder_name, auto_sort_into_app_batch_store
from services.crosscheck_service import _find_pdf_for_folder
from core.dataset_database import (
    list_datasets_from_db, get_dataset_from_db, delete_dataset_from_db, save_dataset_to_db
)
from core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
from core.batch_analyzer import BatchAcademicAnalyzer
from core.pdf_classifier import get_canonical_pdf_path
from exporter import write_csv_file, write_excel_file
from models import ExamMeta

history_bp = Blueprint("history_api", __name__)


def _norm_history_key(s: str) -> str:
    if not s:
        return ""
    clean = str(s).replace("supabase_", "").replace("db_", "").replace("excel/", "").replace(".xlsx", "").replace(".pdf", "")
    clean = re.sub(r"[\s_\-]+", "_", clean.lower()).strip("_")
    clean = clean.replace("semester", "sem")
    return clean


def _match_cloud_semester(folder_identifier: str, cloud_hier: list):
    """
    Given a folder identifier (synth_id, raw folder name, or path), finds the
    matching semester entry, batch entry, and department entry in cloud_hier.
    Returns (semester_dict, batch_dict, dept_dict) or (None, None, None).
    """
    if not folder_identifier or not cloud_hier:
        return None, None, None
    nt = _norm_history_key(folder_identifier)
    for d in cloud_hier:
        for b in d.get("batches", []):
            for s in b.get("semesters", []):
                s_name = s.get("semester_name") or ""
                f_stem = os.path.splitext(s.get("file_name", ""))[0]
                r_stem = os.path.splitext(s.get("rel_path", ""))[0]
                candidates = [
                    _norm_history_key(f"{d['id']}_{b['id']}_{s_name}"),
                    _norm_history_key(f"{d['id']}_{b['id']}_{f_stem}"),
                    _norm_history_key(r_stem),
                    _norm_history_key(s.get("storage_path", ""))
                ]
                if nt in candidates or any(c == nt for c in candidates):
                    return s, b, d
    return None, None, None


@history_bp.route("/api/history", methods=["GET"])
def list_history():
    """
    List historical extractions with comprehensive metadata.
    Combines output/ job records with SQLite academic_records.db datasets,
    sorted by most recently extracted/saved first.
    """
    history_entries = []
    seen_keys = set()

    # 1. Inspect output/ directory (direct extraction jobs)
    if os.path.isdir(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            fpath = os.path.join(OUTPUT_DIR, entry)
            if not os.path.isdir(fpath):
                continue
            # Batch analysis outputs are summary reports, not ledger extraction records
            if entry.startswith("batch_analysis_"):
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

            ds_meta = meta.get("dataset_meta") or {}
            dept = ds_meta.get("department") or meta.get("department") or meta.get("college_name") or "Artificial_Intelligence_and_Data_Science"
            batch = ds_meta.get("batch") or meta.get("academic_year") or "21-22"
            sem = ds_meta.get("semester") or meta.get("semester") or "Semester_1"
            total_students = meta.get("total_students", 0)

            # Check if CSV exists to verify records count
            csv_path = os.path.join(fpath, f"{entry}_extracted_data.csv")
            if os.path.isfile(csv_path) and not total_students:
                try:
                    with open(csv_path, "r", encoding="utf-8-sig") as cf:
                        total_students = max(0, sum(1 for _ in cf) - 1)
                except Exception:
                    pass

            item_key = f"{dept}_{batch}_{sem}".lower()
            seen_keys.add(item_key)

            history_entries.append({
                "id": entry,
                "folder_name": entry,
                "display_name": entry.replace("_", " "),
                "department": dept,
                "batch": batch,
                "semester": sem,
                "total_students": total_students,
                "archetype": meta.get("archetype", "gazette_spool"),
                "source_type": "PDF" if (ds_meta.get("pdf_path") or os.path.isfile(os.path.join(fpath, f"{entry}.pdf"))) else "Excel / Ingested",
                "status": "Verified",
                "created_at": created_str,
                "timestamp": created_str,
                "pdf_path": ds_meta.get("pdf_path"),
                "excel_path": ds_meta.get("excel_path") or os.path.join(fpath, f"{entry}_extracted_data.xlsx"),
                "_mtime": mtime
            })

    # 2. Also incorporate saved SQLite datasets from academic_records.db if not already in history
    try:
        db_datasets = list_datasets_from_db(DB_PATH)
        for ds in db_datasets:
            d_dept = ds.get("department")
            d_batch = ds.get("batch")
            d_sem = ds.get("semester")
            ds_key = f"{d_dept}_{d_batch}_{d_sem}".lower()
            if ds_key in seen_keys:
                continue

            upd_time = ds.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S")
            # Parse approximate timestamp for sorting
            try:
                import datetime
                mtime_approx = datetime.datetime.strptime(upd_time, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                mtime_approx = 0.0

            synth_id = f"db_{d_dept}_{d_batch}_{d_sem}"
            history_entries.append({
                "id": synth_id,
                "folder_name": synth_id,
                "display_name": f"{d_dept} - {d_batch} ({d_sem})",
                "department": d_dept,
                "batch": d_batch,
                "semester": d_sem,
                "total_students": ds.get("total_students", 0),
                "archetype": "tabulation",
                "source_type": "Database / Excel",
                "status": "Verified",
                "created_at": upd_time,
                "timestamp": upd_time,
                "pdf_path": ds.get("pdf_path"),
                "excel_path": ds.get("excel_path"),
                "_mtime": mtime_approx
            })
    except Exception as e:
        logger.warning(f"Error querying SQLite datasets for history: {e}")

    # 3. Incorporate datasets from Supabase Cloud
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            for dept_item in cloud_hier:
                d_dept = dept_item["id"]
                for b_item in dept_item.get("batches", []):
                    d_batch = b_item["id"]
                    for s_item in b_item.get("semesters", []):
                        d_sem = s_item.get("semester_name") or os.path.splitext(s_item.get("file_name", ""))[0]
                        ds_key = f"{d_dept}_{d_batch}_{d_sem}".lower()
                        if ds_key in seen_keys:
                            continue
                        seen_keys.add(ds_key)
                        d_sem_clean = str(d_sem).replace(" ", "_")
                        synth_id = f"db_{d_dept}_{d_batch}_{d_sem_clean}"
                        history_entries.append({
                            "id": synth_id,
                            "folder_name": synth_id,
                            "display_name": f"{dept_item.get('name', d_dept)} - {d_batch} ({d_sem})",
                            "department": d_dept,
                            "batch": d_batch,
                            "semester": d_sem,
                            "total_students": s_item.get("total_students", 0),
                            "archetype": "tabulation",
                            "source_type": "Supabase Cloud",
                            "status": "Verified",
                            "created_at": s_item.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                            "timestamp": s_item.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                            "pdf_path": s_item.get("pdf_storage_path"),
                            "excel_path": s_item.get("storage_path"),
                            "_mtime": 0.0
                        })
    except Exception as sup_e:
        logger.warning(f"Error querying Supabase datasets for history: {sup_e}")

    # Sort most recent extractions first
    history_entries.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    for e in history_entries:
        e.pop("_mtime", None)

    resp = jsonify({"success": True, "history": history_entries})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@history_bp.route("/api/history/<path:folder_name>", methods=["GET"])
def load_history_entry(folder_name):
    """
    Load extracted records from history.
    Supports Supabase Cloud datasets, SQLite synth_ids, or ephemeral output/ folder names.
    """
    clean_folder = safe_folder_name(folder_name)

    # 1. Try finding match in SQLite database if DB exists
    try:
        db_datasets = list_datasets_from_db(DB_PATH)
        matched = None
        for ds in db_datasets:
            test_id = f"db_{ds['department']}_{ds['batch']}_{ds['semester']}"
            if test_id.lower() in (clean_folder.lower(), folder_name.lower()):
                matched = ds
                break
        if matched:
            db_data = get_dataset_from_db(
                DB_PATH,
                matched["department"],
                matched["batch"],
                matched["semester"]
            )
            if db_data:
                students = db_data.get("students", [])
                ds_meta = {
                    "department": matched["department"],
                    "batch": matched["batch"],
                    "semester": matched["semester"],
                    "pdf_path": matched.get("pdf_path"),
                    "excel_path": matched.get("excel_path")
                }
                return jsonify({
                    "success": True,
                    "folder_name": clean_folder,
                    "csv_file": f"{clean_folder}_extracted_data.csv",
                    "excel_file": f"{clean_folder}_extracted_data.xlsx",
                    "json_file": f"{clean_folder}_catalog.json",
                    "academic_report_file": f"{clean_folder}_Academic_Report.xlsx",
                    "total_students": len(students),
                    "students": students,
                    "dataset_meta": ds_meta,
                    "meta": {
                        "exam_title": f"{matched['department']} {matched['batch']} {matched['semester']}",
                        "semester": matched["semester"],
                        "college_name": matched["department"],
                        "archetype": "tabulation",
                        "dataset_meta": ds_meta
                    }
                })
    except Exception as e:
        logger.debug(f"SQLite check bypassed/not present: {e}")

    # 2. Check Supabase Cloud via robust hierarchy matcher
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            s_item, b_item, d_item = _match_cloud_semester(folder_name, cloud_hier)
            if not s_item:
                s_item, b_item, d_item = _match_cloud_semester(clean_folder, cloud_hier)

            if s_item and s_item.get("rel_path"):
                from services.academic_service import resolve_subject_dataset
                records, catalog, meta = resolve_subject_dataset(f"supabase_excel_{s_item['rel_path']}")
                if records is not None:
                    dept_id = (d_item and d_item.get("id")) or meta.get("department") or ""
                    dept_name = (d_item and d_item.get("name")) or meta.get("department") or dept_id
                    batch_id = (b_item and b_item.get("id")) or meta.get("batch") or ""
                    sem_name = (s_item and s_item.get("semester_name")) or meta.get("semester") or ""

                    ds_meta = {
                        "department": dept_id,
                        "batch": batch_id,
                        "semester": sem_name,
                        "excel_path": s_item.get("storage_path"),
                        "pdf_path": s_item.get("pdf_storage_path")
                    }
                    academic_fn = get_academic_filename(clean_folder, {
                        "dataset_meta": ds_meta,
                        "department": dept_id,
                        "college_name": dept_name,
                        "batch": batch_id,
                        "semester": sem_name
                    })
                    return jsonify({
                        "success": True,
                        "folder_name": clean_folder,
                        "csv_file": f"{clean_folder}_extracted_data.csv",
                        "excel_file": f"{clean_folder}_extracted_data.xlsx",
                        "json_file": f"{clean_folder}_catalog.json",
                        "academic_report_file": academic_fn,
                        "total_students": len(records),
                        "students": records,
                        "dataset_meta": ds_meta,
                        "meta": {
                            "exam_title": meta.get("dataset_name") or f"{dept_name} {batch_id} {sem_name}",
                            "semester": sem_name or meta.get("semester"),
                            "college_name": dept_name,
                            "archetype": "tabulation",
                            "dataset_meta": ds_meta
                        }
                    })
    except Exception as sup_load_err:
        logger.warning(f"Failed loading Supabase dataset for history item {clean_folder}: {sup_load_err}")

    # 3. Otherwise fall back to local output/<clean_folder>
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"success": False, "error": f"History dataset '{folder_name}' not found."}), 404

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
        "meta": cat_meta,
        "dataset_meta": cat_meta.get("dataset_meta")
    })


def _handle_remove_readonly(func, path, exc_info):
    """Clear readonly bit and reattempt removal on Windows files."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        logger.warning(f"Failed clearing readonly flag on {path}: {e}")


@history_bp.route("/api/history/<path:folder_name>", methods=["DELETE"])
def delete_history_entry(folder_name):
    """
    Delete an extraction record from history.
    Requires explicit user confirmation from UI.
    Removes output/ entry folder and removes SQLite/Supabase datasets entry if applicable.
    Safely leaves original source documents intact.
    """
    clean_folder = safe_folder_name(folder_name)
    deleted_any = False

    # 1. Check SQLite database (match by synth_id or department/batch/semester)
    try:
        db_datasets = list_datasets_from_db(DB_PATH)
        for ds in db_datasets:
            test_id = f"db_{ds['department']}_{ds['batch']}_{ds['semester']}"
            plain_id = f"{ds['department']}_{ds['batch']}_{ds['semester']}"
            candidates = {
                test_id.lower(),
                safe_folder_name(test_id).lower(),
                plain_id.lower(),
                safe_folder_name(plain_id).lower()
            }
            if clean_folder.lower() in candidates or folder_name.lower() in candidates:
                delete_dataset_from_db(DB_PATH, ds["department"], ds["batch"], ds["semester"])
                excel_p = ds.get("excel_path")
                if excel_p and os.path.isfile(excel_p):
                    try:
                        os.remove(excel_p)
                    except Exception as err:
                        logger.warning(f"Could not remove dataset excel file {excel_p}: {err}")
                deleted_any = True
                break
    except Exception as e:
        logger.warning(f"Error checking/removing dataset from SQLite: {e}")

    # 2. Check Supabase Cloud catalog and storage
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            s_item, b_item, d_item = _match_cloud_semester(folder_name, cloud_hier)
            if not s_item:
                s_item, b_item, d_item = _match_cloud_semester(clean_folder, cloud_hier)
            if s_item and d_item and b_item:
                sem_name = s_item.get("semester_name") or os.path.splitext(s_item.get("file_name", ""))[0]
                if supabase_svc.delete_dataset_record(d_item["id"], b_item["id"], sem_name):
                    deleted_any = True
    except Exception as e:
        logger.warning(f"Error removing dataset from Supabase: {e}")

    # 3. Check output/ directory (test both clean_folder and raw folder_name)
    targets = [t for t in [clean_folder, folder_name] if t]
    for target_name in targets:
        folder_path = os.path.join(OUTPUT_DIR, target_name)
        if os.path.isdir(folder_path):
            try:
                cat_file = os.path.join(folder_path, f"{target_name}_catalog.json")
                if not os.path.isfile(cat_file):
                    for f in os.listdir(folder_path):
                        if f.endswith("_catalog.json"):
                            cat_file = os.path.join(folder_path, f)
                            break
                if os.path.isfile(cat_file):
                    try:
                        with open(cat_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        dsm = meta.get("dataset_meta") or {}
                        d_dept = dsm.get("department") or meta.get("department") or meta.get("college_name")
                        d_batch = dsm.get("batch") or meta.get("academic_year")
                        d_sem = dsm.get("semester") or meta.get("semester")
                        if d_dept and d_batch and d_sem:
                            delete_dataset_from_db(DB_PATH, str(d_dept), str(d_batch), str(d_sem))
                    except Exception as err:
                        logger.warning(f"Error reading catalog to delete from DB: {err}")

                try:
                    shutil.rmtree(folder_path, onexc=_handle_remove_readonly)
                except TypeError:
                    shutil.rmtree(folder_path, onerror=_handle_remove_readonly)
                deleted_any = True
            except Exception as e:
                return jsonify({"success": False, "error": f"Failed deleting folder: {str(e)}"}), 500

    # 4. Synchronize in-memory job state
    with jobs_lock:
        to_del = [jid for jid, job in jobs.items() if job.get("folder_name") in (clean_folder, folder_name)]
        for jid in to_del:
            jobs.pop(jid, None)

    if not deleted_any:
        return jsonify({"success": False, "error": f"Dataset '{folder_name}' not found."}), 404

    resp = jsonify({
        "success": True,
        "message": f"Successfully deleted historical extraction '{folder_name}'."
    })
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@history_bp.route("/api/download/<folder>/<filename>", methods=["GET"])
def download_extracted_file(folder, filename):
    """Download exported CSV, Excel, or JSON, with streaming from Supabase Storage."""
    clean_folder = safe_folder_name(folder)
    clean_filename = safe_folder_name(filename)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)
    if os.path.isdir(folder_path):
        fpath = os.path.join(folder_path, clean_filename)
        if os.path.isfile(fpath):
            return send_from_directory(folder_path, clean_filename, as_attachment=True)

    # Stream from Supabase Storage
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            s_item, b_item, d_item = _match_cloud_semester(folder, cloud_hier)
            if not s_item:
                s_item, b_item, d_item = _match_cloud_semester(clean_folder, cloud_hier)

            if s_item:
                is_pdf = clean_filename.lower().endswith(".pdf")
                is_csv = clean_filename.lower().endswith(".csv")
                is_json = clean_filename.lower().endswith(".json")

                if is_pdf and s_item.get("pdf_storage_path"):
                    pdf_bytes = supabase_svc.download_file_bytes(s_item["pdf_storage_path"])
                    if pdf_bytes:
                        return send_file(io.BytesIO(pdf_bytes), download_name=clean_filename, as_attachment=True, mimetype="application/pdf")

                elif is_csv and s_item.get("rel_path"):
                    from services.academic_service import resolve_subject_dataset
                    records, _, _ = resolve_subject_dataset(f"supabase_excel_{s_item['rel_path']}")
                    if records:
                        output = io.StringIO()
                        writer = csv.DictWriter(output, fieldnames=list(records[0].keys()))
                        writer.writeheader()
                        writer.writerows(records)
                        csv_bytes = output.getvalue().encode("utf-8-sig")
                        return send_file(io.BytesIO(csv_bytes), download_name=clean_filename, as_attachment=True, mimetype="text/csv")

                elif is_json and s_item.get("rel_path"):
                    from services.academic_service import resolve_subject_dataset
                    records, cat, meta = resolve_subject_dataset(f"supabase_excel_{s_item['rel_path']}")
                    json_bytes = json.dumps({"students": records or [], "catalog": cat or {}, "meta": meta or {}}, indent=2).encode("utf-8")
                    return send_file(io.BytesIO(json_bytes), download_name=clean_filename, as_attachment=True, mimetype="application/json")

                elif s_item.get("storage_path"):
                    excel_bytes = supabase_svc.download_file_bytes(s_item["storage_path"])
                    if excel_bytes:
                        return send_file(io.BytesIO(excel_bytes), download_name=clean_filename, as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        logger.warning(f"Error streaming download from Supabase: {e}")

    return jsonify({"success": False, "error": f"File '{clean_filename}' not found."}), 404


@history_bp.route("/api/export/academic-analysis/<folder>", methods=["GET"])
def export_academic_analysis(folder):
    """
    Generate and stream the publication-grade 6-sheet Academic Analysis Workbook
    (e.g. '21-22 sem 8.xlsx') matching the exact departmental Google Sheet format.
    Streams directly from Supabase Cloud or disk buffer.
    """
    clean_folder = safe_folder_name(folder)
    folder_path = os.path.join(OUTPUT_DIR, clean_folder)

    # 1. Check local output/<clean_folder>
    if os.path.isdir(folder_path):
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

        if records:
            academic_filename = get_academic_filename(clean_folder, cat_meta)
            academic_path = os.path.join(folder_path, academic_filename)
            try:
                generator = AcademicExcelReportGenerator(
                    students=records,
                    catalog=cat_meta,
                    meta=cat_meta,
                    folder_name=clean_folder,
                    fieldnames=fieldnames
                )
                generator.generate_workbook(academic_path)
                return send_from_directory(folder_path, academic_filename, as_attachment=True)
            except Exception as e:
                return jsonify({"success": False, "error": f"Failed to generate academic analysis report: {str(e)}"}), 500

    # 2. Check Supabase Cloud directly
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            s_item, b_item, d_item = _match_cloud_semester(folder, cloud_hier)
            if not s_item:
                s_item, b_item, d_item = _match_cloud_semester(clean_folder, cloud_hier)

            if s_item and s_item.get("rel_path"):
                from services.academic_service import resolve_subject_dataset
                records, catalog_meta, meta = resolve_subject_dataset(f"supabase_excel_{s_item['rel_path']}")
                if records:
                    dept_id = (d_item and d_item.get("id")) or meta.get("department") or ""
                    dept_name = (d_item and d_item.get("name")) or meta.get("department") or dept_id
                    batch_id = (b_item and b_item.get("id")) or meta.get("batch") or ""
                    sem_name = (s_item and s_item.get("semester_name")) or meta.get("semester") or ""

                    ds_meta = {
                        "department": dept_id,
                        "batch": batch_id,
                        "semester": sem_name
                    }
                    full_meta = {
                        "dataset_meta": ds_meta,
                        "department": dept_id,
                        "college_name": dept_name,
                        "batch": batch_id,
                        "semester": sem_name
                    }
                    academic_filename = get_academic_filename(clean_folder, full_meta)
                    out_buf = io.BytesIO()
                    generator = AcademicExcelReportGenerator(
                        students=records,
                        catalog=catalog_meta or full_meta,
                        meta=full_meta,
                        folder_name=clean_folder,
                        fieldnames=list(records[0].keys())
                    )
                    wb = generator.generate_workbook()
                    wb.save(out_buf)
                    out_buf.seek(0)
                    return send_file(
                        out_buf,
                        download_name=academic_filename,
                        as_attachment=True,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
    except Exception as e:
        logger.warning(f"Error generating academic report from Supabase: {e}")

    return jsonify({"success": False, "error": f"Dataset '{folder}' not found."}), 404


@history_bp.route("/api/dataset/sync", methods=["POST"])
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

    is_dirty = data.get("is_dirty", True)
    if not is_dirty:
        return jsonify({
            "success": True,
            "message": "Dataset unmodified; no duplicate copy created.",
            "unmodified": True
        })

    if not folder_name:
        folder_name = "live_edited_results"

    clean_folder = safe_folder_name(folder_name)
    ds_meta = data.get("dataset_meta") or {}

    # If it is a SQLite dataset (db_<dept>_<batch>_<sem>), update in-place without duplicating in output/
    if clean_folder.startswith("db_"):
        try:
            db_datasets = list_datasets_from_db(DB_PATH)
            matched = None
            for ds in db_datasets:
                test_id = f"db_{ds['department']}_{ds['batch']}_{ds['semester']}"
                if test_id.lower() == clean_folder.lower():
                    matched = ds
                    break

            d_dept = (ds_meta.get("department") or (matched and matched.get("department")) or "Artificial_Intelligence_and_Data_Science")
            d_batch = (ds_meta.get("batch") or (matched and matched.get("batch")) or "21-22")
            d_sem = (ds_meta.get("semester") or (matched and matched.get("semester")) or "Semester_1")

            canonical_excel = (matched and matched.get("excel_path")) or os.path.join(DEPARTMENTS_DATA_DIR, "excel", d_dept, d_batch, f"{d_sem}.xlsx")
            os.makedirs(os.path.dirname(canonical_excel), exist_ok=True)

            if not columns and students:
                columns = list(students[0].keys())

            meta_obj = ExamMeta(exam_title=f"{d_dept}_{d_batch}_{d_sem}")
            write_excel_file(students, columns, None, meta_obj, canonical_excel)

            save_dataset_to_db(
                db_path=DB_PATH,
                department=d_dept,
                batch=d_batch,
                semester=d_sem,
                excel_path=canonical_excel,
                pdf_path=matched.get("pdf_path") if matched else None,
                students=students or [],
                catalog=catalog_data
            )

            return jsonify({
                "success": True,
                "folder_name": clean_folder,
                "csv_file": "",
                "excel_file": os.path.basename(canonical_excel),
                "json_file": "",
                "count": len(students),
                "db_saved": True,
                "canonical_excel_path": canonical_excel,
                "message": f"Successfully updated dataset in database and canonical mirror."
            })
        except Exception as e:
            logger.error(f"Error updating SQLite dataset in sync: {e}", exc_info=True)
            return jsonify({"success": False, "error": f"Failed updating database dataset: {str(e)}"}), 500

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
    canonical_excel_path = None
    if not ds_meta:
        ds_meta = existing_cat.get("dataset_meta")
    if not ds_meta:
        with jobs_lock:
            for jid, job in jobs.items():
                if job.get("folder_name") == clean_folder and job.get("dataset_meta"):
                    ds_meta = job.get("dataset_meta")
                    break

    try:
        meta_obj = ExamMeta(exam_title=clean_folder)
        write_excel_file(students, columns, None, meta_obj, excel_path)
        canonical_excel_path = auto_sort_into_app_batch_store(
            excel_path=excel_path,
            meta=meta_obj,
            source_path=clean_folder,
            students=students,
            catalog=existing_cat,
            dataset_meta=ds_meta
        )
    except Exception as e:
        print(f"[!] Note: Excel export sync warning: {e}")

    # Synchronize corresponding PDF file to canonical location under departments_data/pdf/
    canonical_pdf_path = None
    if ds_meta and isinstance(ds_meta, dict):
        s_dept = ds_meta.get("department")
        s_batch = ds_meta.get("batch")
        s_sem = ds_meta.get("semester")
        if s_dept and s_batch and s_sem:
            target_canonical_pdf = get_canonical_pdf_path(s_dept, s_batch, s_sem, DEPARTMENTS_DATA_DIR)
            # Find current source PDF for this folder/job
            source_pdf_loc = None
            if ds_meta.get("pdf_path") and os.path.isfile(ds_meta["pdf_path"]):
                source_pdf_loc = ds_meta["pdf_path"]
            if not source_pdf_loc:
                with jobs_lock:
                    for jid, job in jobs.items():
                        if job.get("folder_name") == clean_folder and job.get("dataset_meta", {}).get("pdf_path"):
                            cand_p = job["dataset_meta"]["pdf_path"]
                            if os.path.isfile(cand_p):
                                source_pdf_loc = cand_p
                                break
            if not source_pdf_loc:
                source_pdf_loc = _find_pdf_for_folder(clean_folder)

            if source_pdf_loc and os.path.isfile(source_pdf_loc):
                try:
                    src_abs = os.path.abspath(source_pdf_loc)
                    dst_abs = os.path.abspath(target_canonical_pdf)
                    if src_abs != dst_abs:
                        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
                        shutil.copy2(src_abs, dst_abs)
                        # If the old PDF was already in departments_data/pdf, remove the old one to avoid duplicates
                        norm_dept_pdf = os.path.abspath(os.path.join(DEPARTMENTS_DATA_DIR, "pdf"))
                        if src_abs.startswith(norm_dept_pdf):
                            try:
                                os.remove(src_abs)
                            except Exception:
                                pass
                    canonical_pdf_path = target_canonical_pdf
                    ds_meta["pdf_path"] = target_canonical_pdf
                    # Update dataset in SQLite with refreshed pdf_path
                    try:
                        save_dataset_to_db(
                            db_path=DB_PATH,
                            department=s_dept,
                            batch=s_batch,
                            semester=s_sem,
                            excel_path=canonical_excel_path,
                            pdf_path=target_canonical_pdf,
                            students=students or [],
                            catalog=existing_cat
                        )
                    except Exception as db_e:
                        logger.warning(f"Failed to update db with canonical pdf: {db_e}")
                except Exception as pdf_sync_err:
                    logger.warning(f"Failed synchronizing canonical PDF: {pdf_sync_err}")
            elif os.path.isfile(target_canonical_pdf):
                canonical_pdf_path = target_canonical_pdf
                ds_meta["pdf_path"] = target_canonical_pdf

    # Synchronize any matching job in memory
    with jobs_lock:
        for jid, job in jobs.items():
            if job.get("folder_name") == clean_folder:
                job["live_students"] = students
                if ds_meta:
                    job["dataset_meta"] = ds_meta
                if "result" in job and isinstance(job["result"], dict):
                    job["result"]["students"] = students
                    job["result"]["total_students"] = len(students)
                    if ds_meta:
                        job["result"]["dataset_meta"] = ds_meta

    return jsonify({
        "success": True,
        "folder_name": clean_folder,
        "csv_file": csv_filename,
        "excel_file": excel_filename,
        "json_file": cat_filename,
        "canonical_excel_path": canonical_excel_path,
        "canonical_pdf_path": canonical_pdf_path,
        "db_saved": True,
        "dataset_meta": ds_meta,
        "count": len(students),
        "columns_count": len(columns),
        "message": f"Successfully saved {len(students)} records to Database and Excel."
    })


