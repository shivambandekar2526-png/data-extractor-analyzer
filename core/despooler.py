"""
Gazette Despooler Engine
========================
Detects spooled university gazette PDFs (e.g. continuous multi-row tape spools such as
Mumbai University Semester VII) and despools them into a unified, high-fidelity visual
gazette PDF suitable for Vision AI extraction and cross-checking.

Architecture:
1. Spool Detection: Identifies continuous tape spools with multi-tier wrapped student blocks.
2. Stream Despooling: Unrolls the continuous spool, resolves course legends, and pairs Part 1
   (Papers 1-6) with Part 2 (Papers 7-12) for each candidate.
3. Visual Gazette Synthesizer: Generates a crisp, high-resolution vector PDF where each candidate
   is rendered in a clean, unified tabular format with all 10-12 courses aligned horizontally.
4. Vision AI Handoff: Hands off the despooled document to the Visual Blueprint Pipeline with
   pre-discovered catalog and locked schema envelopes.
"""

import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[float, str], None]]
AuditCallback = Optional[Callable[[Dict[str, Any]], None]]


def is_spooled_pdf(pdf_path: str) -> bool:
    """
    Checks if a PDF is a spooled continuous tape format (e.g. Mumbai University Sem VII).
    Inspects first 3 pages and last 3 pages for spool markers and legends.
    """
    if not os.path.isfile(pdf_path):
        return False
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return False

        # Sample text from first few pages
        sample_pages = [doc[i].get_text() for i in range(min(3, len(doc)))]
        sample_text = "\n".join(sample_pages).upper()

        # Check for classic spool markers
        has_paper_1_7 = ("PAPER 1" in sample_text and "PAPER 7" in sample_text) or \
                        ("PAPER 1" in sample_text and "42211" in sample_text) or \
                        ("OFFICE REGISTER OF THE B.E." in sample_text and "CR   GR  GP  C*G" in sample_text)
        
        # Check for seat pattern with pipe separator and horizontal dashed separator lines
        has_pipe_blocks = bool(re.search(r"^\s*\d{7}\s+[/A-Za-z\s]+\|", sample_text, re.M))
        has_dashed_split = bool(re.search(r"-{20,}", sample_text))

        doc.close()
        return bool(has_paper_1_7 and has_pipe_blocks and has_dashed_split)
    except Exception as e:
        logger.warning(f"Error checking if PDF is spooled: {e}")
        return False


def is_spool_boilerplate(line: str) -> bool:
    """Filter repeating page header and table header boilerplate for continuous spools."""
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


def parse_spool_marks_components(m1_str: str, m2_str: str = "") -> Dict[str, str]:
    """Parse component marks (TH_OR, IA_TW, Total) from spool string tokens."""
    t1 = [t for t in m1_str.split() if t not in ("+", "=", "->", ":")]
    t2 = [t for t in m2_str.split() if t not in ("+", "=", "->", ":")] if m2_str else []
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


def parse_spool_stream(
    pdf_path: str,
    progress_cb: Optional[ProgressCallback] = None
) -> Tuple[ExamMeta, List[StudentRecord]]:
    """
    Fast extraction and despooling of continuous spool stream using PyMuPDF.
    Discovers all courses from legends and groups Part 1 and Part 2 for all students.
    """
    meta = ExamMeta(archetype="gazette_spool", semester="VII", semester_num=7, max_total_marks=750)
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        return meta, []

    # 1. Discover courses from legends across pages
    legend_pat = r"([A-Za-z0-9]+)-([^:\n\r]+?):\s*(?:ThUA|ORCA|TwCA)"
    legend_map: Dict[str, str] = {}
    clean_lines: List[str] = []

    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text() or ""

        # Extract exam title and date
        for line in text.split("\n"):
            if "OFFICE REGISTER OF THE B.E." in line and not meta.exam_title:
                meta.exam_title = line.strip()
                m_dt = re.search(r"Result Decleration Date:\s*(.*)", line)
                if m_dt:
                    meta.result_date = m_dt.group(1).strip()
            if not is_spool_boilerplate(line):
                clean_lines.append(line.strip())

        for m in re.finditer(legend_pat, text):
            c_code = m.group(1).strip()
            c_name = m.group(2).strip()
            if c_code not in legend_map:
                legend_map[c_code] = c_name

    doc.close()

    # Build CourseCatalog
    for c_code, c_name in legend_map.items():
        is_lab = any(k in c_code.upper() or k in c_name.upper() for k in ["LAB", "PR", "PRACTICAL", "TW", "PROJECT"])
        heads = ["TH", "IA", "TOT"] if not is_lab else ["TW", "PR", "TOT"]
        c = meta.catalog.add_course(code=c_code, name=c_name, heads=heads)
        c.course_type = "Lab" if is_lab else "Theory"
        c.credits = 1 if is_lab and "PROJECT" not in c_name.upper() else (3 if "PROJECT" in c_name.upper() else 3)

    students: List[StudentRecord] = []
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

            # Split student block at horizontal separator line
            dash_idx = -1
            for b_i, b in enumerate(block):
                if re.match(r"^\s*-{10,}\s*$", b):
                    dash_idx = b_i
                    break

            part1 = [b for b in block[:dash_idx] if "|" in b] if dash_idx != -1 else [b for b in block if "|" in b]
            part2 = [b for b in block[dash_idx+1:] if "|" in b] if dash_idx != -1 else []

            is_female = raw_name.startswith("/")
            clean_name = raw_name.lstrip("/ ").strip()
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

            for idx, c_code in enumerate(courses_r1):
                m1_val = m1_cols[idx] if idx < len(m1_cols) else ""
                m2_val = m2_cols[idx] if idx < len(m2_cols) else ""
                gr_val = gr_cols1[idx] if idx < len(gr_cols1) else ""

                m_dict = parse_spool_marks_components(m1_val, m2_val)
                c_name = legend_map.get(c_code, c_code)

                cr = CourseResult(
                    course_code=c_code,
                    course_name=c_name,
                    marks=m_dict
                )
                m_g = re.search(r"([\d\.]+)\s+([A-Za-z0-9\-]+)\s+([\d\.]+)\s+([\d\.]+)", gr_val)
                if m_g:
                    cr.credits = m_g.group(1)
                    cr.grade = m_g.group(2)
                    cr.grade_point = m_g.group(3)
                    cr.credit_points = m_g.group(4)
                rec.course_results.append(cr)

            # --- Row Block 2 Course & Marks Extraction ---
            if len(part2) > 0:
                courses_r2 = [c.strip() for c in part2[0].split("|")[1:7] if c.strip()]
                m1_cols2 = [c.strip() for c in part2[1].split("|")[1:7]] if len(part2) > 1 else []
                gr_idx2 = -1
                for idx_p, pl in enumerate(part2):
                    if grade_pat.search(pl):
                        gr_idx2 = idx_p
                        break
                if gr_idx2 == -1 and len(part2) >= 3:
                    gr_idx2 = len(part2) - 1

                gr_cols2 = [c.strip() for c in part2[gr_idx2].split("|")[1:7]] if gr_idx2 != -1 else []

                for idx, c_code in enumerate(courses_r2):
                    m1_val = m1_cols2[idx] if idx < len(m1_cols2) else ""
                    gr_val = gr_cols2[idx] if idx < len(gr_cols2) else ""

                    m_dict = parse_spool_marks_components(m1_val, "")
                    c_name = legend_map.get(c_code, c_code)

                    cr = CourseResult(
                        course_code=c_code,
                        course_name=c_name,
                        marks=m_dict
                    )
                    m_g = re.search(r"([\d\.]+)\s+([A-Za-z0-9\-]+)\s+([\d\.]+)\s+([\d\.]+)", gr_val)
                    if m_g:
                        cr.credits = m_g.group(1)
                        cr.grade = m_g.group(2)
                        cr.grade_point = m_g.group(3)
                        cr.credit_points = m_g.group(4)
                    rec.course_results.append(cr)

            # Compute sum of marks across all parsed courses
            valid_marks = []
            for cr in rec.course_results:
                m_val = cr.marks.get("Total") if isinstance(cr.marks, dict) else cr.marks
                if m_val and str(m_val).replace('.', '', 1).isdigit():
                    valid_marks.append(float(m_val))
            if valid_marks:
                rec.total_marks = str(int(sum(valid_marks)))

            students.append(rec)
            i = j
        else:
            i += 1

    return meta, students


def render_despooled_pdf(
    meta: ExamMeta,
    students: List[StudentRecord],
    output_pdf_path: str
) -> str:
    """
    Renders a unified, crystal-clear, high-resolution vector PDF using PyMuPDF.
    Every student is presented in a clean, unified tabular format with all 10-12 courses
    aligned in full width across an A3 Landscape canvas (1190 x 842 pt).
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)
    doc = fitz.open()

    page_width = 1190.0
    page_height = 842.0
    margin = 35.0

    # Colors
    c_navy = (0.12, 0.31, 0.47)
    c_gray_bg = (0.96, 0.97, 0.98)
    c_card_border = (0.75, 0.82, 0.88)
    c_line = (0.85, 0.88, 0.92)
    c_green = (0.09, 0.55, 0.24)
    c_red = (0.80, 0.15, 0.15)
    c_dark = (0.10, 0.15, 0.20)
    c_white = (1.0, 1.0, 1.0)

    students_per_page = 4
    total_students = len(students)
    total_pages = max(1, (total_students + students_per_page - 1) // students_per_page)

    all_courses = meta.catalog.courses
    courses_to_display = all_courses[:12] if all_courses else []

    for p_idx in range(total_pages):
        page = doc.new_page(width=page_width, height=page_height)

        # 1. Outer Border
        page.draw_rect(fitz.Rect(margin, margin, page_width - margin, page_height - margin), color=c_card_border, width=1.0)

        # 2. Page Top Banner
        y_top = margin + 10
        exam_title = meta.exam_title or "UNIVERSITY OF MUMBAI - BACHELOR OF ENGINEERING"
        page.insert_text(fitz.Point(margin + 15, y_top + 14), "UNIVERSITY OF MUMBAI", fontsize=11, fontname="helv", color=c_navy)
        page.insert_text(fitz.Point(margin + 15, y_top + 28), f"OFFICIAL GAZETTE REGISTER • {exam_title.upper()}", fontsize=9, fontname="helv", color=c_dark)
        
        meta_sub = f"Semester: {meta.semester or 'VII'} | Result Date: {meta.result_date or 'Recent'} | Total Candidates: {len(students)} | Despooled for Vision AI"
        page.insert_text(fitz.Point(margin + 15, y_top + 42), meta_sub, fontsize=8, fontname="helv", color=(0.4, 0.45, 0.5))

        page.draw_line(fitz.Point(margin + 15, y_top + 48), fitz.Point(page_width - margin - 15, y_top + 48), color=c_line, width=1.0)

        # 3. Master Course Legend Box (on Page 1)
        card_start_y = y_top + 55
        if p_idx == 0 and courses_to_display:
            legend_box_rect = fitz.Rect(margin + 15, card_start_y, page_width - margin - 15, card_start_y + 40)
            page.draw_rect(legend_box_rect, color=c_card_border, fill=c_gray_bg, width=0.5)
            page.insert_text(fitz.Point(margin + 20, card_start_y + 12), "MASTER COURSE CATALOG (ALL PAPERS 1 - 12):", fontsize=7.5, fontname="helv", color=c_navy)

            # Render course tags in 2 rows
            col_x = margin + 20
            row_y = card_start_y + 24
            for c_i, c in enumerate(courses_to_display):
                tag = f"[{c.code}] {c.name[:25]}"
                if col_x + 170 > page_width - margin - 20:
                    col_x = margin + 20
                    row_y += 12
                page.insert_text(fitz.Point(col_x, row_y), tag, fontsize=6.8, fontname="helv", color=c_dark)
                col_x += 175
            card_start_y += 48

        # 4. Render Student Cards for this page
        p_students = students[p_idx * students_per_page : (p_idx + 1) * students_per_page]
        available_height = (page_height - margin - 20) - card_start_y
        card_height = min(150.0, max(120.0, available_height / max(1, len(p_students)) - 10))

        y_cursor = card_start_y
        for s in p_students:
            card_rect = fitz.Rect(margin + 15, y_cursor, page_width - margin - 15, y_cursor + card_height)
            page.draw_rect(card_rect, color=c_card_border, fill=c_white, width=0.8)

            # Candidate Info Header Bar
            header_rect = fitz.Rect(margin + 15, y_cursor, page_width - margin - 15, y_cursor + 20)
            page.draw_rect(header_rect, color=c_navy, fill=c_navy)

            cand_text = f"SEAT: {s.seat_no}   •   NAME: {s.student_name}   •   MOTHER: {s.mother_name or 'N/A'}   •   PRN: {s.prn or 'N/A'}   •   COLLEGE: {s.college_name or 'College'}"
            page.insert_text(fitz.Point(margin + 25, y_cursor + 13), cand_text, fontsize=8, fontname="helv", color=c_white)

            # Sub-table layout
            table_y = y_cursor + 22
            col_cand_w = 140.0
            summary_w = 140.0
            num_courses = max(1, len(s.course_results))
            course_col_w = max(60.0, (page_width - 2 * margin - 30 - col_cand_w - summary_w) / max(1, num_courses))

            # Left Profile Box
            prof_rect = fitz.Rect(margin + 15, table_y, margin + 15 + col_cand_w, y_cursor + card_height)
            page.draw_rect(prof_rect, color=c_line, fill=c_gray_bg, width=0.5)
            page.insert_text(fitz.Point(margin + 20, table_y + 16), f"Seat No: {s.seat_no}", fontsize=7.5, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(margin + 20, table_y + 30), f"Gender: {s.gender}", fontsize=7.5, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(margin + 20, table_y + 44), f"Type: {s.candidate_type}", fontsize=7.5, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(margin + 20, table_y + 58), f"Max Marks: {s.max_marks or '750'}", fontsize=7.5, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(margin + 20, table_y + 72), f"Total Marks: {s.total_marks or 'N/A'}", fontsize=8.5, fontname="helv", color=c_navy)

            # Middle Course Columns
            curr_x = margin + 15 + col_cand_w
            for cr in s.course_results:
                col_box = fitz.Rect(curr_x, table_y, curr_x + course_col_w, y_cursor + card_height)
                page.draw_rect(col_box, color=c_line, width=0.5)

                # Course Code & Name
                page.insert_text(fitz.Point(curr_x + 4, table_y + 12), cr.course_code[:12], fontsize=7, fontname="helv", color=c_navy)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 22), (cr.course_name or "")[:15], fontsize=6, fontname="helv", color=(0.4, 0.45, 0.5))
                page.draw_line(fitz.Point(curr_x, table_y + 26), fitz.Point(curr_x + course_col_w, table_y + 26), color=c_line, width=0.5)

                # Marks & Components
                cm = cr.marks if isinstance(cr.marks, dict) else {}
                th_or = cm.get("TH_OR") or cm.get("TH") or cm.get("OR") or ""
                ia_tw = cm.get("IA_TW") or cm.get("IA") or cm.get("TW") or ""
                tot_mark = cm.get("Total") or cm.get("TOT") or (cr.marks if isinstance(cr.marks, str) else "")
                page.insert_text(fitz.Point(curr_x + 4, table_y + 38), f"TH/OR: {th_or or '-'}", fontsize=6.8, fontname="helv", color=c_dark)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 49), f"IA/TW: {ia_tw or '-'}", fontsize=6.8, fontname="helv", color=c_dark)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 60), f"TOT: {tot_mark or '-'}", fontsize=7.5, fontname="helv", color=c_navy)

                # Grade & GP
                page.draw_line(fitz.Point(curr_x, table_y + 65), fitz.Point(curr_x + course_col_w, table_y + 65), color=c_line, width=0.5)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 76), f"Gr: {cr.grade or '-'}", fontsize=7.5, fontname="helv", color=c_green if cr.grade not in ('F', 'AB') else c_red)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 88), f"GP: {cr.grade_point or '-'}  CR: {cr.credits or '-'}", fontsize=6.5, fontname="helv", color=c_dark)
                page.insert_text(fitz.Point(curr_x + 4, table_y + 98), f"C*G: {cr.credit_points or '-'}", fontsize=7, fontname="helv", color=c_dark)

                curr_x += course_col_w

            # Right Summary Box
            sum_x = page_width - margin - 15 - summary_w
            sum_box = fitz.Rect(sum_x, table_y, page_width - margin - 15, y_cursor + card_height)
            page.draw_rect(sum_box, color=c_line, fill=c_gray_bg, width=0.5)

            res_color = c_green if s.result_status == "PASS" else (c_red if s.result_status == "FAIL" else c_navy)
            page.insert_text(fitz.Point(sum_x + 15, table_y + 20), f"STATUS: {s.result_status}", fontsize=9, fontname="helv", color=res_color)
            page.insert_text(fitz.Point(sum_x + 15, table_y + 38), f"Credits: {s.credits_earned or '-'}", fontsize=7.8, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(sum_x + 15, table_y + 54), f"Total CG: {s.total_cg or '-'}", fontsize=7.8, fontname="helv", color=c_dark)
            page.insert_text(fitz.Point(sum_x + 15, table_y + 72), f"SGPI: {s.sgpi or 'N/A'}", fontsize=11, fontname="helv", color=c_navy)

            y_cursor += card_height + 8

        # Footer
        page.insert_text(fitz.Point(margin + 15, page_height - margin - 8), f"Despooled High-Fidelity Ledger • Sheet {p_idx + 1} of {total_pages}", fontsize=7, fontname="helv", color=(0.5, 0.5, 0.5))

    doc.save(output_pdf_path)
    doc.close()
    return output_pdf_path


def despool_gazette_pdf(
    pdf_path: str,
    output_dir: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
    audit_cb: Optional[AuditCallback] = None
) -> Tuple[str, ExamMeta, List[StudentRecord]]:
    """
    Main entry point for despooling a spooled PDF.
    1. Extracts all courses from legends and groups multi-tier candidate blocks.
    2. Synthesizes a clean, unspooled, high-resolution vector PDF.
    3. Returns (despooled_pdf_path, meta, students).
    """
    if progress_cb:
        progress_cb(2.0, "Analyzing continuous spool structure & unrolling candidate blocks...")

    t0 = time.time()
    meta, students = parse_spool_stream(pdf_path, progress_cb=progress_cb)

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(pdf_path))
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    despooled_pdf_name = f"despooled_{base_name}.pdf"
    despooled_pdf_path = os.path.join(output_dir, despooled_pdf_name)

    if progress_cb:
        progress_cb(6.0, f"Synthesizing high-fidelity visual gazette ({len(students)} candidates, {len(meta.catalog.courses)} courses)...")

    render_despooled_pdf(meta, students, despooled_pdf_path)
    elapsed = round(time.time() - t0, 2)

    if audit_cb:
        audit_cb({
            "timestamp": time.strftime("%H:%M:%S"),
            "type": "despool_engine_success",
            "badge": "DESPOOL COMPLETED",
            "title": f"Despooled {len(students)} Candidates across {len(meta.catalog.courses)} Papers in {elapsed}s",
            "details": {
                "original_spooled_pdf": os.path.basename(pdf_path),
                "despooled_pdf": despooled_pdf_name,
                "despooled_path": despooled_pdf_path,
                "total_candidates": len(students),
                "total_courses": len(meta.catalog.courses),
                "duration_seconds": elapsed
            }
        })

    return despooled_pdf_path, meta, students
