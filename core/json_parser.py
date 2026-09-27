"""
JSON Parser & Normalizer
========================
Extracts and normalizes student records and course catalogs from raw LLM responses.
Handles partial JSON, markdown code fences, and academic symbol annotations.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord


def clean_json_string(raw: str) -> str:
    """Strip markdown formatting, code fences, and leading/trailing chatter."""
    text = raw.strip()

    # Strip markdown code blocks ```json ... ```
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
        else:
            text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

    # Find start of JSON object or array
    idx_brace = text.find("{")
    idx_bracket = text.find("[")

    if idx_brace != -1 and (idx_bracket == -1 or idx_brace < idx_bracket):
        last_brace = text.rfind("}")
        if last_brace != -1:
            text = text[idx_brace : last_brace + 1]
    elif idx_bracket != -1:
        last_bracket = text.rfind("]")
        if last_bracket != -1:
            text = text[idx_bracket : last_bracket + 1]

    return text


def clean_mark(val: Any) -> str:
    """Normalize academic mark values, preserving absences."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s in ("--", "null", "None", "N/A"):
        return ""
    if s.upper() in ("AA", "AB", "ABS"):
        return "ABS"
    # Strip grace / carry-over marks (e.g. 18F -> 18, 50E -> 50, 45* -> 45)
    cleaned = re.sub(r"[^\d\.]", "", s)
    return cleaned if cleaned else s


def parse_catalog_response(raw_text: str) -> Tuple[ExamMeta, CourseCatalog]:
    """Parse exam metadata and course catalog from page 0 response."""
    meta = ExamMeta()
    catalog = CourseCatalog()

    cleaned = clean_json_string(raw_text)
    try:
        data = json.loads(cleaned)
    except Exception:
        return meta, catalog

    if isinstance(data, dict):
        meta.exam_title = data.get("exam_title", "")
        meta.college_name = data.get("college_name", "")
        meta.result_date = data.get("result_date", "")
        meta.semester = data.get("semester", "")

        raw_courses = data.get("courses", [])
        if isinstance(raw_courses, list):
            import math
            for item in raw_courses:
                if isinstance(item, dict) and item.get("code"):
                    raw_max = item.get("max_marks", {}) or {}
                    raw_min = item.get("min_marks", {}) or {}
                    max_marks = {}
                    min_marks = {}
                    if isinstance(raw_max, dict):
                        for k, v in raw_max.items():
                            f_v = normalize_numeric(v)
                            if f_v:
                                try:
                                    max_marks[str(k).upper()] = int(float(f_v))
                                except Exception:
                                    pass
                    if isinstance(raw_min, dict):
                        for k, v in raw_min.items():
                            f_v = normalize_numeric(v)
                            if f_v:
                                try:
                                    min_marks[str(k).upper()] = int(float(f_v))
                                except Exception:
                                    pass

                    for h, mx in list(max_marks.items()):
                        if h not in min_marks and mx > 0:
                            min_marks[h] = math.ceil(0.40 * mx)

                    c = Course(
                        code=str(item.get("code", "")).strip().replace(" ", "").upper(),
                        name=str(item.get("name", "")).strip(),
                        heads=item.get("heads", []),
                        max_marks=max_marks,
                        min_marks=min_marks,
                        credits=float(item.get("credits", 0.0) or 0.0)
                    )
                    catalog.courses.append(c)

    meta.catalog = catalog
    return meta, catalog


def parse_student_response(
    raw_text: str,
    archetype: str,
    catalog: Optional[CourseCatalog] = None
) -> List[StudentRecord]:
    """
    Parse student records array from LLM response into normalized StudentRecord objects.
    """
    cleaned = clean_json_string(raw_text)
    if not cleaned:
        return []

    data = None
    try:
        data = json.loads(cleaned)
    except Exception:
        # Try to fix truncated JSON by finding last valid closing bracket
        for end_char in ["}]", "}"]:
            try:
                data = json.loads(cleaned + end_char)
                break
            except Exception:
                pass

    if not data:
        return []

    def _get_val(obj: dict, candidate_keys: List[str], default: str = "") -> str:
        for k in candidate_keys:
            if k in obj and obj[k] is not None:
                val = str(obj[k]).strip()
                if val:
                    return val
        # Case & punctuation agnostic match
        lower_map = {re.sub(r"[\s_\-]", "", str(k)).lower(): v for k, v in obj.items() if v is not None}
        for k in candidate_keys:
            clean_k = re.sub(r"[\s_\-]", "", k).lower()
            if clean_k in lower_map:
                val = str(lower_map[clean_k]).strip()
                if val:
                    return val
        return default

    # Handle both {"students": [...]} or raw list [...]
    items = []
    if isinstance(data, dict):
        items = data.get("students", [])
        if not items:
            for k in ["records", "student_records", "data", "results"]:
                if k in data and isinstance(data[k], list):
                    items = data[k]
                    break
        if not items and any(k in str(data.keys()).lower() for k in ["seat", "roll", "name", "student"]):
            items = [data]
    elif isinstance(data, list):
        items = data

    results: List[StudentRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        raw_seat = _get_val(item, ["seat_no", "Seat_No", "Roll_No", "roll_no", "SeatNo", "RollNo", "seat", "Seat", "roll", "Roll", "seat_number", "id"])
        seat_no = re.sub(r"[^\d]", "", raw_seat)

        # Fallback: scan item values for any 7 to 10 digit number
        if not seat_no:
            for v in item.values():
                m = re.search(r"\b\d{7,10}\b", str(v))
                if m:
                    seat_no = m.group(0)
                    break

        if not seat_no:
            continue

        raw_name = _get_val(item, ["student_name", "Student_Name", "Name", "name", "student", "Student", "studentName", "StudentName"])
        gender = _get_val(item, ["gender", "Gender", "sex", "Sex"]).upper()
        if raw_name.startswith("/"):
            gender = "F"
            raw_name = raw_name.lstrip("/").strip()
        elif not gender:
            gender = "M"

        student = StudentRecord(
            seat_no=seat_no,
            student_name=raw_name,
            gender=gender,
            candidate_type=_get_val(item, ["candidate_type", "Candidate_Type", "type", "Type"], "Regular"),
            prn=re.sub(r"[^\d]", "", _get_val(item, ["prn", "PRN", "PRN_NO", "prn_no"])),
            abc_id=re.sub(r"[^\d]", "", _get_val(item, ["abc_id", "ABC_ID", "abcid", "ABCID"])),
            ern=_get_val(item, ["ern", "ERN", "enrollment", "Enrollment"]),
            mother_name=_get_val(item, ["mother_name", "Mother_Name", "mother", "Mother"]),
            result_status=_get_val(item, ["result_status", "Result_Status", "result", "Result", "status", "Status", "Grade", "grade"]).upper(),
            remark=_get_val(item, ["remark", "Remark"], "None"),
            total_marks=clean_mark(_get_val(item, ["total_marks", "Total_Marks", "Marks_Obtained", "total", "Total"])),
            max_marks=clean_mark(_get_val(item, ["max_marks", "Max_Marks", "max", "Max"], "750")),
            credits_earned=clean_mark(_get_val(item, ["credits_earned", "Credits_Earned", "credits", "Credits", "EC", "Credit_Pt"])),
            total_cg=clean_mark(_get_val(item, ["total_cg", "Total_CG", "cg", "CG", "ECG", "CP*GP"])),
            sgpi=clean_mark(_get_val(item, ["sgpi", "SGPI", "gpa", "GPA", "GPA_Obtained"])),
            cgpi=clean_mark(_get_val(item, ["cgpi", "CGPI"])),
        )

        # Parse course results
        raw_courses = item.get("courses", {})
        if isinstance(raw_courses, dict):
            for c_code, c_data in raw_courses.items():
                if not isinstance(c_data, dict):
                    continue
                clean_code = c_code.strip().replace(" ", "").upper()
                c_name = ""
                if catalog:
                    c_obj = catalog.get_by_code(clean_code)
                    if c_obj:
                        c_name = c_obj.name

                marks_dict = {}
                grade = ""
                gp = ""
                credits_val = ""
                cg_val = ""

                for k, v in c_data.items():
                    k_clean = k.strip()
                    if k_clean.lower() == "grade":
                        grade = str(v).strip().upper()
                    elif k_clean.lower() in ("gp", "gradepoint", "grade_point"):
                        gp = clean_mark(v)
                    elif k_clean.lower() == "credits":
                        credits_val = clean_mark(v)
                    elif k_clean.lower() in ("cg", "c*g", "credit_points"):
                        cg_val = clean_mark(v)
                    elif k_clean.lower() == "subject":
                        if not c_name:
                            c_name = str(v).strip()
                    else:
                        marks_dict[k_clean] = clean_mark(v)

                cr = CourseResult(
                    course_code=clean_code,
                    course_name=c_name,
                    marks=marks_dict,
                    grade=grade,
                    grade_point=gp,
                    credits=credits_val,
                    credit_points=cg_val
                )
                student.course_results.append(cr)

        results.append(student)

    return results
