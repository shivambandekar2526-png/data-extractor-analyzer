"""
Batch Academic Multi-Semester Intelligence Analyzer
===================================================
Analyzes multi-semester cohorts (Semester 1 through Semester 8) from mixed
sources (PDF Gazettes, Tabulation Ledgers, and Excel/CSV Spreadsheets).

Features:
1. Automated Batch File & Semester Detection (1st sem to 8th sem, PDF / Excel / CSV).
2. Dual-Mode Excel Ingestion: Zero-Token Deterministic Direct Import vs AI Restructuring.
3. Multi-Engine PDF Extraction: Local Ollama / Possibility Engine, Pure Cloud AI, or Visual Blueprint.
4. Intelligent Student Entity Alignment (PRN, Roll No, and Ambiguity-Safe Name Token Matching).
5. Comprehensive Academic Metrics Synthesis across all 10 analysis categories:
   - Batch Overall Summary
   - Semester-by-Semester Analysis
   - Consolidated Student Progression Matrix (Sem 1-8)
   - KT & Backlog Load Analysis & Repeat Tracking
   - Semester Toppers & Multi-Semester Merit Consistency
   - Cohort Retention & Progression Flow (Regular vs Lateral Entry)
   - Student Performance Categorization (Consistently Strong, Improving, Declining, etc.)
   - Rigorous Data Quality & Anomaly Auditing
   - Traceable Semester Data Records
"""

import os
import re
import math
import time
import json
import logging
import difflib
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd
import numpy as np
import openpyxl

try:
    from ..models import StudentRecord, CourseResult, ExamMeta
except (ImportError, ValueError):
    try:
        from models import StudentRecord, CourseResult, ExamMeta
    except ImportError:
        StudentRecord, CourseResult, ExamMeta = None, None, None
from .possibility_extractor import extract_with_possibility_engine
from .page_ingester import ingest_pdf
from .possibility_classifier import classify_document
from .excel_csv_ingester import TabularIngester, sanitize_dict_for_json
from .cloud_client import CloudClient
try:
    from .student_identity_matcher import StudentIdentityMatcher
except (ImportError, ValueError):
    from core.student_identity_matcher import StudentIdentityMatcher

logger = logging.getLogger("BatchAnalyzer")

SEMESTER_PATTERNS = [
    (1, re.compile(r"(sem(?:ester)?[\s_\-]*1|1st[\s_\-]*sem|sem[\s_\-]*i\b|fe[\s_\-]*1|first[\s_\-]*sem|2122-1st|2223-1st|2324-1st|2425-1st|2526-1st)", re.I)),
    (2, re.compile(r"(sem(?:ester)?[\s_\-]*2|2nd[\s_\-]*sem|sem[\s_\-]*ii\b|fe[\s_\-]*2|second[\s_\-]*sem|2122-2nd|2223-2nd|2324-2nd|2425-2nd|2526-2nd)", re.I)),
    (3, re.compile(r"(sem(?:ester)?[\s_\-]*3|3rd[\s_\-]*sem|sem[\s_\-]*iii\b|se[\s_\-]*3|third[\s_\-]*sem|2122-3rd|2223-3rd|2324-3rd|2425-3rd|2526-3rd)", re.I)),
    (4, re.compile(r"(sem(?:ester)?[\s_\-]*4|4th[\s_\-]*sem|sem[\s_\-]*iv\b|se[\s_\-]*4|fourth[\s_\-]*sem|2122-4th|2223-4th|2324-4th|2425-4th|2526-4th)", re.I)),
    (5, re.compile(r"(sem(?:ester)?[\s_\-]*5|5th[\s_\-]*sem|sem[\s_\-]*v\b|te[\s_\-]*5|fifth[\s_\-]*sem|2122-5th|2223-5th|2324-5th|2425-5th|2526-5th)", re.I)),
    (6, re.compile(r"(sem(?:ester)?[\s_\-]*6|6th[\s_\-]*sem|sem[\s_\-]*vi\b|te[\s_\-]*6|sixth[\s_\-]*sem|2122-6th|2223-6th|2324-6th|2425-6th|2526-6th)", re.I)),
    (7, re.compile(r"(sem(?:ester)?[\s_\-]*7|7th[\s_\-]*sem|sem[\s_\-]*vii\b|be[\s_\-]*7|seventh[\s_\-]*sem|2122-7th|2223-7th|2324-7th|2425-7th|2526-7th)", re.I)),
    (8, re.compile(r"(sem(?:ester)?[\s_\-]*8|8th[\s_\-]*sem|sem[\s_\-]*viii\b|be[\s_\-]*8|eighth[\s_\-]*sem|2122-8th|2223-8th|2324-8th|2425-8th|2526-8th)", re.I)),
]

STOP_NAME_WORDS = {"MR", "MS", "MRS", "KUMARI", "MISS", "SMT", "SHRI", "SHRIMATI", "DR"}


def detect_semester_number(filename: str) -> Optional[int]:
    """Detect integer semester number (1 to 8) from file name."""
    clean = os.path.basename(filename).lower()
    for sem_num, pattern in SEMESTER_PATTERNS:
        if pattern.search(clean):
            return sem_num
    # Fallback digit search
    m = re.search(r"(\d)(?:st|nd|rd|th)", clean)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 8:
            return num
    return None


def normalize_student_name_tokens(name: str) -> List[str]:
    """Normalize student name into cleaned uppercase tokens without honorifics."""
    if not name:
        return []
    clean = re.sub(r"[^a-zA-Z\s]", " ", str(name)).upper()
    tokens = [t for t in clean.split() if t not in STOP_NAME_WORDS and len(t) >= 2]
    return tokens


def format_canonical_name(name: str) -> str:
    """Standardize name capitalization and spacing."""
    tokens = normalize_student_name_tokens(name)
    return " ".join(tokens)


HONORIFIC_PREFIXES = {"KU", "KUMAR", "KUMARI", "MR", "MISS", "MRS", "SMT", "SHRI", "SHRIMATI", "DR", "MS"}


def get_clean_student_tokens(name_or_tokens: Any) -> List[str]:
    """Extract cleaned uppercase tokens without honorifics from string or collection."""
    if isinstance(name_or_tokens, (list, set, tuple)):
        toks = list(name_or_tokens)
    else:
        toks = normalize_student_name_tokens(str(name_or_tokens or ""))
    return [t.upper() for t in toks if t.upper() not in HONORIFIC_PREFIXES and len(t) >= 2]


def are_student_names_matching(n1: Any, n2: Any) -> bool:
    """
    Check if two student name representations refer to the same individual.
    Handles permutations (e.g. 'ADULKAR VEDANT' vs 'VEDANT ADULKAR' vs 'ADULKAR VEDANT RAMESH'),
    and slight phonetic/transcription typos (e.g. 'DALVI' vs 'DALAVI', 'DUBE' vs 'DUBEY').
    """
    t1 = get_clean_student_tokens(n1)
    t2 = get_clean_student_tokens(n2)
    if not t1 or not t2:
        return False

    s1, s2 = set(t1), set(t2)
    # Exact set match or subset match (e.g. First Last vs First Middle Last)
    if s1 == s2 or s1.issubset(s2) or s2.issubset(s1):
        return True

    inter = s1.intersection(s2)
    # If 2 or more tokens match exactly (e.g. surname + first name)
    if len(inter) >= 2:
        return True

    # If exactly 1 token matches (e.g. given name 'VEDANT' or 'NIHAL'):
    # Check if there is a near-identical token (ratio >= 0.85) between remaining tokens
    if len(inter) == 1:
        exact = list(inter)[0]
        rem1 = [x for x in t1 if x != exact]
        rem2 = [x for x in t2 if x != exact]
        for r1 in rem1:
            for r2 in rem2:
                if difflib.SequenceMatcher(None, r1, r2).ratio() >= 0.85:
                    return True

    return False


def format_branch_label(branch_name: str) -> str:
    """Return clean human-readable label for engineering branch/department."""
    if not branch_name:
        return "General"
    b_up = branch_name.upper().replace("-", "_").replace(" ", "_")
    if b_up in ("AIDS", "AI_DS", "AI&DS", "ARTIFICIAL_INTELLIGENCE", "ARTIFICIAL_INTELLIGENCE_AND_DATA_SCIENCE"):
        return "Artificial Intelligence & Data Science (AIDS)"
    if b_up in ("COMPUTER_ENGINEERING", "COMP", "COMPUTER", "CSE", "CS"):
        return "Computer Engineering"
    if b_up in ("INFORMATION_TECHNOLOGY", "IT"):
        return "Information Technology"
    if b_up in ("MECHANICAL_ENGINEERING", "MECH"):
        return "Mechanical Engineering"
    if b_up in ("EXTC", "ELECTRONICS_AND_TELECOMMUNICATION"):
        return "Electronics & Telecommunication"
    # Fallback: replace underscores with spaces and title case
    return branch_name.replace("_", " ").title()


def normalize_batch_slug(name: str) -> str:
    """Normalize batch folder name to clean canonical slug (e.g. '21-22 data' -> '21-22', 'Batch 2021-2022' -> '21-22', '2122' -> '21-22')."""
    if not name:
        return "batch"
    clean = os.path.basename(name).strip()
    clean = re.sub(r"[\s_\-]+data$", "", clean, flags=re.IGNORECASE).strip()
    m = re.search(r"(\b20\d{2}|\b\d{2})[\s_\-/]+(\b20\d{2}|\b\d{2})", clean)
    if m:
        y1 = m.group(1)[-2:]
        y2 = m.group(2)[-2:]
        return f"{y1}-{y2}"
    m4 = re.search(r"\b(1[89]|2[0-9]|3[0-5])(1[89]|2[0-9]|3[0-5])\b", clean)
    if m4:
        y1, y2 = int(m4.group(1)), int(m4.group(2))
        if y2 == y1 + 1:
            return f"{y1:02d}-{y2:02d}"
    return clean or "batch"


class BatchAcademicAnalyzer:
    """
    Core Batch Academic Intelligence Engine.
    Handles discovery, extraction, cross-semester entity resolution, and metrics synthesis.
    """

    def __init__(self, output_dir: str, app_batch_dir: Optional[str] = None, root_batch_dir: Optional[str] = None):
        self.output_dir = output_dir
        if app_batch_dir:
            self.app_batch_dir = os.path.abspath(app_batch_dir)
        else:
            self.app_batch_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "departments_data"))

        if root_batch_dir:
            self.root_batch_dir = os.path.abspath(root_batch_dir)
        else:
            self.root_batch_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "Batch Data"))

        os.makedirs(self.app_batch_dir, exist_ok=True)
        self.tabular_ingester = TabularIngester(output_dir)

    def save_clean_semester_excel(
        self,
        records: List[Dict[str, Any]],
        batch_slug: str,
        sem_num: int,
        target_dir: Optional[str] = None
    ) -> str:
        """
        Saves clean extracted or imported student records into the designated
        app batch store: data_extractor_AI/departments_data/<branch>/<year>/Sem_<sem_num>.xlsx
        or fallback data_extractor_AI/departments_data/<batch_slug>/Sem_<sem_num>.xlsx.
        """
        if target_dir and os.path.isdir(target_dir):
            dest_dir = target_dir
        elif "/" in batch_slug or "\\" in batch_slug:
            dest_dir = os.path.join(self.app_batch_dir, batch_slug.replace("\\", "/"))
        else:
            clean_slug = normalize_batch_slug(batch_slug)
            dest_dir = os.path.join(self.app_batch_dir, clean_slug)

        os.makedirs(dest_dir, exist_ok=True)
        # Preserve existing naming convention (Semester_N.xlsx vs Sem_N.xlsx) to prevent duplicate extra files
        cand_canonical = os.path.join(dest_dir, f"Semester_{sem_num}.xlsx")
        cand_legacy = os.path.join(dest_dir, f"Sem_{sem_num}.xlsx")
        if os.path.isfile(cand_canonical):
            target_file = cand_canonical
        elif os.path.isfile(cand_legacy):
            target_file = cand_legacy
        else:
            target_file = cand_legacy

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Student Results"

        headers = ["Sr. No.", "Seat No", "Student Name", "PRN", "Result", "SGPI", "KT Count", "Total Marks"]
        ws.append(headers)

        from openpyxl.styles import Font, PatternFill, Alignment
        h_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        h_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = h_fill
            cell.font = h_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, r in enumerate(records, start=1):
            row_data = [
                idx,
                str(r.get("seat_no") or r.get("Seat_No") or ""),
                str(r.get("name") or r.get("Name") or ""),
                str(r.get("prn") or r.get("PRN") or ""),
                str(r.get("result") or r.get("Result") or ""),
                r.get("sgpi") if r.get("sgpi") is not None else r.get("SGPI", ""),
                r.get("kt_count") if r.get("kt_count") is not None else r.get("KT_Count", 0),
                r.get("total_marks") if r.get("total_marks") is not None else r.get("Total_Marks", "")
            ]
            ws.append(row_data)

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 10)

        # Also add Semester Summary sheet
        ws_sum = wb.create_sheet("Semester Summary")
        ws_sum.append(["Semester", f"Semester {sem_num}"])
        ws_sum.append(["Total Enrolled Students", len(records)])
        pass_cnt = sum(1 for r in records if str(r.get("result") or r.get("Result") or "").upper() == "PASS")
        ws_sum.append(["Total Passed", pass_cnt])
        ws_sum.append(["Total Failed / KT", len(records) - pass_cnt])

        wb.save(target_file)
        return target_file

    def detect_batch_files(self, batch_folder: str, target_branch: str = "AIDS") -> Dict[str, Any]:
        """
        Inspect batch sources across both the app's designated hierarchical departments_data store
        and the root project Batch Data store. Prioritizes clean Excel files already
        present in departments_data, and flags missing semesters for auto-extraction.
        """
        detected: Dict[int, Dict[str, Any]] = {}
        batch_folder_clean = batch_folder.replace("\\", "/").strip("/")
        batch_slug = normalize_batch_slug(batch_folder)

        # Candidate folder paths
        candidate_dirs = []
        # 1. Absolute path check
        if os.path.isabs(batch_folder) and os.path.isdir(batch_folder):
            candidate_dirs.append(batch_folder)
            # Check mirror directory (pdf <-> excel)
            bf_norm = batch_folder.replace("\\", "/")
            if "/excel/" in bf_norm:
                mirror_cand = os.path.normpath(bf_norm.replace("/excel/", "/pdf/"))
                if os.path.isdir(mirror_cand) and mirror_cand not in candidate_dirs:
                    candidate_dirs.append(mirror_cand)
            elif "/pdf/" in bf_norm:
                mirror_cand = os.path.normpath(bf_norm.replace("/pdf/", "/excel/"))
                if os.path.isdir(mirror_cand) and mirror_cand not in candidate_dirs:
                    candidate_dirs.append(mirror_cand)
        else:
            # 2. Hierarchical or relative under app_batch_dir (checking direct, excel/, and pdf/ stores)
            path_variants = [batch_folder_clean]
            if "/" in batch_folder_clean or "\\" in batch_folder_clean:
                b_p, y_p = re.split(r"[/\\]", batch_folder_clean, maxsplit=1)
                try:
                    from .pdf_classifier import normalize_department
                    norm_b = normalize_department(b_p)
                except Exception:
                    try:
                        from pdf_classifier import normalize_department
                        norm_b = normalize_department(b_p)
                    except Exception:
                        norm_b = b_p
                norm_y = normalize_batch_slug(y_p)
                for bv in [b_p, norm_b]:
                    for yv in [y_p, norm_y]:
                        combo = f"{bv}/{yv}"
                        if combo not in path_variants:
                            path_variants.append(combo)

            for variant in path_variants:
                for sub_store in ["", "excel", "pdf"]:
                    p_cand = os.path.join(self.app_batch_dir, sub_store, variant) if sub_store else os.path.join(self.app_batch_dir, variant)
                    if os.path.isdir(p_cand) and p_cand not in candidate_dirs:
                        candidate_dirs.append(p_cand)
            if not candidate_dirs and os.path.isdir(batch_folder):
                candidate_dirs.append(os.path.abspath(batch_folder))

        # Determine target app store folder for outputs
        if candidate_dirs and os.path.abspath(candidate_dirs[0]).startswith(os.path.abspath(self.app_batch_dir)):
            app_store_dir = candidate_dirs[0]
        else:
            app_store_dir = os.path.join(self.app_batch_dir, "excel", batch_slug) if os.path.isdir(os.path.join(self.app_batch_dir, "excel")) else os.path.join(self.app_batch_dir, batch_slug)

        if os.path.isdir(app_store_dir) and app_store_dir not in candidate_dirs:
            candidate_dirs.append(app_store_dir)

        # Only check root store if needed
        if self.root_batch_dir and os.path.isdir(self.root_batch_dir):
            for sub_store in ["", "excel", "pdf"]:
                r_cand = os.path.join(self.root_batch_dir, sub_store, batch_folder_clean) if sub_store else os.path.join(self.root_batch_dir, batch_folder_clean)
                if os.path.isdir(r_cand) and r_cand not in candidate_dirs:
                    candidate_dirs.append(r_cand)

            if not candidate_dirs or ("/" not in batch_folder_clean):
                for candidate_name in [batch_folder, batch_slug, f"{batch_slug} data"]:
                    r_cand = os.path.join(self.root_batch_dir, os.path.basename(candidate_name))
                    if os.path.isdir(r_cand) and r_cand not in candidate_dirs:
                        candidate_dirs.append(r_cand)

        # Scan candidate folders: prioritize Excel files first, especially from app_batch_dir
        for d in candidate_dirs:
            is_app_store = os.path.abspath(d).startswith(os.path.abspath(self.app_batch_dir))
            for entry in sorted(os.listdir(d)):
                fp = os.path.join(d, entry)
                if not os.path.isfile(fp):
                    continue
                ext = os.path.splitext(entry)[1].lower()
                if ext not in [".xlsx", ".xls", ".csv", ".pdf"]:
                    continue
                sem_num = detect_semester_number(entry)
                if not sem_num:
                    continue

                file_type = "pdf" if ext == ".pdf" else "excel"
                file_info = {
                    "filename": entry,
                    "full_path": fp,
                    "file_type": file_type,
                    "ext": ext,
                    "size_bytes": os.path.getsize(fp),
                    "detected_sem": sem_num,
                    "is_app_store": is_app_store,
                    "needs_auto_extract": (file_type == "pdf")
                }

                # Priority logic:
                # 1. Existing entry is None -> take it
                # 2. If new file is Excel and existing is PDF -> replace with Excel!
                # 3. If new file is Excel in app_store -> prefer app_store Excel!
                if sem_num not in detected:
                    detected[sem_num] = file_info
                else:
                    existing = detected[sem_num]
                    if file_type == "excel" and existing["file_type"] == "pdf":
                        detected[sem_num] = file_info
                    elif file_type == "excel" and existing["file_type"] == "excel" and is_app_store:
                        detected[sem_num] = file_info

        excel_count = sum(1 for f in detected.values() if f["file_type"] == "excel")
        pdf_count = sum(1 for f in detected.values() if f["file_type"] == "pdf")
        all_excel = len(detected) > 0 and excel_count == len(detected)
        has_excel = excel_count > 0

        # Sort detected semesters (1 to 8)
        sorted_sems = []
        for s in range(1, 9):
            if s in detected:
                sorted_sems.append({
                    "semester": s,
                    **detected[s]
                })

        parts = batch_folder_clean.split("/")
        branch_name = parts[0] if len(parts) > 1 else target_branch
        year_name = parts[-1] if len(parts) > 1 else os.path.basename(batch_folder)

        return {
            "batch_path": candidate_dirs[0] if candidate_dirs else batch_folder,
            "batch_folder": batch_folder_clean,
            "branch": branch_name,
            "academic_year": year_name,
            "batch_slug": batch_slug,
            "batch_name": year_name,
            "display_name": f"{format_branch_label(branch_name)} - {year_name}" if len(parts) > 1 else os.path.basename(batch_folder),
            "app_batch_dir": app_store_dir,
            "semesters_detected": sorted_sems,
            "total_semesters": len(sorted_sems),
            "excel_count": excel_count,
            "pdf_count": pdf_count,
            "all_excel": all_excel,
            "has_excel": has_excel,
            "prompt_excel_ai": has_excel
        }

    def discover_batch_hierarchy(self) -> Dict[str, Any]:
        """
        Scans departments_data recursively for the hierarchical structure:
        departments_data/
        ├── <Branch> / <Department>/
        │   ├── <Academic_Year>/
        │   │   ├── Sem_<1-8>.(pdf|xlsx)
        """
        branches_dict: Dict[str, Dict[str, Any]] = {}

        # Scan roots: check if app_batch_dir is organized with excel/ and pdf/ subtrees
        scan_roots = []
        for sub_store in ["excel", "pdf"]:
            sub_p = os.path.join(self.app_batch_dir, sub_store)
            if os.path.isdir(sub_p):
                scan_roots.append(sub_p)
        if not scan_roots:
            scan_roots.append(self.app_batch_dir)

        # Pass 1: Discover hierarchical Branch/Year folders across all scan roots
        for s_root in scan_roots:
            all_entries = sorted(os.listdir(s_root))
            for entry in all_entries:
                if entry.lower().endswith(".xlsx") or entry.lower().endswith(".csv") or entry.lower() in ("pdf", "excel"):
                    continue
                dir_path = os.path.join(s_root, entry)
                if not os.path.isdir(dir_path):
                    continue

                sub_dirs = [d for d in sorted(os.listdir(dir_path)) if os.path.isdir(os.path.join(dir_path, d))]
                if sub_dirs:
                    branch_id = entry
                    if branch_id not in branches_dict:
                        branches_dict[branch_id] = {
                            "id": branch_id,
                            "name": branch_id,
                            "display_name": format_branch_label(branch_id),
                            "years": []
                        }

                    for yr in sub_dirs:
                        if yr.lower().endswith(".xlsx") or yr.lower().endswith(".csv"):
                            continue
                        yr_path = os.path.join(dir_path, yr)
                        rel_folder = f"{branch_id}/{yr}"
                        meta = self.detect_batch_files(rel_folder, target_branch=branch_id)
                        
                        # Avoid duplicate years if discovered in both excel and pdf
                        existing_yr = next((y for y in branches_dict[branch_id]["years"] if y["year"] == yr), None)
                        if existing_yr:
                            if meta["excel_count"] > existing_yr["excel_count"]:
                                existing_yr.update({
                                    "total_semesters": meta["total_semesters"],
                                    "excel_count": meta["excel_count"],
                                    "pdf_count": meta["pdf_count"],
                                    "semesters": [s["semester"] for s in meta["semesters_detected"]],
                                    "semesters_detected": meta["semesters_detected"],
                                    "has_excel": meta["has_excel"]
                                })
                        else:
                            branches_dict[branch_id]["years"].append({
                                "year": yr,
                                "batch_folder": rel_folder,
                                "display_name": yr,
                                "full_path": yr_path,
                                "branch": branch_id,
                                "total_semesters": meta["total_semesters"],
                                "excel_count": meta["excel_count"],
                                "pdf_count": meta["pdf_count"],
                                "semesters": [s["semester"] for s in meta["semesters_detected"]],
                                "semesters_detected": meta["semesters_detected"],
                                "has_excel": meta["has_excel"]
                            })

        # Pass 2: Check any flat folders for backwards compatibility
        for entry in all_entries:
            if entry.lower().endswith(".xlsx") or entry.lower().endswith(".csv"):
                continue
            dir_path = os.path.join(self.app_batch_dir, entry)
            if not os.path.isdir(dir_path):
                continue

            sub_dirs = [d for d in sorted(os.listdir(dir_path)) if os.path.isdir(os.path.join(dir_path, d))]
            if not sub_dirs:
                meta = self.detect_batch_files(entry)
                if meta["total_semesters"] > 0:
                    entry_slug = normalize_batch_slug(entry)
                    inferred_branch = "AIDS" if ("21-22" in entry or "22-23" in entry) else "General"
                    # Check if already present under inferred branch
                    already_has = False
                    if inferred_branch in branches_dict:
                        for y_info in branches_dict[inferred_branch]["years"]:
                            if y_info["year"] == entry or normalize_batch_slug(y_info["year"]) == entry_slug:
                                already_has = True
                                break
                    if not already_has:
                        if inferred_branch not in branches_dict:
                            branches_dict[inferred_branch] = {
                                "id": inferred_branch,
                                "name": inferred_branch,
                                "display_name": format_branch_label(inferred_branch),
                                "years": []
                            }
                        branches_dict[inferred_branch]["years"].append({
                            "year": entry,
                            "batch_folder": entry,
                            "display_name": entry,
                            "full_path": dir_path,
                            "branch": inferred_branch,
                            "total_semesters": meta["total_semesters"],
                            "excel_count": meta["excel_count"],
                            "pdf_count": meta["pdf_count"],
                            "semesters": [s["semester"] for s in meta["semesters_detected"]],
                            "semesters_detected": meta["semesters_detected"],
                            "has_excel": meta["has_excel"]
                        })

        branches_list = list(branches_dict.values())
        def branch_sort_key(b):
            b_name = b["name"].lower()
            if "comp" in b_name:
                return (0, b_name)
            if "aids" in b_name or "ai" in b_name:
                return (1, b_name)
            return (2, b_name)

        branches_list.sort(key=branch_sort_key)
        for b in branches_list:
            b["years"].sort(key=lambda y: y["year"], reverse=True)

        total_batches = sum(len(b["years"]) for b in branches_list)
        return {
            "branches": branches_list,
            "total_branches": len(branches_list),
            "total_batches": total_batches
        }

    def parse_excel_semester(
        self,
        file_path: str,
        sem_num: int,
        target_branch: str = "AIDS",
        use_ai_fix: bool = False,
        progress_cb: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract student records from an Excel semester file.
        Uses smart multi-tier header fusion and branch sheet matching.
        """
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet_name = None

        # 1. Match branch sheet (e.g. AIDS, AI&DS, or branch specific)
        for name in wb.sheetnames:
            if target_branch.lower() in name.lower():
                sheet_name = name
                break
        if not sheet_name:
            # Look for common semester names
            for name in wb.sheetnames:
                if any(k in name.lower() for k in [f"sem{sem_num}", f"sem_{sem_num}", f"sem {sem_num}", "se_", "fe_", "te_", "be_"]):
                    sheet_name = name
                    break
        if not sheet_name:
            for name in wb.sheetnames:
                if name.lower() not in ["overall", "subjectwise", "final sheet", "summary"]:
                    sheet_name = name
                    break
        if not sheet_name:
            sheet_name = wb.sheetnames[0]

        s = wb[sheet_name]
        rows = list(s.iter_rows(values_only=True))
        if not rows:
            return []

        # Multi-tier candidate header row discovery (scans rows 0 to 15)
        header_rows_indices = []
        for idx in range(min(16, len(rows))):
            r_str = " ".join([str(c).lower() for c in rows[idx] if c is not None])
            if any(k in r_str for k in ["name", "student", "seat", "sr.", "sr no", "sgpi", "result"]):
                header_rows_indices.append(idx)

        if not header_rows_indices:
            header_rows_indices = [0]

        name_col, seat_col, sgpi_col, res_col, prn_col = None, None, None, None, None
        total_marks_col, ctype_col = None, None

        for h_idx in header_rows_indices:
            row = rows[h_idx]
            for c_idx, val in enumerate(row):
                if val is None:
                    continue
                v_str = str(val).strip().lower()
                v_norm = re.sub(r"[\s_\-]+", " ", v_str).strip()
                is_excluded_name = any(k in v_norm for k in ["mother", "father", "parent", "college", "institute", "teacher", "faculty"])

                if not name_col and not is_excluded_name:
                    if any(k in v_norm for k in ["name of student", "name of the student", "candidate name", "student name"]):
                        name_col = c_idx
                    elif v_norm in ["name", "student", "candidate"]:
                        name_col = c_idx

                if not seat_col:
                    if any(k in v_norm for k in ["seat no", "seat"]):
                        seat_col = c_idx
                    elif any(k in v_norm for k in ["sr no", "sr.", "roll no"]):
                        seat_col = c_idx

                if any(k in v_norm for k in ["candidate type", "admission type", "type"]):
                    ctype_col = c_idx

                if any(k in v_norm for k in ["sgpi", "sgpa", "gpa", "spi"]):
                    sgpi_col = c_idx  # Keep latest (earned SGPI at end of sheet)

                if "result" in v_norm or "pass fail" in v_norm:
                    res_col = c_idx
                elif not res_col and v_norm in ["status", "p f", "result status"]:
                    res_col = c_idx

                if any(k in v_norm for k in ["prn", "enrollment", "registration"]):
                    prn_col = c_idx

                if any(k in v_norm for k in ["total marks", "tot marks", "grand total", "aggregate marks", "total"]):
                    total_marks_col = c_idx

        # Find data start row (after last header row index)
        data_start = max(header_rows_indices) + 1
        while data_start < len(rows):
            val0 = str(rows[data_start][0] if len(rows[data_start]) > 0 and rows[data_start][0] is not None else "").strip()
            val1 = str(rows[data_start][name_col] if name_col is not None and len(rows[data_start]) > name_col and rows[data_start][name_col] is not None else "").strip()
            if (re.search(r"^\d+(\.0)?$", val0) or val1) and not any(kw in (val0 + " " + val1).lower() for kw in ["credit", "th", "max", "column", "sr", "name"]):
                break
            data_start += 1

        students = []
        for r_idx in range(data_start, len(rows)):
            r = rows[r_idx]
            name = str(r[name_col]).strip() if name_col is not None and len(r) > name_col and r[name_col] is not None else ""
            if not name or any(k in name.lower() for k in ["total", "average", "percentage", "passed", "failed", "column"]) or name.startswith("Column"):
                continue

            seat = str(r[seat_col]).replace(".0", "").strip() if seat_col is not None and len(r) > seat_col and r[seat_col] is not None else str(len(students) + 1)
            prn = str(r[prn_col]).replace(".0", "").strip() if prn_col is not None and len(r) > prn_col and r[prn_col] is not None else ""
            ctype = str(r[ctype_col]).strip() if ctype_col is not None and len(r) > ctype_col and r[ctype_col] is not None else ""

            sgpi_val = None
            if sgpi_col is not None and len(r) > sgpi_col and r[sgpi_col] is not None:
                try:
                    s_float = float(r[sgpi_col])
                    if not math.isnan(s_float) and 0.0 <= s_float <= 10.0:
                        sgpi_val = round(s_float, 2)
                except (ValueError, TypeError):
                    sgpi_val = None

            tot_marks_val = None
            if total_marks_col is not None and len(r) > total_marks_col and r[total_marks_col] is not None:
                try:
                    tot_marks_val = round(float(r[total_marks_col]), 1)
                except (ValueError, TypeError):
                    tot_marks_val = None

            res_val = "PASS"
            if res_col is not None and len(r) > res_col and r[res_col] is not None:
                raw_res = str(r[res_col]).strip().upper()
                if "P" in raw_res:
                    res_val = "PASS"
                elif "F" in raw_res:
                    res_val = "FAIL"
            elif sgpi_val is not None:
                res_val = "PASS" if sgpi_val > 0 else "FAIL"

            # Invert SGPI if fail
            if res_val == "FAIL" and (sgpi_val is None or sgpi_val == 0.0):
                sgpi_val = None

            # Check KT subjects if any F markings in course cells
            kt_count = 0
            if res_val == "FAIL":
                f_hits = sum(1 for c in r if str(c).strip().upper() in ["F", "FAIL", "FF", "KT"])
                kt_count = max(1, f_hits)

            students.append({
                "seat_no": seat,
                "name": name,
                "prn": prn,
                "candidate_type": ctype,
                "sgpi": sgpi_val,
                "result": res_val,
                "kt_count": kt_count if res_val == "FAIL" else 0,
                "total_marks": tot_marks_val,
                "source_file": os.path.basename(file_path),
                "sheet_name": sheet_name,
                "semester": sem_num
            })

        return students

    def extract_pdf_semester(
        self,
        file_path: str,
        sem_num: int,
        engine_mode: str = "local",
        progress_cb: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """Extract student records from a PDF semester file using chosen engine."""
        arch = "tabulation"
        students = []
        meta = None

        if engine_mode == "visual_blueprint":
            try:
                from .visual_extractor import run_visual_blueprint_pipeline
                from .cloud_client import CloudClient
                cc = CloudClient()
                if cc.is_configured():
                    meta, students, arch = run_visual_blueprint_pipeline(file_path, progress_cb=progress_cb, client=cc)
                else:
                    pages = ingest_pdf(file_path)
                    arch, conf = classify_document(pages)
                    meta, students = extract_with_possibility_engine(file_path, arch, progress_cb)
            except Exception as e:
                logger.warning("Visual blueprint failed in batch analyzer for Sem %d: %s. Falling back to possibility engine.", sem_num, e)
                pages = ingest_pdf(file_path)
                arch, conf = classify_document(pages)
                meta, students = extract_with_possibility_engine(file_path, arch, progress_cb)
        else:
            pages = ingest_pdf(file_path)
            arch, conf = classify_document(pages)
            if progress_cb:
                progress_cb(10.0, f"Sem {sem_num}: Classified as {arch.upper()} ({conf:.0%})")
            meta, students = extract_with_possibility_engine(file_path, arch, progress_cb)

        records = []
        for s in students:
            sgpi_val = None
            try:
                if s.sgpi and str(s.sgpi).strip().lower() not in ["", "none", "null", "nan", "0", "0.0"]:
                    s_f = float(s.sgpi)
                    if 0.0 < s_f <= 10.0:
                        sgpi_val = round(s_f, 2)
            except (ValueError, TypeError):
                sgpi_val = None

            res_val = "PASS" if "P" in s.result_status.upper() else "FAIL"
            if res_val == "FAIL":
                sgpi_val = None

            # Detect KT subjects from course results
            kt_subs = []
            if s.course_results:
                for cr in s.course_results:
                    if str(cr.grade).strip().upper() == "F" or str(cr.grade_point).strip() == "0":
                        kt_subs.append(cr.course_code)

            kt_count = len(kt_subs) if kt_subs else (1 if res_val == "FAIL" else 0)

            tot_m = None
            try:
                if s.total_marks:
                    tot_m = float(s.total_marks)
            except (ValueError, TypeError):
                pass

            records.append({
                "seat_no": s.seat_no,
                "name": s.student_name,
                "prn": s.prn,
                "sgpi": sgpi_val,
                "result": res_val,
                "kt_count": kt_count,
                "kt_subjects": kt_subs,
                "total_marks": tot_m,
                "source_file": os.path.basename(file_path),
                "archetype": arch,
                "semester": sem_num
            })

        return records

    def align_master_students(
        self,
        semester_data: Dict[int, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """
        Intelligently resolves and tracks student identities across all semesters
        using the Universal StudentIdentityMatcher with Zero-Assumption Disjoint-Set Graph
        and AI-powered entity resolution.
        """
        clean_sem_data: Dict[int, List[Dict[str, Any]]] = {}
        for sem, raw_sem in semester_data.items():
            records = raw_sem.get("records", []) if isinstance(raw_sem, dict) else raw_sem
            clean_sem_data[int(sem)] = records

        matcher = StudentIdentityMatcher(enable_ai=True)
        return matcher.resolve_all_semesters(clean_sem_data)

    def compute_batch_analytics(
        self,
        batch_meta_or_sem_data: Any,
        semester_data: Optional[Dict[int, Any]] = None,
        master_students: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Computes the complete multi-semester analytical metrics across all 10 requirements:
        1. Read Me details
        2. Overall Summary
        3. Semester Analysis
        4. Student Progression
        5. KT Analysis
        6. Topper Analysis
        7. Retention / Progression Flow
        8. Student Performance Categorization
        9. Data Quality Audit Log
        10. Semester Data Traceability
        """
        if master_students is None and isinstance(batch_meta_or_sem_data, dict) and isinstance(semester_data, list):
            master_students = semester_data
            semester_data = batch_meta_or_sem_data
            batch_meta = {}
        else:
            batch_meta = batch_meta_or_sem_data if isinstance(batch_meta_or_sem_data, dict) else {}
        if not batch_meta:
            batch_meta = {}
        if not semester_data:
            semester_data = {}
        if not master_students:
            master_students = []
        available_sems = sorted(semester_data.keys())
        total_students = len(master_students)

        # -------------------------------------------------------------
        # 1. Overall Summary Metrics
        # -------------------------------------------------------------
        appeared_all_sems = sum(
            1 for m in master_students
            if (m.get("entry_type") == "Lateral Entry / DSE (Semester 3)" and len([s for s in available_sems if int(s) >= 3 and (int(s) in m["semesters"] or str(s) in m["semesters"])]) == len([s for s in available_sems if int(s) >= 3]))
            or len(m["semesters"]) == len(available_sems)
        )
        
        # Check cleared all available semesters without any fail
        cleared_all_sems = 0
        students_with_any_kt = 0
        graduated_eligible = 0  # cleared all available sems up to 8

        all_sgpis: List[float] = []
        student_progression_list: List[Dict[str, Any]] = []

        for m in master_students:
            sems_appeared = m["semesters"]
            cleared_every = True
            had_kt = False
            valid_sgpis = []

            for sem, rec in sems_appeared.items():
                if str(rec.get("result") or "").upper() != "PASS":
                    cleared_every = False
                    had_kt = True
                if rec.get("sgpi") is not None:
                    try:
                        valid_sgpis.append(float(rec["sgpi"]))
                    except (ValueError, TypeError):
                        pass

            if cleared_every and len(sems_appeared) > 0:
                cleared_all_sems += 1
            if had_kt:
                students_with_any_kt += 1

            # Graduation eligibility check: appeared in sem 8 and cleared sem 8 and all appeared
            sem8_rec = sems_appeared.get(8) or sems_appeared.get("8")
            if sem8_rec and str(sem8_rec.get("result") or "").upper() == "PASS":
                if all(str(r.get("result") or "").upper() == "PASS" for r in sems_appeared.values()):
                    graduated_eligible += 1

            avg_sgpi = round(float(np.mean(valid_sgpis)), 2) if valid_sgpis else None
            max_sgpi = max(valid_sgpis) if valid_sgpis else None
            min_sgpi = min(valid_sgpis) if valid_sgpis else None

            if avg_sgpi is not None:
                all_sgpis.append(avg_sgpi)

            # Performance Progression / Trend Slope
            progression_text = "N/A"
            if len(valid_sgpis) >= 2:
                first_val = valid_sgpis[0]
                last_val = valid_sgpis[-1]
                delta = round(last_val - first_val, 2)
                if delta >= 0.50:
                    progression_text = f"Improving (+{delta:.2f})"
                elif delta <= -0.50:
                    progression_text = f"Declining ({delta:.2f})"
                elif all(v >= 8.25 for v in valid_sgpis):
                    progression_text = "Consistent High (≥8.25)"
                else:
                    std_dev = np.std(valid_sgpis)
                    if std_dev > 0.8:
                        progression_text = "Fluctuating"
                    else:
                        progression_text = f"Stable ({delta:+.2f})"
            elif len(valid_sgpis) == 1:
                progression_text = "Single Semester"

            # Last available status
            last_sem = max(sems_appeared.keys()) if sems_appeared else None
            if last_sem == 8 and sems_appeared[8]["result"] == "PASS":
                final_status = "Graduated / Cleared (Sem 8)"
            elif last_sem:
                res_last = sems_appeared[last_sem]["result"]
                final_status = f"{res_last} in Sem {last_sem}"
            else:
                final_status = "N/A"

            student_progression_list.append({
                "student_id": m["student_id"],
                "canonical_name": m["canonical_name"],
                "prn": m.get("prn", "N/A"),
                "semesters_appeared_count": len(sems_appeared),
                "semesters_appeared": sorted(list(sems_appeared.keys())),
                "sem_records": {s: sems_appeared.get(s, None) for s in range(1, 9)},
                "average_sgpi": avg_sgpi,
                "highest_sgpi": max_sgpi,
                "lowest_sgpi": min_sgpi,
                "progression": progression_text,
                "final_status": final_status
            })

        # -------------------------------------------------------------
        # Cohort Partitioning & Graduation Evaluation
        # -------------------------------------------------------------
        final_sem = max(available_sems) if available_sems else 8

        regular_students = [
            m for m in master_students
            if m.get("entry_category") == "Regular Entry (Semester 1)" or (m.get("first_appeared_sem") in (1, 2) and not m.get("is_diploma"))
        ]
        lateral_students = [
            m for m in master_students
            if m.get("entry_category") == "Diploma Student (Lateral Entry)" or (m.get("is_diploma") and m.get("first_appeared_sem") in (3, 4))
        ]
        other_students = [m for m in master_students if m not in regular_students and m not in lateral_students]
        diploma_students = lateral_students

        N1 = len(regular_students)
        N3 = len(lateral_students)
        N_other = len(other_students)
        total_cohort_intake = N1 + N3
        cohort_students = regular_students + lateral_students

        def student_passed_sem(m_rec, s_num):
            if m_rec.get("passed_final") and s_num == final_sem:
                return True
            sems = m_rec.get("semesters") or {}
            rec = sems.get(s_num) or sems.get(str(s_num))
            if isinstance(rec, dict):
                return str(rec.get("result") or "").upper() == "PASS"
            return str(m_rec.get("sem_records", {}).get(str(s_num), "")).upper() == "PASS"

        def student_zero_kt(m_rec):
            if "zero_kt" in m_rec:
                return bool(m_rec["zero_kt"])
            if "passed_without_backlog" in m_rec:
                return bool(m_rec["passed_without_backlog"])
            sems = m_rec.get("semesters") or {}
            if isinstance(sems, dict) and sems:
                return all(str(r.get("result") or "").upper() == "PASS" for r in sems.values() if isinstance(r, dict))
            return not m_rec.get("had_backlogs", False)

        passed_final_regular = sum(1 for m in regular_students if student_passed_sem(m, final_sem))
        passed_final_lateral = sum(1 for m in lateral_students if student_passed_sem(m, final_sem))
        passed_final_other = sum(1 for m in other_students if student_passed_sem(m, final_sem))
        P_final = passed_final_regular + passed_final_lateral

        passed_no_kt_regular = sum(
            1 for m in regular_students
            if student_passed_sem(m, final_sem) and student_zero_kt(m)
        )
        passed_no_kt_lateral = sum(
            1 for m in lateral_students
            if student_passed_sem(m, final_sem) and student_zero_kt(m)
        )
        P_no_kt = passed_no_kt_regular + passed_no_kt_lateral
        P_with_kt = max(0, P_final - P_no_kt)

        success_rate_pct = round((P_final / total_cohort_intake) * 100, 2) if total_cohort_intake > 0 else 0.0
        success_rate_no_kt_pct = round((P_no_kt / total_cohort_intake) * 100, 2) if total_cohort_intake > 0 else 0.0
        success_rate_with_kt_pct = round((P_with_kt / total_cohort_intake) * 100, 2) if total_cohort_intake > 0 else 0.0

        regular_success_rate = round((passed_final_regular / N1) * 100, 2) if N1 > 0 else 0.0
        lateral_success_rate = round((passed_final_lateral / N3) * 100, 2) if N3 > 0 else 0.0

        # Cohort Graduates Analysis (Official Degree Classification on P_final candidates)
        cohort_graduates = [m for m in cohort_students if student_passed_sem(m, final_sem)]
        grad_sgpis = [m["average_sgpi"] for m in cohort_graduates if m.get("average_sgpi") is not None]

        batch_avg_sgpi = round(float(np.mean(grad_sgpis)), 2) if grad_sgpis else None
        batch_max_sgpi = max(grad_sgpis) if grad_sgpis else None
        batch_min_sgpi = min(grad_sgpis) if grad_sgpis else None

        # Institutional Classifications for Degree Recipients
        distinction_count = sum(1 for s in grad_sgpis if s >= 7.50)
        first_class_count = sum(1 for s in grad_sgpis if 6.75 <= s < 7.50)
        second_class_count = sum(1 for s in grad_sgpis if 6.00 <= s < 6.75)
        pass_class_count = sum(1 for s in grad_sgpis if s < 6.00)
        fc_dist_count = distinction_count + first_class_count
        pending_backlog_count = max(0, total_cohort_intake - P_final)

        # Real Histogram Bins from Graduated Students
        sgpi_distribution = [
            {"range": "9.00 – 10.00", "count": sum(1 for s in grad_sgpis if s >= 9.0), "percentage": round(sum(1 for s in grad_sgpis if s >= 9.0) / len(grad_sgpis) * 100, 2) if grad_sgpis else 0.0},
            {"range": "8.00 – 8.99", "count": sum(1 for s in grad_sgpis if 8.0 <= s < 9.0), "percentage": round(sum(1 for s in grad_sgpis if 8.0 <= s < 9.0) / len(grad_sgpis) * 100, 2) if grad_sgpis else 0.0},
            {"range": "7.00 – 7.99", "count": sum(1 for s in grad_sgpis if 7.0 <= s < 8.0), "percentage": round(sum(1 for s in grad_sgpis if 7.0 <= s < 8.0) / len(grad_sgpis) * 100, 2) if grad_sgpis else 0.0},
            {"range": "6.00 – 6.99", "count": sum(1 for s in grad_sgpis if 6.0 <= s < 7.0), "percentage": round(sum(1 for s in grad_sgpis if 6.0 <= s < 7.0) / len(grad_sgpis) * 100, 2) if grad_sgpis else 0.0},
            {"range": "< 6.00", "count": sum(1 for s in grad_sgpis if s < 6.0), "percentage": round(sum(1 for s in grad_sgpis if s < 6.0) / len(grad_sgpis) * 100, 2) if grad_sgpis else 0.0}
        ]

        overall_summary = {
            "total_students": total_cohort_intake,
            "total_cohort_intake": total_cohort_intake,
            "total_intake_all": len(master_students),
            "regular_intake_n1": N1,
            "lateral_entry_n3": N3,
            "other_entry": N_other,
            "appeared_all_available_semesters": appeared_all_sems,
            "cleared_all_available_semesters": P_no_kt,
            "students_with_any_kt": students_with_any_kt,
            "graduated_eligible": P_final,
            "batch_average_sgpi": batch_avg_sgpi,
            "batch_highest_sgpi": batch_max_sgpi,
            "batch_lowest_passing_sgpi": batch_min_sgpi,
            "cleared_all_percentage": round((P_final / total_cohort_intake) * 100, 2) if total_cohort_intake else 0.0,
            "kt_student_percentage": round((students_with_any_kt / total_cohort_intake) * 100, 2) if total_cohort_intake else 0.0,
            "first_class_distinction_count": fc_dist_count,
            "first_class_distinction_percentage": round((fc_dist_count / total_cohort_intake) * 100, 2) if total_cohort_intake else 0.0,
            "pending_backlog_count": pending_backlog_count,
            "pending_backlog_percentage": round((pending_backlog_count / total_cohort_intake) * 100, 2) if total_cohort_intake else 0.0,
            "grade_distribution": {
                "distinction": distinction_count,
                "first_class": first_class_count,
                "second_class": second_class_count,
                "pass_class": pass_class_count,
                "pending_backlog": pending_backlog_count,
                "total_graduated": P_final
            },
            "sgpi_distribution": sgpi_distribution
        }
        lateral_success_rate = round((passed_final_lateral / N3) * 100, 2) if N3 > 0 else 0.0

        # Detailed roster for entity cross-checking & audit
        student_success_roster = []
        for idx, m in enumerate(master_students, start=1):
            sems = m.get("semesters") or {}
            had_any_kt = not student_zero_kt(m)
            passed_final = student_passed_sem(m, final_sem)
            passed_without_kt = passed_final and not had_any_kt

            avg_sgpi = m.get("average_sgpi")

            sem_map = {}
            sem_res_map = {}
            for s in range(1, 9):
                rec = sems.get(s) or sems.get(str(s))
                if isinstance(rec, dict):
                    s_r = str(rec.get("result") or "").upper()
                    sem_map[f"sem_{s}_result"] = s_r
                    sem_map[f"sem_{s}_sgpi"] = rec.get("sgpi")
                    sem_res_map[s] = s_r
                else:
                    s_r = m.get("sem_records", {}).get(str(s), "—")
                    sem_map[f"sem_{s}_result"] = s_r
                    sem_map[f"sem_{s}_sgpi"] = m.get("sem_sgpis", {}).get(str(s))
                    sem_res_map[s] = s_r

            student_success_roster.append({
                "sr_no": idx,
                "student_id": m["student_id"],
                "canonical_name": m["canonical_name"],
                "name": m["canonical_name"],
                "prn": m.get("prn", ""),
                "entry_type": m.get("entry_type", "Regular Entry (Semester 1)"),
                "entry_category": m.get("entry_category", "Regular Entry (Semester 1)"),
                "is_multiple_entry": False,
                "is_multiple_exit": False,
                "exit_status": "Graduated (Sem 8)" if passed_final else ("KT / Dropped" if not had_any_kt else "KT Backlogs Pending"),
                "exit_category": "Graduated" if passed_final else "Dropped / KT",
                "entry_sem": m.get("first_appeared_sem", 1),
                "first_appeared_sem": m.get("first_appeared_sem", 1),
                "last_appeared_sem": m.get("last_appeared_sem", 8),
                "semesters_appeared_count": m.get("semesters_appeared_count", len(sems)),
                "final_status": "PASS" if passed_final else "NOT_CLEARED",
                "passed_without_backlog": passed_without_kt,
                "zero_kt": passed_without_kt,
                "had_backlogs": had_any_kt,
                "cumulative_avg_sgpi": avg_sgpi,
                "average_sgpi": avg_sgpi,
                "sem_records": sem_res_map,
                **sem_map
            })

        breakdown_table = [
            {
                "category": "Regular Entry (Semester 1)",
                "entry_sem": "Semester 1",
                "admitted": N1,
                "multiple_exit_after_y1": 0,
                "passed_final": passed_final_regular,
                "passed_no_kt": passed_no_kt_regular,
                "passed_with_kt": max(0, passed_final_regular - passed_no_kt_regular),
                "success_rate": regular_success_rate
            },
            {
                "category": "Diploma Student (Lateral Entry)",
                "entry_sem": "Semester 3",
                "admitted": N3,
                "multiple_exit_after_y1": 0,
                "passed_final": passed_final_lateral,
                "passed_no_kt": passed_no_kt_lateral,
                "passed_with_kt": max(0, passed_final_lateral - passed_no_kt_lateral),
                "success_rate": lateral_success_rate
            }
        ]
        if other_students:
            breakdown_table.append({
                "category": "Transfer / Readmitted Students",
                "entry_sem": "Intermediate Semesters",
                "admitted": N_other,
                "multiple_exit_after_y1": 0,
                "passed_final": passed_final_other,
                "passed_no_kt": max(0, passed_final_other),
                "passed_with_kt": 0,
                "success_rate": round((passed_final_other / N_other) * 100, 2) if N_other > 0 else 0.0
            })

        success_rate_analysis = {
            "N1_regular_admitted": N1,
            "regular_intake_n1": N1,
            "N3_lateral_entry": N3,
            "lateral_entry_n3": N3,
            "diploma_intake_count": N3,
            "N_other_transfer": N_other,
            "other_entry": N_other,
            "total_cohort_intake": total_cohort_intake,
            "total_admitted_n1_n3": total_cohort_intake,
            "total_intake_all": len(master_students),
            "multiple_entry_count": 0,
            "multiple_exit_after_y1_count": 0,
            "multiple_exit_students": [],
            "multiple_entry_students": [],
            "diploma_students_list": [
                {
                    "name": m.get("canonical_name", ""),
                    "prn": m.get("prn", "N/A"),
                    "entry_semester": m.get("first_appeared_sem", 3),
                    "graduated": student_passed_sem(m, final_sem)
                }
                for m in diploma_students
            ],
            "final_semester": final_sem,
            "final_semester_evaluated": final_sem,
            "P_final_passed": P_final,
            "passed_final_sem": P_final,
            "P_final_regular": passed_final_regular,
            "P_final_lateral": passed_final_lateral,
            "P_no_kt_passed": P_no_kt,
            "passed_without_backlogs": P_no_kt,
            "P_no_kt_regular": passed_no_kt_regular,
            "P_no_kt_lateral": passed_no_kt_lateral,
            "P_with_kt_passed": P_with_kt,
            "passed_with_backlogs": P_with_kt,
            "failed_final_sem": max(0, total_cohort_intake - P_final),
            "success_rate_percentage": success_rate_pct,
            "success_rate_without_backlog_percentage": success_rate_no_kt_pct,
            "success_rate_no_backlog_percentage": success_rate_no_kt_pct,
            "success_rate_with_backlog_percentage": success_rate_with_kt_pct,
            "regular_success_rate": regular_success_rate,
            "lateral_success_rate": lateral_success_rate,
            "formula": "Success Rate = P_final / (N1 + N3) * 100%",
            "student_roster": student_success_roster,
            "breakdown": breakdown_table
        }

        # -------------------------------------------------------------
        # 3. Semester-Wise Result Analysis (Comparative Sem 1 to 8)
        # -------------------------------------------------------------
        semester_analysis: List[Dict[str, Any]] = []
        for sem in available_sems:
            raw_s = semester_data[sem]
            recs = raw_s.get("records", []) if isinstance(raw_s, dict) else raw_s
            tot_cnt = len(recs)
            pass_cnt = sum(1 for r in recs if str(r.get("result") or r.get("Result") or "").upper() == "PASS")
            fail_cnt = sum(1 for r in recs if str(r.get("result") or r.get("Result") or "").upper() != "PASS")
            pass_pct = round((pass_cnt / tot_cnt) * 100, 1) if tot_cnt > 0 else 0.0
            fail_pct = round((fail_cnt / tot_cnt) * 100, 1) if tot_cnt > 0 else 0.0

            sem_sgpis = [float(r.get("sgpi") if r.get("sgpi") is not None else r.get("SGPI", 0)) for r in recs if (r.get("sgpi") is not None or r.get("SGPI") is not None) and float(r.get("sgpi") or r.get("SGPI") or 0) > 0]
            avg_sgpi = round(float(np.mean(sem_sgpis)), 2) if sem_sgpis else None
            max_sgpi = max(sem_sgpis) if sem_sgpis else None
            min_sgpi = min(sem_sgpis) if sem_sgpis else None

            dist_cnt = sum(1 for s in sem_sgpis if s >= 7.5)
            first_cnt = sum(1 for s in sem_sgpis if 6.75 <= s < 7.5)
            second_cnt = sum(1 for s in sem_sgpis if 6.0 <= s < 6.75)
            pass_class_cnt = sum(1 for s in sem_sgpis if s < 6.0)

            # Count Regular (N1) vs Lateral Diploma (N3) vs Transfer / Other in this semester
            reg_cnt = sum(1 for m in master_students if (sem in m["semesters"] or str(sem) in m["semesters"]) and m.get("entry_category") == "Regular Entry (Semester 1)")
            lat_cnt = sum(1 for m in master_students if (sem in m["semesters"] or str(sem) in m["semesters"]) and m.get("entry_category") == "Diploma Student (Lateral Entry)")
            other_cnt = sum(1 for m in master_students if (sem in m["semesters"] or str(sem) in m["semesters"]) and m.get("entry_category") not in ["Regular Entry (Semester 1)", "Diploma Student (Lateral Entry)"])

            # KT info
            kt_students = fail_cnt
            kt_sub_counts = [r.get("kt_count") if r.get("kt_count") is not None else r.get("KT_Count", 0) for r in recs if str(r.get("result") or r.get("Result") or "").upper() != "PASS" and (r.get("kt_count") or r.get("KT_Count"))]
            total_kt_subjects = sum(kt_sub_counts) if kt_sub_counts else None

            file_info = next((f for f in batch_meta.get("semesters_detected", []) if f["semester"] == sem), {})
            in_type = file_info.get("file_type") or (raw_s.get("input_type") if isinstance(raw_s, dict) else "EXCEL")
            fn = file_info.get("filename") or (raw_s.get("source_files", [f"Sem_{sem}"])[0] if isinstance(raw_s, dict) and raw_s.get("source_files") else f"Sem_{sem}")

            semester_analysis.append({
                "semester": sem,
                "semester_name": f"Semester {sem}",
                "input_type": in_type.upper(),
                "filename": fn,
                "total_students": tot_cnt,
                "regular_students": reg_cnt,
                "lateral_students": lat_cnt,
                "other_students": other_cnt,
                "passed": pass_cnt,
                "failed": fail_cnt,
                "pass_percentage": pass_pct,
                "fail_percentage": fail_pct,
                "average_sgpi": avg_sgpi,
                "highest_sgpi": max_sgpi,
                "lowest_sgpi": min_sgpi,
                "distinction_count": dist_cnt,
                "first_class_count": first_cnt,
                "second_class_count": second_cnt,
                "pass_class_count": pass_class_cnt,
                "kt_students_count": kt_students,
                "total_kt_subjects": total_kt_subjects
            })

        # -------------------------------------------------------------
        # 3. KT Analysis & Repeat Tracking
        # -------------------------------------------------------------
        kt_analysis_table: List[Dict[str, Any]] = []
        for sem in available_sems:
            raw_s = semester_data[sem]
            recs = raw_s.get("records", []) if isinstance(raw_s, dict) else raw_s
            tot = len(recs)
            kt_stds = sum(1 for r in recs if str(r.get("result") or r.get("Result") or "").upper() != "PASS")
            kt_rate = round((kt_stds / tot) * 100, 1) if tot > 0 else 0.0
            sub_counts = [r.get("kt_count") if r.get("kt_count") is not None else r.get("KT_Count", 0) for r in recs if str(r.get("result") or r.get("Result") or "").upper() != "PASS" and (r.get("kt_count") or r.get("KT_Count"))]
            total_kt_subs = sum(sub_counts) if sub_counts else None
            avg_kt_per_fail = round(total_kt_subs / kt_stds, 1) if kt_stds and total_kt_subs else None

            kt_analysis_table.append({
                "semester": sem,
                "semester_name": f"Semester {sem}",
                "total_students": tot,
                "kt_students": kt_stds,
                "kt_percentage": kt_rate,
                "total_kt_subjects": total_kt_subs if total_kt_subs is not None else "N/A",
                "avg_kt_per_failed_student": avg_kt_per_fail if avg_kt_per_fail is not None else "N/A"
            })

        # Repeated KT Occurrences: Students who failed in >= 2 semesters
        repeated_kt_students: List[Dict[str, Any]] = []
        for m in master_students:
            failed_sems = [s for s, r in m["semesters"].items() if str(r.get("result") or r.get("Result") or "").upper() != "PASS"]
            if len(failed_sems) >= 2:
                repeated_kt_students.append({
                    "student_id": m["student_id"],
                    "canonical_name": m["canonical_name"],
                    "failed_semesters": failed_sems,
                    "repeat_count": len(failed_sems),
                    "latest_sem": max(m["semesters"].keys()),
                    "latest_result": m["semesters"][max(m["semesters"].keys())]["result"]
                })

        repeated_kt_students.sort(key=lambda x: x["repeat_count"], reverse=True)

        # -------------------------------------------------------------
        # 4. Topper Analysis (Top performers per semester + Multi-Sem)
        # -------------------------------------------------------------
        topper_by_semester: Dict[int, List[Dict[str, Any]]] = {}
        topper_frequency: Dict[str, Dict[str, Any]] = {}

        # Master student lookup for cross-semester identity resolution in toppers
        student_lookup: Dict[Tuple[int, str], Dict[str, Any]] = {}
        for m in master_students:
            for s_num, s_info in m.get("semesters", {}).items():
                if isinstance(s_info, dict) and s_info.get("seat_no"):
                    student_lookup[(int(s_num), str(s_info["seat_no"]).strip())] = m
            for raw_n in m.get("all_names", []):
                for s_num in m.get("semesters", {}):
                    student_lookup[(int(s_num), str(raw_n).strip().upper())] = m
                    student_lookup[(int(s_num), " ".join(normalize_student_name_tokens(raw_n)))] = m

        for sem in available_sems:
            raw_s = semester_data[sem]
            recs = raw_s.get("records", []) if isinstance(raw_s, dict) else raw_s
            cleared = [r for r in recs if (r.get("sgpi") is not None or r.get("SGPI") is not None) and str(r.get("result") or r.get("Result") or "").upper() == "PASS"]
            cleared.sort(key=lambda x: float(x.get("sgpi") if x.get("sgpi") is not None else x.get("SGPI", 0)), reverse=True)

            top_list = []
            for rank_idx, r in enumerate(cleared[:10]):
                r_name = str(r.get("name") or r.get("Name") or "")
                r_seat = str(r.get("seat_no") or r.get("Seat_No") or "")
                r_sgpi = float(r.get("sgpi") if r.get("sgpi") is not None else r.get("SGPI", 0))

                norm_n = " ".join(normalize_student_name_tokens(r_name))
                matched_m = (
                    student_lookup.get((int(sem), r_seat.strip())) or
                    student_lookup.get((int(sem), r_name.strip().upper())) or
                    student_lookup.get((int(sem), norm_n))
                )

                c_name = matched_m["canonical_name"] if matched_m else format_canonical_name(r_name)
                c_id = matched_m["student_id"] if matched_m else (format_canonical_name(r_name) or r_seat)

                top_item = {
                    "rank": rank_idx + 1,
                    "student_name": c_name,
                    "seat_no": r_seat,
                    "student_id": c_id,
                    "sgpi": r_sgpi,
                    "semester": sem
                }
                top_list.append(top_item)

                # Track topper frequency with unified master identity
                if c_id not in topper_frequency:
                    topper_frequency[c_id] = {
                        "student_id": c_id,
                        "canonical_name": c_name,
                        "seat_no": r_seat,
                        "appearances": 0,
                        "semesters": [],
                        "sgpi_values": []
                    }
                topper_frequency[c_id]["appearances"] += 1
                topper_frequency[c_id]["semesters"].append(sem)
                topper_frequency[c_id]["sgpi_values"].append(r_sgpi)

            topper_by_semester[sem] = top_list

        # Multi-Semester Consistent Toppers: appeared in top 10 >= 2 times
        multi_sem_toppers = []
        for v in topper_frequency.values():
            if v["appearances"] >= 2:
                avg_sg = round(float(np.mean(v["sgpi_values"])), 2) if v["sgpi_values"] else 0.0
                multi_sem_toppers.append({
                    "canonical_name": v["canonical_name"],
                    "name": v["canonical_name"],
                    "student_name": v["canonical_name"],
                    "seat_no": v["seat_no"],
                    "student_id": v.get("student_id") or v["seat_no"],
                    "appearances": v["appearances"],
                    "count": v["appearances"],
                    "semesters": sorted(v["semesters"]),
                    "average_sgpi": avg_sg,
                    "avg_sgpi": avg_sg
                })
        multi_sem_toppers.sort(key=lambda x: (x["appearances"], x["average_sgpi"]), reverse=True)
        for rank_idx, t in enumerate(multi_sem_toppers, 1):
            t["rank"] = rank_idx

        # -------------------------------------------------------------
        # 5. Retention & Progression Analysis
        # -------------------------------------------------------------
        retention_flow: List[Dict[str, Any]] = []
        for i in range(len(available_sems) - 1):
            s_from = available_sems[i]
            s_to = available_sems[i + 1]
            raw_from = semester_data[s_from]
            raw_to = semester_data[s_to]
            from_count = len(raw_from.get("records", [])) if isinstance(raw_from, dict) else len(raw_from)
            to_count = len(raw_to.get("records", [])) if isinstance(raw_to, dict) else len(raw_to)

            # Continuing students: present in both
            continuing = sum(1 for m in master_students if s_from in m["semesters"] and s_to in m["semesters"])
            # Lateral / new entry in s_to: present in s_to but not in s_from (or any prior)
            new_lateral = sum(
                1 for m in master_students
                if s_to in m["semesters"] and all(prev not in m["semesters"] for prev in available_sems[:i + 1])
            )

            retention_flow.append({
                "transition": f"Sem {s_from} ➔ Sem {s_to}",
                "from_semester": s_from,
                "to_semester": s_to,
                "initial_count": from_count,
                "next_count": to_count,
                "continuing_students": continuing,
                "lateral_new_students": new_lateral,
                "retention_rate": round((continuing / from_count) * 100, 1) if from_count > 0 else 0.0
            })

        # Cohort summary counts
        cohort_counts = {
            "appearing_in_all_available": appeared_all_sems,
            "appearing_in_ge_4_sems": sum(1 for m in master_students if len(m["semesters"]) >= 4),
            "appearing_in_ge_2_sems": sum(1 for m in master_students if len(m["semesters"]) >= 2),
            "lateral_entry_students_est": sum(
                1 for m in master_students
                if 1 not in m["semesters"] and 2 not in m["semesters"] and 3 in m["semesters"]
            )
        }

        # -------------------------------------------------------------
        # 6. Student Performance Categorization
        # -------------------------------------------------------------
        strong_students: List[Dict[str, Any]] = []
        improving_students: List[Dict[str, Any]] = []
        declining_students: List[Dict[str, Any]] = []
        fluctuating_students: List[Dict[str, Any]] = []
        difficulty_students: List[Dict[str, Any]] = []

        for p in student_progression_list:
            c_name = p["canonical_name"]
            s_id = p["student_id"]
            sems = p["semesters_appeared"]
            recs_with_sem = [(s, p["sem_records"][s]) for s in sems if p["sem_records"].get(s)]
            sgpis = [r["sgpi"] for s, r in recs_with_sem if r.get("sgpi") is not None]
            fails = [s for s, r in recs_with_sem if str(r.get("result") or "").upper() != "PASS"]

            # Criteria 1: Repeated Academic Difficulties (KT in >= 2 semesters)
            if len(fails) >= 2:
                difficulty_students.append({
                    "student_id": s_id,
                    "canonical_name": c_name,
                    "appeared_count": len(sems),
                    "failed_semesters": fails,
                    "average_sgpi": p["average_sgpi"] or "N/A",
                    "classification_reason": f"Recurring academic backlogs in {len(fails)} semesters ({', '.join([str(s) for s in fails])})"
                })

            # Criteria 2: Consistently Strong Performance (all appeared passed, avg SGPI >= 8.0, all >= 7.75)
            if len(fails) == 0 and len(sgpis) >= 3 and p["average_sgpi"] and p["average_sgpi"] >= 8.0 and min(sgpis) >= 7.50:
                strong_students.append({
                    "student_id": s_id,
                    "canonical_name": c_name,
                    "appeared_count": len(sems),
                    "average_sgpi": p["average_sgpi"],
                    "highest_sgpi": p["highest_sgpi"],
                    "lowest_sgpi": p["lowest_sgpi"],
                    "classification_reason": f"Maintained Distinction (Avg {p['average_sgpi']}) across {len(sems)} semesters with zero backlogs"
                })

            # Criteria 3: Improving Performance (no fails, latest SGPI >= first SGPI + 0.50)
            elif len(sgpis) >= 3 and len(fails) <= 1:
                first_sgpi = sgpis[0]
                last_sgpi = sgpis[-1]
                delta = last_sgpi - first_sgpi
                if delta >= 0.50:
                    improving_students.append({
                        "student_id": s_id,
                        "canonical_name": c_name,
                        "appeared_count": len(sems),
                        "initial_sgpi": first_sgpi,
                        "latest_sgpi": last_sgpi,
                        "delta": round(delta, 2),
                        "classification_reason": f"Progressive upward growth (+{delta:.2f} SGPI from Sem {sems[0]} to Sem {sems[-1]})"
                    })
                elif delta <= -0.60:
                    declining_students.append({
                        "student_id": s_id,
                        "canonical_name": c_name,
                        "appeared_count": len(sems),
                        "initial_sgpi": first_sgpi,
                        "latest_sgpi": last_sgpi,
                        "delta": round(delta, 2),
                        "classification_reason": f"Drop in performance ({delta:.2f} SGPI from Sem {sems[0]} to Sem {sems[-1]})"
                    })
                elif len(sgpis) >= 4 and np.std(sgpis) > 0.90:
                    fluctuating_students.append({
                        "student_id": s_id,
                        "canonical_name": c_name,
                        "appeared_count": len(sems),
                        "average_sgpi": p["average_sgpi"],
                        "std_dev": round(float(np.std(sgpis)), 2),
                        "classification_reason": f"High variance across semesters (Std Dev: {np.std(sgpis):.2f})"
                    })

        student_categorization = {
            "consistently_strong": strong_students,
            "improving_performance": improving_students,
            "declining_performance": declining_students,
            "fluctuating_performance": fluctuating_students,
            "repeated_difficulties": difficulty_students
        }

        # -------------------------------------------------------------
        # 7. Data Quality Audit Log
        # -------------------------------------------------------------
        quality_issues: List[Dict[str, Any]] = []

        # Check 1: Missing values (missing name, missing SGPI when passed)
        for sem in available_sems:
            raw_s = semester_data[sem]
            recs = raw_s.get("records", []) if isinstance(raw_s, dict) else raw_s
            for r in recs:
                r_name = str(r.get("name") or r.get("Name") or "").strip()
                r_seat = str(r.get("seat_no") or r.get("Seat_No") or "N/A").strip()
                r_sgpi = r.get("sgpi") if r.get("sgpi") is not None else r.get("SGPI")
                r_res = str(r.get("result") or r.get("Result") or "").upper()

                if not r_name or len(r_name) < 3:
                    quality_issues.append({
                        "category": "Missing Student Name",
                        "semester": sem,
                        "student_id": r_seat,
                        "description": "Student record is missing a valid name",
                        "severity": "High",
                        "action_taken": "Preserved without modification (source data traceable)"
                    })
                if r_res == "PASS" and r_sgpi is None:
                    quality_issues.append({
                        "category": "Missing SGPI",
                        "semester": sem,
                        "student_id": r_seat,
                        "student_name": r_name or "N/A",
                        "description": "Student is marked PASS but SGPI value is unrecorded or missing in ledger",
                        "severity": "Medium",
                        "action_taken": "Marked as N/A in statistics"
                    })
                # Check 2: Invalid SGPI values
                if r_sgpi is not None and (float(r_sgpi) < 0 or float(r_sgpi) > 10.0):
                    quality_issues.append({
                        "category": "Invalid SGPI",
                        "semester": sem,
                        "student_id": r_seat,
                        "student_name": r_name or "N/A",
                        "description": f"SGPI value {r_sgpi} exceeds university 10-point scale",
                        "severity": "High",
                        "action_taken": "Preserved with anomaly tag"
                    })

        # Check 3: Duplicate identifiers within same semester
        for sem in available_sems:
            seen_seats: Set[str] = set()
            raw_s = semester_data[sem]
            recs = raw_s.get("records", []) if isinstance(raw_s, dict) else raw_s
            for r in recs:
                s_no = str(r.get("seat_no") or r.get("Seat_No") or "").strip()
                r_name = str(r.get("name") or r.get("Name") or "N/A").strip()
                if s_no and s_no != "N/A" and not s_no.startswith("STU_"):
                    if s_no in seen_seats:
                        quality_issues.append({
                            "category": "Duplicate Record",
                            "semester": sem,
                            "student_id": s_no,
                            "student_name": r_name,
                            "description": f"Seat number {s_no} appears multiple times in Semester {sem}",
                            "severity": "Medium",
                            "action_taken": "Preserved both rows traceable to source"
                        })
                    else:
                        seen_seats.add(s_no)

        # Check 4: Discontinuous appearances (gap semesters, e.g. appeared in Sem 1 and Sem 3 but missing in Sem 2)
        for m in master_students:
            sems = sorted(list(m["semesters"].keys()))
            if len(sems) >= 2:
                for i in range(len(sems) - 1):
                    if sems[i + 1] - sems[i] > 1:
                        missing_sems = list(range(sems[i] + 1, sems[i + 1]))
                        # Only flag if missing semester was in available batch files
                        missing_available = [s for s in missing_sems if s in available_sems]
                        if missing_available:
                            quality_issues.append({
                                "category": "Missing Student Record / Gap Semester",
                                "semester": f"Sem {', '.join(map(str, missing_available))}",
                                "student_id": m["student_id"],
                                "student_name": m["canonical_name"],
                                "description": f"Student appeared in Sem {sems[i]} and Sem {sems[i+1]} but is unrecorded in Sem {', '.join(map(str, missing_available))}",
                                "severity": "Informational",
                                "action_taken": "Noted: Do not assume student dropped out (may be KT ledger / gap)"
                            })

        # Check 5: Name variations across semesters
        for m in master_students:
            all_names_list = m.get("all_names") or []
            if len(all_names_list) > 1:
                quality_issues.append({
                    "category": "Inconsistent Student Name",
                    "semester": "Multi-Semester",
                    "student_id": m["student_id"],
                    "student_name": m["canonical_name"],
                    "description": f"Name recorded in different formats across semesters: {', '.join(list(all_names_list)[:3])}",
                    "severity": "Informational",
                    "action_taken": "Harmonized to most complete canonical name"
                })

        # Assemble Master Payload
        return sanitize_dict_for_json({
            "batch_metadata": batch_meta,
            "available_semesters": available_sems,
            "overall_summary": overall_summary,
            "success_rate_analysis": success_rate_analysis,
            "semester_wise_analysis": semester_analysis,
            "semester_analysis": semester_analysis,
            "sgpi_distribution": sgpi_distribution,
            "student_progression": student_progression_list,
            "kt_analysis": kt_analysis_table,
            "repeated_kt_students": repeated_kt_students,
            "topper_by_semester": topper_by_semester,
            "multi_sem_toppers": multi_sem_toppers,
            "retention_flow": retention_flow,
            "cohort_retention_summary": cohort_counts,
            "student_performance_categorization": student_categorization,
            "data_quality_issues": quality_issues,
            "semester_raw_data": semester_data
        })

    def run_batch_analysis_job(
        self,
        batch_folder: str,
        target_branch: str = "AIDS",
        pdf_pipeline: str = "local",
        excel_mode: str = "direct",
        progress_cb: Optional[Callable[[float, str], None]] = None,
        audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        selected_semesters: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end multi-semester batch analysis with live progress reporting.
        Automatically uses existing clean Excel files in data_extractor_AI/departments_data,
        and auto-extracts missing semesters from PDF, saving clean Excel copies to departments_data.
        """
        t0 = time.time()
        if progress_cb:
            progress_cb(2.0, "Discovering batch semesters and file structures...")

        batch_meta = self.detect_batch_files(batch_folder, target_branch)
        detected_sems = batch_meta["semesters_detected"]
        if selected_semesters:
            detected_sems = [s for s in detected_sems if s["semester"] in selected_semesters]
        if not detected_sems:
            raise ValueError(f"No valid semester files (1st to 8th sem) found in {batch_folder} for selected scope")

        batch_slug = batch_meta.get("batch_slug") or normalize_batch_slug(batch_folder)
        app_store_dir = batch_meta.get("app_batch_dir") or os.path.join(self.app_batch_dir, batch_slug)
        semester_data: Dict[int, List[Dict[str, Any]]] = {}
        total_sems = len(detected_sems)

        # Process each semester
        for idx, s_info in enumerate(detected_sems):
            sem = s_info["semester"]
            fpath = s_info["full_path"]
            ftype = s_info["file_type"]

            step_base = 5.0 + (idx / total_sems) * 75.0
            msg = f"Processing Semester {sem} ({ftype.upper()}): {s_info['filename']}..."
            if progress_cb:
                progress_cb(step_base, msg)

            if audit_cb:
                audit_cb({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "sem_start",
                    "badge": f"SEM {sem}",
                    "title": f"Processing Semester {sem} ({ftype.upper()})",
                    "details": {"file": s_info["filename"], "engine": pdf_pipeline if ftype == "pdf" else excel_mode}
                })

            step_next = 5.0 + ((idx + 1) / total_sems) * 75.0

            def sem_prog(pct: float, prog_msg: str):
                if progress_cb:
                    scaled = step_base + (pct / 100.0) * (step_next - step_base)
                    progress_cb(scaled, f"Sem {sem}: {prog_msg}")

            if ftype == "excel":
                use_ai = (excel_mode == "ai_fix")
                records = self.parse_excel_semester(fpath, sem, target_branch, use_ai_fix=use_ai, progress_cb=sem_prog)
                # Never generate redundant duplicate Excel files if an Excel already exists for this semester
                store_has_excel = any(
                    os.path.isfile(os.path.join(app_store_dir, f"{p}_{sem}.xlsx"))
                    for p in ["Semester", "Sem"]
                )
                if not store_has_excel and not s_info.get("is_app_store") and os.path.abspath(app_store_dir) != os.path.abspath(os.path.dirname(fpath)):
                    try:
                        self.save_clean_semester_excel(records, batch_slug, sem, target_dir=app_store_dir)
                    except Exception as e:
                        logger.warning(f"Could not copy clean excel for Sem {sem}: {e}")
            else:
                # PDF format: extract and only auto-save to clean Excel if no Excel file exists for this semester
                records = self.extract_pdf_semester(fpath, sem, engine_mode=pdf_pipeline, progress_cb=sem_prog)
                store_has_excel = any(
                    os.path.isfile(os.path.join(app_store_dir, f"{p}_{sem}.xlsx"))
                    for p in ["Semester", "Sem"]
                )
                if not store_has_excel:
                    try:
                        clean_path = self.save_clean_semester_excel(records, batch_slug, sem, target_dir=app_store_dir)
                        if audit_cb:
                            audit_cb({
                                "timestamp": time.strftime("%H:%M:%S"),
                                "type": "clean_excel_saved",
                                "badge": f"SEM {sem} EXCEL SAVED",
                                "title": f"Auto-sorted clean Excel saved to {os.path.basename(os.path.dirname(clean_path))}/{os.path.basename(clean_path)}",
                                "details": {"path": clean_path, "students": len(records)}
                            })
                    except Exception as e:
                        logger.warning(f"Could not auto-save extracted PDF records to clean Excel: {e}")

            semester_data[sem] = records

            if audit_cb:
                audit_cb({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "sem_done",
                    "badge": f"SEM {sem} DONE",
                    "title": f"Extracted {len(records)} students for Semester {sem}",
                    "details": {"count": len(records), "sample": records[0]["name"] if records else "None"}
                })

        if progress_cb:
            progress_cb(82.0, "Aligning student identities across all semesters...")

        master_students = self.align_master_students(semester_data)

        if progress_cb:
            progress_cb(90.0, "Synthesizing 10-category multi-semester academic intelligence...")

        analytics = self.compute_batch_analytics(batch_meta, semester_data, master_students)
        analytics["duration_seconds"] = round(time.time() - t0, 2)
        analytics["batch_folder"] = batch_folder
        analytics["pdf_pipeline"] = pdf_pipeline
        analytics["excel_mode"] = excel_mode

        if progress_cb:
            progress_cb(100.0, f"Analysis complete: {len(master_students)} students across {total_sems} semesters.")

        return analytics
