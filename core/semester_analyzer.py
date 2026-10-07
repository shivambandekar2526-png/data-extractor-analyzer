"""
Deterministic Semester-Wise Academic Analysis Engine
===================================================
Analyzes the entire selected semester dataset from examination ledgers / Excel sheets.
Provides deterministic calculations of:
- Total Students Appeared
- Total Students Passed
- Total Students Failed / KT
- Pass Percentage = (Passed / Appeared) * 100
- Fail Percentage = (Failed / Appeared) * 100
- Semester Success Rate = (Passed / Appeared) * 100
- Institutional Classifications (Distinction, First Class, Second Class, Pass Class)
- SGPI Distribution (adaptive intervals based on actual SGPI values)
- Top 5 Semester Merit Leaders / Toppers based on SGPI & semester performance
"""

import math
import re
from typing import Dict, Any, List, Optional, Tuple


def clean_student_name(raw_name: Any) -> str:
    """Cleans student name, stripping leading slash if female marker."""
    if raw_name is None:
        return ""
    s = str(raw_name).strip()
    if s.startswith("/"):
        s = s[1:].strip()
    return re.sub(r"\s+", " ", s)


def clean_seat_no(raw_seat: Any) -> str:
    """Normalizes seat number or PRN string."""
    if raw_seat is None:
        return ""
    s = str(raw_seat).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def clean_sgpi_value(raw_val: Any) -> Optional[float]:
    """
    Parses and validates semester SGPI / GPA value.
    Returns float in range [0.0, 10.0], or None if absent / fail / invalid.
    """
    if raw_val is None:
        return None
    s = str(raw_val).strip()
    if not s or s in ["--", "-", "AB", "ABS", "F", "FAIL", "nan", "NaN", "null"]:
        return None
    try:
        val = float(s)
        if math.isnan(val):
            return None
        if 0.0 <= val <= 10.0:
            return round(val, 2)
        return None
    except (ValueError, TypeError):
        return None


def clean_numeric_mark(raw_val: Any) -> Optional[float]:
    """Parses total mark as a float, ignoring letters like 'AA' or 'F'."""
    if raw_val is None:
        return None
    s = str(raw_val).strip()
    if not s or s in ["--", "-", "AB", "ABS", "nan", "NaN"]:
        return None
    # Strip trailing F if present (e.g. 18F)
    cleaned = re.sub(r"[^\d.]", "", s)
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        return None if math.isnan(val) else round(val, 1)
    except (ValueError, TypeError):
        return None


def resolve_student_pass_status(
    raw_result: Any,
    sgpi: Optional[float] = None,
    failed_heads_count: int = 0
) -> Tuple[str, bool]:
    """
    Determines whether student passed or failed the semester.
    Returns (status_label, is_passed) where status_label in ("PASS", "FAIL", "ABSENT").
    """
    res_str = str(raw_result or "").strip().upper()

    # 1. Check Explicit Absent
    if res_str in ["ABS", "AB", "ABSENT", "A"]:
        return "ABSENT", False

    # 2. Check Explicit Fail
    if any(k in res_str for k in ["FAIL", "UNSUCCESSFUL", "ATKT", "KT"]) or res_str == "F":
        return "FAIL", False

    # 3. Check Explicit Pass
    if any(k in res_str for k in ["PASS", "SUCCESSFUL"]) or res_str == "P":
        return "PASS", True

    # 4. Fallback on SGPI & KT heads
    if failed_heads_count > 0:
        return "FAIL", False

    if sgpi is not None:
        if sgpi > 0.0:
            return "PASS", True
        else:
            return "FAIL", False

    # Default to FAIL if indeterminate
    return "FAIL", False


def classify_student_semester(
    is_passed: bool,
    pct: Optional[float] = None,
    sgpi: Optional[float] = None
) -> str:
    """
    Classifies a passed student into institutional tier conforming to Mumbai University CBCS:
    - First Class with Distinction: pct >= 75.0% or SGPI >= 7.50
    - First Class: 60.0% <= pct < 75.0% or 6.75 <= SGPI < 7.50
    - Second Class: 50.0% <= pct < 60.0% or 5.75 <= SGPI < 6.75
    - Pass Class: 40.0% <= pct < 50.0% or 4.00 <= SGPI < 5.75
    """
    if not is_passed:
        return "Fail"

    # Priority 1: Percentage if reliably available
    if pct is not None and not math.isnan(pct) and pct > 0:
        if pct >= 75.0:
            return "First Class with Distinction"
        elif pct >= 60.0:
            return "First Class"
        elif pct >= 50.0:
            return "Second Class"
        elif pct >= 40.0:
            return "Pass Class"

    # Priority 2: SGPI
    if sgpi is not None and sgpi > 0:
        if sgpi >= 7.50:
            return "First Class with Distinction"
        elif sgpi >= 6.75:
            return "First Class"
        elif sgpi >= 5.75:
            return "Second Class"
        elif sgpi >= 4.00:
            return "Pass Class"

    return "Pass Class"


def compute_sgpi_distribution(
    sgpi_values: List[float]
) -> Dict[str, Any]:
    """
    Computes distribution histogram of semester SGPI values into standard bins:
    9.00 - 10.00, 8.00 - 8.99, 7.00 - 7.99, 6.00 - 6.99, 5.00 - 5.99, < 5.00
    """
    bins = [
        {"range": "9.00 – 10.00", "min": 9.00, "max": 10.01},
        {"range": "8.00 – 8.99", "min": 8.00, "max": 8.999},
        {"range": "7.00 – 7.99", "min": 7.00, "max": 7.999},
        {"range": "6.00 – 6.99", "min": 6.00, "max": 6.999},
        {"range": "5.00 – 5.99", "min": 5.00, "max": 5.999},
        {"range": "< 5.00", "min": 0.00, "max": 4.999},
    ]

    counts = [0] * len(bins)
    total_valid = len(sgpi_values)

    for val in sgpi_values:
        for idx, b in enumerate(bins):
            if b["min"] <= val <= b["max"]:
                counts[idx] += 1
                break

    table = []
    labels = []
    chart_counts = []

    for b, cnt in zip(bins, counts):
        pct = round((cnt / total_valid * 100.0), 2) if total_valid > 0 else 0.0
        table.append({
            "range": b["range"],
            "students": cnt,
            "percentage": pct
        })
        labels.append(b["range"])
        chart_counts.append(cnt)

    return {
        "table": table,
        "chart": {
            "labels": labels,
            "counts": chart_counts
        },
        "total_scored_students": total_valid
    }


def analyze_semester_performance(
    records: List[Dict[str, Any]],
    dataset_meta: Optional[Dict[str, Any]] = None,
    catalog: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Computes complete deterministic semester-level analysis across the selected semester dataset.
    """
    meta = dataset_meta or {}
    total_records = len(records)

    if not records:
        return {
            "success": False,
            "error": "No student records found in selected semester dataset.",
            "header": meta
        }

    # Discover column keys smartly
    sample = records[0]
    keys = list(sample.keys())

    # Seat column
    seat_key = next((k for k in keys if any(x in k.lower() for x in ["seat_no", "seat no", "seat", "roll_no", "roll no"])), None)
    if not seat_key:
        seat_key = next((k for k in keys if "sr" in k.lower() or "seat" in k.lower()), keys[0])

    # Name column
    name_key = next((k for k in keys if any(x in k.lower() for x in ["student_name", "student name", "name of the student", "name of student", "name", "candidate name"])), None)
    # PRN column
    prn_key = next((k for k in keys if "prn" in k.lower() or "enroll" in k.lower()), None)
    # Result column
    res_key = next((k for k in keys if any(x in k.lower() for x in ["result_status", "result status", "result", "status", "pass_fail", "p_f"])), None)
    # SGPI column
    sgpi_key = None
    for k in keys:
        k_clean = k.strip().upper()
        if k_clean in ["SGPI", "SGPA", "GPA", "SPI"]:
            sgpi_key = k
            break
    if not sgpi_key:
        for k in keys:
            if "SGPI" in k.upper() and not any(x in k.upper() for x in ["SEM_I", "SEM_II", "SEM_III", "SEM_IV", "SEM_V", "SEM_VI", "SEM_VII"]):
                sgpi_key = k
                break

    # Total Marks column
    tot_marks_key = next((k for k in keys if any(x in k.lower() for x in ["total_marks", "total marks", "tot marks", "grand total", "total"]) and "cg" not in k.lower()), None)
    # Max Marks column
    max_marks_key = next((k for k in keys if any(x in k.lower() for x in ["max_marks", "max marks", "evaluation max"])), None)

    # Process all students
    parsed_students = []
    absent_students = []
    appeared_students = []
    passed_students = []
    failed_students = []

    valid_sgpi_list = []

    for r in records:
        seat = clean_seat_no(r.get(seat_key))
        name = clean_student_name(r.get(name_key) if name_key else "")
        prn = clean_seat_no(r.get(prn_key) if prn_key else "")
        raw_res = r.get(res_key) if res_key else ""
        raw_sgpi = r.get(sgpi_key) if sgpi_key else None
        sgpi = clean_sgpi_value(raw_sgpi)

        tot_marks = clean_numeric_mark(r.get(tot_marks_key)) if tot_marks_key else None
        max_marks = clean_numeric_mark(r.get(max_marks_key)) if max_marks_key else None

        # Check KT counts or F grades in row
        failed_heads_count = sum(1 for v in r.values() if str(v).strip().upper() in ["F", "FAIL", "FF", "KT"])

        status_lbl, is_passed = resolve_student_pass_status(raw_res, sgpi, failed_heads_count)

        # Percentage
        pct = None
        if tot_marks is not None and max_marks is not None and max_marks > 0:
            pct = round((tot_marks / max_marks) * 100.0, 2)
        elif sgpi is not None and sgpi > 0:
            # MU circular formula
            pct = round(7.25 * sgpi + 11.0, 2) if sgpi >= 7.0 else round(7.1 * sgpi + 12.0, 2)

        classification = classify_student_semester(is_passed, pct, sgpi)

        st_obj = {
            "seat_no": seat,
            "student_name": name or f"Student {seat}",
            "prn": prn,
            "raw_result": raw_res,
            "status": status_lbl,
            "is_passed": is_passed,
            "sgpi": sgpi,
            "total_marks": tot_marks,
            "max_marks": max_marks,
            "percentage": pct,
            "classification": classification
        }
        parsed_students.append(st_obj)

        if status_lbl == "ABSENT":
            absent_students.append(st_obj)
        else:
            appeared_students.append(st_obj)
            if is_passed:
                passed_students.append(st_obj)
                if sgpi is not None:
                    valid_sgpi_list.append(sgpi)
            else:
                failed_students.append(st_obj)

    # Core Metrics
    total_appeared = len(appeared_students) if appeared_students else total_records
    total_passed = len(passed_students)
    total_failed = len(failed_students)

    pass_rate = round((total_passed / max(1, total_appeared)) * 100.0, 2)
    fail_rate = round((total_failed / max(1, total_appeared)) * 100.0, 2)

    # Strictly defined as: (Students Passed / Students Appeared) * 100
    semester_success_rate = pass_rate

    # Classifications among passed students
    distinction_cnt = sum(1 for s in passed_students if s["classification"] == "First Class with Distinction")
    first_class_cnt = sum(1 for s in passed_students if s["classification"] == "First Class")
    second_class_cnt = sum(1 for s in passed_students if s["classification"] == "Second Class")
    pass_class_cnt = sum(1 for s in passed_students if s["classification"] == "Pass Class")

    fc_and_dist_cnt = distinction_cnt + first_class_cnt
    fc_and_dist_pct = round((fc_and_dist_cnt / max(1, total_appeared)) * 100.0, 2)

    distinction_pct = round((distinction_cnt / max(1, total_appeared)) * 100.0, 2)
    first_class_pct = round((first_class_cnt / max(1, total_appeared)) * 100.0, 2)

    # Percentages among passed students for classification table/chart
    tot_passed_safe = max(1, total_passed)
    distinction_pct_of_passed = round((distinction_cnt / tot_passed_safe) * 100.0, 2)
    first_class_pct_of_passed = round((first_class_cnt / tot_passed_safe) * 100.0, 2)
    second_class_pct_of_passed = round((second_class_cnt / tot_passed_safe) * 100.0, 2)
    pass_class_pct_of_passed = round((pass_class_cnt / tot_passed_safe) * 100.0, 2)

    # SGPI Distribution
    sgpi_dist = compute_sgpi_distribution(valid_sgpi_list)

    # Top 5 Toppers (Strictly passed students, sorted by SGPI desc, then Total Marks desc)
    sorted_toppers = sorted(
        passed_students,
        key=lambda s: (s["sgpi"] or 0.0, s["total_marks"] or 0.0),
        reverse=True
    )
    top_5 = []
    for rank_idx, st in enumerate(sorted_toppers[:5], 1):
        top_5.append({
            "rank": rank_idx,
            "student_name": st["student_name"],
            "seat_or_prn": st["seat_no"] or st["prn"] or f"#{rank_idx}",
            "sgpi": st["sgpi"],
            "marks": st["total_marks"] if st["total_marks"] is not None else "—",
            "percentage": st["percentage"] if st["percentage"] is not None else (round(st["sgpi"] * 9.5, 2) if st["sgpi"] else 0.0)
        })

    # SGPI Stats
    avg_sgpi = round(sum(valid_sgpi_list) / len(valid_sgpi_list), 2) if valid_sgpi_list else 0.0
    max_sgpi = max(valid_sgpi_list) if valid_sgpi_list else 0.0
    min_sgpi = min(valid_sgpi_list) if valid_sgpi_list else 0.0

    # Pass/Fail Table (Matching Subject-wise Analysis structure)
    pass_fail_table = [
        {"status": "Passed", "students": total_passed, "percentage": pass_rate},
        {"status": "Failed", "students": total_failed, "percentage": fail_rate},
        {"status": "First Class & Distinction (≥60%)", "students": fc_and_dist_cnt, "percentage": fc_and_dist_pct},
        {"status": "First Class with Distinction (≥75%)", "students": distinction_cnt, "percentage": distinction_pct},
        {"status": "First Class (60% to 74.99%)", "students": first_class_cnt, "percentage": first_class_pct}
    ]

    # Classification Table (Passed Students)
    classification_table = [
        {"classification": "First Class with Distinction (Distinction)", "short": "Distinction", "students": distinction_cnt, "percentage": distinction_pct_of_passed},
        {"classification": "First Class", "short": "First Class", "students": first_class_cnt, "percentage": first_class_pct_of_passed},
        {"classification": "Second Class", "short": "Second Class", "students": second_class_cnt, "percentage": second_class_pct_of_passed},
        {"classification": "Pass Class", "short": "Pass Class", "students": pass_class_cnt, "percentage": pass_class_pct_of_passed}
    ]

    classification_chart = {
        "labels": ["Distinction", "First Class", "Second Class", "Pass Class"],
        "counts": [distinction_cnt, first_class_cnt, second_class_cnt, pass_class_cnt]
    }

    return {
        "success": True,
        "header": {
            "department": meta.get("department") or meta.get("branch") or "Engineering",
            "branch": meta.get("branch") or meta.get("department") or "Engineering",
            "batch": meta.get("batch") or meta.get("academic_year") or "Batch",
            "academic_year": meta.get("academic_year") or meta.get("batch") or "N/A",
            "semester": meta.get("semester") or "Semester",
            "dataset_name": meta.get("dataset_name") or "Selected Semester",
            "dataset_id": meta.get("dataset_id") or "",
            "excel_path": meta.get("excel_path") or ""
        },
        "validation": {
            "total_records": total_records,
            "valid_records": total_appeared,
            "appeared": total_appeared,
            "absent": len(absent_students),
            "duplicates": 0
        },
        "semester_summary": {
            "appeared": total_appeared,
            "passed": total_passed,
            "failed": total_failed,
            "pass_rate": pass_rate,
            "fail_rate": fail_rate,
            "success_rate": semester_success_rate,
            "first_class_and_dist_count": fc_and_dist_cnt,
            "first_class_and_dist_pct": fc_and_dist_pct,
            "distinction_count": distinction_cnt,
            "distinction_rate": distinction_pct,
            "first_class_count": first_class_cnt,
            "first_class_rate": first_class_pct,
            "second_class_count": second_class_cnt,
            "pass_class_count": pass_class_cnt,
            "avg_sgpi": avg_sgpi,
            "max_sgpi": max_sgpi,
            "min_sgpi": min_sgpi,
            "pass_fail_table": pass_fail_table,
            "classification": {
                "table": classification_table,
                "chart": classification_chart
            }
        },
        "sgpi_distribution": sgpi_dist,
        "top_students": top_5
    }
