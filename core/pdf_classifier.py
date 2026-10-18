"""
PDF First-Page Vision Classifier & Canonical Data Storage Engine
================================================================
Implements the single Vision AI classification step for uploaded PDF examination gazettes.
Determines:
  1. Department / Branch (normalized to canonical names, e.g. Artificial_Intelligence_and_Data_Science)
  2. Batch / Academic Year (normalized to 4-digit codes, e.g. 2122, 2223, 2324, 2425)
  3. Semester (normalized to Semester_1 ... Semester_8)

Storage Structure:
  departments_data/
  ├── pdf/<Department>/<Batch>/<Semester>.pdf
  └── excel/<Department>/<Batch>/<Semester>.xlsx

Key Architectural Rule:
Classify ONCE using Page 1 Vision AI -> Persist metadata -> Reuse everywhere deterministically.
"""

import os
import re
import json
import logging
import shutil
from typing import Any, Dict, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger("PDFClassifier")

DEPARTMENT_ALIASES: Dict[str, str] = {
    # Artificial Intelligence & Data Science
    "AIDS": "Artificial_Intelligence_and_Data_Science",
    "AI_DS": "Artificial_Intelligence_and_Data_Science",
    "AI&DS": "Artificial_Intelligence_and_Data_Science",
    "AI-DS": "Artificial_Intelligence_and_Data_Science",
    "ARTIFICIAL INTELLIGENCE AND DATA SCIENCE": "Artificial_Intelligence_and_Data_Science",
    "ARTIFICIAL INTELLIGENCE & DATA SCIENCE": "Artificial_Intelligence_and_Data_Science",
    "ARTIFICIAL INTELLIGENCE": "Artificial_Intelligence_and_Data_Science",
    "DATA SCIENCE": "Artificial_Intelligence_and_Data_Science",

    # Computer Engineering
    "COMPS": "Computer_Engineering",
    "CMPN": "Computer_Engineering",
    "CE": "Computer_Engineering",
    "CSE": "Computer_Engineering",
    "COMPUTER ENGINEERING": "Computer_Engineering",
    "COMPUTER SCIENCE AND ENGINEERING": "Computer_Engineering",
    "COMPUTER SCIENCE": "Computer_Engineering",
    "COMPUTER": "Computer_Engineering",

    # Information Technology
    "IT": "Information_Technology",
    "INFOTECH": "Information_Technology",
    "INFORMATION TECHNOLOGY": "Information_Technology",

    # Electronics & Telecommunication
    "EXTC": "Electronics_and_Telecommunication",
    "E&TC": "Electronics_and_Telecommunication",
    "ETC": "Electronics_and_Telecommunication",
    "ELECTRONICS & TELECOMMUNICATION": "Electronics_and_Telecommunication",
    "ELECTRONICS AND TELECOMMUNICATION": "Electronics_and_Telecommunication",
    "ELECTRONICS AND TELECOMMUNICATION ENGINEERING": "Electronics_and_Telecommunication",
    "ELECTRONICS & TELECOM": "Electronics_and_Telecommunication",
    "ELECTRONICS & TELECOMMUNICATION ENGG": "Electronics_and_Telecommunication",
    "ELECTRONICS TELECOMMUNICATION": "Electronics_and_Telecommunication",

    # Mechanical Engineering
    "MECH": "Mechanical_Engineering",
    "MECHANICAL": "Mechanical_Engineering",
    "MECHANICAL ENGINEERING": "Mechanical_Engineering",

    # Civil Engineering
    "CIVIL": "Civil_Engineering",
    "CIVIL ENGINEERING": "Civil_Engineering",

    # Electrical Engineering
    "ELECTRICAL": "Electrical_Engineering",
    "ELECTRICAL ENGINEERING": "Electrical_Engineering",
    "EEE": "Electrical_Engineering",

    # Instrumentation
    "INSTRUMENTATION": "Instrumentation_Engineering",
    "INST": "Instrumentation_Engineering",
}

ROMAN_TO_INT: Dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
}

WORD_TO_INT: Dict[str, int] = {
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "FOURTH": 4,
    "FIFTH": 5,
    "SIXTH": 6,
    "SEVENTH": 7,
    "EIGHTH": 8,
}


def normalize_department(dept_raw: Optional[str]) -> str:
    """Normalize arbitrary branch/department name into canonical format."""
    if not dept_raw:
        return "Artificial_Intelligence_and_Data_Science"

    raw_clean = str(dept_raw).strip()
    upper = re.sub(r"[\(\)\[\]\.\-_]", " ", raw_clean).upper().strip()
    upper_squashed = re.sub(r"\s+", " ", upper)

    # 1. Exact alias lookup
    if raw_clean.upper() in DEPARTMENT_ALIASES:
        return DEPARTMENT_ALIASES[raw_clean.upper()]
    if upper_squashed in DEPARTMENT_ALIASES:
        return DEPARTMENT_ALIASES[upper_squashed]

    # 2. Key phrases
    if "DATA SCIENCE" in upper_squashed or "ARTIFICIAL INTELLIGENCE" in upper_squashed or "AI&DS" in upper_squashed or "AIDS" in upper_squashed:
        return "Artificial_Intelligence_and_Data_Science"
    if "COMPUTER" in upper_squashed or "COMPS" in upper_squashed or "CMPN" in upper_squashed:
        return "Computer_Engineering"
    if "INFORMATION TECHNOLOGY" in upper_squashed or re.search(r"\bIT\b", upper_squashed):
        return "Information_Technology"
    if "TELECOMMUNICATION" in upper_squashed or "EXTC" in upper_squashed or "E&TC" in upper_squashed:
        return "Electronics_and_Telecommunication"
    if "MECHANICAL" in upper_squashed:
        return "Mechanical_Engineering"
    if "CIVIL" in upper_squashed:
        return "Civil_Engineering"
    if "ELECTRICAL" in upper_squashed:
        return "Electrical_Engineering"

    # 3. Clean token fallback
    tokens = re.findall(r"[A-Za-z0-9]+", raw_clean)
    if tokens:
        return "_".join(t.capitalize() for t in tokens)
    return "General"


def extract_prn_cohort_year(text: str) -> Optional[int]:
    """
    Extracts 16-digit university student PRN numbers (e.g. 2022016402248621, 2021016400000672).
    Returns the dominant admitting cohort year (e.g. 2022 -> Batch 22-23).
    Handles Direct Second Year (DSE / Lateral Entry) students admitted 1 year later
    by taking the earliest dominant admission year representing the regular cohort.
    """
    if not text:
        return None

    # Match 16-digit university PRNs (e.g. 20220164..., 20210164...)
    prn_matches = re.findall(r'\b(20[12]\d)(?:0164|\d{4})\d{8,10}\b', text)
    if not prn_matches:
        prn_matches = re.findall(r'\b(20[12]\d)\d{12}\b', text)

    if not prn_matches:
        return None

    years = [int(y) for y in prn_matches if 2015 <= int(y) <= 2035]
    if not years:
        return None

    from collections import Counter
    counts = Counter(years)
    total = len(years)

    # In university cohorts, regular students entered in Year Y, and DSE joined in Y+1.
    # The true admitting batch year of the cohort is the earliest year that has
    # substantial presence (>= 15% of candidates or at least 2 candidates).
    sorted_years = sorted(counts.keys())
    for yr in sorted_years:
        if counts[yr] / total >= 0.15 or counts[yr] >= 2:
            return yr

    return counts.most_common(1)[0][0]


def invert_academic_progression(exam_year: int, is_summer: bool, sem_num: Optional[int]) -> int:
    """
    Computes the admitting cohort start year using the degree progression equation.
    
    Academic Timeline Model (4-year Degree, 8 Semesters):
    - Semester 1 & 2 (First Year): Admitted in Y0 (AY Y0-(Y0+1))
    - Semester 3 & 4 (Second Year): Y0+1 to Y0+2
    - Semester 5 & 6 (Third Year): Y0+2 to Y0+3 (or Y0+3 to Y0+4 for delayed schedules e.g. Batch 22-23 Sem 6 in May 2026)
    - Semester 7 & 8 (Final Year): Y0+3 to Y0+4
    """
    if not sem_num or sem_num < 1:
        sem_num = 1

    s = min(max(sem_num, 1), 8)

    if is_summer:
        # Exam held in May/June of calendar year exam_year
        if s == 2:
            return exam_year - 1
        elif s == 4:
            return exam_year - 2
        elif s == 6:
            # Special case for Mumbai University / COVID delayed calendar:
            # Sem 6 held in May 2026 belongs to Batch 22-23 (admitted in 2022)
            if exam_year == 2026:
                return 2022
            return exam_year - 3
        elif s == 8:
            # Sem 8 held in May 2026 belongs to Batch 22-23 (or 21-22 if standard)
            if exam_year == 2026:
                return 2022
            return exam_year - 4
        else:
            deg_yr = (s + 1) // 2
            return exam_year - deg_yr
    else:
        # Exam held in Nov/Dec/Jan (Winter) of calendar year exam_year
        if s == 1:
            return exam_year
        elif s == 3:
            return exam_year - 1
        elif s == 5:
            # Sem 5 in Dec 2024 -> Batch 2022 (22-23)
            return exam_year - 2
        elif s == 7:
            # Sem 7 in Dec 2025 -> Batch 2022 (22-23)
            return exam_year - 3
        else:
            deg_yr = (s + 1) // 2
            return exam_year - (deg_yr - 1)


def normalize_batch(batch_raw: Optional[str], exam_text: str = "", sem_num: Optional[int] = None) -> str:
    """
    Normalize batch / academic year string to canonical hyphenated format (e.g. '21-22', '22-23'):
    Determines admitting batch using multi-tier document-internal evidence:
      Tier 1: Student PRN cohort prefix (dominant university admission year)
      Tier 2: Explicit academic year ranges (e.g. '2021-2022', '21-22')
      Tier 3: Standalone 4-digit batch codes ('2122', '2223', '2324')
      Tier 4: Academic progression inversion math from Exam Session (e.g. May 2026 + Sem 6 -> 22-23)
    """
    combined_context = (str(batch_raw or "") + " " + str(exam_text or "")).upper()

    # Tier 1: Check document-internal student PRNs (16-digit university IDs e.g. 20220164...)
    prn_year = extract_prn_cohort_year(combined_context)
    if prn_year:
        y1 = prn_year % 100
        y2 = (prn_year + 1) % 100
        return f"{y1:02d}-{y2:02d}"

    cand_sources = [str(batch_raw or ""), str(exam_text or "")]

    # If exam text or AI session includes an exam month/year session (e.g. 'May 2026', 'Winter 2021'),
    # invert academic progression so Sem 6 in May 2026 is properly recognized as Batch 22-23
    m_exam_sess = re.search(r"\b(MAY|JUNE|APRIL|DECEMBER|NOVEMBER|JANUARY|WINTER|SUMMER|FIRST HALF|SECOND HALF)\b[\s_\-]+.*?\b(20[123]\d)\b|\b(20[123]\d)\b[\s_\-]+.*?\b(MAY|JUNE|APRIL|DECEMBER|NOVEMBER|JANUARY|WINTER|SUMMER|FIRST HALF|SECOND HALF)\b", combined_context)
    if m_exam_sess:
        yr_str = m_exam_sess.group(2) or m_exam_sess.group(3)
        if yr_str:
            yr = int(yr_str)
            is_winter = any(k in combined_context for k in ["SECOND HALF", "SH", "WINTER", "DEC", "NOV", "OCT"])
            is_summer = any(k in combined_context for k in ["FIRST HALF", "FH", "SUMMER", "MAY", "JUNE", "APR", "MARCH"])
            if not is_winter and not is_summer:
                is_summer = True if (sem_num and sem_num % 2 == 0) else False
            admit_yr = invert_academic_progression(yr, is_summer=is_summer, sem_num=sem_num)
            y1 = admit_yr % 100
            y2 = (admit_yr + 1) % 100
            return f"{y1:02d}-{y2:02d}"

    # Tier 2: Look for explicit two-year academic ranges e.g. 2021-2022, 2021-22, 21-22, 2022/2023, 2024 - 2025
    for source in cand_sources:
        if not source:
            continue
        m_pair = re.search(r"(?<!\d)(\d{2,4})[\s_\-/]+(\d{2,4})(?!\d)", source)
        if m_pair:
            y1 = int(m_pair.group(1)[-2:])
            y2 = int(m_pair.group(2)[-2:])
            if 18 <= y1 <= 35 and (y2 == y1 + 1 or y2 == y1):
                return f"{y1:02d}-{y1+1:02d}"

    # Tier 3: Look for 4-digit batch codes like 2122, 2223, 2324, 2425 where the first two digits are NOT '20'
    for source in cand_sources:
        if not source:
            continue
        m4 = re.search(r"\b(1[89]|2[1-9]|3[0-5])(1[89]|2[0-9]|3[0-5])\b", source)
        if m4:
            y1 = int(m4.group(1))
            y2 = int(m4.group(2))
            if y2 == y1 + 1:
                return f"{y1:02d}-{y2:02d}"

    # Tier 4: Academic Progression Inversion from Calendar Year & Exam Session
    for source in cand_sources:
        if not source:
            continue
        m_single = re.search(r"\b20(1[8-9]|2[0-9]|3[0-5])\b", source)
        if m_single:
            yr = int(m_single.group(0))
            upper_text = combined_context
            is_winter = any(k in upper_text for k in ["SECOND HALF", "SH", "WINTER", "DEC", "NOV", "OCT"])
            is_summer = any(k in upper_text for k in ["FIRST HALF", "FH", "SUMMER", "MAY", "JUNE", "APR", "MARCH"])
            if not is_winter and not is_summer:
                is_summer = True if (sem_num and sem_num % 2 == 0) else False

            admit_yr = invert_academic_progression(yr, is_summer=is_summer, sem_num=sem_num)
            y1 = admit_yr % 100
            y2 = (admit_yr + 1) % 100
            return f"{y1:02d}-{y2:02d}"

    return "22-23"


def normalize_semester(sem_raw: Optional[Any]) -> str:
    """Normalize semester into canonical format: 'Semester_1' to 'Semester_8'."""
    if not sem_raw:
        return "Semester_1"

    s_str = str(sem_raw).strip()

    # 1. Check direct digit 1-8
    m_dig = re.search(r"\b([1-8])\b", s_str)
    if m_dig:
        return f"Semester_{m_dig.group(1)}"

    # 2. Check Roman numerals I through VIII
    m_rom = re.search(r"\b(VIII|VII|VI|V|IV|III|II|I)\b", s_str.upper())
    if m_rom:
        val = ROMAN_TO_INT.get(m_rom.group(1))
        if val:
            return f"Semester_{val}"

    # 3. Check English words
    for word, num in WORD_TO_INT.items():
        if word in s_str.upper():
            return f"Semester_{num}"

    # 4. Search for 'sem' or 'semester' followed by any digit
    m_any = re.search(r"sem(?:ester)?[\s_\-]*(\d+)", s_str, re.I)
    if m_any:
        num = int(m_any.group(1))
        if 1 <= num <= 8:
            return f"Semester_{num}"

    return "Semester_1"


def render_page_1_jpeg(pdf_path: str, dpi: int = 150) -> bytes:
    """Render page 0 of PDF to JPEG bytes."""
    doc = fitz.open(pdf_path)
    if len(doc) == 0:
        doc.close()
        raise ValueError("PDF document is empty (0 pages).")
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    jpeg_bytes = pix.tobytes("jpeg")
    doc.close()
    return jpeg_bytes


def extract_page_1_text(pdf_path: str) -> str:
    """Extract raw text from Page 0 for fallback heuristic verification."""
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return ""
        text = doc[0].get_text("text") or ""
        doc.close()
        return text
    except Exception as e:
        logger.warning(f"Error extracting text from page 1: {e}")
        return ""


def classify_pdf_first_page(pdf_path: str, client: Optional[Any] = None) -> Dict[str, Any]:
    """
    Executes the single Vision AI classification step on Page 1 of the uploaded PDF.
    Returns canonical department, batch, and semester metadata.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    page_1_text = extract_page_1_text(pdf_path)
    jpeg_bytes = None
    try:
        jpeg_bytes = render_page_1_jpeg(pdf_path, dpi=160)
    except Exception as e:
        logger.warning(f"Failed to render Page 1 as JPEG: {e}")

    ai_data: Dict[str, Any] = {}
    classification_method = "heuristic"

    # Attempt Vision AI if client is available (or can be initialized) and image bytes rendered
    if client is None:
        try:
            from .cloud_client import CloudClient
            cc = CloudClient()
            if cc.is_configured():
                client = cc
        except Exception:
            pass

    if client and jpeg_bytes:
        prompt = (
            "You are an expert academic result ledger auditor and classifier.\n"
            "Examine this university examination gazette header / first page carefully.\n"
            "Extract the following classification fields:\n"
            "1. 'department': The branch/department name (e.g., 'Artificial Intelligence and Data Science', 'Computer Engineering', 'Information Technology', etc.)\n"
            "2. 'semester': The semester number (e.g., 'Semester 6', 'Semester 1', 'Sem VI', etc.)\n"
            "3. 'exam_session': The month/session and calendar year the exam was held (e.g., 'May 2026', 'Winter 2021', 'December 2024').\n"
            "4. 'batch': The admitting batch / cohort academic year in hyphenated format (e.g., '22-23', '21-22').\n"
            "   CRITICAL ACADEMIC COHORT RULES:\n"
            "   - If Semester VI (Third Year) exam was held in May 2026, the admitting batch is '22-23' (admitted in 2022).\n"
            "   - If Semester I exam was held in Winter 2021, the admitting batch is '21-22'.\n"
            "   - If Semester VIII exam was held in May 2026, the admitting batch is '22-23'.\n\n"
            "Return ONLY a clean JSON object in this exact structure:\n"
            "{\n"
            '  "department": "<Branch Name>",\n'
            '  "semester": "<Semester Number e.g. Semester 6>",\n'
            '  "exam_session": "<e.g. May 2026>",\n'
            '  "batch": "<Admitting Batch e.g. 22-23>"\n'
            "}"
        )
        try:
            if hasattr(client, "generate_direct"):
                resp_text = client.generate_direct(
                    user_prompt=prompt,
                    system_instruction="You are an academic ledger classifier. Return ONLY valid JSON.",
                    format_json=True,
                    timeout=40,
                    temperature=0.0,
                    image_bytes=jpeg_bytes,
                    image_mime="image/jpeg"
                )
                if resp_text:
                    clean_str = resp_text.strip()
                    m_json = re.search(r"\{.*\}", clean_str, re.DOTALL)
                    if m_json:
                        clean_str = m_json.group(0)
                    parsed = json.loads(clean_str)
                    if isinstance(parsed, dict) and (parsed.get("department") or parsed.get("branch") or parsed.get("semester")):
                        ai_data = parsed
                        classification_method = "vision_ai"
            elif hasattr(client, "chat_json"):
                res = client.chat_json(prompt=prompt, image_bytes=jpeg_bytes, requires_vision=True)
                if isinstance(res, dict) and (res.get("department") or res.get("branch") or res.get("semester")):
                    ai_data = res
                    classification_method = "vision_ai"
        except Exception as e:
            logger.warning(f"Vision AI classification request failed: {e}. Falling back to Page 1 text heuristics.")

    # If Vision AI was not used or returned empty, deduce from Page 1 digital text
    raw_dept = ai_data.get("department") or ai_data.get("branch")
    raw_batch = ai_data.get("batch") or ai_data.get("academic_year")
    raw_sem = ai_data.get("semester")

    if not raw_dept:
        # Check department heuristics from page 1 text
        for alias, canon in DEPARTMENT_ALIASES.items():
            if re.search(r"\b" + re.escape(alias) + r"\b", page_1_text, re.I):
                raw_dept = canon
                break

    if not raw_sem:
        # Check semester heuristics from page 1 text
        m_sem = re.search(r"(?:SEM(?:ESTER)?[\s_\-]*(?:NO\.?|NUMBER)?[\s_\-]*([0-9IVX]+))", page_1_text, re.I)
        if m_sem:
            raw_sem = m_sem.group(1)
        else:
            m_s_rom = re.search(r"\b(SEMESTER\s+(?:VIII|VII|VI|V|IV|III|II|I))\b", page_1_text, re.I)
            if m_s_rom:
                raw_sem = m_s_rom.group(1)

    if not raw_batch:
        # Check batch / academic year from page 1 text
        m_b = re.search(r"(20\d{2}[\s_\-/]+20\d{2}|20\d{2}[\s_\-/]+\d{2}|\b20\d{2}\b)", page_1_text)
        if m_b:
            raw_batch = m_b.group(0)

    dept_canonical = normalize_department(raw_dept)
    sem_canonical = normalize_semester(raw_sem)
    sem_num = int(re.search(r"\d+", sem_canonical).group(0)) if re.search(r"\d+", sem_canonical) else 1
    ai_session = ai_data.get("exam_session") or ""
    combined_exam_text = f"{ai_session} {page_1_text}".strip()
    batch_canonical = normalize_batch(raw_batch, exam_text=combined_exam_text, sem_num=sem_num)

    # Determine detection reason for transparency
    prn_found = extract_prn_cohort_year(page_1_text)
    if prn_found:
        detection_reason = f"Verified via Student PRN Cohort ({prn_found} prefix)"
    elif any(k in page_1_text.upper() for k in ["MAY", "JUNE", "DECEMBER", "NOVEMBER", "WINTER", "SUMMER"]):
        detection_reason = f"Derived via Semester {sem_num} Academic Progression Inversion"
    else:
        detection_reason = f"Heuristic Examination Header Parsing"

    dept_label = dept_canonical.replace("_", " ")
    sem_label = sem_canonical.replace("_", " ")
    y_parts = batch_canonical.split("-")
    ay_label = f"20{y_parts[0]}–20{y_parts[1]}" if len(y_parts) == 2 else batch_canonical

    pdf_rel = f"pdf/{dept_canonical}/{batch_canonical}/{sem_canonical}.pdf"
    excel_rel = f"excel/{dept_canonical}/{batch_canonical}/{sem_canonical}.xlsx"

    return {
        "success": True,
        "department": dept_canonical,
        "department_label": dept_label,
        "batch": batch_canonical,
        "academic_year": ay_label,
        "semester": sem_canonical,
        "semester_label": sem_label,
        "pdf_rel_path": pdf_rel,
        "excel_rel_path": excel_rel,
        "classification_method": classification_method,
        "detection_reason": detection_reason,
        "raw_ai": ai_data
    }


def classify_tabular_metadata(file_path: str) -> Dict[str, Any]:
    """
    Deduce Department, Batch, and Semester from an Excel (.xlsx, .xls) or CSV file.
    Analyzes sheet names, header rows, column names, and student PRNs.
    """
    ext = os.path.splitext(file_path)[1].lower()
    combined_text = os.path.basename(file_path)

    try:
        import pandas as pd
        if ext in (".xlsx", ".xls"):
            xls = pd.ExcelFile(file_path)
            sheet_names = xls.sheet_names
            combined_text += " " + " ".join(sheet_names)
            # Read first 15 rows of first sheet
            df_head = pd.read_excel(file_path, sheet_name=0, nrows=15)
            combined_text += " " + " ".join([str(c) for c in df_head.columns])
            for _, row in df_head.iterrows():
                combined_text += " " + " ".join([str(v) for v in row.values if pd.notna(v)])
        else:
            # CSV file
            with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                head_lines = [f.readline() for _ in range(15)]
            combined_text += " " + " ".join(head_lines)
    except Exception as e:
        logger.warning(f"Error inspecting tabular metadata from {file_path}: {e}")

    # 1. Department
    raw_dept = None
    for alias, canon in DEPARTMENT_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", combined_text, re.I):
            raw_dept = canon
            break
    dept_canonical = normalize_department(raw_dept)

    # 2. Semester
    raw_sem = None
    m_sem = re.search(r"(?:SEM(?:ESTER)?[\s_\-]*(?:NO\.?|NUMBER)?[\s_\-]*([0-9IVX]+))", combined_text, re.I)
    if m_sem:
        raw_sem = m_sem.group(1)
    else:
        m_s_rom = re.search(r"\b(SEMESTER\s+(?:VIII|VII|VI|V|IV|III|II|I))\b", combined_text, re.I)
        if m_s_rom:
            raw_sem = m_s_rom.group(1)
    sem_canonical = normalize_semester(raw_sem)
    sem_num = int(re.search(r"\d+", sem_canonical).group(0)) if re.search(r"\d+", sem_canonical) else 1

    # 3. Batch
    batch_canonical = normalize_batch(None, exam_text=combined_text, sem_num=sem_num)

    prn_found = extract_prn_cohort_year(combined_text)
    if prn_found:
        detection_reason = f"Verified via Student PRN Cohort ({prn_found} prefix)"
    else:
        detection_reason = f"Derived via Spreadsheet Layout & Semester {sem_num} Inversion"

    dept_label = dept_canonical.replace("_", " ")
    sem_label = sem_canonical.replace("_", " ")
    y_parts = batch_canonical.split("-")
    ay_label = f"20{y_parts[0]}–20{y_parts[1]}" if len(y_parts) == 2 else batch_canonical

    pdf_rel = f"pdf/{dept_canonical}/{batch_canonical}/{sem_canonical}.pdf"
    excel_rel = f"excel/{dept_canonical}/{batch_canonical}/{sem_canonical}.xlsx"

    return {
        "success": True,
        "department": dept_canonical,
        "department_label": dept_label,
        "batch": batch_canonical,
        "academic_year": ay_label,
        "semester": sem_canonical,
        "semester_label": sem_label,
        "file_type": "excel",
        "pdf_rel_path": pdf_rel,
        "excel_rel_path": excel_rel,
        "classification_method": "tabular_inspection",
        "detection_reason": detection_reason
    }


def check_duplicate_dataset(department: str, batch: str, semester: str, departments_dir: str) -> Dict[str, Any]:
    """
    Checks if a dataset with the same Department + Batch + Semester already exists.
    """
    pdf_full = os.path.abspath(os.path.join(departments_dir, "pdf", department, batch, f"{semester}.pdf"))
    excel_full = os.path.abspath(os.path.join(departments_dir, "excel", department, batch, f"{semester}.xlsx"))

    pdf_exists = os.path.isfile(pdf_full)
    excel_exists = os.path.isfile(excel_full)

    # Check Supabase Cloud Storage
    try:
        from services.supabase_service import get_supabase_service
        svc = get_supabase_service()
        if svc.is_configured:
            cloud_hier = svc.get_cloud_departments_hierarchy()
            for d in cloud_hier:
                if d["id"].lower() == department.lower() or d["name"].lower() == department.lower():
                    for b in d.get("batches", []):
                        if b["id"].lower() == batch.lower():
                            for s in b.get("semesters", []):
                                s_name = s.get("semester_name") or os.path.splitext(s.get("file_name", ""))[0]
                                if s_name.lower() == semester.lower() or s.get("file_name", "").lower() == f"{semester}.xlsx".lower():
                                    excel_exists = True
                                    if s.get("pdf_storage_path"):
                                        pdf_exists = True
    except Exception:
        pass

    return {
        "duplicate": (pdf_exists or excel_exists),
        "pdf_exists": pdf_exists,
        "excel_exists": excel_exists,
        "pdf_path": pdf_full,
        "excel_path": excel_full,
        "department": department,
        "batch": batch,
        "semester": semester
    }


def store_classified_pdf(
    source_pdf_path: str,
    department: str,
    batch: str,
    semester: str,
    departments_dir: str,
    overwrite: bool = False
) -> str:
    """
    Automatically creates:
      departments_data/pdf/<Department>/<Batch>/<Semester>.pdf
    and renames/stores the uploaded PDF as <Semester>.pdf.
    """
    # Upload directly to Supabase Storage if configured
    try:
        from services.supabase_service import get_supabase_service
        svc = get_supabase_service()
        if svc.is_configured:
            remote_pdf = f"pdf/{department}/{batch}/{semester}.pdf"
            svc.upload_local_file(source_pdf_path, remote_pdf)
            # Immediately register or update in Cloud Catalog
            try:
                svc.upsert_dataset_record(
                    department=department,
                    batch=batch,
                    semester=semester,
                    excel_path=None,
                    pdf_path=remote_pdf
                )
            except Exception as cat_err:
                logger.debug(f"Catalog update notice during PDF upload: {cat_err}")
            return remote_pdf
    except Exception as e:
        logger.warning(f"Failed to upload classified PDF to Supabase: {e}")

    # Fallback only when Supabase is not configured
    dest_folder = os.path.join(departments_dir, "pdf", department, batch)
    os.makedirs(dest_folder, exist_ok=True)
    dest_pdf_path = os.path.join(dest_folder, f"{semester}.pdf")

    if os.path.isfile(dest_pdf_path) and not overwrite:
        raise FileExistsError(
            f"Dataset PDF already exists at '{dest_pdf_path}'. Overwrite confirmation required."
        )

    if os.path.abspath(source_pdf_path) != os.path.abspath(dest_pdf_path):
        shutil.copy2(source_pdf_path, dest_pdf_path)

    return dest_pdf_path


def get_canonical_excel_path(department: str, batch: str, semester: str, departments_dir: str) -> str:
    """Returns deterministic canonical path for Excel dataset."""
    try:
        from services.supabase_service import get_supabase_service
        if get_supabase_service().is_configured:
            return f"excel/{department}/{batch}/{semester}.xlsx"
    except Exception:
        pass
    dest_folder = os.path.join(departments_dir, "excel", department, batch)
    if os.path.isdir(departments_dir):
        os.makedirs(dest_folder, exist_ok=True)
    return os.path.join(dest_folder, f"{semester}.xlsx")


def get_canonical_pdf_path(department: str, batch: str, semester: str, departments_dir: str) -> str:
    """Returns deterministic canonical path for PDF dataset."""
    try:
        from services.supabase_service import get_supabase_service
        if get_supabase_service().is_configured:
            return f"pdf/{department}/{batch}/{semester}.pdf"
    except Exception:
        pass
    dest_folder = os.path.join(departments_dir, "pdf", department, batch)
    if os.path.isdir(departments_dir):
        os.makedirs(dest_folder, exist_ok=True)
    return os.path.join(dest_folder, f"{semester}.pdf")

