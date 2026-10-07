"""
Academic Classifier & Subject-Wise Analysis Engine
==================================================
Deterministic statistical analysis for subject-level examination results.
Provides data cleaning, dynamic subject extraction, pass/fail metrics,
institutional classifications (Distinction, First Class, Second Class, Pass Class),
adaptive marks distributions, Term Work analysis, Oral/Practical analysis,
and Top 5 student ranking.
"""

import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


class AcademicClassificationRules:
    """
    Configurable grading and classification thresholds conforming to
    university examination standards (e.g. Mumbai University / CBCS / NEP 2020).
    """

    DEFAULT_CLASSIFICATIONS = [
        {"name": "First Class with Distinction", "min_pct": 75.0, "max_pct": 100.0, "short": "Distinction"},
        {"name": "First Class", "min_pct": 60.0, "max_pct": 74.99, "short": "First Class"},
        {"name": "Second Class", "min_pct": 50.0, "max_pct": 59.99, "short": "Second Class"},
        {"name": "Pass Class", "min_pct": 40.0, "max_pct": 49.99, "short": "Pass Class"},
    ]

    GRADE_TO_CLASSIFICATION = {
        "O": "First Class with Distinction",
        "A+": "First Class with Distinction",
        "A": "First Class",
        "B+": "First Class",
        "B": "Second Class",
        "C": "Second Class",
        "D": "Pass Class",
        "E": "Pass Class",
        "P": "Pass Class",
        "F": "Fail",
    }

    @classmethod
    def classify_percentage(cls, pct: float, pass_threshold_pct: float = 40.0) -> str:
        """Determines academic classification based on percentage."""
        if pct is None or math.isnan(pct):
            return "Unknown"
        if pct >= 75.0:
            return "First Class with Distinction"
        elif pct >= 60.0:
            return "First Class"
        elif pct >= 50.0:
            return "Second Class"
        elif pct >= pass_threshold_pct:
            return "Pass Class"
        return "Fail"

    @classmethod
    def classify_grade(cls, grade_str: str) -> Optional[str]:
        """Maps letter grade to institutional classification."""
        if not grade_str:
            return None
        g = str(grade_str).strip().upper()
        return cls.GRADE_TO_CLASSIFICATION.get(g)


def clean_mark_entry(raw_val: Any) -> Tuple[str, Optional[float], bool]:
    """
    Validates and cleans a mark entry.
    Returns:
        status: 'VALID', 'ABSENT', 'MISSING', 'INVALID', 'NOT_APPLICABLE'
        numeric_val: float value if valid, else None
        is_fail_flag: True if mark has fail indicators (e.g. suffix 'F')
    """
    if raw_val is None or pd.isna(raw_val):
        return "MISSING", None, False

    s = str(raw_val).strip()
    if not s or s.upper() in ("--", "-", "NULL", "NONE", "NAN", ""):
        return "MISSING", None, False

    u = s.upper()
    if u in ("ABS", "AB", "A", "ABSENT"):
        return "ABSENT", None, False

    if u in ("N/A", "NA", "NOT APPLICABLE", "NE", "NOT ELIGIBLE"):
        return "NOT_APPLICABLE", None, False

    # Check for fail suffix (e.g. 18F, 22F)
    has_f_suffix = bool(re.search(r"[0-9]\s*F\b", u)) or u.endswith("F")

    # Match numeric portion
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", s)
    if m:
        try:
            val = float(m.group(1))
            return "VALID", val, has_f_suffix
        except ValueError:
            return "INVALID", None, False

    return "INVALID", None, False


def calculate_adaptive_intervals(max_marks: float) -> List[Tuple[float, float, str]]:
    """
    Determines adaptive intervals based on the maximum marks.
    Avoids hardcoding intervals.
    """
    max_m = float(max_marks) if max_marks and max_marks > 0 else 100.0

    if max_m <= 25:
        step = 5.0
    elif max_m <= 50:
        step = 10.0
    elif max_m <= 80:
        step = 10.0
    elif max_m <= 100:
        step = 10.0
    elif max_m <= 150:
        step = 20.0
    else:
        step = 25.0

    intervals = []
    current = 0.0
    while current < max_m:
        next_step = min(current + step, max_m)
        label = f"{int(current)}–{int(next_step)}" if (current.is_integer() and next_step.is_integer()) else f"{current:.1f}–{next_step:.1f}"
        intervals.append((current, next_step, label))
        current = next_step

    return intervals


def compute_marks_distribution(
    valid_marks: List[float],
    max_marks: float,
    appeared_count: int
) -> Dict[str, Any]:
    """Generates an adaptive marks distribution table and chart-ready structure."""
    intervals = calculate_adaptive_intervals(max_marks)
    total_app = max(1, appeared_count)

    table = []
    labels = []
    data_counts = []
    data_pcts = []

    for i, (start, end, label) in enumerate(intervals):
        if i == len(intervals) - 1:
            # Include right boundary in last interval
            count = sum(1 for m in valid_marks if start <= m <= end)
        else:
            count = sum(1 for m in valid_marks if start <= m < end)

        pct = round((count / total_app) * 100.0, 2)
        table.append({
            "range": label,
            "students": count,
            "percentage": pct
        })
        labels.append(label)
        data_counts.append(count)
        data_pcts.append(pct)

    return {
        "max_marks": max_marks,
        "table": table,
        "chart": {
            "labels": labels,
            "counts": data_counts,
            "percentages": data_pcts
        }
    }


def find_column_for_head(
    columns: List[str],
    subject_code: str,
    head_type: str
) -> Optional[str]:
    """
    Finds the exact column corresponding to subject code and head type
    (e.g. 'CSC801', 'TH' -> 'CSC801_TH' or 'CSC801_TH_OR').
    """
    code_upper = subject_code.upper()
    head_upper = head_type.upper()

    # 1. Exact match <CODE>_<HEAD>
    exact = f"{code_upper}_{head_upper}"
    for c in columns:
        if c.upper() == exact:
            return c

    # 2. Match with variant suffixes
    patterns = []
    if head_upper == "TH":
        patterns = [f"{code_upper}_TH", f"{code_upper}_THEORY", f"{code_upper}_TH_OR", f"{code_upper}_ESE"]
    elif head_upper == "IA":
        patterns = [f"{code_upper}_IA", f"{code_upper}_INTERNAL", f"{code_upper}_TEST", f"{code_upper}_IA_TW"]
    elif head_upper == "TW":
        patterns = [f"{code_upper}_TW", f"{code_upper}_TERMWORK", f"{code_upper}_IA_TW"]
    elif head_upper in ("PR", "ORPR", "OR_PR", "OR"):
        patterns = [
            f"{code_upper}_PR", f"{code_upper}_ORPR", f"{code_upper}_OR_PR",
            f"{code_upper}_OR", f"{code_upper}_PRACTICAL", f"{code_upper}_VIVA"
        ]
    elif head_upper == "TOT":
        patterns = [f"{code_upper}_TOT", f"{code_upper}_TOTAL"]

    for pat in patterns:
        for c in columns:
            if c.upper() == pat:
                return c

    # 3. Substring check
    for c in columns:
        c_u = c.upper()
        if c_u.startswith(code_upper) and f"_{head_upper}" in c_u:
            return c

    return None


NON_COURSE_CODES = {
    "SEAT", "PRN", "SGPI", "CGPI", "RESULT", "TOTAL", "ABC", "STUDENT",
    "CANDIDATE", "MOTHER", "GENDER", "CREDITS", "REMARK", "SR", "NO", "SEM", "SEMESTER"
}


def extract_subject_definitions(
    df: pd.DataFrame,
    catalog: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Dynamically extracts all valid subject definitions from dataframe and catalog.
    Never uses a hardcoded subject list.
    """
    subjects_dict: Dict[str, Dict[str, Any]] = {}
    catalog_courses = (catalog or {}).get("courses", [])

    # Index catalog courses by upper code
    cat_by_code = {}
    for c in catalog_courses:
        code = str(c.get("code") or c.get("course_code") or "").strip().upper()
        if code:
            cat_by_code[code] = c

    # 1. Inspect dataframe columns
    for col in df.columns:
        col_str = str(col).strip()
        if "_" not in col_str:
            continue

        parts = col_str.split("_")
        code = parts[0].strip().upper()
        head = "_".join(parts[1:]).strip().upper()

        if not code or code in NON_COURSE_CODES:
            continue

        if code not in subjects_dict:
            name = ""
            # Check if there is a _Subject column
            subj_col = f"{code}_Subject"
            for c in df.columns:
                if c.upper() == subj_col.upper():
                    valid_names = df[c].dropna().astype(str).str.strip()
                    valid_names = [v for v in valid_names if v and v not in ("--", "-", "None")]
                    if valid_names:
                        name = valid_names[0]
                    break

            cat_info = cat_by_code.get(code, {})
            if not name:
                name = cat_info.get("name") or cat_info.get("official_course_name") or code

            max_m = cat_info.get("max_marks", {})
            min_m = cat_info.get("min_marks", {})

            subjects_dict[code] = {
                "code": code,
                "name": name,
                "display_name": f"{code} - {name}" if name and name != code else code,
                "heads": set(),
                "columns": {},
                "max_marks": dict(max_m),
                "min_marks": dict(min_m),
                "credits": float(cat_info.get("credits") or 0.0)
            }

        subjects_dict[code]["heads"].add(head)
        subjects_dict[code]["columns"][head] = col_str

    # 2. Add any courses in catalog that might not have triggered above
    for code, c in cat_by_code.items():
        if code not in subjects_dict:
            name = c.get("name") or c.get("official_course_name") or code
            subjects_dict[code] = {
                "code": code,
                "name": name,
                "display_name": f"{code} - {name}" if name and name != code else code,
                "heads": set(c.get("heads", [])),
                "columns": {},
                "max_marks": dict(c.get("max_marks", {})),
                "min_marks": dict(c.get("min_marks", {})),
                "credits": float(c.get("credits") or 0.0)
            }
        else:
            # Supplement max/min marks from catalog
            for k, v in c.get("max_marks", {}).items():
                if k not in subjects_dict[code]["max_marks"]:
                    subjects_dict[code]["max_marks"][k] = v
            for k, v in c.get("min_marks", {}).items():
                if k not in subjects_dict[code]["min_marks"]:
                    subjects_dict[code]["min_marks"][k] = v

    # Finalize subjects list
    result = []
    cols_list = list(df.columns)
    for code, s in subjects_dict.items():
        heads_set = s["heads"]
        has_th = any(h in heads_set for h in ("TH", "THEORY", "TH_OR", "ESE")) or bool(find_column_for_head(cols_list, code, "TH"))
        has_tw = any(h in heads_set for h in ("TW", "TERMWORK", "IA_TW")) or bool(find_column_for_head(cols_list, code, "TW"))
        has_pr = any(h in heads_set for h in ("PR", "ORPR", "OR_PR", "OR", "PRACTICAL", "VIVA")) or bool(find_column_for_head(cols_list, code, "PR"))

        s["heads"] = sorted(list(heads_set))
        s["has_theory"] = has_th
        s["has_term_work"] = has_tw
        s["has_practical_oral"] = has_pr

        # Defaults for max marks if not specified
        if has_th and "TH" not in s["max_marks"]:
            s["max_marks"]["TH"] = 80.0
        if has_th and "TH" not in s["min_marks"]:
            s["min_marks"]["TH"] = 32.0
        if has_tw and "TW" not in s["max_marks"]:
            s["max_marks"]["TW"] = 25.0
        if has_tw and "TW" not in s["min_marks"]:
            s["min_marks"]["TW"] = 10.0
        if has_pr and "PR" not in s["max_marks"]:
            s["max_marks"]["PR"] = 25.0
        if has_pr and "PR" not in s["min_marks"]:
            s["min_marks"]["PR"] = 10.0
        if "TOT" not in s["max_marks"]:
            tot_max = (s["max_marks"].get("TH", 0) + s["max_marks"].get("IA", 20 if has_th else 0)
                       + (s["max_marks"].get("TW", 0) if not has_th else 0)
                       + (s["max_marks"].get("PR", 0) if not has_th else 0))
            s["max_marks"]["TOT"] = tot_max if tot_max > 0 else 100.0

        result.append(s)

    # Sort subjects by course code
    result.sort(key=lambda x: x["code"])
    return result


def analyze_subject_performance(
    records: List[Dict[str, Any]],
    catalog: Optional[Dict[str, Any]],
    subject_code: str,
    dataset_meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Computes complete deterministic academic statistics for a chosen subject.
    Never uses LLM for computation.
    """
    df = pd.DataFrame(records)
    if df.empty:
        return {
            "success": False,
            "error": "Dataset records are empty."
        }

    # Discover subject definition
    subjects = extract_subject_definitions(df, catalog)
    subject_info = next((s for s in subjects if s["code"].upper() == subject_code.upper()), None)

    if not subject_info:
        # Create minimal fallback definition
        subject_info = {
            "code": subject_code,
            "name": subject_code,
            "display_name": subject_code,
            "max_marks": {"TH": 80.0, "TOT": 100.0},
            "min_marks": {"TH": 32.0, "TOT": 40.0},
            "has_theory": True,
            "has_term_work": False,
            "has_practical_oral": False,
        }

    cols = list(df.columns)

    # Identify candidate identifiers
    seat_col = next((c for c in cols if c.upper() in ("SEAT_NO", "SEATNO", "SEAT", "ROLL_NO")), None)
    name_col = next((c for c in cols if c.upper() in ("STUDENT_NAME", "STUDENTNAME", "NAME")), None)
    prn_col = next((c for c in cols if c.upper() in ("PRN", "PRN_NO")), None)

    # ==========================================
    # 1. DATA VALIDATION & CLEANSING AUDIT
    # ==========================================
    total_records = len(df)
    seen_ids = set()
    duplicate_indices = set()

    for idx, row in df.iterrows():
        raw_prn = row.get(prn_col) if prn_col else None
        raw_seat = row.get(seat_col) if seat_col else None
        
        prn_str = str(raw_prn).strip() if (raw_prn is not None and not pd.isna(raw_prn)) else ""
        if prn_str.lower() in ("nan", "none", "null", ""):
            prn_str = ""

        seat_str = str(raw_seat).strip() if (raw_seat is not None and not pd.isna(raw_seat)) else ""
        if seat_str.lower() in ("nan", "none", "null", ""):
            seat_str = ""

        cand_id = prn_str or seat_str
        if cand_id and cand_id in seen_ids:
            duplicate_indices.add(idx)
        elif cand_id:
            seen_ids.add(cand_id)

    # Prune duplicates for deterministic analysis
    if duplicate_indices:
        df_clean = df.drop(index=list(duplicate_indices)).reset_index(drop=True)
    else:
        df_clean = df.copy()

    # Locate relevant head columns
    th_col = find_column_for_head(cols, subject_code, "TH")
    ia_col = find_column_for_head(cols, subject_code, "IA")
    tw_col = find_column_for_head(cols, subject_code, "TW")
    pr_col = find_column_for_head(cols, subject_code, "PR")
    tot_col = find_column_for_head(cols, subject_code, "TOT")
    grade_col = next((c for c in cols if c.upper() == f"{subject_code.upper()}_GRADE"), None)

    # Determine max/min marks
    th_max = float(subject_info.get("max_marks", {}).get("TH") or 80.0)
    th_min = float(subject_info.get("min_marks", {}).get("TH") or 32.0)
    tw_max = float(subject_info.get("max_marks", {}).get("TW") or 25.0)
    tw_min = float(subject_info.get("min_marks", {}).get("TW") or 10.0)
    pr_max = float(subject_info.get("max_marks", {}).get("PR") or 25.0)
    pr_min = float(subject_info.get("min_marks", {}).get("PR") or 10.0)
    tot_max = float(subject_info.get("max_marks", {}).get("TOT") or (th_max + 20.0))

    # ==========================================
    # 2. THEORY ANALYSIS
    # ==========================================
    theory_analysis: Dict[str, Any] = {"available": False}
    th_valid_marks: List[float] = []
    th_passed_marks: List[float] = []
    th_grades: List[str] = []

    th_appeared = 0
    th_passed = 0
    th_failed = 0
    th_absent = 0
    th_missing = 0
    th_invalid = 0

    if th_col and th_col in df_clean.columns:
        theory_analysis["available"] = True

        for idx, row in df_clean.iterrows():
            raw_th = row.get(th_col)
            status, val, fail_flag = clean_mark_entry(raw_th)
            g_val = str(row.get(grade_col) or "").strip().upper() if grade_col else ""

            if status == "ABSENT":
                th_absent += 1
            elif status == "MISSING":
                th_missing += 1
            elif status == "INVALID":
                th_invalid += 1
            elif status == "VALID":
                th_appeared += 1
                th_valid_marks.append(val)
                if g_val:
                    th_grades.append(g_val)

                # Passing criteria: mark >= th_min, no fail flag, and grade not 'F'
                is_pass = (val >= th_min) and not fail_flag and (g_val not in ("F", "FAIL"))
                if is_pass:
                    th_passed += 1
                    th_passed_marks.append(val)
                else:
                    th_failed += 1

        pass_rate = round((th_passed / max(1, th_appeared)) * 100.0, 2) if th_appeared > 0 else 0.0
        fail_rate = round((th_failed / max(1, th_appeared)) * 100.0, 2) if th_appeared > 0 else 0.0

        # Classification of PASSED students
        class_counts = {
            "First Class with Distinction": 0,
            "First Class": 0,
            "Second Class": 0,
            "Pass Class": 0
        }

        for mark in th_passed_marks:
            pct = (mark / max(1.0, th_max)) * 100.0
            cat = AcademicClassificationRules.classify_percentage(pct, pass_threshold_pct=(th_min / th_max) * 100.0)
            if cat in class_counts:
                class_counts[cat] += 1
            else:
                class_counts["Pass Class"] += 1

        tot_passed = max(1, th_passed)
        class_table = [
            {"classification": "First Class with Distinction (Distinction)", "short": "Distinction", "students": class_counts["First Class with Distinction"], "percentage": round((class_counts["First Class with Distinction"] / tot_passed) * 100.0, 2)},
            {"classification": "First Class", "short": "First Class", "students": class_counts["First Class"], "percentage": round((class_counts["First Class"] / tot_passed) * 100.0, 2)},
            {"classification": "Second Class", "short": "Second Class", "students": class_counts["Second Class"], "percentage": round((class_counts["Second Class"] / tot_passed) * 100.0, 2)},
            {"classification": "Pass Class", "short": "Pass Class", "students": class_counts["Pass Class"], "percentage": round((class_counts["Pass Class"] / tot_passed) * 100.0, 2)},
        ]

        marks_dist = compute_marks_distribution(th_valid_marks, th_max, th_appeared)

        theory_analysis.update({
            "column_used": th_col,
            "max_marks": th_max,
            "min_marks": th_min,
            "appeared": th_appeared,
            "passed": th_passed,
            "failed": th_failed,
            "absent": th_absent,
            "missing": th_missing,
            "invalid": th_invalid,
            "pass_rate": pass_rate,
            "fail_rate": fail_rate,
            "pass_fail_table": [
                {"status": "Passed", "students": th_passed, "percentage": pass_rate},
                {"status": "Failed", "students": th_failed, "percentage": fail_rate}
            ],
            "classification": {
                "table": class_table,
                "chart": {
                    "labels": [c["short"] for c in class_table],
                    "counts": [c["students"] for c in class_table],
                    "percentages": [c["percentage"] for c in class_table]
                }
            },
            "marks_distribution": marks_dist
        })
    else:
        theory_analysis["message"] = "Theory examination not applicable or unavailable for this course."

    # ==========================================
    # 3. TERM WORK ANALYSIS
    # ==========================================
    term_work_analysis: Dict[str, Any] = {"available": False}
    if tw_col and tw_col in df_clean.columns:
        tw_valid_marks = []
        tw_passed_marks = []
        tw_appeared = 0
        tw_passed = 0
        tw_failed = 0
        tw_absent = 0

        for _, row in df_clean.iterrows():
            status, val, fail_flag = clean_mark_entry(row.get(tw_col))
            if status == "ABSENT":
                tw_absent += 1
            elif status == "VALID":
                tw_appeared += 1
                tw_valid_marks.append(val)
                if val >= tw_min and not fail_flag:
                    tw_passed += 1
                    tw_passed_marks.append(val)
                else:
                    tw_failed += 1

        if tw_appeared > 0:
            term_work_analysis["available"] = True
            tw_pass_rate = round((tw_passed / max(1, tw_appeared)) * 100.0, 2)
            tw_fail_rate = round((tw_failed / max(1, tw_appeared)) * 100.0, 2)
            tw_dist = compute_marks_distribution(tw_valid_marks, tw_max, tw_appeared)

            # TW Classifications
            tw_class_counts = {
                "Distinction": 0,
                "First Class": 0,
                "Second Class": 0,
                "Pass Class": 0
            }
            for m in tw_passed_marks:
                pct = (m / max(1.0, tw_max)) * 100.0
                if pct >= 75.0:
                    tw_class_counts["Distinction"] += 1
                elif pct >= 60.0:
                    tw_class_counts["First Class"] += 1
                elif pct >= 50.0:
                    tw_class_counts["Second Class"] += 1
                else:
                    tw_class_counts["Pass Class"] += 1

            tot_tw_p = max(1, tw_passed)
            tw_class_table = [
                {"classification": "Distinction (≥75%)", "short": "Distinction", "students": tw_class_counts["Distinction"], "percentage": round((tw_class_counts["Distinction"] / tot_tw_p) * 100.0, 2)},
                {"classification": "First Class (60–74%)", "short": "First Class", "students": tw_class_counts["First Class"], "percentage": round((tw_class_counts["First Class"] / tot_tw_p) * 100.0, 2)},
                {"classification": "Second Class (50–59%)", "short": "Second Class", "students": tw_class_counts["Second Class"], "percentage": round((tw_class_counts["Second Class"] / tot_tw_p) * 100.0, 2)},
                {"classification": "Pass Class (≥40%)", "short": "Pass Class", "students": tw_class_counts["Pass Class"], "percentage": round((tw_class_counts["Pass Class"] / tot_tw_p) * 100.0, 2)},
            ]

            term_work_analysis.update({
                "column_used": tw_col,
                "max_marks": tw_max,
                "min_marks": tw_min,
                "appeared": tw_appeared,
                "passed": tw_passed,
                "failed": tw_failed,
                "absent": tw_absent,
                "pass_rate": tw_pass_rate,
                "fail_rate": tw_fail_rate,
                "pass_fail_table": [
                    {"status": "Passed", "students": tw_passed, "percentage": tw_pass_rate},
                    {"status": "Failed", "students": tw_failed, "percentage": tw_fail_rate}
                ],
                "classification": {
                    "table": tw_class_table,
                    "chart": {
                        "labels": [c["short"] for c in tw_class_table],
                        "counts": [c["students"] for c in tw_class_table]
                    }
                },
                "marks_distribution": tw_dist
            })
        else:
            term_work_analysis["message"] = "Term work data unavailable in selected dataset."
    else:
        term_work_analysis["message"] = "Term work data unavailable in selected dataset."

    # ==========================================
    # 4. ORAL / PRACTICAL ANALYSIS
    # ==========================================
    practical_oral_analysis: Dict[str, Any] = {"available": False}
    if pr_col and pr_col in df_clean.columns:
        pr_valid_marks = []
        pr_appeared = 0
        pr_passed = 0
        pr_failed = 0
        pr_absent = 0

        for _, row in df_clean.iterrows():
            status, val, fail_flag = clean_mark_entry(row.get(pr_col))
            if status == "ABSENT":
                pr_absent += 1
            elif status == "VALID":
                pr_appeared += 1
                pr_valid_marks.append(val)
                if val >= pr_min and not fail_flag:
                    pr_passed += 1
                else:
                    pr_failed += 1

        if pr_appeared > 0:
            practical_oral_analysis["available"] = True
            pr_pass_rate = round((pr_passed / max(1, pr_appeared)) * 100.0, 2)
            pr_fail_rate = round((pr_failed / max(1, pr_appeared)) * 100.0, 2)
            pr_dist = compute_marks_distribution(pr_valid_marks, pr_max, pr_appeared)

            practical_oral_analysis.update({
                "column_used": pr_col,
                "max_marks": pr_max,
                "min_marks": pr_min,
                "appeared": pr_appeared,
                "passed": pr_passed,
                "failed": pr_failed,
                "absent": pr_absent,
                "pass_rate": pr_pass_rate,
                "fail_rate": pr_fail_rate,
                "pass_fail_table": [
                    {"status": "Passed", "students": pr_passed, "percentage": pr_pass_rate},
                    {"status": "Failed", "students": pr_failed, "percentage": pr_fail_rate}
                ],
                "marks_distribution": pr_dist
            })
        else:
            practical_oral_analysis["message"] = "Practical/Oral data unavailable in selected dataset."
    else:
        practical_oral_analysis["message"] = "Practical/Oral data unavailable in selected dataset."

    # ==========================================
    # 5. TOP 5 STUDENTS RANKING
    # ==========================================
    # Rank using subject total marks (NOT SGPI)
    ranked_candidates = []
    for _, row in df_clean.iterrows():
        raw_name = row.get(name_col) if name_col else None
        raw_seat = row.get(seat_col) if seat_col else None
        raw_prn = row.get(prn_col) if prn_col else None

        s_name = str(raw_name).strip() if (raw_name is not None and not pd.isna(raw_name)) else ""
        s_seat = str(raw_seat).strip() if (raw_seat is not None and not pd.isna(raw_seat)) else ""
        s_prn = str(raw_prn).strip() if (raw_prn is not None and not pd.isna(raw_prn)) else ""

        if s_seat.lower() in ("nan", "none", "null", ""):
            s_seat = ""
        if s_prn.lower() in ("nan", "none", "null", ""):
            s_prn = ""

        # Subject Total: Use tot_col if available, else sum valid components
        total_score: Optional[float] = None
        if tot_col and tot_col in row and pd.notna(row.get(tot_col)):
            st, val, _ = clean_mark_entry(row.get(tot_col))
            if st == "VALID":
                total_score = val

        if total_score is None:
            # Fallback sum
            comp_vals = []
            for col_h in [th_col, ia_col, tw_col, pr_col]:
                if col_h and col_h in row and pd.notna(row.get(col_h)):
                    st, val, _ = clean_mark_entry(row.get(col_h))
                    if st == "VALID":
                        comp_vals.append(val)
            if comp_vals:
                total_score = sum(comp_vals)

        if total_score is not None:
            pct = round((total_score / max(1.0, tot_max)) * 100.0, 2)
            ranked_candidates.append({
                "student_name": s_name or f"Candidate {s_seat or s_prn or 'ID'}",
                "seat_no": s_seat,
                "prn": s_prn,
                "seat_or_prn": s_seat or s_prn or "N/A",
                "marks": total_score,
                "max_marks": tot_max,
                "percentage": pct
            })

    # Sort descending by marks
    ranked_candidates.sort(key=lambda x: x["marks"], reverse=True)

    # Assign ranks with ties handling
    top_5 = []
    current_rank = 1
    for i, c in enumerate(ranked_candidates[:5]):
        if i > 0 and c["marks"] < ranked_candidates[i - 1]["marks"]:
            current_rank = i + 1
        top_5.append({
            "rank": current_rank,
            **c
        })

    # Prepare Header Metadata
    d_meta = dataset_meta or {}
    branch_val = d_meta.get("branch") or d_meta.get("department") or "Engineering"
    acad_year = d_meta.get("academic_year") or d_meta.get("batch") or "Current Cohort"
    sem_val = d_meta.get("semester") or (catalog or {}).get("semester") or "N/A"
    dataset_name = d_meta.get("dataset_name") or d_meta.get("folder_name") or "Selected Gazette"

    header_meta = {
        "subject_name": subject_info.get("name") or subject_code,
        "subject_code": subject_code,
        "academic_year": acad_year,
        "branch": branch_val.replace("_", " "),
        "semester": str(sem_val).replace("Semester_", "Sem ").upper(),
        "dataset_name": dataset_name,
        "credits": subject_info.get("credits", 0.0),
        "total_max_marks": tot_max
    }

    validation_meta = {
        "total_records": total_records,
        "valid_records": len(df_clean),
        "duplicate_records": len(duplicate_indices),
        "theory_appeared": th_appeared,
        "theory_absent": th_absent,
        "theory_missing": th_missing,
        "theory_invalid": th_invalid
    }

    return {
        "success": True,
        "header": header_meta,
        "validation": validation_meta,
        "theory": theory_analysis,
        "term_work": term_work_analysis,
        "practical_oral": practical_oral_analysis,
        "top_students": top_5
    }
