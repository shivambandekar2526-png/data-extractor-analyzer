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
except Exception:
    try:
        from data_extractor_AI.core.ollama_client import OllamaClient
    except Exception:
        from core.ollama_client import OllamaClient

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
    "1. 'answer': When user asks ANY question about the dataset (counts, toppers, averages, comparisons, explanations). "
    "   Provide a comprehensive, factual, professional answer in 'reply' using the DATASET SUMMARY provided in the prompt.\n"
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
    other_status_count = 0

    regular_count = 0
    diploma_count = 0

    male_count = 0
    female_count = 0

    sgpi_list: List[Tuple[float, str, str]] = []  # (sgpi, seat, name)
    marks_list: List[Tuple[float, str, str]] = []  # (marks, seat, name)
    failed_students: List[Dict[str, str]] = []

    for s in students:
        seat = _get_field(s, "seat")
        name = _get_field(s, "name")
        res = _get_field(s, "result").upper()
        cand = _get_field(s, "type").upper()
        gen = _get_field(s, "gender").upper()
        sgpi_val = _safe_float(_get_field(s, "sgpi"))
        marks_val = _safe_float(_get_field(s, "marks"))

        # Status
        if any(w in res for w in ["PASS", "P", "SUCCESSFUL"]):
            passed_count += 1
        elif any(w in res for w in ["FAIL", "F", "UNSUCCESSFUL", "ATKT", "KT", "ABS"]):
            failed_count += 1
            failed_students.append({"seat": seat, "name": name, "sgpi": str(sgpi_val), "result": res})
        else:
            other_status_count += 1

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
            sgpi_list.append((sgpi_val, seat, name))
        if marks_val > 0.0:
            marks_list.append((marks_val, seat, name))

    sgpi_vals = [x[0] for x in sgpi_list]
    marks_vals = [x[0] for x in marks_list]

    sgpi_list_sorted = sorted(sgpi_list, key=lambda x: x[0], reverse=True)
    marks_list_sorted = sorted(marks_list, key=lambda x: x[0], reverse=True)

    pass_pct = round((passed_count / total) * 100, 1) if total else 0.0
    fail_pct = round((failed_count / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "pass_percentage": pass_pct,
        "fail_percentage": fail_pct,
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
        "toppers_sgpi": sgpi_list_sorted[:5],
        "toppers_marks": marks_list_sorted[:5],
        "failed_students": failed_students,
        "column_keys": list(students[0].keys()) if students else []
    }


def format_dataset_summary_for_prompt(analytics: Dict[str, Any], sample_rows: List[Dict[str, Any]]) -> str:
    """Format rich analytics block to inject into Ollama prompt context."""
    total = analytics.get("total", 0)
    if total == 0:
        return "No records loaded."

    top_sgpi_str = ", ".join([f"{name} ({seat}): {sgpi:.2f}" for sgpi, seat, name in analytics.get("toppers_sgpi", [])[:3]]) or "N/A"
    failed_preview = ", ".join([f"{s['name']} ({s['seat']})" for s in analytics.get("failed_students", [])[:10]]) or "None"

    return (
        f"DATASET SUMMARY (Calculated across all {total} students in this cohort):\n"
        f"- Total Students: {total}\n"
        f"- Results: {analytics.get('passed_count', 0)} Passed ({analytics.get('pass_percentage', 0.0)}%), "
        f"{analytics.get('failed_count', 0)} Failed/Unsuccessful ({analytics.get('fail_percentage', 0.0)}%)\n"
        f"- Candidate Types: {analytics.get('regular_count', 0)} Regular ({analytics.get('regular_percentage', 0.0)}%), "
        f"{analytics.get('diploma_count', 0)} Diploma ({analytics.get('diploma_percentage', 0.0)}%)\n"
        f"- Gender: {analytics.get('male_count', 0)} Male, {analytics.get('female_count', 0)} Female\n"
        f"- SGPI Metrics: Min={analytics.get('min_sgpi', 0.0):.2f}, Max={analytics.get('max_sgpi', 0.0):.2f}, Avg={analytics.get('avg_sgpi', 0.0):.2f}\n"
        f"- Top Performers (SGPI): {top_sgpi_str}\n"
        f"- Failed Students ({len(analytics.get('failed_students', []))} total): {failed_preview}\n"
        f"- Table Columns: {analytics.get('column_keys', [])[:12]}\n"
        f"- Sample Records (First 3):\n{json.dumps(sample_rows[:3], indent=1)}"
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
    students: List[Dict[str, Any]]
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

    # 2d. Lowest SGPI / At-risk students
    if any(k in p for k in ["lowest sgpi", "minimum sgpi", "lowest marks", "worst sgpi"]):
        toppers = analytics.get("toppers_sgpi", [])
        if toppers:
            lowest_sg, low_seat, low_name = toppers[-1]
            reply = f"⚠️ **Lowest SGPI**: **{low_name}** (Seat `{low_seat}`) with an SGPI of **{lowest_sg:.2f}**."
            return students, reply, "answer"

    # 2e. Diploma vs Regular candidate distribution
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

    # 2f. Gender Ratio / Boys vs Girls
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

    # 2g. List of Failed Students
    if any(k in p for k in [
        "who failed", "which students failed", "list failed", "failed list", "show kt", "failed students"
    ]):
        failed_list = analytics.get("failed_students", [])
        if not failed_list:
            return students, "🎉 Excellent news: **All students in this dataset passed!** (0 failed records).", "answer"

        lines = [f"❌ **Failed / Unsuccessful Students ({len(failed_list)} total)**:"]
        for i, s in enumerate(failed_list[:15]):
            sgpi_disp = f" — SGPI: {s['sgpi']}" if float(s.get('sgpi', 0)) > 0 else ""
            lines.append(f"{i+1}. `{s['seat']}`: **{s['name']}**{sgpi_disp}")
        if len(failed_list) > 15:
            lines.append(f"*(and {len(failed_list) - 15} more...)*")

        return students, "\n".join(lines), "answer"

    # 2h. Lookup single student details
    m_seat = re.search(r"\b(?:find|search|details of|who is|show|get)\s+(?:student\s+|seat\s+)?(\d{6,10})\b", p)
    if m_seat:
        target_seat = m_seat.group(1)
        for s in students:
            if _get_field(s, "seat") == target_seat:
                reply = (
                    f"👤 **Student Record Found** (Seat `{target_seat}`):\n"
                    f"• **Name**: **{_get_field(s, 'name')}**\n"
                    f"• **Result**: **{_get_field(s, 'result')}**\n"
                    f"• **SGPI**: **{_get_field(s, 'sgpi') or 'N/A'}**\n"
                    f"• **Candidate Type**: {_get_field(s, 'type')}\n"
                    f"• **Gender**: {_get_field(s, 'gender')}\n"
                    f"• **PRN**: `{_get_field(s, 'prn')}`\n"
                    f"• **Total Marks**: {_get_field(s, 'marks') or 'N/A'}"
                )
                return students, reply, "answer"
        return students, f"Student with Seat No `{target_seat}` was not found in the current dataset.", "answer"

    # 2i. Overview / Executive Summary
    if any(k in p for k in ["summary", "overview", "dashboard", "tell me about this data", "analyze this data"]):
        toppers = analytics.get("toppers_sgpi", [])
        top_str = f"{toppers[0][2]} (SGPI {toppers[0][0]:.2f})" if toppers else "N/A"
        reply = (
            f"📋 **Executive Cohort Overview ({total} Students)**:\n"
            f"• **Pass Clearance**: **{analytics.get('pass_percentage', 0.0)}%** ({analytics.get('passed_count', 0)} Passed, {analytics.get('failed_count', 0)} Failed)\n"
            f"• **Average SGPI**: **{analytics.get('avg_sgpi', 0.0):.2f}** (Cohort Range: {analytics.get('min_sgpi', 0.0):.2f} - {analytics.get('max_sgpi', 0.0):.2f})\n"
            f"• **Top Scorer**: **{top_str}**\n"
            f"• **Candidate Composition**: **{analytics.get('regular_count', 0)}** Regular, **{analytics.get('diploma_count', 0)}** Diploma\n"
            f"• **Gender Split**: **{analytics.get('male_count', 0)}** Male / **{analytics.get('female_count', 0)}** Female"
        )
        return students, reply, "answer"

    return None


def process_chat_command(
    user_prompt: str,
    students: List[Dict[str, Any]],
    catalog: Optional[List[Dict[str, Any]]] = None,
    client: Optional[OllamaClient] = None
) -> Dict[str, Any]:
    """
    Process natural-language dataset commands using Ollama (Qwen 2.5) with rich statistical fallback.
    Returns: {
        "success": bool,
        "reply": str,
        "action": str,
        "total_students": int,
        "students": list of modified dicts,
        "engine_used": str
    }
    """
    if not students:
        return {
            "success": False,
            "reply": "No student records are currently loaded to work on. Please extract or select a PDF first.",
            "action": "none",
            "total_students": 0,
            "students": []
        }

    # 1. First check deterministic rule engine for instantaneous sub-millisecond execution & exact stats
    rule_res = fallback_rule_engine(user_prompt, students)
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

    if client is None:
        client = OllamaClient()

    # 2. If Ollama is available, invoke neural LLM with full cohort analytics context
    if client.is_available():
        analytics = calculate_dataset_analytics(students)
        context_summary = format_dataset_summary_for_prompt(analytics, students[:5])

        llm_prompt = (
            f"USER REQUEST: \"{user_prompt}\"\n\n"
            f"{context_summary}\n\n"
            "TASK:\n"
            "- If the user asks a question, set \"action\": \"answer\" and write a detailed, helpful answer in \"reply\".\n"
            "- If the user asks to modify records (delete, filter, update, sort), return the structured JSON action and parameters.\n"
            "Return valid JSON matching the schema."
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

            if action == "answer":
                return {
                    "success": True,
                    "reply": reply,
                    "action": "answer",
                    "total_students": len(students),
                    "students": students,
                    "engine_used": f"Ollama ({client.model})"
                }

            updated_data, detail_msg = apply_action(action, params, students)
            full_reply = f"{reply} ({detail_msg})" if reply else detail_msg
            return {
                "success": True,
                "reply": full_reply,
                "action": action,
                "total_students": len(updated_data),
                "students": updated_data,
                "engine_used": f"Ollama ({client.model})"
            }
        except Exception:
            pass

    # 3. Fallback when LLM is offline or query is unhandled
    analytics = calculate_dataset_analytics(students)
    return {
        "success": True,
        "reply": (
            f"I analyzed your request ('{user_prompt}').\n"
            f"Dataset has {analytics.get('total', len(students))} students ({analytics.get('passed_count', 0)} Passed, {analytics.get('failed_count', 0)} Failed, Avg SGPI: {analytics.get('avg_sgpi', 0.0):.2f}).\n"
            "You can ask me questions like: 'how many passed and failed?', 'who is the topper?', 'average sgpi', 'diploma vs regular', or commands like 'remove first 2 records', 'filter passed', or 'sort by SGPI'."
        ),
        "action": "unrecognized",
        "total_students": len(students),
        "students": students,
        "engine_used": "Dataset Analytics Assistant"
    }
