"""
Academic Dataset Resolution & Hierarchy Service
===============================================
Utilities for standardizing academic years, semester labels, resolving dataset
records across folders/SQLite/Excel, and building department hierarchies.
"""

import os
import io
import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import (
    APP_DIR, PROJECT_ROOT, DEPARTMENTS_DATA_DIR,
    OUTPUT_DIR, INPUT_DIR
)
from services.state import DB_PATH
from services.extraction_service import safe_folder_name
from core.dataset_database import list_datasets_from_db, get_dataset_from_db


def _standardize_academic_year(raw_year: Any) -> str:
    """Standardizes batch/year tokens into Academic Year format (e.g. 21-22 -> 2021–2022)."""
    s = str(raw_year or "").strip()
    mapping = {
        "21-22": "2021–2022",
        "22-23": "2022–2023",
        "23-24": "2023–2024",
        "24-25": "2024–2025",
        "25-26": "2025–2026",
        "2021-2022": "2021–2022",
        "2022-2023": "2022–2023",
        "2023-2024": "2023–2024",
        "2024-2025": "2024–2025",
        "2025-2026": "2025–2026",
    }
    return mapping.get(s, s if s else "2024–2025")


def _standardize_semester_label(raw_sem: Any) -> str:
    """Standardizes semester tokens into canonical format (e.g. VIII / Semester_8 -> Semester VIII)."""
    s = str(raw_sem or "").strip().upper()
    roman_map = {
        "1": "Semester I", "2": "Semester II", "3": "Semester III", "4": "Semester IV",
        "5": "Semester V", "6": "Semester VI", "7": "Semester VII", "8": "Semester VIII",
        "SEMESTER_1": "Semester I", "SEMESTER_2": "Semester II", "SEMESTER_3": "Semester III",
        "SEMESTER_4": "Semester IV", "SEMESTER_5": "Semester V", "SEMESTER_6": "Semester VI",
        "SEMESTER_7": "Semester VII", "SEMESTER_8": "Semester VIII",
        "SEM_1": "Semester I", "SEM_2": "Semester II", "SEM_3": "Semester III", "SEM_4": "Semester IV",
        "SEM_5": "Semester V", "SEM_6": "Semester VI", "SEM_7": "Semester VII", "SEM_8": "Semester VIII",
        "I": "Semester I", "II": "Semester II", "III": "Semester III", "IV": "Semester IV",
        "V": "Semester V", "VI": "Semester VI", "VII": "Semester VII", "VIII": "Semester VIII",
    }
    return roman_map.get(s, s.replace("_", " ").title() if s else "Semester VIII")


def get_dept_excel_base_dir() -> str:
    """Finds the base directory for department_data/excel or departments_data/excel."""
    candidates = [
        os.path.join(DEPARTMENTS_DATA_DIR, "excel"),
        os.path.join(APP_DIR, "departments_data", "excel"),
        os.path.join(APP_DIR, "department_data", "excel"),
        os.path.join(PROJECT_ROOT, "departments_data", "excel"),
        os.path.join(PROJECT_ROOT, "department_data", "excel"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(DEPARTMENTS_DATA_DIR, "excel"))


def get_departments_data_excel_hierarchy() -> List[Dict[str, Any]]:
    """
    Returns the structured 3-tier hierarchy:
    Department -> Batch -> Semester Excel File.
    Prioritizes Supabase Cloud Storage/Database, falling back to local disk.
    """
    try:
        from services.supabase_service import get_supabase_service
        supabase_svc = get_supabase_service()
        if supabase_svc.is_configured:
            cloud_hier = supabase_svc.get_cloud_departments_hierarchy()
            if cloud_hier:
                return cloud_hier
    except Exception as e:
        logger.warning(f"Could not load cloud hierarchy from Supabase: {e}")

    base_dir = get_dept_excel_base_dir()
    departments = []
    if not os.path.isdir(base_dir):
        return departments

    for dept_entry in sorted(os.listdir(base_dir)):
        dept_path = os.path.join(base_dir, dept_entry)
        if not os.path.isdir(dept_path) or dept_entry.startswith("."):
            continue

        dept_folder = dept_entry
        dept_label = dept_folder.replace("_", " ")

        batches = []
        for batch_entry in sorted(os.listdir(dept_path)):
            batch_path = os.path.join(dept_path, batch_entry)
            if not os.path.isdir(batch_path) or batch_entry.startswith("."):
                continue

            batch_folder = batch_entry
            ay_label = _standardize_academic_year(batch_folder)
            batch_display = f"{ay_label} ({batch_folder})" if ay_label != batch_folder else batch_folder

            semesters = []
            for f in sorted(os.listdir(batch_path)):
                if not (f.endswith(".xlsx") or f.endswith(".xls")) or f.startswith("~$"):
                    continue
                fpath = os.path.join(batch_path, f)
                rel_path = os.path.relpath(fpath, base_dir).replace("\\", "/")
                sem_base = os.path.splitext(f)[0]
                sem_label = _standardize_semester_label(sem_base)

                semesters.append({
                    "file_name": f,
                    "display_name": f"{sem_label} ({f})",
                    "semester_name": sem_label,
                    "rel_path": rel_path,
                    "dataset_id": f"excel_{rel_path}"
                })

            if semesters:
                batches.append({
                    "id": batch_folder,
                    "folder": batch_folder,
                    "name": batch_display,
                    "academic_year": ay_label,
                    "semesters": semesters
                })

        if batches:
            departments.append({
                "id": dept_folder,
                "folder": dept_folder,
                "name": dept_label,
                "batches": batches
            })

    # Incorporate datasets from SQLite if not already in hierarchy
    try:
        from core.dataset_database import list_datasets_from_db
        from services.state import DB_PATH
        db_datasets = list_datasets_from_db(DB_PATH)
        for ds in db_datasets:
            d_dept = ds.get("department") or "Artificial_Intelligence_and_Data_Science"
            d_batch = ds.get("batch") or "21-22"
            d_sem = ds.get("semester") or "Semester_1"
            synth_id = f"db_{d_dept}_{d_batch}_{d_sem}"

            # Check if this department exists in departments
            dept_obj = next((d for d in departments if d["id"].lower() == d_dept.lower()), None)
            if not dept_obj:
                dept_obj = {
                    "id": d_dept,
                    "folder": d_dept,
                    "name": d_dept.replace("_", " "),
                    "batches": []
                }
                departments.append(dept_obj)

            # Check if batch exists in dept_obj
            batch_obj = next((b for b in dept_obj["batches"] if b["id"].lower() == d_batch.lower() or b["folder"].lower() == d_batch.lower()), None)
            if not batch_obj:
                ay = _standardize_academic_year(d_batch)
                batch_obj = {
                    "id": d_batch,
                    "folder": d_batch,
                    "name": f"{ay} ({d_batch})" if ay != d_batch else d_batch,
                    "academic_year": ay,
                    "semesters": []
                }
                dept_obj["batches"].append(batch_obj)

            # Check if semester already exists in batch_obj
            sem_exists = any(
                s.get("dataset_id", "").lower() == synth_id.lower() or
                d_sem.lower() in s.get("display_name", "").lower() or
                s.get("file_name", "").lower().startswith(d_sem.lower())
                for s in batch_obj["semesters"]
            )
            if not sem_exists:
                sem_label = _standardize_semester_label(d_sem)
                batch_obj["semesters"].append({
                    "file_name": f"{d_sem}.xlsx",
                    "display_name": f"{sem_label} (Saved Database)",
                    "semester_name": sem_label,
                    "rel_path": ds.get("excel_path") or f"{d_dept}/{d_batch}/{d_sem}.xlsx",
                    "dataset_id": synth_id
                })
    except Exception as e:
        logger.warning(f"Error incorporating SQLite datasets into hierarchy: {e}")

    return departments


def resolve_subject_dataset(dataset_id: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Loads student records, catalog, and dataset metadata for a given dataset ID.
    Supports output/ folders, departments_data/excel files, and SQLite datasets.
    """
    clean_id = str(dataset_id or "").strip()
    records: List[Dict[str, Any]] = []
    catalog: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = {}

    # Case 0: Supabase Cloud Excel dataset (supabase_excel_<rel_path>)
    if clean_id.startswith("supabase_excel_"):
        rel_path = clean_id[15:].strip()
        storage_path = f"excel/{rel_path}" if not rel_path.startswith("excel/") else rel_path
        try:
            from services.supabase_service import get_supabase_service
            svc = get_supabase_service()
            file_bytes = svc.download_file_bytes(storage_path)
            if file_bytes:
                bio = io.BytesIO(file_bytes)
                xl = pd.ExcelFile(bio)
                sheet_to_use = (
                    "Extracted Data" if "Extracted Data" in xl.sheet_names
                    else ("Student Results" if "Student Results" in xl.sheet_names else xl.sheet_names[0])
                )
                df = xl.parse(sheet_to_use)
                records = df.to_dict(orient="records")

                if "Course Catalog" in xl.sheet_names:
                    cat_df = xl.parse("Course Catalog")
                    courses = []
                    for _, r in cat_df.iterrows():
                        c_code = str(r.get("Course Code") or "").strip()
                        c_name = str(r.get("Official Course Name") or "").strip()
                        if c_code:
                            courses.append({
                                "code": c_code,
                                "name": c_name,
                                "official_course_name": c_name
                            })
                    catalog = {"courses": courses}

                parts = rel_path.split("/")
                dept = parts[0].replace("_", " ") if len(parts) > 1 else "Engineering"
                batch = parts[1] if len(parts) > 1 else "21-22"
                sem_name = os.path.splitext(os.path.basename(rel_path))[0]

                meta = {
                    "dataset_id": clean_id,
                    "dataset_name": f"{dept} ({batch} {sem_name.replace('_', ' ')})",
                    "department": parts[0] if len(parts) > 1 else dept,
                    "branch": dept,
                    "academic_year": _standardize_academic_year(batch),
                    "semester": _standardize_semester_label(sem_name),
                    "storage_path": storage_path
                }
                return records, catalog, meta
        except Exception as e:
            logger.error(f"Error reading Supabase Excel dataset {clean_id}: {e}")

    # Case 1: Database dataset (db_<dept>_<batch>_<sem>)
    if clean_id.startswith("db_"):
        try:
            db_datasets = list_datasets_from_db(DB_PATH)
            matched = next((d for d in db_datasets if f"db_{d['department']}_{d['batch']}_{d['semester']}".lower() == clean_id.lower()), None)
            if matched:
                db_data = get_dataset_from_db(DB_PATH, matched["department"], matched["batch"], matched["semester"])
                if db_data:
                    records = db_data.get("students", [])
                    raw_meta = json.loads(db_data.get("meta_json") or "{}")
                    catalog = raw_meta if "courses" in raw_meta else None
                    meta = {
                        "dataset_id": clean_id,
                        "dataset_name": f"{matched['department']} ({matched['batch']} {matched['semester']})",
                        "department": matched["department"],
                        "branch": matched["department"].replace("_", " "),
                        "academic_year": _standardize_academic_year(matched["batch"]),
                        "batch": matched["batch"],
                        "semester": _standardize_semester_label(matched["semester"]),
                    }
                    return records, catalog, meta
        except Exception as e:
            logger.warning(f"Error loading DB dataset {clean_id}: {e}")

    # Case 2: Output folder (out_<folder> or direct folder name)
    folder_name = clean_id[4:] if clean_id.startswith("out_") else clean_id
    folder_path = os.path.join(OUTPUT_DIR, safe_folder_name(folder_name))
    if os.path.isdir(folder_path):
        f_clean = safe_folder_name(folder_name)
        csv_p = os.path.join(folder_path, f"{f_clean}_extracted_data.csv")
        cat_p = os.path.join(folder_path, f"{f_clean}_catalog.json")

        if os.path.isfile(cat_p):
            try:
                with open(cat_p, mode="r", encoding="utf-8") as f:
                    catalog = json.load(f)
            except Exception:
                catalog = None

        if os.path.isfile(csv_p):
            try:
                df = pd.read_csv(csv_p)
                records = df.to_dict(orient="records")
            except Exception:
                records = []
        else:
            xlsx_p = os.path.join(folder_path, f"{f_clean}_extracted_data.xlsx")
            if os.path.isfile(xlsx_p):
                try:
                    df = pd.read_excel(xlsx_p)
                    records = df.to_dict(orient="records")
                except Exception:
                    records = []

        ds_meta = (catalog or {}).get("dataset_meta") or {}
        ay = _standardize_academic_year(ds_meta.get("batch") or (catalog or {}).get("academic_year") or ("24-25" if "8th" in f_clean else "21-22"))
        sem = _standardize_semester_label(ds_meta.get("semester") or (catalog or {}).get("semester") or ("VIII" if "8th" in f_clean else "1"))
        branch_name = ds_meta.get("department") or (catalog or {}).get("college_name") or "Computer Engineering"

        meta = {
            "dataset_id": f"out_{f_clean}",
            "dataset_name": f"{f_clean} (Historical Gazette)",
            "department": branch_name,
            "branch": branch_name.replace("_", " "),
            "academic_year": ay,
            "semester": sem,
            "folder_name": f_clean
        }
        return records, catalog, meta

    # Case 3: Excel file from departments_data/excel or department_data/excel
    rel_path = clean_id[6:] if clean_id.startswith("excel_") else clean_id
    base_excel = get_dept_excel_base_dir()

    excel_full_path = os.path.join(base_excel, rel_path)
    if not os.path.isfile(excel_full_path):
        excel_full_path = os.path.join(base_excel, rel_path.replace("/", os.sep).replace("\\", os.sep))
    if not os.path.isfile(excel_full_path):
        excel_full_path = os.path.join(DEPARTMENTS_DATA_DIR, "excel", rel_path.replace("/", os.sep).replace("\\", os.sep))
    if not os.path.isfile(excel_full_path) and os.path.isfile(clean_id):
        excel_full_path = clean_id

    if not os.path.isfile(excel_full_path):
        clean_rel = rel_path.replace("\\", "/").strip("/")
        if clean_rel.startswith("excel/"):
            clean_rel = clean_rel[6:]
        return resolve_subject_dataset(f"supabase_excel_{clean_rel}")

    if os.path.isfile(excel_full_path):
        try:
            xl = pd.ExcelFile(excel_full_path)
            sheet_to_use = (
                "Extracted Data" if "Extracted Data" in xl.sheet_names
                else ("Student Results" if "Student Results" in xl.sheet_names else xl.sheet_names[0])
            )
            df = xl.parse(sheet_to_use)
            records = df.to_dict(orient="records")

            if "Course Catalog" in xl.sheet_names:
                cat_df = xl.parse("Course Catalog")
                courses = []
                for _, r in cat_df.iterrows():
                    c_code = str(r.get("Course Code") or "").strip()
                    c_name = str(r.get("Official Course Name") or "").strip()
                    if c_code:
                        courses.append({
                            "code": c_code,
                            "name": c_name,
                            "official_course_name": c_name
                        })
                catalog = {"courses": courses}

            try:
                rel_norm = os.path.relpath(excel_full_path, base_excel)
            except Exception:
                rel_norm = os.path.basename(excel_full_path)

            parts = rel_norm.split(os.sep)
            dept = parts[0].replace("_", " ") if len(parts) > 1 else "Engineering"
            batch = parts[1] if len(parts) > 1 else "21-22"
            sem_name = os.path.splitext(os.path.basename(excel_full_path))[0]

            meta = {
                "dataset_id": f"excel_{rel_norm.replace(os.sep, '/')}",
                "dataset_name": f"{dept} ({batch} {sem_name.replace('_', ' ')})",
                "department": parts[0] if len(parts) > 1 else dept,
                "branch": dept,
                "academic_year": _standardize_academic_year(batch),
                "semester": _standardize_semester_label(sem_name),
                "excel_path": excel_full_path
            }
            return records, catalog, meta
        except Exception as e:
            logger.error(f"Error reading Excel dataset {excel_full_path}: {e}")

    return records, catalog, meta


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

    # 2. DEPARTMENTS_DATA_DIR relative path check (e.g. '21-22/Sem_8.xlsx')
    clean_rel = str(filename_or_rel).replace("\\", "/").strip().lstrip("/")
    if clean_rel.lower().startswith("departments data/"):
        clean_rel = clean_rel[17:].lstrip("/")
    elif clean_rel.lower().startswith("departments_data/"):
        clean_rel = clean_rel[17:].lstrip("/")
    elif clean_rel.lower().startswith("batch data/"):
        clean_rel = clean_rel[11:].lstrip("/")
    elif clean_rel.lower().startswith("batch_data/"):
        clean_rel = clean_rel[11:].lstrip("/")

    for base_store in [DEPARTMENTS_DATA_DIR]:
        if not os.path.isdir(base_store):
            continue
        cand_batch = os.path.abspath(os.path.join(base_store, clean_rel))
        if cand_batch.startswith(os.path.abspath(base_store)) and os.path.isfile(cand_batch):
            return cand_batch, clean_rel

    # 3. Walk directories to search by filename
    for base_store in [DEPARTMENTS_DATA_DIR, INPUT_DIR]:
        if not os.path.isdir(base_store):
            continue
        for root, _, files in os.walk(base_store):
            for f in files:
                if f.lower() == safe_base.lower() or f.lower() == os.path.basename(clean_rel).lower():
                    found_path = os.path.join(root, f)
                    rel_display = os.path.relpath(found_path, base_store).replace("\\", "/")
                    return found_path, rel_display

    return None
