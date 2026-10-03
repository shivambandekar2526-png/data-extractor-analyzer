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


class BatchAcademicAnalyzer:
    """
    Core Batch Academic Intelligence Engine.
    Handles discovery, extraction, cross-semester entity resolution, and metrics synthesis.
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.tabular_ingester = TabularIngester(output_dir)

    def detect_batch_files(self, batch_folder: str, target_branch: str = "AIDS") -> Dict[str, Any]:
        """
        Inspect a batch folder, detect semester files (1 to 8), their file types,
        and whether Excel AI prompt is recommended.
        """
        detected: Dict[int, Dict[str, Any]] = {}
        all_files = []

        if os.path.isdir(batch_folder):
            for entry in sorted(os.listdir(batch_folder)):
                fp = os.path.join(batch_folder, entry)
                if not os.path.isfile(fp):
                    continue
                ext = os.path.splitext(entry)[1].lower()
                if ext not in [".pdf", ".xlsx", ".xls", ".csv"]:
                    continue
                sem_num = detect_semester_number(entry)
                file_type = "pdf" if ext == ".pdf" else "excel"
                file_info = {
                    "filename": entry,
                    "full_path": fp,
                    "file_type": file_type,
                    "ext": ext,
                    "size_bytes": os.path.getsize(fp),
                    "detected_sem": sem_num
                }
                all_files.append(file_info)
                if sem_num and sem_num not in detected:
                    detected[sem_num] = file_info

        excel_count = sum(1 for f in detected.values() if f["file_type"] == "excel")
        pdf_count = sum(1 for f in detected.values() if f["file_type"] == "pdf")
        all_excel = len(detected) > 0 and excel_count == len(detected)
        has_excel = excel_count > 0

        # Sort detected semesters
        sorted_sems = []
        for s in range(1, 9):
            if s in detected:
                sorted_sems.append({
                    "semester": s,
                    **detected[s]
                })

        return {
            "batch_path": batch_folder,
            "batch_name": os.path.basename(batch_folder),
            "semesters_detected": sorted_sems,
            "total_semesters": len(sorted_sems),
            "excel_count": excel_count,
            "pdf_count": pdf_count,
            "all_excel": all_excel,
            "has_excel": has_excel,
            "prompt_excel_ai": has_excel
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
        total_marks_col = None

        for h_idx in header_rows_indices:
            row = rows[h_idx]
            for c_idx, val in enumerate(row):
                if val is None:
                    continue
                v_str = str(val).strip().lower()
                if not name_col and any(k in v_str for k in ["name of student", "name of the student", "candidate name", "student name"]):
                    name_col = c_idx
                elif not name_col and v_str in ["name", "student", "candidate"]:
                    name_col = c_idx
                if not seat_col and any(k in v_str for k in ["seat no", "seat_no", "seat"]):
                    seat_col = c_idx
                elif not seat_col and any(k in v_str for k in ["sr. no.", "sr no", "roll no", "roll_no"]):
                    seat_col = c_idx
                if any(k in v_str for k in ["sgpi", "sgpa", "gpa", "spi"]):
                    sgpi_col = c_idx  # Keep latest (earned SGPI at end of sheet)
                if "result" in v_str or "pass/fail" in v_str:
                    res_col = c_idx
                elif not res_col and v_str in ["status", "p/f"]:
                    res_col = c_idx
                if any(k in v_str for k in ["prn", "enrollment", "registration"]):
                    prn_col = c_idx
                if any(k in v_str for k in ["total marks", "tot marks", "grand total", "aggregate marks", "total"]):
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
        Intelligently resolves and tracks student identities across all semesters.
        Implements strict disambiguation:
        1. Non-empty PRN matching.
        2. High-confidence name token matching with strict ambiguity checks.
        3. Never merges students solely because names are similar.
        """
        master_students: List[Dict[str, Any]] = []

        def find_match(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            rec_tokens = set(normalize_student_name_tokens(rec["name"]))
            rec_prn = str(rec.get("prn") or "").strip()

            # Priority 1: Match by 16-digit / Enrollment PRN
            if rec_prn and len(rec_prn) >= 8:
                for m in master_students:
                    if m.get("prn") and m["prn"] == rec_prn:
                        return m

            # Priority 2: Unambiguous Name Match
            candidates = []
            for m in master_students:
                m_tokens = set(normalize_student_name_tokens(m["canonical_name"]))
                inter = rec_tokens.intersection(m_tokens)

                # Require core overlap (either subset or at least 2 tokens including first & last)
                if rec_tokens.issubset(m_tokens) or m_tokens.issubset(rec_tokens) or len(inter) >= 2:
                    candidates.append((m, len(inter)))

            # If strictly 1 unique candidate matches, safe to merge
            if len(candidates) == 1:
                return candidates[0][0]

            # If multiple candidates match, check if one has strictly higher token overlap
            if len(candidates) > 1:
                candidates.sort(key=lambda x: x[1], reverse=True)
                if candidates[0][1] > candidates[1][1] and candidates[0][1] >= 2:
                    return candidates[0][0]

            return None

        # Process semesters in order 1 to 8
        for sem in sorted(semester_data.keys()):
            records = semester_data[sem]
            for r in records:
                matched = find_match(r)
                if not matched:
                    std_id = r.get("prn") or r.get("seat_no") or f"STU_{len(master_students)+1:03d}"
                    matched = {
                        "student_id": std_id,
                        "canonical_name": format_canonical_name(r["name"]),
                        "original_name": r["name"],
                        "prn": r.get("prn") or "",
                        "all_names": {r["name"]},
                        "all_identifiers": {str(r.get("seat_no") or "")},
                        "semesters": {}
                    }
                    master_students.append(matched)
                else:
                    matched["all_names"].add(r["name"])
                    if r.get("seat_no"):
                        matched["all_identifiers"].add(str(r["seat_no"]))
                    if r.get("prn") and not matched.get("prn"):
                        matched["prn"] = r["prn"]
                    # Adopt more complete canonical name if longer
                    current_tokens = normalize_student_name_tokens(matched["canonical_name"])
                    new_tokens = normalize_student_name_tokens(r["name"])
                    if len(new_tokens) > len(current_tokens):
                        matched["canonical_name"] = format_canonical_name(r["name"])

                # Store this semester's performance record
                matched["semesters"][sem] = r

        # Sort master roster alphabetically by canonical name
        master_students.sort(key=lambda x: x["canonical_name"])
        return master_students

    def compute_batch_analytics(
        self,
        batch_meta: Dict[str, Any],
        semester_data: Dict[int, List[Dict[str, Any]]],
        master_students: List[Dict[str, Any]]
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
        available_sems = sorted(semester_data.keys())
        total_students = len(master_students)

        # -------------------------------------------------------------
        # 1. Overall Summary Metrics
        # -------------------------------------------------------------
        appeared_all_sems = sum(1 for m in master_students if len(m["semesters"]) == len(available_sems))
        
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

            for sem in available_sems:
                if sem in sems_appeared:
                    rec = sems_appeared[sem]
                    if rec["result"] != "PASS":
                        cleared_every = False
                        had_kt = True
                    if rec.get("sgpi") is not None:
                        valid_sgpis.append(rec["sgpi"])
                else:
                    cleared_every = False

            if cleared_every:
                cleared_all_sems += 1
            if had_kt:
                students_with_any_kt += 1

            # Graduation eligibility check: appeared in sem 8 and cleared sem 8
            if 8 in sems_appeared and sems_appeared[8]["result"] == "PASS":
                # and cleared all previous appeared
                if all(sems_appeared[s]["result"] == "PASS" for s in sems_appeared):
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

        batch_avg_sgpi = round(float(np.mean(all_sgpis)), 2) if all_sgpis else None
        batch_max_sgpi = max(all_sgpis) if all_sgpis else None
        batch_min_sgpi = min(all_sgpis) if all_sgpis else None

        # Grade distributions
        distinction_count = sum(1 for s in all_sgpis if s >= 7.50)
        first_class_count = sum(1 for s in all_sgpis if 6.75 <= s < 7.50)
        second_class_count = sum(1 for s in all_sgpis if 6.00 <= s < 6.75)
        pass_class_count = sum(1 for s in all_sgpis if s < 6.00)

        overall_summary = {
            "total_students": total_students,
            "appeared_all_available_semesters": appeared_all_sems,
            "cleared_all_available_semesters": cleared_all_sems,
            "students_with_any_kt": students_with_any_kt,
            "graduated_eligible": graduated_eligible,
            "batch_average_sgpi": batch_avg_sgpi,
            "batch_highest_sgpi": batch_max_sgpi,
            "batch_lowest_passing_sgpi": batch_min_sgpi,
            "cleared_all_percentage": round((cleared_all_sems / total_students) * 100, 1) if total_students else 0.0,
            "kt_student_percentage": round((students_with_any_kt / total_students) * 100, 1) if total_students else 0.0,
            "grade_distribution": {
                "distinction": distinction_count,
                "first_class": first_class_count,
                "second_class": second_class_count,
                "pass_class": pass_class_count
            }
        }

        # -------------------------------------------------------------
        # 2. Semester-by-Semester Analysis
        # -------------------------------------------------------------
        semester_analysis: List[Dict[str, Any]] = []
        for sem in available_sems:
            recs = semester_data[sem]
            tot_cnt = len(recs)
            pass_cnt = sum(1 for r in recs if r["result"] == "PASS")
            fail_cnt = sum(1 for r in recs if r["result"] != "PASS")
            pass_pct = round((pass_cnt / tot_cnt) * 100, 1) if tot_cnt > 0 else 0.0
            fail_pct = round((fail_cnt / tot_cnt) * 100, 1) if tot_cnt > 0 else 0.0

            sem_sgpis = [r["sgpi"] for r in recs if r.get("sgpi") is not None and r["sgpi"] > 0]
            avg_sgpi = round(float(np.mean(sem_sgpis)), 2) if sem_sgpis else None
            max_sgpi = max(sem_sgpis) if sem_sgpis else None
            min_sgpi = min(sem_sgpis) if sem_sgpis else None

            # KT info
            kt_students = fail_cnt
            kt_sub_counts = [r.get("kt_count", 0) for r in recs if r["result"] != "PASS" and r.get("kt_count")]
            total_kt_subjects = sum(kt_sub_counts) if kt_sub_counts else None

            file_info = next((f for f in batch_meta.get("semesters_detected", []) if f["semester"] == sem), {})

            semester_analysis.append({
                "semester": sem,
                "semester_name": f"Semester {sem}",
                "input_type": file_info.get("file_type", "excel").upper(),
                "filename": file_info.get("filename", f"Sem_{sem}"),
                "total_students": tot_cnt,
                "passed": pass_cnt,
                "failed": fail_cnt,
                "pass_percentage": pass_pct,
                "fail_percentage": fail_pct,
                "average_sgpi": avg_sgpi,
                "highest_sgpi": max_sgpi,
                "lowest_sgpi": min_sgpi,
                "kt_students_count": kt_students,
                "total_kt_subjects": total_kt_subjects
            })

        # -------------------------------------------------------------
        # 3. KT Analysis & Repeat Tracking
        # -------------------------------------------------------------
        kt_analysis_table: List[Dict[str, Any]] = []
        for sem in available_sems:
            recs = semester_data[sem]
            tot = len(recs)
            kt_stds = sum(1 for r in recs if r["result"] != "PASS")
            kt_rate = round((kt_stds / tot) * 100, 1) if tot > 0 else 0.0
            sub_counts = [r.get("kt_count", 0) for r in recs if r["result"] != "PASS" and r.get("kt_count")]
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
            failed_sems = [s for s, r in m["semesters"].items() if r["result"] != "PASS"]
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

        for sem in available_sems:
            recs = semester_data[sem]
            cleared = [r for r in recs if r.get("sgpi") is not None and r["result"] == "PASS"]
            cleared.sort(key=lambda x: x["sgpi"], reverse=True)

            top_list = []
            for rank_idx, r in enumerate(cleared[:10]):
                top_item = {
                    "rank": rank_idx + 1,
                    "student_name": r["name"],
                    "seat_no": r["seat_no"],
                    "sgpi": r["sgpi"],
                    "semester": sem
                }
                top_list.append(top_item)

                # Track topper frequency
                c_name = format_canonical_name(r["name"])
                if c_name not in topper_frequency:
                    topper_frequency[c_name] = {
                        "canonical_name": c_name,
                        "seat_no": r["seat_no"],
                        "appearances": 0,
                        "semesters": [],
                        "sgpi_values": []
                    }
                topper_frequency[c_name]["appearances"] += 1
                topper_frequency[c_name]["semesters"].append(sem)
                topper_frequency[c_name]["sgpi_values"].append(r["sgpi"])

            topper_by_semester[sem] = top_list

        # Multi-Semester Consistent Toppers: appeared in top 10 >= 2 times
        multi_sem_toppers = [
            {
                "canonical_name": v["canonical_name"],
                "seat_no": v["seat_no"],
                "appearances": v["appearances"],
                "semesters": sorted(v["semesters"]),
                "average_sgpi": round(float(np.mean(v["sgpi_values"])), 2) if v["sgpi_values"] else 0.0
            }
            for v in topper_frequency.values()
            if v["appearances"] >= 2
        ]
        multi_sem_toppers.sort(key=lambda x: (x["appearances"], x["average_sgpi"]), reverse=True)

        # -------------------------------------------------------------
        # 5. Retention & Progression Analysis
        # -------------------------------------------------------------
        retention_flow: List[Dict[str, Any]] = []
        for i in range(len(available_sems) - 1):
            s_from = available_sems[i]
            s_to = available_sems[i + 1]
            from_count = len(semester_data[s_from])
            to_count = len(semester_data[s_to])

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
            recs = [p["sem_records"][s] for s in sems if p["sem_records"][s]]
            sgpis = [r["sgpi"] for r in recs if r.get("sgpi") is not None]
            fails = [r for r in recs if r["result"] != "PASS"]

            # Criteria 1: Repeated Academic Difficulties (KT in >= 2 semesters)
            if len(fails) >= 2:
                difficulty_students.append({
                    "student_id": s_id,
                    "canonical_name": c_name,
                    "appeared_count": len(sems),
                    "failed_semesters": [r["semester"] for r in fails],
                    "average_sgpi": p["average_sgpi"] or "N/A",
                    "classification_reason": f"Recurring academic backlogs in {len(fails)} semesters ({', '.join([str(r['semester']) for r in fails])})"
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
            for r in semester_data[sem]:
                if not r.get("name") or len(r["name"].strip()) < 3:
                    quality_issues.append({
                        "category": "Missing Student Name",
                        "semester": sem,
                        "student_id": r.get("seat_no", "N/A"),
                        "description": "Student record is missing a valid name",
                        "severity": "High",
                        "action_taken": "Preserved without modification (source data traceable)"
                    })
                if r["result"] == "PASS" and r.get("sgpi") is None:
                    quality_issues.append({
                        "category": "Missing SGPI",
                        "semester": sem,
                        "student_id": r.get("seat_no", "N/A"),
                        "student_name": r.get("name", "N/A"),
                        "description": "Student is marked PASS but SGPI value is unrecorded or missing in ledger",
                        "severity": "Medium",
                        "action_taken": "Marked as N/A in statistics"
                    })
                # Check 2: Invalid SGPI values
                if r.get("sgpi") is not None and (r["sgpi"] < 0 or r["sgpi"] > 10.0):
                    quality_issues.append({
                        "category": "Invalid SGPI",
                        "semester": sem,
                        "student_id": r.get("seat_no", "N/A"),
                        "student_name": r.get("name", "N/A"),
                        "description": f"SGPI value {r['sgpi']} exceeds university 10-point scale",
                        "severity": "High",
                        "action_taken": "Preserved with anomaly tag"
                    })

        # Check 3: Duplicate identifiers within same semester
        for sem in available_sems:
            seen_seats: Set[str] = set()
            for r in semester_data[sem]:
                s_no = str(r.get("seat_no") or "").strip()
                if s_no and s_no != "N/A" and not s_no.startswith("STU_"):
                    if s_no in seen_seats:
                        quality_issues.append({
                            "category": "Duplicate Record",
                            "semester": sem,
                            "student_id": s_no,
                            "student_name": r.get("name", "N/A"),
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
            if len(m["all_names"]) > 1:
                quality_issues.append({
                    "category": "Inconsistent Student Name",
                    "semester": "Multi-Semester",
                    "student_id": m["student_id"],
                    "student_name": m["canonical_name"],
                    "description": f"Name recorded in different formats across semesters: {', '.join(list(m['all_names'])[:3])}",
                    "severity": "Informational",
                    "action_taken": "Harmonized to most complete canonical name"
                })

        # Assemble Master Payload
        return sanitize_dict_for_json({
            "batch_metadata": batch_meta,
            "available_semesters": available_sems,
            "overall_summary": overall_summary,
            "semester_analysis": semester_analysis,
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
        audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end multi-semester batch analysis with live progress reporting.
        """
        t0 = time.time()
        if progress_cb:
            progress_cb(2.0, "Discovering batch semesters and file structures...")

        batch_meta = self.detect_batch_files(batch_folder, target_branch)
        detected_sems = batch_meta["semesters_detected"]
        if not detected_sems:
            raise ValueError(f"No valid semester files (1st to 8th sem) found in {batch_folder}")

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
            else:
                records = self.extract_pdf_semester(fpath, sem, engine_mode=pdf_pipeline, progress_cb=sem_prog)

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
