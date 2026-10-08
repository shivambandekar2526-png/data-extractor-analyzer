"""
Semester Performance Analysis Word (.docx) Exporter
===================================================
Generates a strictly 1-page institutional academic report in Microsoft Word format
for Terna Engineering College, complying with departmental audit guidelines.

Layout Architecture:
- Calibrated 1-page A4 format with institutional margins spanning the whole page.
- Top Left: Official Terna Engineering College Logo.
- Top Center: Institutional header hierarchy.
- Divider: Solid continuous institutional horizontal separator line.
- Metadata: Batch, Academic Year, Semester, Appeared, Report Date.
- Section 1: Semester Clearance & Success Rate Analysis Table + Clearance Donut Chart.
- Section 2: Institutional Classification Table + Bar Chart & SGPI Distribution Table + Bar Chart.
- Section 3: Top 5 Semester Merit Leaders structured table.
- Section 4: Institutional signatures block.
"""

import os
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, Optional, List

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# Institutional Color Palette
COLOR_NAVY = RGBColor(31, 78, 120)       # #1F4E78
COLOR_SLATE = RGBColor(51, 65, 85)       # #334155
COLOR_MUTED = RGBColor(100, 116, 139)    # #64748B
COLOR_SUCCESS = RGBColor(16, 185, 129)   # #10B981
COLOR_DANGER = RGBColor(239, 68, 68)     # #EF4444
HEX_NAVY = "1F4E78"
HEX_LIGHT_BLUE = "F0F4F8"
HEX_BORDER = "CBD5E1"
HEX_ZEBRA = "F8FAFC"


def _get_terna_logo_path() -> Optional[str]:
    """Finds the Terna logo file path in the project."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, "..", "static", "img", "terna_logo.png"),
        os.path.join(current_dir, "..", "static", "terna_logo.png"),
        os.path.join(current_dir, "static", "img", "terna_logo.png"),
        os.path.join("c:\\Projects\\result-analysis-aids\\data_extractor_AI\\static\\img\\terna_logo.png")
    ]
    for p in candidates:
        norm = os.path.normpath(p)
        if os.path.exists(norm):
            return norm
    return None


def _set_cell_background(cell, hex_color: str):
    """Sets background shading color for a table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tcPr.append(shd)


def _set_cell_margins(cell, top: int = 20, bottom: int = 20, left: int = 35, right: int = 35):
    """Sets internal padding (in dxa) for a table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)


def _set_table_borders(table, color_hex: str = HEX_BORDER):
    """Applies neat institutional borders to a table."""
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="{color_hex}"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="{color_hex}"/>'
        f'<w:left w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="{color_hex}"/>'
        f'<w:insideV w:val="single" w:sz="4" w:space="0" w:color="{color_hex}"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)


# =========================================================================
# MATPLOTLIB CHART GENERATORS FOR EMBEDDING IN WORD DOCUMENT
# =========================================================================

def _generate_semester_clearance_chart(sum_obj: Dict[str, Any]) -> BytesIO:
    """Generates an uncluttered, compact clearance donut chart (Students Passed vs Failed)."""
    passed = int(sum_obj.get("passed", 0) or 0)
    failed = int(sum_obj.get("failed", 0) or 0)

    sizes = []
    labels = []
    colors = []
    if passed > 0:
        sizes.append(passed)
        labels.append(f"Passed ({passed})")
        colors.append("#10b981")
    if failed > 0:
        sizes.append(failed)
        labels.append(f"Failed ({failed})")
        colors.append("#ef4444")
    if not sizes:
        sizes = [1]
        labels = ["No Data"]
        colors = ["#cbd5e1"]

    fig, ax = plt.subplots(figsize=(2.60, 1.75), dpi=180)
    wedges, texts, autotexts = ax.pie(
        sizes,
        colors=colors,
        autopct="%1.0f%%" if (len(sizes) > 1 or sizes[0] > 1) else "",
        pctdistance=0.72,
        startangle=140,
        textprops={"fontsize": 6.8, "color": "white", "fontweight": "bold"}
    )
    centre_circle = plt.Circle((0, 0), 0.50, fc="white")
    ax.add_artist(centre_circle)
    ax.axis("equal")

    ax.legend(
        wedges, labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.25),
        ncol=len(labels),
        fontsize=6.2,
        frameon=False,
        handletextpad=0.3,
        columnspacing=0.8
    )
    ax.set_title("Semester Clearance", fontsize=7.8, fontweight="bold", color="#1F4E78", pad=2)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", transparent=False)
    plt.close(fig)
    buf.seek(0)
    return buf


def _generate_classification_chart_image(sum_obj: Dict[str, Any]) -> BytesIO:
    """Generates an uncluttered, compact institutional classification bar chart."""
    class_info = sum_obj.get("classification", {})
    chart_info = class_info.get("chart", {})
    raw_labels = chart_info.get("labels", [])
    counts = chart_info.get("counts", [])

    if not raw_labels or not counts:
        # Fallback to sum_obj direct fields
        class_table = class_info.get("table", [])
        if class_table:
            raw_labels = [r.get("short") or r.get("classification") for r in class_table]
            counts = [r.get("students", 0) for r in class_table]
        else:
            raw_labels = ["Distinction", "First Class", "Second Class", "Pass Class", "Fail"]
            counts = [
                sum_obj.get("distinction_count", 0),
                sum_obj.get("first_class_count", 0),
                sum_obj.get("second_class_count", 0),
                sum_obj.get("pass_class_count", 0),
                sum_obj.get("failed", 0)
            ]

    # Map labels to compact form
    short_map = {
        "First Class with Distinction (Distinction)": "Distinction",
        "First Class (60% to 74.99%)": "1st Class",
        "Second Class (50% to 59.99%)": "2nd Class",
        "Pass Class (40% to 49.99%)": "Pass",
        "Fail / KT (< 40%)": "Fail",
        "Second Class": "2nd Class",
        "Pass Class": "Pass",
        "Fail / KT": "Fail"
    }
    clean_labels = [short_map.get(str(l), str(l).split()[0]) for l in raw_labels]

    fig, ax = plt.subplots(figsize=(3.15, 1.25), dpi=180)
    if counts and any(c > 0 for c in counts):
        colors = ["#7c3aed", "#2563eb", "#06b6d4", "#64748b", "#ef4444"][:len(counts)]
        bars = ax.bar(clean_labels, counts, color=colors, edgecolor="#1e293b", linewidth=0.35, width=0.52)
        ax.set_ylabel("Students", fontsize=6.0, color="#475569", labelpad=2)
        ax.tick_params(axis="both", labelsize=6.2, pad=1)
        max_c = max(counts) if counts else 1
        ax.set_ylim(0, max_c * 1.25)
        for b, c in zip(bars, counts):
            if c > 0:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height() + (max_c * 0.04),
                    str(c),
                    ha="center",
                    va="bottom",
                    fontsize=6.0,
                    fontweight="bold",
                    color="#1e293b"
                )
        ax.grid(axis="y", linestyle="--", alpha=0.3, color="#94a3b8")
    else:
        ax.text(0.5, 0.5, "No Classification Data", ha="center", va="center", fontsize=7.2, color="#64748b")
        ax.axis("off")

    ax.set_title("Institutional Classification", fontsize=7.5, fontweight="bold", color="#1F4E78", pad=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", transparent=False)
    plt.close(fig)
    buf.seek(0)
    return buf


def _generate_sgpi_distribution_chart_image(dist_obj: Dict[str, Any]) -> BytesIO:
    """Generates an uncluttered, compact SGPI distribution bar chart."""
    chart_info = dist_obj.get("chart", {})
    labels = chart_info.get("labels", [])
    counts = chart_info.get("counts", [])

    if not labels or not counts:
        table_rows = dist_obj.get("table", [])
        if table_rows:
            labels = [r.get("range", "") for r in table_rows]
            counts = [r.get("students", 0) for r in table_rows]

    fig, ax = plt.subplots(figsize=(3.15, 1.25), dpi=180)
    if labels and counts and any(c > 0 for c in counts):
        clean_labels = [str(l).replace("–", "-").replace("Below ", "<") for l in labels]
        bars = ax.bar(clean_labels, counts, color="#0284c7", edgecolor="#1e293b", linewidth=0.35, width=0.52)
        ax.set_ylabel("Students", fontsize=6.0, color="#475569", labelpad=2)
        ax.tick_params(axis="both", labelsize=6.0, pad=1)
        max_c = max(counts) if counts else 1
        ax.set_ylim(0, max_c * 1.25)
        for b, c in zip(bars, counts):
            if c > 0:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height() + (max_c * 0.04),
                    str(c),
                    ha="center",
                    va="bottom",
                    fontsize=6.0,
                    fontweight="bold",
                    color="#1e293b"
                )
        ax.grid(axis="y", linestyle="--", alpha=0.3, color="#94a3b8")
    else:
        ax.text(0.5, 0.5, "No SGPI Data Available", ha="center", va="center", color="#94a3b8", fontsize=7.2)
        ax.axis("off")

    ax.set_title("SGPI Score Distribution", fontsize=7.5, fontweight="bold", color="#1F4E78", pad=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", transparent=False)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_semester_analysis_docx(
    analysis_data: Dict[str, Any],
    department_name: Optional[Any] = None,
    batch_name: Optional[Any] = None,
    semester_name: Optional[Any] = None
) -> BytesIO:
    """
    Constructs a calibrated 1-page institutional semester analysis Word document (.docx)
    where every analytical table is accompanied by its corresponding chart.
    """
    doc = docx.Document()

    # Calibrated 1-page A4 Margins
    for section in doc.sections:
        section.top_margin = Inches(0.38)
        section.bottom_margin = Inches(0.85)
        section.left_margin = Inches(0.45)
        section.right_margin = Inches(0.45)
        section.footer_distance = Inches(0.32)
        section.page_width = Inches(8.27)   # A4 width
        section.page_height = Inches(11.69) # A4 height

    hdr = analysis_data.get("header", {})
    sum_obj = analysis_data.get("semester_summary", {})
    sgpi_dist = analysis_data.get("sgpi_distribution", {})
    toppers = analysis_data.get("top_students", [])

    # Handle string or dict parameters robustly
    if isinstance(department_name, dict):
        department_name = department_name.get("department") or department_name.get("branch")
    if isinstance(batch_name, dict):
        batch_name = batch_name.get("batch") or batch_name.get("academic_year")
    if isinstance(semester_name, dict):
        semester_name = semester_name.get("semester")

    dept_display = department_name or hdr.get("department") or hdr.get("branch") or "Artificial Intelligence & Data Science"
    if not str(dept_display).lower().startswith("department"):
        dept_display = f"Department of {str(dept_display).replace('_', ' ')}"

    batch_display = batch_name or hdr.get("batch") or hdr.get("academic_year") or "2021–2022"
    sem_display = semester_name or hdr.get("semester") or "Semester VIII"
    sem_display = str(sem_display).replace("Semester_", "Semester ").replace("Sem_", "Semester ")

    # =========================================================================
    # HEADER SECTION WITH TOP-LEFT LOGO & SOLID HORIZONTAL SEPARATOR LINE
    # =========================================================================
    logo_path = _get_terna_logo_path()
    tbl_hdr = doc.add_table(rows=1, cols=3)
    tbl_hdr.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_hdr.autofit = False
    col_w_hdr = [Inches(1.10), Inches(5.17), Inches(1.10)]
    row_hdr = tbl_hdr.rows[0]

    # Col 0: Terna Logo
    c_logo = row_hdr.cells[0]
    c_logo.width = col_w_hdr[0]
    _set_cell_margins(c_logo, top=0, bottom=0, left=0, right=10)
    p_logo = c_logo.paragraphs[0]
    p_logo.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if logo_path:
        try:
            p_logo.add_run().add_picture(logo_path, width=Inches(0.95))
        except Exception:
            pass

    # Col 1: Centered Academic Header
    c_mid = row_hdr.cells[1]
    c_mid.width = col_w_hdr[1]
    _set_cell_margins(c_mid, top=0, bottom=0, left=0, right=0)
    p_mid = c_mid.paragraphs[0]
    p_mid.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_mid.paragraph_format.space_before = Pt(0)
    p_mid.paragraph_format.space_after = Pt(0)
    p_mid.paragraph_format.line_spacing = Pt(13)

    r_cname = p_mid.add_run("TERNA ENGINEERING COLLEGE\n")
    r_cname.bold = True
    r_cname.font.size = Pt(14.5)
    r_cname.font.color.rgb = COLOR_NAVY

    r_dname = p_mid.add_run(f"{str(dept_display).upper()}\n")
    r_dname.bold = True
    r_dname.font.size = Pt(10.0)
    r_dname.font.color.rgb = COLOR_SLATE

    r_title = p_mid.add_run("SEMESTER-WISE EXAMINATION PERFORMANCE & RESULT ANALYSIS REPORT")
    r_title.bold = True
    r_title.font.size = Pt(8.8)
    r_title.font.color.rgb = COLOR_NAVY

    # Col 2: Symmetry spacer
    c_spc = row_hdr.cells[2]
    c_spc.width = col_w_hdr[2]
    _set_cell_margins(c_spc, top=0, bottom=0, left=0, right=0)

    # Solid continuous institutional separator line on Header Table bottom
    tblPr_hdr = tbl_hdr._tbl.tblPr
    borders_hdr = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="none"/>'
        f'<w:bottom w:val="single" w:sz="12" w:space="0" w:color="1F4E78"/>'
        f'<w:left w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'<w:insideH w:val="none"/>'
        f'<w:insideV w:val="none"/>'
        f'</w:tblBorders>'
    )
    tblPr_hdr.append(borders_hdr)

    for cell_h in row_hdr.cells:
        tcPr_h = cell_h._tc.get_or_add_tcPr()
        tcBorders_h = parse_xml(
            f'<w:tcBorders {nsdecls("w")}>'
            f'<w:bottom w:val="single" w:sz="12" w:space="0" w:color="1F4E78"/>'
            f'</w:tcBorders>'
        )
        tcPr_h.append(tcBorders_h)

    # Spacing below separator line
    p_sp_hdr = doc.add_paragraph()
    p_sp_hdr.paragraph_format.space_before = Pt(2)
    p_sp_hdr.paragraph_format.space_after = Pt(2)
    p_sp_hdr.paragraph_format.line_spacing = Pt(2)

    # =========================================================================
    # METADATA BLOCK
    # =========================================================================
    tbl_meta = doc.add_table(rows=3, cols=2)
    tbl_meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_meta.autofit = False

    appeared = sum_obj.get("appeared", 0)
    meta_items = [
        ("Batch:", f"{batch_display}", "Academic Year:", f"{hdr.get('academic_year', batch_display)}"),
        ("Semester:", f"{sem_display}", "Total Appeared:", f"{appeared} Students"),
        ("Analysis Level:", "Complete Semester Dataset", "Report Date:", datetime.now().strftime("%d-%b-%Y"))
    ]

    col_widths_meta = [Inches(4.80), Inches(2.57)]
    for r_idx, (k1, v1, k2, v2) in enumerate(meta_items):
        row = tbl_meta.rows[r_idx]

        # Left cell
        cell_l = row.cells[0]
        cell_l.width = col_widths_meta[0]
        _set_cell_margins(cell_l, top=12, bottom=12, left=0, right=15)
        p_l = cell_l.paragraphs[0]
        p_l.paragraph_format.space_before = Pt(0)
        p_l.paragraph_format.space_after = Pt(0)
        p_l.paragraph_format.line_spacing = Pt(10)
        r_k1 = p_l.add_run(f"{k1} ")
        r_k1.bold = True
        r_k1.font.size = Pt(8.8)
        r_k1.font.color.rgb = COLOR_SLATE
        r_v1 = p_l.add_run(v1)
        r_v1.bold = (k1 == "Semester:")
        r_v1.font.size = Pt(8.8)
        if k1 == "Semester:":
            r_v1.font.color.rgb = COLOR_NAVY

        # Right cell
        cell_r = row.cells[1]
        cell_r.width = col_widths_meta[1]
        _set_cell_margins(cell_r, top=12, bottom=12, left=15, right=0)
        p_r = cell_r.paragraphs[0]
        p_r.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_r.paragraph_format.space_before = Pt(0)
        p_r.paragraph_format.space_after = Pt(0)
        p_r.paragraph_format.line_spacing = Pt(10)
        r_k2 = p_r.add_run(f"{k2} ")
        r_k2.bold = True
        r_k2.font.size = Pt(8.5)
        r_k2.font.color.rgb = COLOR_MUTED
        r_v2 = p_r.add_run(v2)
        r_v2.font.size = Pt(8.5)
        r_v2.font.color.rgb = COLOR_SLATE

    # =========================================================================
    # 1. SEMESTER CLEARANCE & SUCCESS RATE ANALYSIS (TABLE + DONUT CHART)
    # =========================================================================
    p_sec1 = doc.add_paragraph()
    p_sec1.paragraph_format.space_before = Pt(6)
    p_sec1.paragraph_format.space_after = Pt(2)
    p_sec1.paragraph_format.line_spacing = Pt(10)
    r_s1 = p_sec1.add_run("1. SEMESTER CLEARANCE & SUCCESS RATE ANALYSIS")
    r_s1.bold = True
    r_s1.font.size = Pt(9.5)
    r_s1.font.color.rgb = COLOR_NAVY

    passed = sum_obj.get("passed", 0)
    failed = sum_obj.get("failed", 0)
    pass_rate = sum_obj.get("pass_rate", 0.0)
    fail_rate = sum_obj.get("fail_rate", 0.0)
    success_rate = sum_obj.get("success_rate", pass_rate)

    first_class_cnt = sum_obj.get("first_class_count", 0)
    first_class_pct = sum_obj.get("first_class_rate", 0.0)
    dist_cnt = sum_obj.get("distinction_count", 0)
    dist_pct = sum_obj.get("distinction_rate", 0.0)
    fc_and_dist_cnt = sum_obj.get("first_class_and_dist_count", first_class_cnt + dist_cnt)
    fc_and_dist_pct = sum_obj.get("first_class_and_dist_pct", 0.0)

    table_data_sem = [
        ("Total Students Appeared", appeared, "100.00%", None),
        ("Students Passed (All Clear)", passed, f"{pass_rate:.2f}%", COLOR_SUCCESS),
        ("Students Failed (Backlog / KT)", failed, f"{fail_rate:.2f}%", COLOR_DANGER),
        ("Semester Success Rate", passed, f"{success_rate:.2f}%", COLOR_NAVY),
        ("Students with First Class (60% to 74.99%)", first_class_cnt, f"{first_class_pct:.2f}%", None),
        ("Students with Distinction (≥ 75%)", dist_cnt, f"{dist_pct:.2f}%", None),
        ("Total First Class & Distinction (≥ 60%)", fc_and_dist_cnt, f"{fc_and_dist_pct:.2f}%", COLOR_NAVY),
    ]

    tbl_sem = doc.add_table(rows=len(table_data_sem) + 1, cols=4)
    tbl_sem.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(tbl_sem)
    col_w_sem = [Inches(2.55), Inches(0.95), Inches(1.10), Inches(2.77)]

    # Header Row
    hdr_row_sem = tbl_sem.rows[0]
    sem_headers = ["Clearance Status / Metric", "Count", "Percentage", "Clearance Distribution"]
    for i, h_text in enumerate(sem_headers):
        c = hdr_row_sem.cells[i]
        c.width = col_w_sem[i]
        _set_cell_background(c, HEX_NAVY)
        _set_cell_margins(c, top=26, bottom=26, left=35, right=35)
        p = c.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(9)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if (i in (1, 2)) else (WD_ALIGN_PARAGRAPH.CENTER if i == 3 else WD_ALIGN_PARAGRAPH.LEFT)
        r = p.add_run(h_text)
        r.bold = True
        r.font.size = Pt(7.8)
        r.font.color.rgb = RGBColor(255, 255, 255)

    # Data Rows
    for r_idx, (label, count, pct, text_color) in enumerate(table_data_sem):
        row = tbl_sem.rows[r_idx + 1]
        is_highlight = ("Success Rate" in label or "Total First Class" in label or "Students Passed" in label)
        bg = HEX_LIGHT_BLUE if is_highlight else (HEX_ZEBRA if r_idx % 2 == 1 else "FFFFFF")

        for c_idx, val in enumerate([label, str(count), str(pct)]):
            cell = row.cells[c_idx]
            cell.width = col_w_sem[c_idx]
            _set_cell_background(cell, bg)
            _set_cell_margins(cell, top=16, bottom=16, left=35, right=35)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(9)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx > 0 else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(val)
            run.font.size = Pt(7.8)
            if is_highlight or c_idx > 0:
                run.bold = True
            if text_color:
                run.font.color.rgb = text_color
            else:
                run.font.color.rgb = COLOR_SLATE

    # Merge Col 3 across rows 1 to 7 for the Donut Chart
    cell_top_c1 = tbl_sem.cell(1, 3)
    cell_bot_c1 = tbl_sem.cell(len(table_data_sem), 3)
    c_merged_c1 = cell_top_c1.merge(cell_bot_c1)
    c_merged_c1.width = col_w_sem[3]
    _set_cell_margins(c_merged_c1, top=6, bottom=6, left=10, right=10)
    _set_cell_background(c_merged_c1, "FFFFFF")
    sem_chart_buf = _generate_semester_clearance_chart(sum_obj)
    p_c_donut = c_merged_c1.paragraphs[0]
    p_c_donut.paragraph_format.space_before = Pt(0)
    p_c_donut.paragraph_format.space_after = Pt(0)
    p_c_donut.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_c_donut.add_run().add_picture(sem_chart_buf, width=Inches(2.70))

    # =========================================================================
    # 2. INSTITUTIONAL CLASSIFICATION & SGPI DISTRIBUTION (TABLES + DUAL CHARTS)
    # =========================================================================
    p_sec2 = doc.add_paragraph()
    p_sec2.paragraph_format.space_before = Pt(6)
    p_sec2.paragraph_format.space_after = Pt(2)
    p_sec2.paragraph_format.line_spacing = Pt(10)
    r_s2 = p_sec2.add_run("2. INSTITUTIONAL CLASSIFICATION & SGPI DISTRIBUTION")
    r_s2.bold = True
    r_s2.font.size = Pt(9.5)
    r_s2.font.color.rgb = COLOR_NAVY

    class_table = sum_obj.get("classification", {}).get("table", [])
    if class_table:
        class_rows = [
            (r.get("classification", "Class"), r.get("students", 0), f"{r.get('percentage', 0.0):.2f}%")
            for r in class_table
        ]
    else:
        class_rows = [
            ("First Class with Distinction (Distinction)", dist_cnt, f"{dist_pct:.2f}%"),
            ("First Class (60% to 74.99%)", first_class_cnt, f"{first_class_pct:.2f}%"),
            ("Second Class", sum_obj.get("second_class_count", 0), f"{(sum_obj.get('second_class_count', 0) / max(1, appeared) * 100):.2f}%"),
            ("Pass Class", sum_obj.get("pass_class_count", 0), f"{(sum_obj.get('pass_class_count', 0) / max(1, appeared) * 100):.2f}%"),
            ("Fail / KT", failed, f"{fail_rate:.2f}%")
        ]

    sgpi_table_data = sgpi_dist.get("table", [])
    if sgpi_table_data:
        sgpi_rows = [
            (item.get("range", "Range"), item.get("students", 0), f"{item.get('percentage', 0.0):.2f}%")
            for item in sgpi_table_data
        ]
    else:
        sgpi_rows = [
            ("9.00 - 10.00", 0, "0.00%"),
            ("8.00 - 8.99", 0, "0.00%"),
            ("7.00 - 7.99", 0, "0.00%"),
            ("6.00 - 6.99", 0, "0.00%"),
            ("Below 6.00", 0, "0.00%")
        ]

    max_data_rows = max(len(class_rows), len(sgpi_rows), 5)
    tbl_sec2 = doc.add_table(rows=max_data_rows + 2, cols=4)
    tbl_sec2.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(tbl_sec2)
    col_w_sec2 = [Inches(2.42), Inches(1.26), Inches(2.42), Inches(1.27)]

    hdr_s2 = tbl_sec2.rows[0]
    sec2_headers = ["Institutional Classification", "Students (%)", "SGPI Range Distribution", "Students (%)"]
    for i, h_text in enumerate(sec2_headers):
        c = hdr_s2.cells[i]
        c.width = col_w_sec2[i]
        _set_cell_background(c, "2C5282")
        _set_cell_margins(c, top=28, bottom=28, left=35, right=35)
        p = c.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(9)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if (i % 2 == 1) else WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(h_text)
        r.bold = True
        r.font.size = Pt(8.2)
        r.font.color.rgb = RGBColor(255, 255, 255)

    for r_idx in range(max_data_rows):
        row = tbl_sec2.rows[r_idx + 1]
        bg = HEX_ZEBRA if r_idx % 2 == 1 else "FFFFFF"

        # Classification side (Cols 0, 1)
        if r_idx < len(class_rows):
            cl_lbl, cl_cnt, cl_pct = class_rows[r_idx]
            cl_val = f"{cl_cnt} ({cl_pct})"
        else:
            cl_lbl, cl_val = "—", "—"

        for c_i, val in [(0, cl_lbl), (1, cl_val)]:
            c = row.cells[c_i]
            c.width = col_w_sec2[c_i]
            _set_cell_background(c, bg)
            _set_cell_margins(c, top=16, bottom=16, left=35, right=35)
            p = c.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(9)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_i == 1 else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(val)
            run.font.size = Pt(8.2)
            if c_i == 1:
                run.bold = True

        # SGPI side (Cols 2, 3)
        if r_idx < len(sgpi_rows):
            sg_lbl, sg_cnt, sg_pct = sgpi_rows[r_idx]
            sg_val = f"{sg_cnt} ({sg_pct})"
        else:
            sg_lbl, sg_val = "—", "—"

        for c_i, val in [(2, sg_lbl), (3, sg_val)]:
            c = row.cells[c_i]
            c.width = col_w_sec2[c_i]
            _set_cell_background(c, bg)
            _set_cell_margins(c, top=16, bottom=16, left=35, right=35)
            p = c.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(9)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_i == 3 else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(val)
            run.font.size = Pt(8.2)
            if c_i == 3:
                run.bold = True

    # Chart row at bottom of Section 2
    row_charts = tbl_sec2.rows[max_data_rows + 1]
    c_class_chart = row_charts.cells[0].merge(row_charts.cells[1])
    c_sgpi_chart = row_charts.cells[2].merge(row_charts.cells[3])
    _set_cell_margins(c_class_chart, top=8, bottom=8, left=10, right=10)
    _set_cell_margins(c_sgpi_chart, top=8, bottom=8, left=10, right=10)
    _set_cell_background(c_class_chart, "FFFFFF")
    _set_cell_background(c_sgpi_chart, "FFFFFF")

    class_chart_buf = _generate_classification_chart_image(sum_obj)
    p_cl = c_class_chart.paragraphs[0]
    p_cl.paragraph_format.space_before = Pt(0)
    p_cl.paragraph_format.space_after = Pt(0)
    p_cl.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cl.add_run().add_picture(class_chart_buf, width=Inches(3.30))

    sgpi_chart_buf = _generate_sgpi_distribution_chart_image(sgpi_dist)
    p_sg = c_sgpi_chart.paragraphs[0]
    p_sg.paragraph_format.space_before = Pt(0)
    p_sg.paragraph_format.space_after = Pt(0)
    p_sg.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sg.add_run().add_picture(sgpi_chart_buf, width=Inches(3.30))

    # =========================================================================
    # 3. TOP 5 SEMESTER MERIT LEADERS (TABLE ONLY)
    # =========================================================================
    p_sec3 = doc.add_paragraph()
    p_sec3.paragraph_format.space_before = Pt(6)
    p_sec3.paragraph_format.space_after = Pt(2)
    p_sec3.paragraph_format.line_spacing = Pt(10)
    r_s3 = p_sec3.add_run("3. TOP 5 SEMESTER MERIT LEADERS (RANKED BY SGPI)")
    r_s3.bold = True
    r_s3.font.size = Pt(9.5)
    r_s3.font.color.rgb = COLOR_NAVY

    toppers_5 = toppers[:5] if toppers else []
    tbl_top = doc.add_table(rows=max(len(toppers_5), 1) + 1, cols=5)
    tbl_top.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(tbl_top)
    col_w_top = [Inches(0.85), Inches(2.72), Inches(1.30), Inches(1.25), Inches(1.25)]

    hdr_top = tbl_top.rows[0]
    toppers_headers = ["Rank", "Student Name", "Seat No / PRN", "Semester SGPI", "Percentage"]
    for i, h_text in enumerate(toppers_headers):
        c = hdr_top.cells[i]
        c.width = col_w_top[i]
        _set_cell_background(c, HEX_NAVY)
        _set_cell_margins(c, top=28, bottom=28, left=35, right=35)
        p = c.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(9)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i in (3, 4) else (WD_ALIGN_PARAGRAPH.CENTER if i in (0, 2) else WD_ALIGN_PARAGRAPH.LEFT)
        r = p.add_run(h_text)
        r.bold = True
        r.font.size = Pt(8.2)
        r.font.color.rgb = RGBColor(255, 255, 255)

    if toppers_5:
        for r_idx, st in enumerate(toppers_5):
            row = tbl_top.rows[r_idx + 1]
            bg = HEX_ZEBRA if r_idx % 2 == 1 else "FFFFFF"

            rk = st.get("rank") or (r_idx + 1)
            name = st.get("student_name") or st.get("name") or "—"
            seat = st.get("seat_or_prn") or st.get("seat_no") or st.get("prn") or "—"
            sg = f"{st.get('sgpi'):.2f}" if st.get("sgpi") is not None else "—"
            pct = f"{st.get('percentage'):.2f}%" if st.get("percentage") is not None else "—"

            vals = [f"Rank {rk}", name, seat, sg, pct]
            for c_idx, val in enumerate(vals):
                c = row.cells[c_idx]
                c.width = col_w_top[c_idx]
                _set_cell_background(c, bg)
                _set_cell_margins(c, top=18, bottom=18, left=35, right=35)
                p = c.paragraphs[0]
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = Pt(9)
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx in (3, 4) else (WD_ALIGN_PARAGRAPH.CENTER if c_idx in (0, 2) else WD_ALIGN_PARAGRAPH.LEFT)
                run = p.add_run(val)
                run.font.size = Pt(8.2)
                if c_idx in (0, 3, 4):
                    run.bold = True
    else:
        row = tbl_top.rows[1]
        c_emp = row.cells[0].merge(row.cells[4])
        _set_cell_background(c_emp, "FFFFFF")
        p_emp = c_emp.paragraphs[0]
        p_emp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_emp = p_emp.add_run("No topper records available for this semester.")
        r_emp.font.size = Pt(8.0)
        r_emp.font.color.rgb = COLOR_MUTED

    # =========================================================================
    # FOOTER: INSTITUTIONAL SIGNATURES (RESULT COORDINATOR & HOD ONLY)
    # =========================================================================
    footer = doc.sections[0].footer
    footer.is_linked_to_previous = False

    # Remove default empty paragraph in footer if present
    if footer.paragraphs:
        p0 = footer.paragraphs[0]
        p0._element.getparent().remove(p0._element)

    tbl_sig = footer.add_table(rows=1, cols=2, width=Inches(7.37))
    tbl_sig.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_sig.autofit = False
    col_w_sig = [Inches(3.68), Inches(3.69)]

    sig_titles = [
        ("Result Coordinator", f"{dept_display}"),
        ("Head of Department (HOD)", "Terna Engineering College")
    ]

    row_sig = tbl_sig.rows[0]
    for i, (title, sub) in enumerate(sig_titles):
        c = row_sig.cells[i]
        c.width = col_w_sig[i]
        _set_cell_margins(c, top=0, bottom=0, left=15, right=15)
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(11)

        r_l = p.add_run("_____________________________________\n")
        r_l.font.size = Pt(8.5)
        r_l.font.color.rgb = COLOR_MUTED

        r_t = p.add_run(f"{title}\n")
        r_t.bold = True
        r_t.font.size = Pt(9.0)
        r_t.font.color.rgb = COLOR_NAVY

        r_s = p.add_run(sub)
        r_s.font.size = Pt(7.8)
        r_s.font.color.rgb = COLOR_SLATE

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
