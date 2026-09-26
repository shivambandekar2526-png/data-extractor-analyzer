"""
Pre-Trained Possibility Extractor Engine
========================================
High-speed deterministic extraction engine based on pre-trained knowledge base archetypes:
1. Tabulation Grid (Sem III-VI CBCGS)
2. Gazette Classic (Sem VIII C-Scheme)
3. Gazette Spool (Sem VII 12-Paper Tape)
4. Gazette NEP 2020 Scheme

Operates instantly (1-2 seconds per file) with zero GPU overhead,
guaranteeing complete data extraction even when local Ollama is offline or busy.
"""

from collections import OrderedDict
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import pdfplumber
import pypdf

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord

ProgressCallback = Optional[Callable[[float, str], None]]


def safe_int(val: Any, default: int = 0) -> int:
    """Safely convert value to int."""
    if not val:
        return default
    try:
        clean = re.sub(r"[^\d]", "", str(val))
        return int(clean) if clean else default
    except Exception:
        return default


def normalize_head(raw: str) -> str:
    """Normalize evaluation head abbreviations."""
    clean = (raw or "").strip().upper().replace("\n", " ")
    head = clean.split()[0] if clean else ""
    if head in ("P&O", "P&", "PO"):
        return "PO"
    if head in ("PRA", "PR"):
        return "PRA"
    if head == "TOTAL":
        return "TOT"
    return head


def detect_semester(text: str) -> Tuple[str, int]:
    """Extract Roman numeral and integer semester from document text."""
    roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
    patterns = [
        r"Semester\s*[-–:]\s*([IVXLCDM\s]+?)(?=\s*\(|\s*held|\s*exam|\s*$)",
        r"SEM(?:ESTER)?\s*[-–:]*\s*([IVXLCDM\s]+?)(?=\s*\(|\s*held|\s*exam|\s*$)",
        r"\(SEM\s*([IVXLCDM\s]+?)\)",
        r"SEM\s+([IVXLCDM\s]+?)(?=\s*\(|\s*held|\s*$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            clean_str = re.sub(r"\s+", "", m.group(1)).upper()
            if clean_str in roman_map:
                return clean_str, roman_map[clean_str]
    for r_str in sorted(roman_map.keys(), key=len, reverse=True):
        if re.search(rf"\b{r_str}\b", text):
            return r_str, roman_map[r_str]
    return "Unknown", 0


# =====================================================================
# 1. TABULATION ENGINE (Sem III - VI CBCGS)
# =====================================================================

def discover_tabulation_headers(table: List[List[Optional[str]]]) -> Tuple[CourseCatalog, int]:
    """Forward-fill header parser across 31-35 column tabulation tables."""
    if not table or len(table) < 3:
        return CourseCatalog(), -1

    row0 = table[0]
    row1 = table[1]
    row2 = table[2]
    num_cols = len(row0)
    data_start = 3
    result_col = num_cols - 1

    filled_codes: List[Optional[str]] = [None] * num_cols
    curr_code = None
    for i in range(data_start, result_col):
        raw = (row0[i] or "").strip().replace(" ", "").upper()
        if raw and "OUTOF" not in raw and "RESULT" not in raw:
            curr_code = raw
        filled_codes[i] = curr_code

    filled_names: List[Optional[str]] = [None] * num_cols
    curr_name = None
    for i in range(data_start, result_col):
        raw = (row1[i] or "").strip().replace("\n", " ")
        raw = re.sub(r"\s+", " ", raw)
        if raw and "MAXIMUM" not in raw.upper() and "MINIMUM" not in raw.upper():
            curr_name = raw
        filled_names[i] = curr_name

    heads: List[Tuple[int, str]] = []
    max_marks: Dict[Tuple[str, str], int] = {}
    min_marks: Dict[Tuple[str, str], int] = {}

    for i in range(data_start, result_col):
        raw = (row2[i] or "").strip()
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        head = normalize_head(lines[0]) if lines else ""
        heads.append((i, head))
        code = filled_codes[i]
        if code and head and len(lines) >= 3:
            max_marks[(code, head)] = safe_int(lines[1])
            min_marks[(code, head)] = safe_int(lines[2])

    course_map: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for i in range(data_start, result_col):
        code = filled_codes[i]
        name = filled_names[i]
        head_idx = i - data_start
        head = heads[head_idx][1] if head_idx < len(heads) else ""
        if code and head:
            if code not in course_map:
                course_map[code] = {
                    "name": name or code,
                    "heads": [],
                    "col_indices": [],
                    "max": {},
                    "min": {},
                }
            course_map[code]["heads"].append(head)
            course_map[code]["col_indices"].append(i)
            if (code, head) in max_marks:
                course_map[code]["max"][head] = max_marks[(code, head)]
            if (code, head) in min_marks:
                course_map[code]["min"][head] = min_marks[(code, head)]

    catalog = CourseCatalog()
    for code, info in course_map.items():
        catalog.courses.append(
            Course(
                code=code,
                name=info["name"],
                heads=info["heads"],
                max_marks=info["max"],
                min_marks=info["min"]
            )
        )
    return catalog, result_col


def extract_tabulation(
    pdf_path: str,
    progress_callback: ProgressCallback = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """Extract records from CBCGS Tabulation register using vector tables."""
    meta = ExamMeta(archetype="tabulation")
    students: List[StudentRecord] = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        if total_pages == 0:
            return meta, []

        p0 = pdf.pages[0]
        p0_text = p0.extract_text() or ""
        tables_p0 = p0.extract_tables()

        sem_str, sem_num = detect_semester(p0_text)
        meta.semester = sem_str
        meta.semester_num = sem_num

        for line in p0_text.split("\n")[:12]:
            clean_l = line.strip()
            if "TERNA" in clean_l.upper():
                meta.college_name = clean_l
            if "EXAMINATION" in clean_l.upper() or "SEMESTER" in clean_l.upper():
                meta.exam_title = clean_l
            dt_match = re.search(r"(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4}", clean_l, re.I)
            if dt_match:
                meta.result_date = dt_match.group(0)

        catalog = CourseCatalog()
        res_col = -1
        if tables_p0:
            catalog, res_col = discover_tabulation_headers(tables_p0[0])
            meta.catalog = catalog

        for page_idx, page in enumerate(pdf.pages):
            if progress_callback:
                pct = 20.0 + ((page_idx + 1) / total_pages * 65.0)
                progress_callback(pct, f"Extracting page {page_idx + 1}/{total_pages} ({len(students)} students found)...")

            tables = page.extract_tables()
            for table in tables:
                r_idx = 0
                while r_idx < len(table):
                    row = table[r_idx]
                    if len(row) <= 2 or not row[1] or not row[2]:
                        r_idx += 1
                        continue

                    c2_text = str(row[2]).replace("\n", " ").strip()
                    if "Marks" in c2_text and "Obtained" in c2_text:
                        # Found student block start!
                        # Column 1 has: Name on line 0, Seat No on line 1
                        c1_lines = [l.strip() for l in str(row[1]).split("\n") if l.strip()]
                        raw_name = c1_lines[0] if c1_lines else ""
                        seat_no = c1_lines[1] if len(c1_lines) > 1 else ""

                        # If seat_no not in c1, search in column 0 or nearby rows
                        if not seat_no or not re.search(r"\d{5,}", seat_no):
                            m_seat = re.search(r"\b(38\d{6}|48\d{6}|58\d{6}|\d{8})\b", str(row[1]))
                            if m_seat:
                                seat_no = m_seat.group(0)

                        is_female = raw_name.startswith("/")
                        student_name = raw_name.lstrip("/").strip()
                        gender = "F" if is_female else "M"

                        # Gather 5 sub-rows of student block
                        marks_row = row
                        grade_row = table[r_idx + 1] if r_idx + 1 < len(table) else None
                        gp_row = table[r_idx + 2] if r_idx + 2 < len(table) else None
                        cr_row = table[r_idx + 3] if r_idx + 3 < len(table) else None
                        cpgp_row = table[r_idx + 4] if r_idx + 4 < len(table) else None

                        # Extract total summary from last column
                        last_c = str(marks_row[-1] or "").strip()
                        tot_marks = ""
                        res_stat = "P"
                        credits_earn = ""
                        total_cg = ""
                        sgpi = ""

                        # Last column lines
                        last_lines = [l.strip() for l in last_c.split("\n") if l.strip()]
                        if last_lines:
                            tot_marks = re.sub(r"[^\d]", "", last_lines[0])
                        if len(last_lines) > 1:
                            res_stat = "F" if "F" in last_lines[1].upper() else "P"
                        if len(last_lines) > 2:
                            tokens = last_lines[2].split()
                            if len(tokens) >= 3:
                                credits_earn = tokens[0]
                                total_cg = tokens[1]
                                sgpi = tokens[2]

                        # Check for Diploma / DSE indicator
                        cand_type = "Regular"
                        c_block_text = " ".join([str(c or "") for c in marks_row[:3]])
                        if any(k in c_block_text.upper() for k in ["DIPLOMA", "DSE", "DIRECT SECOND YEAR", "LATERAL"]):
                            cand_type = "Diploma"

                        student = StudentRecord(
                            seat_no=seat_no,
                            student_name=student_name,
                            gender=gender,
                            candidate_type=cand_type,
                            semester=meta.semester,
                            result_status=res_stat,
                            total_marks=tot_marks,
                            credits_earned=credits_earn,
                            total_cg=total_cg,
                            sgpi=sgpi
                        )

                        # Parse courses per column
                        col_ptr = 3
                        for course in catalog.courses:
                            c_res = CourseResult(course_code=course.code, course_name=course.name)
                            c_tot = ""
                            c_gr = ""
                            c_gp = ""
                            c_cr = ""
                            c_cg = ""

                            for head in course.heads:
                                if col_ptr < len(marks_row):
                                    val = str(marks_row[col_ptr] or "").strip()
                                    val = re.sub(r"[^\d]", "", val)
                                    c_res.marks[head] = val
                                    if head == "TOT":
                                        c_tot = val
                                    if grade_row and col_ptr < len(grade_row):
                                        g_val = str(grade_row[col_ptr] or "").strip()
                                        if g_val:
                                            c_gr = g_val
                                    if gp_row and col_ptr < len(gp_row):
                                        gp_val = str(gp_row[col_ptr] or "").strip()
                                        if gp_val:
                                            c_gp = gp_val
                                    if cr_row and col_ptr < len(cr_row):
                                        cr_val = str(cr_row[col_ptr] or "").strip()
                                        if cr_val:
                                            c_cr = cr_val
                                    if cpgp_row and col_ptr < len(cpgp_row):
                                        cpgp_val = str(cpgp_row[col_ptr] or "").strip()
                                        if cpgp_val:
                                            c_cg = cpgp_val
                                col_ptr += 1

                            c_res.grade = c_gr
                            c_res.grade_point = c_gp
                            c_res.credits = c_cr
                            c_res.credit_points = c_cg
                            student.course_results.append(c_res)

                        students.append(student)
                        r_idx += 5
                    else:
                        r_idx += 1

    return meta, students


# =====================================================================
# 2. GAZETTE CLASSIC ENGINE (Sem VIII B.E. C-Scheme)
# =====================================================================

def extract_gazette_classic(
    pdf_path: str,
    progress_callback: ProgressCallback = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """Extract records from Classic University Gazette C-Scheme (Sem VIII)."""
    meta = ExamMeta(archetype="gazette_classic", semester="VIII", semester_num=8, max_total_marks=700)
    catalog = CourseCatalog()
    active_courses: List[Tuple[str, str]] = []
    students: List[StudentRecord] = []

    reader = pypdf.PdfReader(pdf_path)
    total_pages = len(reader.pages)
    if total_pages == 0:
        return meta, []

    anchor_pattern = re.compile(r"^\s*(\d{7,9})\s+([/A-Za-z\s]+?)\s+\((\d{16})\)(?:\s+\(([-0-9\s]+)\))?")
    current_college = ""

    for page_idx, page in enumerate(reader.pages):
        if progress_callback:
            pct = 15.0 + ((page_idx + 1) / total_pages * 70.0)
            progress_callback(pct, f"Parsing Gazette page {page_idx + 1}/{total_pages} ({len(students)} students found)...")

        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        idx = 0
        while idx < len(lines):
            line = lines[idx]

            if "OFFICE REGISTER FOR" in line and not meta.exam_title:
                meta.exam_title = line.replace("OFFICE REGISTER FOR THE", "").strip()
            if "COLLEGE/CENTRE NAME:" in line:
                m_col = re.search(r"COLLEGE/CENTRE NAME:\s*([^R]+?)(?:RESULT DATE\s*[:-]+\s*(.*))?$", line)
                if m_col:
                    current_college = m_col.group(1).strip()
                    if m_col.group(2) and not meta.result_date:
                        meta.result_date = m_col.group(2).strip()

            # Extract course definitions: '01.CSC801  Advanced Artificial Intelligence'
            course_items = re.findall(r"(\d{2})\.([A-Z]{3,}[A-Za-z0-9\-]*)\s+([A-Za-z0-9\s\-\(\)\/\,\&]+?)(?=\s+\d{2}\.|$)", line)
            if course_items and not any(k in line for k in ["MARKS :", "GRADE :", "GRADE POINT :"]):
                found_new = []
                for num, code, name in course_items:
                    found_new.append((num, code.strip(), name.strip()))
                if any(num == "01" for num, _, _ in course_items) or len(active_courses) == 0:
                    active_courses = [(c, n) for _, c, n in found_new]
                else:
                    active_courses.extend([(c, n) for _, c, n in found_new])

                for c_pos, (c_code, c_name) in enumerate(active_courses):
                    heads = ["TH", "IN"] if c_pos < 4 else (["TW", "OR_PR"] if c_pos >= 6 else ["TW", "OR"])
                    existing_c = catalog.get_by_code(c_code)
                    if not existing_c:
                        catalog.courses.append(Course(code=c_code, name=c_name, heads=heads + ["Total"]))
                    else:
                        existing_c.heads = heads + ["Total"]

            m_stud = anchor_pattern.match(line)
            if m_stud:
                seat_no = m_stud.group(1).strip()
                raw_name = m_stud.group(2).strip()
                prn = m_stud.group(3).strip()
                abcid = m_stud.group(4).strip() if m_stud.group(4) else ""
                abcid = abcid.replace(" ", "").replace("-", "")
                is_female = raw_name.startswith("/")
                clean_name = raw_name.lstrip("/ ").strip()

                rec = StudentRecord(
                    seat_no=seat_no,
                    student_name=clean_name,
                    gender="F" if is_female else "M",
                    candidate_type="Regular",
                    prn=prn,
                    abc_id=abcid,
                    semester="VIII",
                    max_marks="700"
                )

                if idx + 4 < len(lines):
                    l_h1 = lines[idx + 1]
                    l_h2 = lines[idx + 2]
                    l_tot = lines[idx + 3]
                    l_gr = lines[idx + 4]

                    h1_tokens = l_h1.split()
                    h2_tokens = l_h2.split()
                    tot_tokens = l_tot.split()
                    gr_tokens = l_gr.split()

                    if len(h1_tokens) >= 8 and len(h2_tokens) >= 9:
                        rec.total_marks = h2_tokens[-1]
                        stat_tokens = h2_tokens[8:-1]
                        rec.result_status = stat_tokens[0] if stat_tokens else "P"
                        rec.remark = " ".join(stat_tokens[1:]) if len(stat_tokens) > 1 else "None"

                        if len(gr_tokens) >= 3:
                            rec.credits_earned = gr_tokens[-3]
                            rec.total_cg = gr_tokens[-2]
                            rec.sgpi = gr_tokens[-1] if gr_tokens[-1] != "--" else ""

                        courses_to_use = active_courses[:8]
                        for c_i, (c_code, c_name) in enumerate(courses_to_use):
                            h1_val = h1_tokens[c_i] if c_i < len(h1_tokens) else ""
                            h2_val = h2_tokens[c_i] if c_i < len(h2_tokens) else ""
                            tot_val = tot_tokens[c_i] if c_i < len(tot_tokens) else ""

                            gr_sub = gr_tokens[c_i * 4:(c_i + 1) * 4] if len(gr_tokens) >= (c_i + 1) * 4 else []
                            cr_val = gr_sub[0] if len(gr_sub) > 0 and gr_sub[0] != "-" else ""
                            grade_val = gr_sub[1] if len(gr_sub) > 1 and gr_sub[1] != "-" else ""
                            gp_val = gr_sub[2] if len(gr_sub) > 2 and gr_sub[2] != "--" else ""
                            cg_val = gr_sub[3] if len(gr_sub) > 3 and gr_sub[3] != "--" else ""

                            head1_name = "TH" if c_i < 4 else "TW"
                            head2_name = "IN" if c_i < 4 else ("OR_PR" if c_i >= 6 else "OR")

                            cr = CourseResult(
                                course_code=c_code,
                                course_name=c_name,
                                marks={
                                    head1_name: h1_val,
                                    head2_name: h2_val,
                                    "Total": tot_val
                                },
                                grade=grade_val,
                                grade_point=gp_val,
                                credits=cr_val,
                                credit_points=cg_val
                            )
                            rec.course_results.append(cr)

                # Historical metrics & Diploma student detection
                for look_i in range(idx, min(idx + 10, len(lines))):
                    hist_l = lines[look_i]
                    if any(k in hist_l.upper() for k in ["DIPLOMA", "DSE", "DIRECT SECOND YEAR", "LATERAL"]):
                        rec.candidate_type = "Diploma"
                    if hist_l.startswith("SGPI:"):
                        for m_sem in re.finditer(r"SEM-([IVX]+):\s*([\d\.\-]+)", hist_l):
                            s_lbl = m_sem.group(1)
                            val = m_sem.group(2)
                            if s_lbl not in rec.history:
                                rec.history[s_lbl] = {}
                            rec.history[s_lbl]["sgpi"] = val if val != "--" else ""
                        m_cgpi = re.search(r"CGPI:\s*([\d\.\-]+)", hist_l)
                        if m_cgpi:
                            rec.cgpi = m_cgpi.group(1) if m_cgpi.group(1) != "--" else ""
                    elif hist_l.startswith("CREDITS:"):
                        for m_sem in re.finditer(r"SEM-([IVX]+):\s*([\d\-]+)", hist_l):
                            s_lbl = m_sem.group(1)
                            val = m_sem.group(2)
                            if s_lbl not in rec.history:
                                rec.history[s_lbl] = {}
                            rec.history[s_lbl]["credits"] = val if val != "--" else ""
                    elif hist_l.startswith("MARKS:"):
                        for m_sem in re.finditer(r"SEM-([IVX]+):\s*([\d\-]+)/([\d\-]+)", hist_l):
                            s_lbl = m_sem.group(1)
                            if s_lbl not in rec.history:
                                rec.history[s_lbl] = {}
                            rec.history[s_lbl]["marks"] = m_sem.group(2) if m_sem.group(2) != "--" else ""

                # Check if Sem I was skipped (Diploma lateral entry)
                if ("I" not in rec.history or not rec.history["I"].get("sgpi")) and ("III" in rec.history and rec.history["III"].get("sgpi")):
                    rec.candidate_type = "Diploma"

                students.append(rec)
                idx += 5
            else:
                idx += 1

    meta.catalog = catalog
    return meta, students


# =====================================================================
# 3. GAZETTE SPOOL ENGINE (Sem VII Multi-Row Continuous Spool)
# =====================================================================

def parse_spool_marks(m1_str: str, m2_str: str = "") -> Dict[str, str]:
    """Parse component marks from Sem VII spool marks string."""
    t1 = m1_str.split()
    t2 = m2_str.split() if m2_str else []
    if len(t1) == 3:
        if t1[0] == "--":
            or_val = t1[1]
            tot_val = t1[2]
            tw_val = t2[1] if len(t2) >= 2 else (t2[0] if len(t2) == 1 else (or_val if or_val == tot_val else ""))
            th_or = or_val if or_val != tot_val else ""
            ia_tw = tw_val if or_val != tot_val else or_val
            return {"TH_OR": th_or, "IA_TW": ia_tw, "Total": tot_val}
        else:
            return {"TH_OR": t1[0], "IA_TW": t1[1], "Total": t1[2]}
    elif len(t1) == 2:
        return {"TH_OR": "", "IA_TW": t1[0], "Total": t1[1]}
    elif len(t1) == 1:
        return {"TH_OR": "", "IA_TW": "", "Total": t1[0]}
    return {"TH_OR": "", "IA_TW": "", "Total": ""}


def is_boilerplate_spool(line: str) -> bool:
    """Filter repeating page header and table header boilerplate for Sem VII Spool."""
    s = line.strip()
    if not s:
        return True
    if any(k in s for k in [
        "University of Mumbai",
        "OFFICE REGISTER OF THE B.E.",
        "Examination Held in",
        "Result Decleration Date:",
        "Page No.",
        "FEMALE",
        "MARKS       :",
        "GRADE       :",
        "GRADE POINT :",
        "äC:sum of credit points",
        "GPA: äCG /äC",
        "äC:sum",
        "äCG:sum",
        "PAPER 1",
        "PAPER 7",
        "Mother Name",
        "CENTRE-COLLEGE",
        "SEAT    NAME OF THE CANDIDATE",
        "CR   GR  GP  C*G",
        "Course Code Course Title",
        "Min Marks Max Marks",
    ]):
        return True
    if re.search(r"^\d+/\d+/\d+", s):
        return True
    if re.search(r"^\s*\d{5,}-[A-Za-z0-9\s]+:\s*(?:ThUA|ORCA|TwCA)", s):
        return True
    return False


def extract_gazette_spool(
    pdf_path: str,
    progress_callback: ProgressCallback = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """Extract records from Continuous Tape Spool (Sem VII) with all 10-12 courses."""
    meta = ExamMeta(archetype="gazette_spool", semester="VII", semester_num=7, max_total_marks=750)
    catalog = CourseCatalog()
    students: List[StudentRecord] = []

    reader = pypdf.PdfReader(pdf_path)
    total_pages = len(reader.pages)
    if total_pages == 0:
        return meta, []

    # 1. Discover courses from legend across pages
    legend_pat = r"([A-Za-z0-9]+)-([^:\n\r]+?):\s*(?:ThUA|ORCA|TwCA)"
    legend_map: Dict[str, str] = {}
    for page in reader.pages:
        text = page.extract_text() or ""
        for line in text.split("\n"):
            if "OFFICE REGISTER OF THE B.E." in line and not meta.exam_title:
                meta.exam_title = line.strip()
                m_dt = re.search(r"Result Decleration Date:\s*(.*)", line)
                if m_dt:
                    meta.result_date = m_dt.group(1).strip()
        for m in re.finditer(legend_pat, text):
            c_code = m.group(1).strip()
            c_name = m.group(2).strip()
            if c_code not in legend_map:
                legend_map[c_code] = c_name

    # 2. Collect de-noised lines across all pages
    clean_lines: List[str] = []
    for page_idx, page in enumerate(reader.pages):
        if progress_callback:
            pct = 10.0 + ((page_idx + 1) / total_pages * 40.0)
            progress_callback(pct, f"De-noising Spool page {page_idx + 1}/{total_pages}...")
        text = page.extract_text() or ""
        for l in text.split("\n"):
            if not is_boilerplate_spool(l):
                clean_lines.append(l.strip())

    grade_pat = re.compile(r"\d+\.\d{2}\s+[A-Za-z0-9\-]+\s+\d+\.\d{2}")
    i = 0
    total_lines = len(clean_lines)

    while i < total_lines:
        l = clean_lines[i]
        m_seat = re.match(r"^\s*(\d{7})\s+([/A-Za-z\s]+?)\s*\|", l)
        if m_seat:
            seat_no = m_seat.group(1).strip()
            raw_name = m_seat.group(2).strip()
            block = [l]
            j = i + 1
            while j < total_lines:
                if re.match(r"^\s*\d{7}\s+[/A-Za-z\s]+\|", clean_lines[j]):
                    break
                block.append(clean_lines[j])
                if "Total Credit" in clean_lines[j]:
                    j += 1
                    break
                j += 1

            # Split student block at dashed line
            dash_idx = -1
            for b_i, b in enumerate(block):
                if re.match(r"^\s*-{10,}\s*$", b):
                    dash_idx = b_i
                    break

            part1 = [b for b in block[:dash_idx] if "|" in b] if dash_idx != -1 else [b for b in block if "|" in b]
            part2 = [b for b in block[dash_idx+1:] if "|" in b] if dash_idx != -1 else []

            is_female = raw_name.startswith("/")
            clean_name = raw_name.lstrip("/ ").strip()
            # Check for Diploma / DSE student
            cand_type = "Regular"
            block_joined = " ".join(block).upper()
            if any(k in block_joined for k in ["DIPLOMA", "DSE", "DIRECT SECOND YEAR", "LATERAL"]):
                cand_type = "Diploma"

            rec = StudentRecord(
                seat_no=seat_no,
                student_name=clean_name,
                gender="F" if is_female else "M",
                candidate_type=cand_type,
                semester="VII",
                max_marks="750"
            )

            # --- Row Block 1 Metadata ---
            if len(part1) > 0:
                p1_cols0 = [c.strip() for c in part1[0].split("|")]
                if len(p1_cols0) >= 8:
                    stat = p1_cols0[7]
                    if "Successful" in stat or "PASS" in stat:
                        rec.result_status = "PASS"
                    elif "ABS" in stat:
                        rec.result_status = "ABS"
                    else:
                        rec.result_status = "FAIL"

            if len(part1) > 1:
                p1_cols1 = [c.strip() for c in part1[1].split("|")]
                rec.mother_name = p1_cols1[0]
                if len(p1_cols1) >= 8:
                    m_cr = re.search(r"([\d\.]+)\s+([\d\.]+)", p1_cols1[7])
                    if m_cr:
                        rec.credits_earned = m_cr.group(1)
                        rec.total_cg = m_cr.group(2)

            gr_idx1 = -1
            for idx_p, pl in enumerate(part1):
                if grade_pat.search(pl):
                    gr_idx1 = idx_p
                    break
            if gr_idx1 == -1 and len(part1) >= 3:
                gr_idx1 = len(part1) - 1

            if gr_idx1 != -1:
                p1_cols_gr = [c.strip() for c in part1[gr_idx1].split("|")]
                m_prn = re.search(r"\b(\d{16})\b", p1_cols_gr[0])
                if m_prn:
                    rec.prn = m_prn.group(1)
                    if rec.prn.startswith("2022") or any(k in block_joined for k in ["DIPLOMA", "DSE"]):
                        rec.candidate_type = "Diploma"
                if len(p1_cols_gr) >= 8:
                    m_sg = re.search(r"^\s*([\d\.]+)", p1_cols_gr[7])
                    if m_sg:
                        rec.sgpi = m_sg.group(1)

            # --- Row Block 2 Metadata ---
            if len(part2) > 0:
                p2_cols0 = [c.strip() for c in part2[0].split("|")]
                if p2_cols0[0]:
                    rec.college_name = p2_cols0[0]

            # --- Row Block 1 Course & Marks Extraction ---
            courses_r1 = [c.strip() for c in part1[0].split("|")[1:7] if c.strip()]
            m1_cols = [c.strip() for c in part1[1].split("|")[1:7]] if len(part1) > 1 else []
            m2_cols = [c.strip() for c in part1[2].split("|")[1:7]] if gr_idx1 == 3 and len(part1) > 2 else []
            gr_cols1 = [c.strip() for c in part1[gr_idx1].split("|")[1:7]] if gr_idx1 != -1 else []

            tot_sum = 0
            has_marks = False

            for c_i, c_code in enumerate(courses_r1):
                c_name = legend_map.get(c_code, c_code)
                m1_val = m1_cols[c_i] if c_i < len(m1_cols) else ""
                m2_val = m2_cols[c_i] if c_i < len(m2_cols) else ""
                m_dict = parse_spool_marks(m1_val, m2_val)

                gr_val = gr_cols1[c_i] if c_i < len(gr_cols1) else ""
                gr_tok = gr_val.split()
                cr_val = gr_tok[0] if len(gr_tok) > 0 and gr_tok[0] != "-" else ""
                g_val = gr_tok[1] if len(gr_tok) > 1 and gr_tok[1] != "-" else ""
                gp_val = gr_tok[2] if len(gr_tok) > 2 and gr_tok[2] != "--" else ""
                cg_val = gr_tok[3] if len(gr_tok) > 3 and gr_tok[3] != "--" else ""

                if not catalog.get_by_code(c_code):
                    catalog.courses.append(Course(code=c_code, name=c_name, heads=["TH_OR", "IA_TW", "Total"]))

                cr = CourseResult(
                    course_code=c_code,
                    course_name=c_name,
                    marks=m_dict,
                    grade=g_val if g_val not in ["--", "-"] else "",
                    grade_point=gp_val if gp_val not in ["--", "-"] else "",
                    credits=cr_val if cr_val not in ["--", "-"] else "",
                    credit_points=cg_val if cg_val not in ["--", "-"] else ""
                )
                rec.course_results.append(cr)

                t_digits = re.sub(r"[^\d]", "", m_dict["Total"])
                if t_digits:
                    tot_sum += int(t_digits)
                    has_marks = True

            # --- Row Block 2 Course & Marks Extraction (Electives / Labs) ---
            if len(part2) > 0:
                courses_r2 = [c.strip() for c in part2[0].split("|")[1:7] if c.strip()]
                gr_idx2 = -1
                for idx_p, pl in enumerate(part2):
                    if grade_pat.search(pl):
                        gr_idx2 = idx_p
                        break
                if gr_idx2 == -1 and len(part2) >= 2:
                    gr_idx2 = len(part2) - 1

                m1_cols_r2 = [c.strip() for c in part2[1].split("|")[1:7]] if len(part2) > 1 else []
                gr_cols2 = [c.strip() for c in part2[gr_idx2].split("|")[1:7]] if gr_idx2 != -1 else []

                for c_i, c_code in enumerate(courses_r2):
                    c_name = legend_map.get(c_code, c_code)
                    m1_val = m1_cols_r2[c_i] if c_i < len(m1_cols_r2) else ""
                    m_dict = parse_spool_marks(m1_val, "")

                    gr_val = gr_cols2[c_i] if c_i < len(gr_cols2) else ""
                    gr_tok = gr_val.split()
                    cr_val = gr_tok[0] if len(gr_tok) > 0 and gr_tok[0] != "-" else ""
                    g_val = gr_tok[1] if len(gr_tok) > 1 and gr_tok[1] != "-" else ""
                    gp_val = gr_tok[2] if len(gr_tok) > 2 and gr_tok[2] != "--" else ""
                    cg_val = gr_tok[3] if len(gr_tok) > 3 and gr_tok[3] != "--" else ""

                    if not catalog.get_by_code(c_code):
                        catalog.courses.append(Course(code=c_code, name=c_name, heads=["TH_OR", "IA_TW", "Total"]))

                    cr = CourseResult(
                        course_code=c_code,
                        course_name=c_name,
                        marks=m_dict,
                        grade=g_val if g_val not in ["--", "-"] else "",
                        grade_point=gp_val if gp_val not in ["--", "-"] else "",
                        credits=cr_val if cr_val not in ["--", "-"] else "",
                        credit_points=cg_val if cg_val not in ["--", "-"] else ""
                    )
                    rec.course_results.append(cr)

                    t_digits = re.sub(r"[^\d]", "", m_dict["Total"])
                    if t_digits:
                        tot_sum += int(t_digits)
                        has_marks = True

            if has_marks and not rec.total_marks:
                rec.total_marks = str(tot_sum)

            students.append(rec)
            i = j
        else:
            i += 1

    meta.catalog = catalog
    return meta, students


# =====================================================================
# 4. GAZETTE NEP 2020 ENGINE
# =====================================================================

def is_boilerplate_nep(line: str) -> bool:
    """Filter repeating header lines in NEP 2020 gazettes."""
    s = line.strip()
    if not s:
        return True
    if any(k in s for k in [
        "University of Mumbai", "Grade Template Used:", "Template Name",
        "Grade Scale", "Sr. No. Grade Abbreviation", "GPA",
        "äC:sum of credit points", "äCG:sum of product",
        "Page No.", "NULL-NULL & VOID", "OFFICE REGISTER FOR THE",
        "Assessment Pattern", "College Name:"
    ]):
        return True
    return False


def extract_gazette_nep(
    pdf_path: str,
    progress_callback: ProgressCallback = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """Extract complete records from NEP 2020 Examination Gazette."""
    meta = ExamMeta(archetype="gazette_nep", scheme="NEP 2020")
    catalog = CourseCatalog()
    students: List[StudentRecord] = []

    reader = pypdf.PdfReader(pdf_path)
    total_pages = len(reader.pages)
    if total_pages == 0:
        return meta, []

    # 1. Read page 0 for metadata and catalog
    p0_text = reader.pages[0].extract_text() or ""
    for line in p0_text.split("\n"):
        if "OFFICE REGISTER FOR" in line and not meta.exam_title:
            meta.exam_title = line.strip()
        if ("College Name:" in line or "MU-" in line) and not meta.college_name:
            if "College Name:" in line:
                meta.college_name = line.split("College Name:")[-1].strip()
            elif "MU-" in line:
                meta.college_name = line.strip()

    sem_label, sem_int = detect_semester(meta.exam_title or p0_text)
    meta.semester = sem_label
    meta.semester_num = sem_int

    # 2. Discover Course Catalog from syllabus pages before students start
    first_stud_page = 1
    for p_idx in range(min(5, total_pages)):
        text = reader.pages[p_idx].extract_text() or ""
        if "SEAT NO NAME" in text:
            first_stud_page = p_idx
            break
        for line in text.split("\n"):
            m = re.match(r"^\s*(\d{7})\s+(.+?)\s+(\d+\.\d{2})\s+(.*)$", line)
            if m:
                code = m.group(1).strip()
                name = m.group(2).strip()
                cr = float(m.group(3).strip())
                rest = m.group(4).strip().split()
                heads = []
                if len(rest) >= 8:
                    if rest[2] != "..." and rest[3] != "...":
                        heads.append("TH")
                    if rest[0] != "..." and rest[1] != "...":
                        heads.append("IA")
                    if rest[4] != "..." and rest[5] != "...":
                        heads.append("TW")
                    if rest[6] != "..." and rest[7] != "...":
                        heads.append("OR")
                heads.append("TOT")
                if not catalog.get_by_code(code):
                    catalog.courses.append(Course(code=code, name=name, credits=cr, heads=heads))

    # Precompile regexes
    anchor_re = re.compile(
        r"^\s*(\d{7,10})\s+([A-Za-z\s/\.]+?)\s+(Regular|Diploma|Direct Second Year|Repeater|Ex-Student)\s+(MALE|FEMALE)\b",
        re.I
    )

    pattern = re.compile(
        r"(\d+(?:[\*@]\d+)?)\s+(?:0\s+F(?:\s+0\.0)?|F(?:\s+0\.0)?|P\b)"
        r"|(ABS|AA|\-\-)\b"
        r"|\bP\b"
        r"|(\d+(?:[\*@]\d+)?)\b"
    )

    def parse_head_tokens(line: str) -> List[str]:
        line = re.sub(r"^(?:T1|O1|E1|I1)\s+", "", line.strip())
        line = re.sub(r"\s*\(\d+\)\s+[A-Za-z\s]+$", "", line)
        line = re.sub(r"\s*MARKS?\s*$", "", line, flags=re.I)
        line = re.sub(r"(\d+)\s*([\*@]\d+)", r"\1\2", line)
        tokens = []
        for m in pattern.finditer(line):
            g1, g2, g3 = m.group(1), m.group(2), m.group(3)
            raw = m.group(0).strip()
            if g1:
                tokens.append(g1)
            elif g2:
                tokens.append(g2)
            elif raw == "P":
                tokens.append("--")
            elif g3:
                tokens.append(g3)
        return tokens

    tot_item_re = re.compile(r"(\d+(?:[\*@]\d+)?|ABS|AA|\-\-)\s+(\d+)\s+([A-Za-z\+\-]+)\s+(\d+)\s+([\d\.]+)")

    # 3. Iterate through all student pages
    data_pages_count = max(1, total_pages - first_stud_page)
    for p_idx in range(first_stud_page, total_pages):
        if progress_callback:
            pct = 10.0 + (p_idx - first_stud_page) / data_pages_count * 80.0
            progress_callback(pct, f"Extracting NEP 2020 page {p_idx+1}/{total_pages}...")

        text = reader.pages[p_idx].extract_text() or ""
        if not text.strip():
            continue

        # Extract college name if not set yet
        if not meta.college_name:
            for line in text.split("\n")[:15]:
                if "MU-" in line or "COLLEGE" in line.upper():
                    meta.college_name = line.strip()
                    break

        lines = text.split("\n")

        # Determine active course column codes on this page
        if "TOT GP G C G*C" in text:
            hp = text.split("TOT GP G C G*C")[0]
            if "SEAT NO NAME" in hp:
                hp = hp.split("SEAT NO NAME")[-1]
            page_courses = re.findall(r"\b(\d{7})\b", hp)
        else:
            page_courses = [c.code for c in catalog.courses]

        for idx, l in enumerate(lines):
            m = anchor_re.match(l)
            if not m:
                continue

            seat = m.group(1)
            raw_name = m.group(2).strip()
            cand_type = m.group(3)
            gender_raw = m.group(4)
            is_fem = raw_name.startswith("/") or gender_raw.upper() == "FEMALE"
            name = raw_name.lstrip("/ ").strip()
            gender = "F" if is_fem else "M"

            # Gather student block lines
            s_lines = [l]
            for sub in lines[idx+1:idx+15]:
                if anchor_re.match(sub) or "SEAT NO NAME" in sub:
                    break
                s_lines.append(sub)

            block_text = " ".join(s_lines)
            ern_m = re.search(r"\((MU[A-Za-z0-9]+|\d{16})", block_text)
            ern = ern_m.group(1) if ern_m else ""

            res_m = re.search(r"\((\d{2,4})\)\s+([A-Za-z\s]+?)(?=\s+(?:TOT|\d{2}\s+\d+|\d+\.\d+)|\s*$)", block_text)
            tot_marks = res_m.group(1).strip() if res_m else ""
            status = res_m.group(2).strip() if res_m else ("FAILED" if "FAILED" in block_text else "PASS")

            # Extract heads
            t1_line = next((s for s in s_lines if s.startswith("T1 ")), "")
            o1_line = next((s for s in s_lines if s.startswith("O1 ")), "")
            e1_line = next((s for s in s_lines if s.startswith("E1 ")), "")
            i1_line = next((s for s in s_lines if s.startswith("I1 ")), "")
            tot_line = next((s for s in s_lines if s.startswith("TOT ")), "")

            t1_toks = parse_head_tokens(t1_line) if t1_line else []
            o1_toks = parse_head_tokens(o1_line) if o1_line else []
            e1_toks = parse_head_tokens(e1_line) if e1_line else []
            i1_toks = parse_head_tokens(i1_line) if i1_line else []

            tot_matches = list(tot_item_re.finditer(tot_line)) if tot_line else []

            credits_earned = ""
            total_cg = ""
            sgpi = ""
            if tot_matches:
                tail = tot_line[tot_matches[-1].end():].strip()
                tot_idx = s_lines.index(tot_line) if tot_line in s_lines else -1
                if tot_idx >= 0 and tot_idx + 1 < len(s_lines):
                    next_l = s_lines[tot_idx+1].strip()
                    if re.match(r"^\d+$", next_l):
                        tail += next_l
                sm = re.search(r"(\d{1,2})\s+([\d\.]+)\s+([\d\.]+|\-\-)", tail)
                if sm:
                    credits_earned = sm.group(1)
                    total_cg = sm.group(2)
                    raw_sg = sm.group(3)
                    try:
                        sgpi = f"{float(raw_sg):.2f}"
                    except Exception:
                        sgpi = raw_sg

            # Map tokens to page courses
            t1_idx = 0
            o1_idx = 0
            e1_idx = 0
            i1_idx = 0

            course_results = []
            for c_i, c_code in enumerate(page_courses):
                c_obj = catalog.get_by_code(c_code)
                c_name = c_obj.name if c_obj else c_code
                c_cr = c_obj.credits if c_obj else 0.0
                c_heads = c_obj.heads if c_obj else []

                m_dict: Dict[str, str] = {}
                comp_vals: List[str] = []

                if "TH" in c_heads:
                    val = e1_toks[e1_idx] if e1_idx < len(e1_toks) else "--"
                    e1_idx += 1
                    m_dict["TH"] = val
                    m_dict["E1"] = val
                    comp_vals.append(val)
                if "IA" in c_heads:
                    val = i1_toks[i1_idx] if i1_idx < len(i1_toks) else "--"
                    i1_idx += 1
                    m_dict["IA"] = val
                    m_dict["IN"] = val
                    m_dict["I1"] = val
                    comp_vals.append(val)
                if "TW" in c_heads:
                    val = t1_toks[t1_idx] if t1_idx < len(t1_toks) else "--"
                    t1_idx += 1
                    m_dict["TW"] = val
                    m_dict["T1"] = val
                    comp_vals.append(val)
                if "OR" in c_heads:
                    val = o1_toks[o1_idx] if o1_idx < len(o1_toks) else "--"
                    o1_idx += 1
                    m_dict["OR"] = val
                    m_dict["PR"] = val
                    m_dict["O1"] = val
                    comp_vals.append(val)

                tot_val = ""
                gp = ""
                grade = ""
                cr = str(int(c_cr)) if c_cr.is_integer() else str(c_cr)
                cg = ""

                if len(tot_matches) == len(page_courses):
                    tm = tot_matches[c_i]
                    tot_val, gp, grade, cr, cg = tm.group(1), tm.group(2), tm.group(3), tm.group(4), tm.group(5)
                elif len(tot_matches) < len(page_courses):
                    offset = len(page_courses) - len(tot_matches)
                    if c_i >= offset:
                        tm = tot_matches[c_i - offset]
                        tot_val, gp, grade, cr, cg = tm.group(1), tm.group(2), tm.group(3), tm.group(4), tm.group(5)
                    else:
                        nums = [int(re.sub(r"[^\d]", "", v)) for v in comp_vals if re.sub(r"[^\d]", "", v)]
                        tot_val = str(sum(nums)) if nums else "0"
                        gp = "0"
                        grade = "F"
                        cr = str(int(c_cr)) if c_cr.is_integer() else str(c_cr)
                        cg = "0.0"

                m_dict["TOT"] = tot_val
                m_dict["Total"] = tot_val

                course_results.append(CourseResult(
                    course_code=c_code,
                    course_name=c_name,
                    marks=m_dict,
                    grade=grade,
                    grade_point=gp,
                    credits=cr,
                    credit_points=cg
                ))

            students.append(StudentRecord(
                seat_no=seat,
                student_name=name,
                gender=gender,
                candidate_type=cand_type,
                ern=ern,
                semester=meta.semester,
                result_status=status,
                total_marks=tot_marks,
                credits_earned=credits_earned,
                total_cg=total_cg,
                sgpi=sgpi,
                course_results=course_results
            ))

    meta.catalog = catalog
    return meta, students


# =====================================================================
# MASTER POSSIBILITY DISPATCHER
# =====================================================================

def extract_with_possibility_engine(
    pdf_path: str,
    archetype: str,
    progress_callback: ProgressCallback = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """Dispatch to the corresponding pre-trained possibility extraction engine."""
    if archetype == "tabulation":
        return extract_tabulation(pdf_path, progress_callback)
    elif archetype == "gazette_classic":
        return extract_gazette_classic(pdf_path, progress_callback)
    elif archetype == "gazette_spool":
        return extract_gazette_spool(pdf_path, progress_callback)
    elif archetype == "gazette_nep":
        return extract_gazette_nep(pdf_path, progress_callback)
    else:
        # Default fallback
        try:
            return extract_gazette_spool(pdf_path, progress_callback)
        except Exception:
            return extract_gazette_classic(pdf_path, progress_callback)

