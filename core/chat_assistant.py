"""
Ollama Conversational Data Assistant
====================================
Enables interactive, natural-language modifications and analytical Q&A on extracted student datasets.
Allows users to ask Ollama to filter, delete, update, sort, or analyze the current data in real time.
Equipped with an instantaneous mathematical analytics engine and rich dataset contextualization.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from .ollama_client import OllamaClient
    from .cloud_client import CloudClient
    from .query_planner import LocalQueryPlanner, df_to_markdown_table
except Exception:
    try:
        from data_extractor_AI.core.ollama_client import OllamaClient
        from data_extractor_AI.core.cloud_client import CloudClient
        from data_extractor_AI.core.query_planner import LocalQueryPlanner, df_to_markdown_table
    except Exception:
        from core.ollama_client import OllamaClient
        from core.cloud_client import CloudClient
        from core.query_planner import LocalQueryPlanner, df_to_markdown_table

CHAT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["delete_slice", "delete_where", "filter_keep", "update_where", "sort", "answer"]
        },
        "reply": {"type": "string"},
        "params": {
            "type": "object",
            "properties": {
                "start": {"type": "integer"},
                "count": {"type": "integer"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "values": {"type": "array", "items": {"type": "string"}},
                "match_key": {"type": "string"},
                "match_value": {"type": "string"},
                "match_prefix": {"type": "string"},
                "updates": {"type": "object"},
                "reverse": {"type": "boolean"},
                "numeric": {"type": "boolean"}
            }
        }
    },
    "required": ["action", "reply"]
}

SYSTEM_PROMPT = (
    "You are an expert academic data assistant analyzing and manipulating a student result dataset. "
    "Respond with valid JSON matching the schema.\n"
    "Supported actions:\n"
    "1. 'answer': When user asks ANY question about the dataset (individual student details, counts, toppers, averages, comparisons, explanations). "
    "   Provide a comprehensive, factual, professional answer in 'reply' using the COMPLETE student records and analytics provided in the session context.\n"
    "2. 'delete_slice': Remove N records from start/end. params: {start: 0, count: 2}\n"
    "3. 'delete_where': Remove matching records. params: {key: 'Result_Status', values: ['FAIL', 'F']}\n"
    "4. 'filter_keep': Keep only matching records. params: {key: 'Gender', value: 'F'}\n"
    "5. 'update_where': Update specific fields. params: {match_key: 'Seat_No', match_value: '1033205', updates: {'Candidate_Type': 'Diploma'}}\n"
    "   or by prefix: {match_key: 'PRN', match_prefix: '2022', updates: {'Candidate_Type': 'Diploma'}}\n"
    "6. 'sort': Sort by column. params: {key: 'SGPI', reverse: true, numeric: true}\n"
    "Always provide a helpful, polite explanation in the 'reply' field."
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        clean = re.sub(r"[^\d\.]", "", str(val or ""))
        return float(clean) if clean else default
    except Exception:
        return default


def _resolve_key(d: Dict[str, Any], target: str) -> str:
    """Find the exact matching key in dict, ignoring case, underscores and spaces."""
    if not d or target in d:
        return target
    clean_target = re.sub(r"[_\s\-]", "", str(target).lower())
    for k in d.keys():
        if re.sub(r"[_\s\-]", "", str(k).lower()) == clean_target:
            return k
    return target


def _get_field(row: Dict[str, Any], field_type: str) -> str:
    """Extract standard student fields regardless of column naming conventions."""
    if not row:
        return ""

    aliases = {
        "seat": ["seat_no", "seat no", "seat", "seatno", "roll_no", "roll no"],
        "name": ["student_name", "student name", "name", "candidate_name", "candidate name"],
        "prn": ["prn", "prn_no", "prn no", "registration_no"],
        "result": ["result_status", "result status", "result", "status", "remark", "remarks"],
        "type": ["candidate_type", "candidate type", "type", "admission_type"],
        "gender": ["gender", "sex"],
        "sgpi": ["sgpi", "gpa", "cgpa", "sgpa", "spi"],
        "marks": ["total_marks", "total marks", "total", "marks_obtained", "marks"],
        "credits": ["credits_earned", "credits earned", "credits", "total_credits"]
    }

    target_aliases = aliases.get(field_type.lower(), [field_type.lower()])
    for alias in target_aliases:
        k = _resolve_key(row, alias)
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            return str(row[k]).strip()

    return ""


def calculate_dataset_analytics(students: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute comprehensive statistical metrics across the entire cohort."""
    total = len(students)
    if total == 0:
        return {"total": 0}

    passed_count = 0
    failed_count = 0
    kt_null_gpa_count = 0

    regular_count = 0
    diploma_count = 0

    male_count = 0
    female_count = 0

    sgpi_list: List[Tuple[float, str, str, float]] = []  # (sgpi, seat, name, marks)
    marks_list: List[Tuple[float, str, str]] = []  # (marks, seat, name)
    failed_students: List[Dict[str, str]] = []

    # Grade buckets
    distinction_count = 0
    first_class_count = 0
    second_class_count = 0
    pass_class_count = 0

    for s in students:
        seat = _get_field(s, "seat") or "--"
        name = _get_field(s, "name") or "Unknown"
        res = _get_field(s, "result").upper()
        cand = _get_field(s, "type").upper()
        gen = _get_field(s, "gender").upper()
        raw_sgpi = _get_field(s, "sgpi")
        sgpi_val = _safe_float(raw_sgpi)
        marks_val = _safe_float(_get_field(s, "marks"))

        # Academic Rule: If GPA is null/empty/0, student has a KT (Keep Terms / failed semester)
        is_kt = False
        kt_reason = ""
        if not raw_sgpi or raw_sgpi in ("--", "-", "null", "None", "ABS", "N/A") or sgpi_val <= 0.0:
            is_kt = True
            kt_null_gpa_count += 1
            kt_reason = "Null / Withheld GPA (KT in examination)"
        elif any(w in res for w in ["FAIL", "F", "UNSUCCESSFUL", "ATKT", "KT", "ABS"]) or sgpi_val < 4.0:
            is_kt = True
            kt_reason = f"Result marked as {res or 'Fail'} (SGPI: {sgpi_val:.2f})"

        if is_kt:
            failed_count += 1
            failed_students.append({
                "seat": seat,
                "name": name,
                "sgpi": str(raw_sgpi or "Null/KT"),
                "result": res or "KT",
                "reason": kt_reason
            })
        else:
            passed_count += 1
            # Classifications
            if sgpi_val >= 7.5:
                distinction_count += 1
            elif sgpi_val >= 6.75:
                first_class_count += 1
            elif sgpi_val >= 6.0:
                second_class_count += 1
            else:
                pass_class_count += 1

        # Candidate Type
        if "DIPLOMA" in cand or "DSE" in cand or "LATERAL" in cand:
            diploma_count += 1
        else:
            regular_count += 1

        # Gender
        if gen.startswith("F") or "/" in name:
            female_count += 1
        else:
            male_count += 1

        # Numeric scores
        if sgpi_val > 0.0:
            sgpi_list.append((sgpi_val, seat, name, marks_val))
        if marks_val > 0.0:
            marks_list.append((marks_val, seat, name))

    sgpi_vals = [x[0] for x in sgpi_list]
    marks_vals = [x[0] for x in marks_list]

    # Full sorted lists across ALL students
    sgpi_list_desc = sorted(sgpi_list, key=lambda x: (x[0], x[3]), reverse=True)
    sgpi_list_asc = sorted(sgpi_list, key=lambda x: (x[0], x[3]))
    marks_list_desc = sorted(marks_list, key=lambda x: x[0], reverse=True)

    pass_pct = round((passed_count / total) * 100, 1) if total else 0.0
    fail_pct = round((failed_count / total) * 100, 1) if total else 0.0

    # Discover subject pass rates
    subjects_stats: Dict[str, Any] = {}
    if students:
        cols = list(students[0].keys())
        tot_cols = [c for c in cols if c.endswith("_TOT") or c.endswith("_Total") or c.endswith("_TW")]
        seen_codes = set()
        for tc in tot_cols:
            code = tc.split("_")[0]
            if code in seen_codes or code in ("TOTAL", "MAX", "MIN", "CREDITS", "SGPI", "MARKS"):
                continue
            seen_codes.add(code)
            sub_pass = 0
            sub_app = 0
            sub_marks = []
            for s in students:
                val = _safe_float(s.get(tc))
                if val > 0.0:
                    sub_app += 1
                    sub_marks.append(val)
                    if val >= 40.0:
                        sub_pass += 1
            if sub_app > 0:
                subjects_stats[code] = {
                    "code": code,
                    "appeared": sub_app,
                    "passed": sub_pass,
                    "pass_pct": round((sub_pass / sub_app) * 100, 1),
                    "avg": round(sum(sub_marks) / len(sub_marks), 1)
                }

    toughest_sub = min(subjects_stats.values(), key=lambda x: x["pass_pct"]) if subjects_stats else None
    top_sub = max(subjects_stats.values(), key=lambda x: x["avg"]) if subjects_stats else None

    return {
        "total": total,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "pass_percentage": pass_pct,
        "fail_percentage": fail_pct,
        "kt_null_gpa_count": kt_null_gpa_count,
        "regular_count": regular_count,
        "diploma_count": diploma_count,
        "regular_percentage": round((regular_count / total) * 100, 1) if total else 0.0,
        "diploma_percentage": round((diploma_count / total) * 100, 1) if total else 0.0,
        "male_count": male_count,
        "female_count": female_count,
        "min_sgpi": min(sgpi_vals) if sgpi_vals else 0.0,
        "max_sgpi": max(sgpi_vals) if sgpi_vals else 0.0,
        "avg_sgpi": round(sum(sgpi_vals) / len(sgpi_vals), 2) if sgpi_vals else 0.0,
        "min_marks": min(marks_vals) if marks_vals else 0.0,
        "max_marks": max(marks_vals) if marks_vals else 0.0,
        "avg_marks": round(sum(marks_vals) / len(marks_vals), 1) if marks_vals else 0.0,
        "distinction_count": distinction_count,
        "first_class_count": first_class_count,
        "second_class_count": second_class_count,
        "pass_class_count": pass_class_count,
        "toppers_sgpi": [(x[0], x[1], x[2]) for x in sgpi_list_desc[:5]],
        "lowest_5_sgpi": [(x[0], x[1], x[2]) for x in sgpi_list_asc[:5]],
        "lowest_sgpi_student": (sgpi_list_asc[0][0], sgpi_list_asc[0][1], sgpi_list_asc[0][2]) if sgpi_list_asc else None,
        "toppers_marks": marks_list_desc[:5],
        "failed_students": failed_students,
        "subjects": subjects_stats,
        "toughest_subject": toughest_sub,
        "top_subject": top_sub,
        "column_keys": list(students[0].keys()) if students else []
    }


def format_dataset_summary_for_prompt(
    analytics: Dict[str, Any],
    sample_rows: List[Dict[str, Any]],
    user_prompt: str = "",
    all_students: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Format rich analytics block to inject into prompt context."""
    total = analytics.get("total", 0)
    if total == 0:
        return "No records loaded."

    top_sgpi_str = ", ".join([f"{name} ({seat}): {sgpi:.2f}" for sgpi, seat, name in analytics.get("toppers_sgpi", [])[:3]]) or "N/A"
    low_student = analytics.get("lowest_sgpi_student")
    low_sgpi_str = f"{low_student[2]} (Seat {low_student[1]}): {low_student[0]:.2f}" if low_student else "N/A"
    failed_preview = ", ".join([f"{s['name']} ({s['seat']})" for s in analytics.get("failed_students", [])[:8]]) or "None"
    toughest = analytics.get("toughest_subject")
    tough_str = f"{toughest['code']} ({toughest['pass_pct']}% pass)" if toughest else "N/A"

    # Check if a specific student is mentioned in user_prompt to inject their complete profile
    queried_student_str = ""
    if user_prompt and all_students:
        p_clean = user_prompt.upper()
        # Check seat numbers or names
        for s in all_students:
            s_seat = _get_field(s, "seat")
            s_name = _get_field(s, "name").upper()
            if (s_seat and s_seat in p_clean) or (s_name and (s_name in p_clean or any(part in p_clean for part in s_name.split() if len(part) >= 4))):
                queried_student_str = (
                    f"\n- SPECIFIC STUDENT FOUND IN DATASET: {s_name} (Seat `{s_seat}`), "
                    f"Result: {_get_field(s, 'result') or 'Pass'}, "
                    f"SGPI: {_get_field(s, 'sgpi') or 'Null (KT)'}, "
                    f"Total Marks: {_get_field(s, 'marks') or 'N/A'}, "
                    f"Candidate Type: {_get_field(s, 'type') or 'Regular'}, "
                    f"Gender: {_get_field(s, 'gender') or 'N/A'}, "
                    f"PRN: {_get_field(s, 'prn') or 'N/A'}"
                )
                break

    # Compute explicit missing SGPI count
    missing_sgpi_count = 0
    if all_students:
        missing_sgpi_count = sum(1 for s in all_students if s.get("SGPI") is None or str(s.get("SGPI")).strip().lower() in ["nan", "null", "none", "", "--"])
    else:
        missing_sgpi_count = analytics.get("failed_count", 0)

    return (
        f"DATASET FACTS (Calculated across all {total} students in this cohort):\n"
        f"- Total Enrolled Students: {total}\n"
        f"- Clearance: {analytics.get('passed_count', 0)} Passed ({analytics.get('pass_percentage', 0.0)}%), "
        f"{analytics.get('failed_count', 0)} Failed / KT ({analytics.get('fail_percentage', 0.0)}%)\n"
        f"- SGPI Missing / Unannounced / KT: {missing_sgpi_count} student(s)\n"
        f"- SGPI Range (Cleared Students): Min={analytics.get('min_sgpi', 0.0):.2f}, Max={analytics.get('max_sgpi', 0.0):.2f}, Avg={analytics.get('avg_sgpi', 0.0):.2f}\n"
        f"- Overall Cleared Cohort SGPI Average: {analytics.get('avg_sgpi', 0.0):.2f}\n"
        f"- Note on KT: In university grading, students with null or unannounced GPA have KT (pending backlogs).\n"
        f"- Distinction (SGPI >= 7.5): {analytics.get('distinction_count', 0)} students\n"
        f"- Candidate Types: {analytics.get('regular_count', 0)} Regular, {analytics.get('diploma_count', 0)} Diploma\n"
        f"- Gender: {analytics.get('male_count', 0)} Male, {analytics.get('female_count', 0)} Female\n"
        f"- Lowest Cleared SGPI Student: {low_sgpi_str}\n"
        f"- Cohort Topper (Highest SGPI): {top_sgpi_str}\n"
        f"- Toughest Course: {tough_str}\n"
        f"- Failed / KT Students ({len(analytics.get('failed_students', []))} total): {failed_preview}\n"
        f"{queried_student_str}\n"
        f"- Available Columns: {', '.join(analytics.get('column_keys', [])[:12])}\n"
        f"IMPORTANT INSTRUCTION: Use the above exact facts to answer. Do NOT guess or recite sample rows."
    )


def apply_action(
    action: str,
    params: Dict[str, Any],
    students: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], str]:
    """Execute the structured action on the in-memory student records."""
    modified = [dict(s) for s in students]
    total_before = len(modified)
    first_row = modified[0] if modified else {}

    if action == "delete_slice":
        start = int(params.get("start", 0))
        count = int(params.get("count", 1))
        if start < len(modified):
            del modified[start : start + count]
        return modified, f"Removed {min(count, total_before)} record(s). Total records now: {len(modified)}."

    elif action == "delete_where":
        raw_key = params.get("key", "Result_Status")
        key = _resolve_key(first_row, raw_key)
        vals = [str(v).strip().upper() for v in params.get("values", [])]
        val = params.get("value")
        if val:
            vals.append(str(val).strip().upper())
        modified = [
            s for s in modified
            if str(s.get(key, "")).strip().upper() not in vals
        ]
        return modified, f"Removed {total_before - len(modified)} matching record(s). Total records now: {len(modified)}."

    elif action == "filter_keep":
        raw_key = params.get("key", "Result_Status")
        key = _resolve_key(first_row, raw_key)
        val = str(params.get("value", "")).strip().upper()
        modified = [
            s for s in modified
            if str(s.get(key, "")).strip().upper() == val
        ]
        return modified, f"Filtered dataset to {len(modified)} matching record(s)."

    elif action == "update_where":
        raw_match_key = params.get("match_key", "Seat_No")
        match_key = _resolve_key(first_row, raw_match_key)
        match_val = str(params.get("match_value", "")).strip()
        match_pfx = str(params.get("match_prefix", "")).strip()
        updates = params.get("updates", {})

        count_updated = 0
        for s in modified:
            cur_val = str(s.get(match_key, "")).strip()
            match = False
            if match_val and cur_val == match_val:
                match = True
            elif match_pfx and cur_val.startswith(match_pfx):
                match = True
            elif not match_val and not match_pfx:
                match = True

            if match:
                for uk, uv in updates.items():
                    resolved_uk = _resolve_key(s, uk)
                    s[resolved_uk] = uv
                count_updated += 1

        return modified, f"Updated {count_updated} student record(s)."

    elif action == "sort":
        raw_key = params.get("key", "SGPI")
        key = _resolve_key(first_row, raw_key)
        rev = bool(params.get("reverse", False))
        is_num = bool(params.get("numeric", False))

        if is_num or any(k in key.upper() for k in ["SGPI", "TOTAL", "MARKS", "CREDITS", "CG"]):
            modified.sort(key=lambda s: _safe_float(s.get(key, 0)), reverse=rev)
        else:
            modified.sort(key=lambda s: str(s.get(key, "")).strip().lower(), reverse=rev)

        order_str = "descending" if rev else "ascending"
        return modified, f"Sorted records by {key} ({order_str})."

    return modified, "Dataset unchanged."


def fallback_rule_engine(
    user_prompt: str,
    students: List[Dict[str, Any]],
    folder_name: str = ""
) -> Optional[Tuple[List[Dict[str, Any]], str, str]]:
    """
    Fast deterministic regex rule engine.
    Handles common actions (delete, filter, update, sort) and analytical Q&A (toppers, pass/fail, averages)
    with sub-millisecond precision.
    """
    p = user_prompt.strip().lower()
    analytics = calculate_dataset_analytics(students)
    total = analytics.get("total", len(students))

    # ==========================
    # 0. EXPORT / REPORT COMMANDS
    # ==========================
    if any(k in p for k in [
        "export excel", "download excel", "academic analysis", "6 sheet", "six sheet",
        "exact format", "google sheet format", "build excel", "export sheet",
        "download analysis", "generate excel report", "generate academic", "export academic",
        "export in this format", "excel in this format"
    ]):
        folder_suffix = f"/{folder_name}" if folder_name else ""
        link_md = f"**[📥 Download Academic Analysis Workbook (6 Sheets)](/api/export/academic-analysis{folder_suffix})**" if folder_name else "via the **🏛️ Academic Analysis (6 Sheets)** button in the export bar above"
        reply = (
            "🏛️ **Official Academic Analysis Workbook (6 Sheets) Ready!**\n\n"
            f"{link_md}\n\n"
            "This workbook is formatted to exactly match the target university departmental standard:\n"
            "• **1. Data**: Full unpivoted raw student records (Theory, IAT, TW, Practical, EC, ECG, SGPI) for copy-pasting.\n"
            "• **2. IAT KT analysis**: Backlog tracking for internal tests (KT Fail, KT Absent, em-dash `—` for pass).\n"
            "• **3. PR KT analysis**: Backlog tracking for practicals, labs, and orals.\n"
            "• **4. Summary**: Topper students table, Overall Result summary (Pass %, All Clear, KT), and Grade Point breakdown.\n"
            "• **5. Subject Wise Result**: Subject-wise pass/fail counts, marks range analysis, practical results, and subject toppers.\n"
            "• **6. Distributions**: Full percentage distributions across 10-point brackets (90-100 down to Fail <40) with min/max/average %."
        )
        return students, reply, "answer"

    # ==========================
    # 1. ACTION COMMANDS
    # ==========================

    # 1a. Remove first N records
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:the\s+)?(?:first|top)\s+(\d+)\s+records?", p)
    if m:
        n = int(m.group(1))
        updated, desc = apply_action("delete_slice", {"start": 0, "count": n}, students)
        return updated, f"✂️ Successfully removed the first {n} record(s). Total records now: {len(updated)}.", "delete_slice"

    # 1b. Remove last N records
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:the\s+)?(?:last|bottom)\s+(\d+)\s+records?", p)
    if m:
        n = int(m.group(1))
        start_idx = max(0, len(students) - n)
        updated, desc = apply_action("delete_slice", {"start": start_idx, "count": n}, students)
        return updated, f"✂️ Successfully removed the last {n} record(s). Total records now: {len(updated)}.", "delete_slice"

    # 1c. Remove single record by seat
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:student\s+|seat\s+)?(\d{6,10})\b", p)
    if m:
        seat = m.group(1)
        updated, desc = apply_action("delete_where", {"key": "Seat_No", "value": seat}, students)
        return updated, f"✂️ Removed student record with Seat No {seat}.", "delete_where"

    # 1d. Remove failed students
    if any(k in p for k in ["remove fail", "delete fail", "remove failed", "drop fail", "remove unsuccessful", "delete failed"]):
        updated, desc = apply_action("delete_where", {"key": "Result_Status", "values": ["FAIL", "FAILED", "Unsuccessful", "ABS"]}, students)
        return updated, f"✓ Removed all failed/unsuccessful students ({total - len(updated)} removed). Remaining: {len(updated)}.", "delete_where"

    # 1e. Keep only passed students
    if any(k in p for k in ["only pass", "only passed", "keep passed", "filter pass", "keep only pass"]):
        updated, desc = apply_action("filter_keep", {"key": "Result_Status", "value": "PASS"}, students)
        return updated, f"✓ Filtered dataset to keep only passed students ({len(updated)} records).", "filter_keep"

    # 1f. Keep only diploma students
    if any(k in p for k in ["only diploma", "keep diploma", "filter diploma", "keep only diploma"]):
        updated, desc = apply_action("filter_keep", {"key": "Candidate_Type", "value": "Diploma"}, students)
        return updated, f"🎓 Filtered dataset to keep only Diploma students ({len(updated)} records).", "filter_keep"

    # 1g. Keep only regular students
    if any(k in p for k in ["only regular", "keep regular", "filter regular"]):
        updated, desc = apply_action("filter_keep", {"key": "Candidate_Type", "value": "Regular"}, students)
        return updated, f"🎓 Filtered dataset to keep only Regular students ({len(updated)} records).", "filter_keep"

    # 1h. Keep only female students
    if any(k in p for k in ["only female", "only girls", "filter female", "keep female", "keep girls"]):
        updated, desc = apply_action("filter_keep", {"key": "Gender", "value": "F"}, students)
        return updated, f"👩 Filtered dataset to keep only Female students ({len(updated)} records).", "filter_keep"

    # 1i. Keep only male students
    if any(k in p for k in ["only male", "only boys", "filter male", "keep male", "keep boys"]):
        updated, desc = apply_action("filter_keep", {"key": "Gender", "value": "M"}, students)
        return updated, f"👨 Filtered dataset to keep only Male students ({len(updated)} records).", "filter_keep"

    # 1j. Mark 2022 PRN as Diploma
    if "diploma" in p and ("2022" in p or "prn" in p):
        updated, desc = apply_action("update_where", {"match_key": "PRN", "match_prefix": "2022", "updates": {"Candidate_Type": "Diploma"}}, students)
        return updated, "🎓 Updated Candidate_Type to 'Diploma' for students with PRN starting with 2022.", "update_where"

    # 1k. Sort by SGPI
    if "sort" in p and "sgpi" in p:
        rev = "asc" not in p
        updated, desc = apply_action("sort", {"key": "SGPI", "reverse": rev, "numeric": True}, students)
        return updated, f"📊 Sorted dataset by SGPI {'(highest first)' if rev else '(lowest first)'}.", "sort"

    # 1l. Sort by Total Marks
    if "sort" in p and ("total" in p or "marks" in p):
        rev = "asc" not in p
        updated, desc = apply_action("sort", {"key": "Total_Marks", "reverse": rev, "numeric": True}, students)
        return updated, f"📊 Sorted dataset by Total Marks {'(highest first)' if rev else '(lowest first)'}.", "sort"

    # 1m. Sort by Student Name
    if "sort" in p and ("name" in p or "alphabet" in p):
        rev = "desc" in p
        updated, desc = apply_action("sort", {"key": "Student_Name", "reverse": rev, "numeric": False}, students)
        return updated, f"📊 Sorted dataset by Student Name {'(Z-A)' if rev else '(A-Z)'}.", "sort"

    # 1n. Sort by Seat Number
    if "sort" in p and "seat" in p:
        rev = "desc" in p
        updated, desc = apply_action("sort", {"key": "Seat_No", "reverse": rev, "numeric": True}, students)
        return updated, f"📊 Sorted dataset by Seat Number {'(descending)' if rev else '(ascending)'}.", "sort"

    # ==========================
    # 2. ANALYTICAL Q&A RULES
    # ==========================

    # 2a. Pass vs Fail count & percentage
    if any(k in p for k in [
        "passed and failed", "pass and fail", "how many pass", "how many failed",
        "pass percentage", "pass rate", "how many students passed", "fail count", "pass count"
    ]):
        passed = analytics.get("passed_count", 0)
        failed = analytics.get("failed_count", 0)
        p_pct = analytics.get("pass_percentage", 0.0)
        f_pct = analytics.get("fail_percentage", 0.0)
        reply = (
            f"📊 **Result Distribution ({total} Total Students)**:\n"
            f"• **Passed**: **{passed}** students ({p_pct}%)\n"
            f"• **Failed / Unsuccessful**: **{failed}** students ({f_pct}%)\n"
            f"• **Overall Cohort Clearance Rate**: **{p_pct}%**"
        )
        return students, reply, "answer"

    # 2b. Toppers / Highest SGPI / Rank 1
    if any(k in p for k in [
        "topper", "topped", "highest sgpi", "top student", "rank 1", "first rank",
        "highest marks", "who got highest", "top 3", "top 5"
    ]):
        toppers = analytics.get("toppers_sgpi", [])
        if not toppers:
            return students, "No SGPI records found in the current dataset.", "answer"

        lines = [f"🏆 **Top Performers by SGPI (Cohort Size: {total})**:"]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (sgpi, seat, name) in enumerate(toppers[:5]):
            badge = medals[i] if i < len(medals) else f"{i+1}."
            lines.append(f"{badge} **{name}** (Seat: `{seat}`) — **SGPI: {sgpi:.2f}**")

        reply = "\n".join(lines)
        return students, reply, "answer"

    # 2c. Average / Mean SGPI and Marks
    if any(k in p for k in [
        "average sgpi", "mean sgpi", "avg sgpi", "average marks", "mean marks", "average score"
    ]):
        avg_sg = analytics.get("avg_sgpi", 0.0)
        min_sg = analytics.get("min_sgpi", 0.0)
        max_sg = analytics.get("max_sgpi", 0.0)
        avg_m = analytics.get("avg_marks", 0.0)
        reply = (
            f"📈 **Cohort Performance Averages ({total} Students)**:\n"
            f"• **Average SGPI**: **{avg_sg:.2f}** (Range: {min_sg:.2f} to {max_sg:.2f})\n"
            f"• **Average Total Marks**: **{avg_m:.1f}**\n"
            f"• **Pass Percentage**: **{analytics.get('pass_percentage', 0.0)}%**"
        )
        return students, reply, "answer"

    # 2d. Lowest SGPI / Bottom Performers
    if any(k in p for k in [
        "lowest sgpi", "minimum sgpi", "lowest pointer", "minimum pointer", "worst sgpi",
        "lowest marks", "least marks", "who got lowest", "who is at bottom", "bottom student",
        "bottom performers", "least sgpi", "minimum score", "lowest score", "who scored lowest"
    ]):
        lowest = analytics.get("lowest_sgpi_student")
        if not lowest:
            return students, "No valid evaluated SGPI records found in the current dataset.", "answer"

        low_sg, low_seat, low_name = lowest
        null_cnt = analytics.get("kt_null_gpa_count", 0)
        kt_note = f"\n\n*(Note: {null_cnt} student(s) with null/withheld GPA have KT / failed courses, so their numerical SGPI was not evaluated)*" if null_cnt > 0 else ""

        lines = [
            f"📉 **Lowest Evaluated SGPI in Cohort**: **{low_name}** (Seat `{low_seat}`) — **SGPI: {low_sg:.2f}**{kt_note}",
            "",
            "**Bottom 5 Performers with Valid SGPI**:"
        ]
        for i, (sg, st, nm) in enumerate(analytics.get("lowest_5_sgpi", [])):
            lines.append(f"{i+1}. **{nm}** (Seat `{st}`) — **SGPI: {sg:.2f}**")

        reply = "\n".join(lines)
        return students, reply, "answer"

    # 2e. Distinction / Grade Classifications
    if any(k in p for k in [
        "distinction", "first class", "second class", "pass class", "grade breakdown",
        "grade distribution", "how many distinction", "how many first class", "classification"
    ]):
        d_cnt = analytics.get("distinction_count", 0)
        f_cnt = analytics.get("first_class_count", 0)
        s_cnt = analytics.get("second_class_count", 0)
        p_cnt = analytics.get("pass_class_count", 0)
        fail_cnt = analytics.get("failed_count", 0)
        reply = (
            f"🎓 **Cohort Grade Classification ({total} Students)**:\n"
            f"• 🥇 **First Class with Distinction (SGPI ≥ 7.50)**: **{d_cnt}** students ({round(d_cnt/total*100, 1) if total else 0}%)\n"
            f"• 🥈 **First Class (6.75 - 7.49)**: **{f_cnt}** students ({round(f_cnt/total*100, 1) if total else 0}%)\n"
            f"• 🥉 **Second Class (6.00 - 6.74)**: **{s_cnt}** students ({round(s_cnt/total*100, 1) if total else 0}%)\n"
            f"• 📜 **Pass Class (4.00 - 5.99)**: **{p_cnt}** students ({round(p_cnt/total*100, 1) if total else 0}%)\n"
            f"• ❌ **KT / ATKT / Failed (SGPI < 4.0 or Null)**: **{fail_cnt}** students ({round(fail_cnt/total*100, 1) if total else 0}%)"
        )
        return students, reply, "answer"

    # 2f. Toughest Subject & Subject-Wise Analysis
    if any(k in p for k in [
        "toughest subject", "difficult subject", "hardest subject", "worst subject",
        "easiest subject", "best subject", "subject analysis", "subject pass",
        "subject wise", "which subject has most fails", "which subject is hard"
    ]):
        toughest = analytics.get("toughest_subject")
        top = analytics.get("top_subject")
        subs = analytics.get("subjects", {})
        if not subs:
            return students, "No subject-level component columns were discovered in this dataset schema.", "answer"

        lines = ["🎯 **Subject-Wise Curriculum Analytics**:"]
        if toughest:
            failed_num = toughest['appeared'] - toughest['passed']
            lines.append(f"• ⚠️ **Toughest Course (Lowest Pass Rate)**: **{toughest['code']}** with **{toughest['pass_pct']}%** clearance ({failed_num} failed out of {toughest['appeared']} appeared).")
        if top:
            lines.append(f"• 🌟 **Highest Scoring Course**: **{top['code']}** with average score of **{top['avg']}** marks.")

        lines.append("\n**Course-by-Course Clearance Rates**:")
        sorted_subs = sorted(subs.values(), key=lambda x: x["pass_pct"])
        for s in sorted_subs:
            icon = "🔴" if s["pass_pct"] < 75 else ("🟡" if s["pass_pct"] < 90 else "🟢")
            lines.append(f"{icon} **{s['code']}**: **{s['pass_pct']}% pass** (Avg: {s['avg']} | {s['passed']}/{s['appeared']} passed)")

        reply = "\n".join(lines)
        return students, reply, "answer"

    # 2g. Diploma vs Regular candidate distribution
    if any(k in p for k in [
        "how many diploma", "diploma vs regular", "candidate type", "regular vs diploma",
        "how many regular", "lateral entry count", "diploma students"
    ]):
        reg = analytics.get("regular_count", 0)
        dip = analytics.get("diploma_count", 0)
        r_pct = analytics.get("regular_percentage", 0.0)
        d_pct = analytics.get("diploma_percentage", 0.0)
        reply = (
            f"🎓 **Candidate Category Distribution ({total} Total Students)**:\n"
            f"• **Regular Students**: **{reg}** ({r_pct}%)\n"
            f"• **Diploma / Lateral Entry Students**: **{dip}** ({d_pct}%)"
        )
        return students, reply, "answer"

    # 2h. Gender Ratio / Boys vs Girls
    if any(k in p for k in [
        "how many boys", "how many girls", "gender ratio", "male vs female", "gender breakdown", "female count"
    ]):
        male = analytics.get("male_count", 0)
        fem = analytics.get("female_count", 0)
        reply = (
            f"👥 **Gender Distribution ({total} Total Students)**:\n"
            f"• **Male**: **{male}** students ({round(male/total*100, 1) if total else 0}%)\n"
            f"• **Female**: **{fem}** students ({round(fem/total*100, 1) if total else 0}%)"
        )
        return students, reply, "answer"

    # 2i. List of Failed Students & KT Analysis
    if any(k in p for k in [
        "who failed", "which students failed", "list failed", "failed list", "show kt",
        "failed students", "kt count", "how many kt", "null gpa", "students with kt", "backlogs"
    ]):
        failed_list = analytics.get("failed_students", [])
        null_cnt = analytics.get("kt_null_gpa_count", 0)
        if not failed_list:
            return students, "🎉 Excellent news: **All students in this dataset passed!** (0 failed / KT records).", "answer"

        lines = [
            f"❌ **KT (Keep Terms) & Failed Students ({len(failed_list)} total / {analytics.get('fail_percentage', 0.0)}%)**:",
            f"• **Students with Null / Withheld GPA**: **{null_cnt}** *(In Mumbai University grading, students with unannounced GPA have KT in one or more heads)*",
            "",
            "**KT Candidates List**:"
        ]
        for i, s in enumerate(failed_list[:15]):
            reason = f" ({s.get('reason')})" if s.get('reason') else ""
            lines.append(f"{i+1}. `{s['seat']}`: **{s['name']}** — SGPI: `{s['sgpi']}`{reason}")
        if len(failed_list) > 15:
            lines.append(f"*(and {len(failed_list) - 15} more...)*")

        return students, "\n".join(lines), "answer"

    # 2j. Score threshold queries: e.g. "students with sgpi greater than 8" or "sgpi less than 6"
    m_gt = re.search(r"sgpi\s*(?:greater than|above|>|>=)\s*(\d+(?:\.\d+)?)", p)
    if m_gt:
        thresh = float(m_gt.group(1))
        matching = [s for s in students if _safe_float(_get_field(s, "sgpi")) >= thresh]
        lines = [f"🌟 **Students with SGPI ≥ {thresh:.2f} ({len(matching)} students out of {total})**:"]
        for i, s in enumerate(matching[:10]):
            lines.append(f"{i+1}. **{_get_field(s, 'name')}** (Seat `{_get_field(s, 'seat')}`) — SGPI: **{_get_field(s, 'sgpi')}**")
        if len(matching) > 10:
            lines.append(f"*(and {len(matching) - 10} more...)*")
        return students, "\n".join(lines), "answer"

    m_lt = re.search(r"sgpi\s*(?:less than|below|<|<=)\s*(\d+(?:\.\d+)?)", p)
    if m_lt:
        thresh = float(m_lt.group(1))
        matching = [s for s in students if 0.0 < _safe_float(_get_field(s, "sgpi")) <= thresh]
        lines = [f"⚠️ **Students with Valid SGPI ≤ {thresh:.2f} ({len(matching)} students)**:"]
        for i, s in enumerate(matching[:10]):
            lines.append(f"{i+1}. **{_get_field(s, 'name')}** (Seat `{_get_field(s, 'seat')}`) — SGPI: **{_get_field(s, 'sgpi')}**")
        if len(matching) > 10:
            lines.append(f"*(and {len(matching) - 10} more...)*")
        return students, "\n".join(lines), "answer"

    # 2k. Lookup student by Seat Number (supporting direct seat number or prefixed queries)
    seat_candidates = re.findall(r"\b\d{1,12}\b", p)
    for target_seat in seat_candidates:
        for s in students:
            if _get_field(s, "seat") == target_seat:
                raw_sg = _get_field(s, "sgpi")
                sg_disp = f"**{raw_sg}**" if raw_sg and raw_sg not in ("--", "-", "null") else "**Null (KT - Keep Terms)**"
                reply = (
                    f"👤 **Student Record Found** (Seat `{target_seat}`):\n"
                    f"• **Name**: **{_get_field(s, 'name')}**\n"
                    f"• **Result**: **{_get_field(s, 'result') or 'N/A'}**\n"
                    f"• **SGPI**: {sg_disp}\n"
                    f"• **Candidate Type**: {_get_field(s, 'type') or 'Regular'}\n"
                    f"• **Gender**: {_get_field(s, 'gender') or 'N/A'}\n"
                    f"• **PRN**: `{_get_field(s, 'prn') or 'N/A'}`\n"
                    f"• **Total Marks**: {_get_field(s, 'marks') or 'N/A'}"
                )
                return students, reply, "answer"

    # 2l. Lookup student by Name (supporting direct name like 'BASUDE ADITYA YASHWANT KALAWATI', surname, or queries)
    has_subject_inquiry = any(w in p for w in ["score in", "marks in", "grade in", "in ", "subject", "theory", "internal", "lab", "project"])
    raw_name_query = re.sub(r"^(?:find|search|details of|who is|show|get|pointer of|sgpi of|marks of|result of|status of)\s+", "", p).strip()
    raw_name_query = re.sub(r"[^\w\s]", "", raw_name_query).strip().upper()
    stopwords = {"THE", "TOPPER", "STUDENT", "DATASET", "RESULTS", "FAILED", "PASSED", "LOWEST", "HIGHEST", "AVERAGE", "SUMMARY", "OVERVIEW", "ALL", "REPORT"}
    if not has_subject_inquiry and len(raw_name_query) >= 3 and raw_name_query not in stopwords:
        matches = []
        for s in students:
            s_name = _get_field(s, "name").upper()
            if raw_name_query == s_name or raw_name_query in s_name or any(part in s_name for part in raw_name_query.split() if len(part) >= 4 and part not in stopwords):
                matches.append(s)
        if matches:
            target_s = matches[0]
            raw_sg = _get_field(target_s, "sgpi")
            sg_disp = f"**{raw_sg}**" if raw_sg and raw_sg not in ("--", "-", "null") else "**Null (KT - Keep Terms)**"
            reply = (
                f"👤 **Student Record Found**: **{_get_field(target_s, 'name')}** (Seat `{_get_field(target_s, 'seat')}`):\n"
                f"• **Result**: **{_get_field(target_s, 'result') or 'N/A'}**\n"
                f"• **SGPI**: {sg_disp}\n"
                f"• **Candidate Type**: {_get_field(target_s, 'type') or 'Regular'}\n"
                f"• **Gender**: {_get_field(target_s, 'gender') or 'N/A'}\n"
                f"• **PRN**: `{_get_field(target_s, 'prn') or 'N/A'}`\n"
                f"• **Total Marks**: {_get_field(target_s, 'marks') or 'N/A'}"
            )
            if len(matches) > 1:
                reply += f"\n*(found {len(matches)} matching students, displaying best match)*"
            return students, reply, "answer"

    # 2m. Overview / Executive Summary
    if any(k in p for k in ["summary", "overview", "dashboard", "tell me about this data", "analyze this data", "report"]):
        toppers = analytics.get("toppers_sgpi", [])
        top_str = f"{toppers[0][2]} (SGPI {toppers[0][0]:.2f})" if toppers else "N/A"
        lowest_st = analytics.get("lowest_sgpi_student")
        low_str = f"{lowest_st[2]} (SGPI {lowest_st[0]:.2f})" if lowest_st else "N/A"
        null_cnt = analytics.get("kt_null_gpa_count", 0)
        reply = (
            f"📋 **Executive Cohort Overview ({total} Students)**:\n"
            f"• **Pass Clearance**: **{analytics.get('pass_percentage', 0.0)}%** ({analytics.get('passed_count', 0)} Passed, {analytics.get('failed_count', 0)} KT/Failed)\n"
            f"• **Average SGPI**: **{analytics.get('avg_sgpi', 0.0):.2f}** (Evaluated Range: {analytics.get('min_sgpi', 0.0):.2f} - {analytics.get('max_sgpi', 0.0):.2f})\n"
            f"• **Cohort Topper**: **{top_str}**\n"
            f"• **Lowest Cleared SGPI**: **{low_str}**\n"
            f"• **KT Backlogs**: **{analytics.get('failed_count', 0)}** students ({null_cnt} with null/withheld GPA)\n"
            f"• **First Class with Distinction (≥7.5)**: **{analytics.get('distinction_count', 0)}** students\n"
            f"• **Candidate Mix**: **{analytics.get('regular_count', 0)}** Regular, **{analytics.get('diploma_count', 0)}** Diploma\n"
            f"• **Gender Split**: **{analytics.get('male_count', 0)}** Male / **{analytics.get('female_count', 0)}** Female"
        )
        return students, reply, "answer"

    return None


def is_complex_query(prompt: str) -> bool:
    """
    Determine if a query requires cloud-level multi-step reasoning, synthesis,
    comparative analysis, or deep academic narrative formulation.
    """
    p = prompt.strip().lower()

    # Complex keywords
    complex_triggers = [
        "compare", "comparison", "correlate", "correlation", "difference between",
        "why did", "why are", "reason for", "explain why", "root cause",
        "report", "draft", "formal letter", "hod", "principal", "recommendation",
        "action plan", "remedial", "strategy", "curriculum", "syllabus",
        "trend", "drop in pass", "identify weakness", "subject wise analysis",
        "difficult subject", "easiest subject", "qualitative", "comprehensive review"
    ]
    if any(trigger in p for trigger in complex_triggers):
        return True

    # Multi-clause or long inquiries (>16 words)
    words = p.split()
    if len(words) >= 16:
        return True

    return False


def process_chat_command(
    user_prompt: str,
    students: List[Dict[str, Any]],
    catalog: Optional[List[Dict[str, Any]]] = None,
    client: Optional[OllamaClient] = None,
    cloud_client: Optional[CloudClient] = None,
    session_id: str = "default_session",
    folder_name: str = ""
) -> Dict[str, Any]:
    """
    Process natural-language dataset commands using a smart Hybrid Routing strategy:
    1. Instant Rule Engine (<1ms, 0 tokens) for deterministic commands and exact stats.
    2. Cloud AI (Gemini / OpenAI) for complex reasoning, reports, or when cloud is preferred,
       utilizing persistent session context caching to minimize token usage.
    3. Seamless fallback to local Ollama (qwen2.5:7b) if offline or basic query.
    4. Dataset Analytics Assistant if local LLM is also unavailable.
    """
    if not students:
        return {
            "success": False,
            "reply": "No student records are currently loaded to work on. Please extract or select a PDF first.",
            "action": "none",
            "total_students": 0,
            "students": []
        }

    if cloud_client is None:
        cloud_client = CloudClient()
    if client is None:
        client = OllamaClient()

    routing_mode = cloud_client.config.get("routing_mode", "auto_hybrid")
    complex_query = is_complex_query(user_prompt)
    is_cloud_active = cloud_client.is_configured()
    prefer_cloud_active = is_cloud_active and routing_mode == "prefer_cloud"

    # Export / Report requests are always handled deterministically with direct download link
    p_lower = user_prompt.strip().lower()
    if any(k in p_lower for k in [
        "export excel", "download excel", "academic analysis", "6 sheet", "six sheet",
        "exact format", "google sheet format", "build excel", "export sheet",
        "download analysis", "generate excel report", "generate academic", "export academic",
        "export in this format", "excel in this format"
    ]):
        rule_res = fallback_rule_engine(user_prompt, students, folder_name=folder_name)
        if rule_res is not None:
            updated_data, desc, action_type = rule_res
            return {
                "success": True,
                "reply": desc,
                "action": action_type,
                "total_students": len(updated_data),
                "students": updated_data,
                "engine_used": "Academic Export Engine"
            }

    # 1. In auto_hybrid or offline mode, check deterministic rule engine first (<1ms, 0 tokens)
    if not prefer_cloud_active:
        rule_res = fallback_rule_engine(user_prompt, students, folder_name=folder_name)
        if rule_res is not None:
            updated_data, desc, action_type = rule_res
            return {
                "success": True,
                "reply": desc,
                "action": action_type,
                "total_students": len(updated_data),
                "students": updated_data,
                "engine_used": "Instant Analytics Engine"
            }

    # 2. Extract catalog courses and available columns for query planning
    catalog_courses = []
    if isinstance(catalog, dict):
        catalog_courses = catalog.get("courses", [])
    elif isinstance(catalog, list):
        catalog_courses = catalog

    columns = list(students[0].keys()) if students else []

    # 3. Agentic Query Planning: predict required columns and student candidate rows
    planner = LocalQueryPlanner(ollama_client=client)
    plan = planner.plan_query(
        query=user_prompt,
        columns=columns,
        catalog_courses=catalog_courses,
        students=students
    )

    # If planner identifies a dataset modification action (delete, filter, sort)
    plan_action = plan.get("action", "answer")
    if plan_action != "answer" and plan.get("params"):
        updated_data, detail_msg = apply_action(plan_action, plan["params"], students)
        cloud_client.notify_dataset_modified(session_id, detail_msg)
        return {
            "success": True,
            "reply": detail_msg,
            "action": plan_action,
            "total_students": len(updated_data),
            "students": updated_data,
            "engine_used": "Query Planner (Instant Action)"
        }

    # 4. Extract targeted DataFrame slice in-memory (<1ms)
    df_slice, md_table, slice_meta = planner.extract_dataframe_slice(students, plan)
    slice_records = df_slice.to_dict(orient="records") if df_slice is not None else students[:5]

    analytics = calculate_dataset_analytics(students)
    context_summary = format_dataset_summary_for_prompt(
        analytics, students[:5], user_prompt=user_prompt, all_students=students
    )

    # Build compact full cohort representation for Cloud AI
    # Keeps token footprint minimal (~1,500 tokens for 70 students) while providing 100% of student records
    core_cols = ["Seat_No", "Student_Name", "Result_Status", "SGPI", "Total_Marks"]
    plan_cols = plan.get("relevant_columns", [])
    needed_cols = list(dict.fromkeys(core_cols + [c for c in plan_cols if c not in core_cols]))

    full_cohort_compact = []
    for s in students:
        row_dict = {}
        for col in needed_cols:
            val = s.get(col)
            if val is not None and str(val).lower() != "nan":
                row_dict[col] = val
        full_cohort_compact.append(row_dict)

    planner_tag = "Ollama Planner" if plan.get("planner_type") == "ollama_neural" else "Local Query Planner"

    # Determine if Cloud AI should handle this request
    should_try_cloud = is_cloud_active and (
        prefer_cloud_active or routing_mode == "auto_hybrid" or complex_query
    )

    cloud_error = None
    if should_try_cloud:
        try:
            raw_json = cloud_client.generate(
                user_prompt=user_prompt,
                system_instruction=SYSTEM_PROMPT,
                dataset_summary=context_summary,
                students=full_cohort_compact,
                dataframe_slice=md_table,
                session_id=session_id,
                format_json=True,
                timeout=40
            )
            parsed = json.loads(raw_json)
            action = parsed.get("action", "answer")
            reply = parsed.get("reply", "Processed your request.")
            params = parsed.get("params", {})
            provider_label = cloud_client.config.get("provider", "Cloud").title()
            model_label = cloud_client.config.get("model", "")

            if action == "answer":
                return {
                    "success": True,
                    "reply": reply,
                    "action": "answer",
                    "total_students": len(students),
                    "students": students,
                    "engine_used": f"{planner_tag} + Cloud AI ({provider_label} - {model_label})"
                }

            updated_data, detail_msg = apply_action(action, params, students)
            cloud_client.notify_dataset_modified(session_id, detail_msg)
            full_reply = f"{reply} ({detail_msg})" if reply else detail_msg
            return {
                "success": True,
                "reply": full_reply,
                "action": action,
                "total_students": len(updated_data),
                "students": updated_data,
                "engine_used": f"{planner_tag} + Cloud AI ({provider_label} - {model_label})"
            }
        except Exception as e:
            cloud_error = str(e)
            # If Cloud AI failed (e.g. rate limit, quota, network error), check deterministic rule engine first
            rule_res = fallback_rule_engine(user_prompt, students)
            if rule_res is not None:
                updated_data, desc, action_type = rule_res
                return {
                    "success": True,
                    "reply": f"*(Cloud AI temporarily unavailable [{cloud_error[:60]}] — processed by Instant Analytics Engine)*\n\n{desc}",
                    "action": action_type,
                    "total_students": len(updated_data),
                    "students": updated_data,
                    "engine_used": "Instant Analytics Engine"
                }

    # 5. Local Ollama Neural Fallback with focused DataFrame slice (~200 tokens instead of 38,000!)
    if client.is_available():
        clean_slice = [CloudClient._clean_for_json(s) for s in slice_records]
        slice_payload = json.dumps(clean_slice, ensure_ascii=False)

        llm_prompt = (
            f"USER REQUEST: \"{user_prompt}\"\n\n"
            f"{context_summary}\n\n"
            f"=== TARGETED RELEVANT DATAFRAME SLICE ({len(slice_records)} STUDENTS, {slice_meta.get('columns', 0)} COLUMNS) ===\n"
            f"{md_table}\n\n"
            f"=== COMPACT SLICE JSON ===\n"
            f"{slice_payload}\n\n"
            "TASK:\n"
            "- Answer factually, completely, and accurately using the relevant DataFrame slice above.\n"
            "- Set \"action\": \"answer\" and return valid JSON with \"reply\" and \"action\"."
        )

        try:
            raw_json = client.generate(
                prompt=llm_prompt,
                system=SYSTEM_PROMPT,
                temperature=0.0,
                format_json=CHAT_JSON_SCHEMA,
                timeout=35
            )
            parsed = json.loads(raw_json)
            action = parsed.get("action", "answer")
            reply = parsed.get("reply", "Processed your request.")
            params = parsed.get("params", {})

            engine_note = f"{planner_tag} + Local Ollama ({client.model})"
            if cloud_error:
                reply = f"*(Cloud AI offline or unavailable [{cloud_error[:60]}] — seamlessly routed to Local Ollama)*\n\n{reply}"

            if action == "answer":
                return {
                    "success": True,
                    "reply": reply,
                    "action": "answer",
                    "total_students": len(students),
                    "students": students,
                    "engine_used": engine_note
                }

            updated_data, detail_msg = apply_action(action, params, students)
            full_reply = f"{reply} ({detail_msg})" if reply else detail_msg
            return {
                "success": True,
                "reply": full_reply,
                "action": action,
                "total_students": len(updated_data),
                "students": updated_data,
                "engine_used": engine_note
            }
        except Exception:
            pass

    # 4. Final Fallback: Instant Dataset Analytics Assistant
    reply_text = (
        f"I analyzed your request ('{user_prompt}').\n"
        f"Dataset has {analytics.get('total', len(students))} students ({analytics.get('passed_count', 0)} Passed, {analytics.get('failed_count', 0)} Failed, Avg SGPI: {analytics.get('avg_sgpi', 0.0):.2f}).\n"
        "You can ask me questions like: 'how many passed and failed?', 'who is the topper?', 'average sgpi', 'diploma vs regular', or commands like 'remove first 2 records', 'filter passed', or 'sort by SGPI'."
    )
    if cloud_error:
        reply_text = f"*(Cloud AI error: {cloud_error[:80]})*\n\n{reply_text}"

    return {
        "success": True,
        "reply": reply_text,
        "action": "unrecognized",
        "total_students": len(students),
        "students": students,
        "engine_used": "Dataset Analytics Assistant"
    }
