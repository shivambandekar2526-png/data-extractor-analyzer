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


def normalize_batch(batch_raw: Optional[str], exam_text: str = "", sem_num: Optional[int] = None) -> str:
    """
    Normalize batch / academic year string to canonical hyphenated format (e.g. '21-22', '22-23'):
    Handles:
      - Explicit academic ranges: '2021-2022', '2021-22', '21-22' -> '21-22'
      - Standalone 4-digit batch codes: '2122' -> '21-22', '2223' -> '22-23'
      - Exam sessions with calendar years:
          'Winter Session 2021' Sem 1 -> '21-22'
          'Summer 2022' Sem 2         -> '21-22'
          'Winter 2022' Sem 3         -> '22-23'
          'Summer 2023' Sem 4         -> '22-23'
    """
    combined_context = (str(batch_raw or "") + " " + str(exam_text or "")).upper()

    # 1. First look for explicit two-year academic ranges e.g. 2021-2022, 2021-22, 21-22, 2022/2023, 2024 - 2025
    cand_sources = [str(batch_raw or ""), str(exam_text or "")]
    for source in cand_sources:
        if not source:
            continue
        m_pair = re.search(r"(?<!\d)(\d{2,4})[\s_\-/]+(\d{2,4})(?!\d)", source)
        if m_pair:
            y1 = int(m_pair.group(1)[-2:])
            y2 = int(m_pair.group(2)[-2:])
            if 18 <= y1 <= 35 and (y2 == y1 + 1 or y2 == y1):
                return f"{y1:02d}-{y1+1:02d}"

    # 2. Look for 4-digit batch codes like 2122, 2223, 2324, 2425 where the first two digits are NOT '20'
    for source in cand_sources:
        if not source:
            continue
        m4 = re.search(r"\b(1[89]|2[1-9]|3[0-5])(1[89]|2[0-9]|3[0-5])\b", source)
        if m4:
            y1 = int(m4.group(1))
            y2 = int(m4.group(2))
            if y2 == y1 + 1:
                return f"{y1:02d}-{y2:02d}"

    # 3. Calendar Year detection (e.g. Winter 2021, Summer 2022, Dec 2023, May 2024)
    for source in cand_sources:
        if not source:
            continue
        m_single = re.search(r"\b20(1[8-9]|2[0-9]|3[0-5])\b", source)
        if m_single:
            yr = int(m_single.group(1))
            upper_text = combined_context
            is_winter = any(k in upper_text for k in ["SECOND HALF", "SH", "WINTER", "DEC", "NOV", "OCT"])
            is_summer = any(k in upper_text for k in ["FIRST HALF", "FH", "SUMMER", "MAY", "JUNE", "APR", "MARCH"])

            if is_winter:
                return f"{yr:02d}-{yr+1:02d}"
            elif is_summer:
                return f"{yr-1:02d}-{yr:02d}"
            elif sem_num:
                if sem_num % 2 == 1:
                    return f"{yr:02d}-{yr+1:02d}"
                else:
                    return f"{yr-1:02d}-{yr:02d}"
            else:
                return f"{yr:02d}-{yr+1:02d}"

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
            "Extract the following three classification fields:\n"
            "1. 'department': The branch/department name (e.g., 'Artificial Intelligence and Data Science', 'Computer Engineering', 'Information Technology', etc.)\n"
            "2. 'batch': The cohort batch / academic year in hyphenated format e.g. '21-22', '22-23', '23-24', '24-25'.\n"
            "   IMPORTANT: Winter/Second Half (SH) exams of year Y belong to batch Y-(Y+1) (e.g. Winter 2021 Sem 1 is '21-22').\n"
            "   Summer/First Half (FH) exams of year Y belong to batch (Y-1)-Y (e.g. Summer 2022 Sem 2 is '21-22').\n"
            "3. 'semester': The semester number (e.g., 'Semester 1', 'Semester 8', 'Sem VII', etc.)\n\n"
            "Return ONLY a clean JSON object in this exact structure:\n"
            "{\n"
            '  "department": "<Branch Name>",\n'
            '  "batch": "<Academic Year / Batch e.g. 21-22>",\n'
            '  "semester": "<Semester Number>"\n'
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

    # Normalize canonical results
    dept_canonical = normalize_department(raw_dept)
    sem_canonical = normalize_semester(raw_sem)
    sem_num = int(re.search(r"\d+", sem_canonical).group(0)) if re.search(r"\d+", sem_canonical) else 1
    batch_canonical = normalize_batch(raw_batch, exam_text=page_1_text, sem_num=sem_num)

    pdf_rel = f"pdf/{dept_canonical}/{batch_canonical}/{sem_canonical}.pdf"
    excel_rel = f"excel/{dept_canonical}/{batch_canonical}/{sem_canonical}.xlsx"

    return {
        "success": True,
        "department": dept_canonical,
        "batch": batch_canonical,
        "semester": sem_canonical,
        "pdf_rel_path": pdf_rel,
        "excel_rel_path": excel_rel,
        "classification_method": classification_method,
        "raw_ai": ai_data
    }


def check_duplicate_dataset(department: str, batch: str, semester: str, departments_dir: str) -> Dict[str, Any]:
    """
    Checks if a dataset with the same Department + Batch + Semester already exists.
    """
    pdf_full = os.path.abspath(os.path.join(departments_dir, "pdf", department, batch, f"{semester}.pdf"))
    excel_full = os.path.abspath(os.path.join(departments_dir, "excel", department, batch, f"{semester}.xlsx"))

    pdf_exists = os.path.isfile(pdf_full)
    excel_exists = os.path.isfile(excel_full)

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
    dest_folder = os.path.join(departments_dir, "pdf", department, batch)
    os.makedirs(dest_folder, exist_ok=True)
    dest_pdf_path = os.path.join(dest_folder, f"{semester}.pdf")

    if os.path.isfile(dest_pdf_path) and not overwrite:
        raise FileExistsError(
            f"Dataset PDF already exists at '{dest_pdf_path}'. Overwrite confirmation required."
        )

    # If source and destination are the exact same path, do not copy
    if os.path.abspath(source_pdf_path) != os.path.abspath(dest_pdf_path):
        shutil.copy2(source_pdf_path, dest_pdf_path)

    return dest_pdf_path


def get_canonical_excel_path(department: str, batch: str, semester: str, departments_dir: str) -> str:
    """Returns deterministic canonical path for Excel dataset."""
    dest_folder = os.path.join(departments_dir, "excel", department, batch)
    os.makedirs(dest_folder, exist_ok=True)
    return os.path.join(dest_folder, f"{semester}.xlsx")
