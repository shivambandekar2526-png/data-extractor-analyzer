"""
Batch Academic Excel Report Generator (10 Mandatory Sheets)
===========================================================
Generates the official multi-semester comprehensive analysis workbook
conforming to all 10 academic audit and university analysis specifications:

1. Read Me: Executive context, data sources, extraction engines, AI correction flag, sheet dictionary.
2. Overall Summary: Total students, cohort completion, pass/fail totals, SGPI metrics, grade distribution.
3. Semester Analysis: Semester metrics (Total, Pass, Fail, Pass %, Avg SGPI, Max, Min, KT count, KT subjects).
4. Student Progression: Consolidated student roster across Sem 1-8 (ID, Name, Results, SGPIs, KTs, Avg, Min, Max, Trend, Final Status).
5. KT Analysis: Semester KT rates, subject counts, and repeated backlog tracking.
6. Topper Analysis: Top performers per semester + Multi-Semester Consistent Honor Roll.
7. Retention / Progression Analysis: Semester counts, progression transitions, lateral entry counts, explicit non-dropout footnote.
8. Student Performance Analysis: Descriptive cohorts (Consistently Strong, Improving, Declining, Fluctuating, Academic Difficulties).
9. Data Quality: Detailed audit log of anomalies, missing values, duplicates, and inconsistencies without silent modifications.
10. Semester Data: Traceable individual worksheets (Sem 1 Data, Sem 2 Data, ...) preserving original records.
"""

import os
import re
from typing import Any, Dict, List, Optional, Union

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --- Professional Styling Palette ---
NAVY_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
NAVY_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1F4E78")
SUBTITLE_FONT = Font(name="Calibri", size=10, italic=True, color="555555")
SECTION_FONT = Font(name="Calibri", size=12, bold=True, color="1F4E78")

TABLE_HEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
TABLE_HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="1F4E78")

ZEBRA_FILL = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
PASS_FILL = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
FAIL_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
GOLD_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

CARD_BG_FILL = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
CARD_LABEL_FONT = Font(name="Calibri", size=9, bold=True, color="666666")
CARD_VAL_FONT = Font(name="Calibri", size=14, bold=True, color="1F4E78")

DATA_FONT = Font(name="Calibri", size=10, bold=False, color="000000")
BOLD_DATA_FONT = Font(name="Calibri", size=10, bold=True, color="000000")
ITALIC_NOTE_FONT = Font(name="Calibri", size=9, italic=True, color="555555")

THIN_BORDER_SIDE = Side(style="thin", color="D3D3D3")
CELL_BORDER = Border(
    left=THIN_BORDER_SIDE,
    right=THIN_BORDER_SIDE,
    top=THIN_BORDER_SIDE,
    bottom=THIN_BORDER_SIDE
)

HEADER_BORDER = Border(
    left=THIN_BORDER_SIDE,
    right=THIN_BORDER_SIDE,
    top=Side(style="medium", color="1F4E78"),
    bottom=Side(style="medium", color="1F4E78")
)

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def auto_fit_columns(ws, min_width: int = 10, max_width: int = 45):
    """Dynamically adjust worksheet column widths based on maximum cell content."""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if "\n" in val_str:
                lines = val_str.split("\n")
                line_len = max(len(l) for l in lines) if lines else 0
                max_len = max(max_len, line_len)
            else:
                max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = max(min(max_len + 3, max_width), min_width)


def _safe_format_cell(cell, val: Any, is_header: bool = False, align=ALIGN_LEFT, fill=None, font=DATA_FONT):
    """Helper to cleanly apply value and styling to a single cell."""
    cell.value = "N/A" if val is None or val == "" else val
    cell.font = font
    cell.alignment = align
    cell.border = CELL_BORDER
    if fill:
        cell.fill = fill


class BatchExcelReportGenerator:
    """Creates the 10-sheet Academic Multi-Semester Excel Workbook."""

    def __init__(self, analytics_data: Dict[str, Any]):
        self.data = analytics_data
        self.batch_meta = self.data.get("batch_metadata", {})
        self.available_sems = self.data.get("available_semesters", [])
        self.overall_summary = self.data.get("overall_summary", {})
        self.semester_analysis = self.data.get("semester_analysis", [])
        self.student_progression = self.data.get("student_progression", [])
        self.kt_analysis = self.data.get("kt_analysis", [])
        self.repeated_kt_students = self.data.get("repeated_kt_students", [])
        self.topper_by_semester = self.data.get("topper_by_semester", {})
        self.multi_sem_toppers = self.data.get("multi_sem_toppers", [])
        self.retention_flow = self.data.get("retention_flow", [])
        self.cohort_counts = self.data.get("cohort_retention_summary", {})
        self.student_cats = self.data.get("student_performance_categorization", {})
        self.quality_issues = self.data.get("data_quality_issues", [])
        self.semester_raw = self.data.get("semester_raw_data", {})

    def generate_workbook(self, target_filepath: str) -> str:
        """Construct all 10 sheets and save to target_filepath."""
        wb = openpyxl.Workbook()
        # Remove default sheet
        default_sheet = wb.active

        # Build each of the 10 sheets
        self._build_sheet_1_readme(wb.create_sheet("Read Me"))
        self._build_sheet_2_overall_summary(wb.create_sheet("Overall Summary"))
        self._build_sheet_3_semester_analysis(wb.create_sheet("Semester Analysis"))
        self._build_sheet_4_student_progression(wb.create_sheet("Student Progression"))
        self._build_sheet_5_kt_analysis(wb.create_sheet("KT Analysis"))
        self._build_sheet_6_topper_analysis(wb.create_sheet("Topper Analysis"))
        self._build_sheet_7_retention(wb.create_sheet("Retention Progression"))
        self._build_sheet_8_performance(wb.create_sheet("Student Performance"))
        self._build_sheet_9_data_quality(wb.create_sheet("Data Quality"))
        self._build_sheet_10_semester_data(wb)

        # Remove initial empty sheet
        if default_sheet in wb.worksheets:
            wb.remove(default_sheet)

        os.makedirs(os.path.dirname(os.path.abspath(target_filepath)), exist_ok=True)
        wb.save(target_filepath)
        return target_filepath

    # -----------------------------------------------------------------
    # SHEET 1: READ ME
    # -----------------------------------------------------------------
    def _build_sheet_1_readme(self, ws):
        ws.title = "Read Me"
        ws.views.sheetView[0].showGridLines = True

        # Title Banner
        ws.cell(row=2, column=2, value="ACADEMIC BATCH ANALYSIS WORKBOOK (SEM 1 TO 8)").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Official Multi-Semester Academic Performance Audit & Intelligence Report").font = SUBTITLE_FONT

        # Section 1: Batch Configuration
        ws.cell(row=5, column=2, value="1. BATCH CONFIGURATION & EXTRACTION PARAMETERS").font = SECTION_FONT

        batch_name = self.batch_meta.get("batch_name", "Academic Batch")
        sems_str = ", ".join([f"Sem {s}" for s in self.available_sems]) or "N/A"
        pdf_engine = self.data.get("pdf_pipeline", "Local AI (Possibility & Ollama GPU)").title()
        excel_mode = "Enabled (AI Restructuring & Header Normalization)" if self.data.get("excel_mode") == "ai_fix" else "Disabled (Direct Fast Tabular Import - 0 Tokens)"

        config_rows = [
            ("Batch Under Analysis:", batch_name),
            ("Semesters Included:", sems_str),
            ("Total Available Semesters:", len(self.available_sems)),
            ("PDF Extraction Engine:", pdf_engine),
            ("Excel AI Restructuring Mode:", excel_mode),
            ("Analysis Generation Date:", os.environ.get("TODAY", "Current Academic Session")),
        ]

        for idx, (label, val) in enumerate(config_rows, start=6):
            c_lbl = ws.cell(row=idx, column=2, value=label)
            c_lbl.font = BOLD_DATA_FONT
            c_lbl.fill = TABLE_HEADER_FILL
            c_lbl.border = CELL_BORDER

            c_val = ws.cell(row=idx, column=3, value=val)
            c_val.font = DATA_FONT
            c_val.border = CELL_BORDER

        # Section 2: Input Files Per Semester
        start_row = 13
        ws.cell(row=start_row, column=2, value="2. INPUT DATA SOURCE PER SEMESTER").font = SECTION_FONT

        headers = ["Semester", "Source Filename", "File Format", "Extraction Mechanism"]
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=start_row + 1, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_CENTER
            c.border = HEADER_BORDER

        r_curr = start_row + 2
        for s in self.available_sems:
            f_info = next((f for f in self.batch_meta.get("semesters_detected", []) if f["semester"] == s), {})
            ftype = f_info.get("file_type", "excel").upper()
            fname = f_info.get("filename", f"Sem_{s}")
            engine = pdf_engine if ftype == "PDF" else ("AI Schema Parser" if self.data.get("excel_mode") == "ai_fix" else "Direct Fast Ingester")

            ws.cell(row=r_curr, column=2, value=f"Semester {s}").font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=3, value=fname).font = DATA_FONT
            ws.cell(row=r_curr, column=4, value=ftype).font = DATA_FONT
            ws.cell(row=r_curr, column=5, value=engine).font = DATA_FONT

            for c in range(2, 6):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER
                if r_curr % 2 == 1:
                    ws.cell(row=r_curr, column=c).fill = ZEBRA_FILL
            r_curr += 1

        # Section 3: Explanation of Workbook Sheets
        r_curr += 2
        ws.cell(row=r_curr, column=2, value="3. WORKBOOK STRUCTURE & METRICS EXPLANATION").font = SECTION_FONT
        r_curr += 1

        sheet_defs = [
            ("1. Read Me", "Overview of batch parameters, input formats, engines, and analytical conventions."),
            ("2. Overall Summary", "Cohort KPIs: Total distinct candidates, pass/fail counts, graduation eligibility, and SGPI statistics."),
            ("3. Semester Analysis", "Comprehensive semester-by-semester breakdown: appeared counts, pass rates, SGPI ranges, and KT counts."),
            ("4. Student Progression", "Master tracking grid for all candidates across Sem 1 to 8: individual results, SGPIs, backlog counts, and progression delta."),
            ("5. KT Analysis", "Semester-wise backlog load, KT rates, and identification of recurring academic backlogs across semesters."),
            ("6. Topper Analysis", "Top merit performers (Top 10) for each semester and multi-semester consistent toppers roll."),
            ("7. Retention Progression", "Identifiable student persistence, transition flow (Sem 1->2, 2->3 lateral diploma entry), without assuming dropouts."),
            ("8. Student Performance", "Descriptive performance segments: Consistently Strong, Improving, Declining, Fluctuating, and Remedial Difficulties."),
            ("9. Data Quality", "Non-destructive audit log: flags missing values, duplicate records, discontinuous appearances, and name discrepancies."),
            ("10. Semester Data", "Traceable raw/processed student records for each semester preserving source examination ledger values.")
        ]

        for s_title, s_desc in sheet_defs:
            c1 = ws.cell(row=r_curr, column=2, value=s_title)
            c1.font = BOLD_DATA_FONT
            c1.fill = CARD_BG_FILL
            c1.border = CELL_BORDER

            c2 = ws.cell(row=r_curr, column=3, value=s_desc)
            c2.font = DATA_FONT
            c2.border = CELL_BORDER
            r_curr += 1

        # Footnote
        r_curr += 1
        note_text = "Important Rule: Missing academic data is explicitly preserved and marked 'N/A' without synthetic estimation. Students missing from individual semesters are not assumed to have dropped out."
        c_note = ws.cell(row=r_curr, column=2, value=note_text)
        c_note.font = ITALIC_NOTE_FONT

        auto_fit_columns(ws, min_width=15, max_width=60)

    # -----------------------------------------------------------------
    # SHEET 2: OVERALL SUMMARY
    # -----------------------------------------------------------------
    def _build_sheet_2_overall_summary(self, ws):
        ws.title = "Overall Summary"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="BATCH-LEVEL OVERALL SUMMARY & PERFORMANCE STATISTICS").font = TITLE_FONT
        ws.cell(row=3, column=2, value=f"Comprehensive Cohort Statistics for {self.batch_meta.get('batch_name', 'Current Batch')}").font = SUBTITLE_FONT

        # Top Metric Cards (Row 5 - 8)
        cards = [
            ("TOTAL STUDENTS", self.overall_summary.get("total_students", 0), "Distinct Enrolled"),
            ("CLEARED ALL AVAILABLE", self.overall_summary.get("cleared_all_available_semesters", 0), f"{self.overall_summary.get('cleared_all_percentage', 0)}% of Cohort"),
            ("STUDENTS WITH KT", self.overall_summary.get("students_with_any_kt", 0), f"{self.overall_summary.get('kt_student_percentage', 0)}% Backlog Burden"),
            ("BATCH AVERAGE SGPI", f"{self.overall_summary.get('batch_average_sgpi', 0.0):.2f}" if self.overall_summary.get("batch_average_sgpi") else "N/A", "Mean Cumulative"),
            ("TOPPER SGPI", f"{self.overall_summary.get('batch_highest_sgpi', 0.0):.2f}" if self.overall_summary.get("batch_highest_sgpi") else "N/A", "Highest Scored"),
            ("GRADUATED / ELIGIBLE", self.overall_summary.get("graduated_eligible", 0), "Cleared Final Degree")
        ]

        col_curr = 2
        for title, val, sub in cards:
            ws.cell(row=5, column=col_curr, value=title).font = CARD_LABEL_FONT
            ws.cell(row=5, column=col_curr).alignment = ALIGN_CENTER
            ws.cell(row=5, column=col_curr).fill = CARD_BG_FILL

            ws.cell(row=6, column=col_curr, value=val).font = CARD_VAL_FONT
            ws.cell(row=6, column=col_curr).alignment = ALIGN_CENTER
            ws.cell(row=6, column=col_curr).fill = CARD_BG_FILL

            ws.cell(row=7, column=col_curr, value=sub).font = ITALIC_NOTE_FONT
            ws.cell(row=7, column=col_curr).alignment = ALIGN_CENTER
            ws.cell(row=7, column=col_curr).fill = CARD_BG_FILL

            for r in [5, 6, 7]:
                ws.cell(row=r, column=col_curr).border = CELL_BORDER

            col_curr += 1

        # Table 1: Cohort Pass / Fail & Completion Breakdown
        ws.cell(row=9, column=2, value="1. COHORT PASS/FAIL & DEGREE COMPLETION STATUS").font = SECTION_FONT

        headers = ["Metric Classification", "Student Count", "Percentage of Cohort (%)", "Academic Audit Context"]
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=10, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_CENTER
            c.border = HEADER_BORDER

        tot = self.overall_summary.get("total_students", 0)
        c_all = self.overall_summary.get("cleared_all_available_semesters", 0)
        kt_all = self.overall_summary.get("students_with_any_kt", 0)
        app_all = self.overall_summary.get("appeared_all_available_semesters", 0)
        grad = self.overall_summary.get("graduated_eligible", 0)

        rows_data = [
            ("Total Distinct Candidates Enrolled Across Batch", tot, 100.0, "Total students with at least 1 semester record"),
            ("Candidates Appearing in All Available Semesters", app_all, round((app_all / tot) * 100, 1) if tot else 0.0, "Students with uninterrupted academic record across cohort"),
            ("Candidates Cleared All Available Semesters (Zero KT)", c_all, round((c_all / tot) * 100, 1) if tot else 0.0, "Consistently cleared all examinations without any active backlog"),
            ("Candidates Incurring KT in One or More Semesters", kt_all, round((kt_all / tot) * 100, 1) if tot else 0.0, "Students who encountered ATKT/backlog in at least one semester"),
            ("Graduation Eligible (Cleared Degree Requirements)", grad, round((grad / tot) * 100, 1) if tot else 0.0, "Cleared all required semester examinations up to Sem 8")
        ]

        r_curr = 11
        for label, cnt, pct, note in rows_data:
            ws.cell(row=r_curr, column=2, value=label).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=3, value=cnt).font = DATA_FONT
            ws.cell(row=r_curr, column=3).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=3).number_format = "#,##0"

            ws.cell(row=r_curr, column=4, value=pct / 100.0).font = DATA_FONT
            ws.cell(row=r_curr, column=4).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=4).number_format = "0.0%"

            ws.cell(row=r_curr, column=5, value=note).font = DATA_FONT

            for c in range(2, 6):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER
                if r_curr % 2 == 1:
                    ws.cell(row=r_curr, column=c).fill = ZEBRA_FILL
            r_curr += 1

        # Table 2: Academic Performance & Grade Distribution
        r_curr += 2
        ws.cell(row=r_curr, column=2, value="2. OVERALL GRADE & SGPI PERFORMANCE DISTRIBUTION").font = SECTION_FONT
        r_curr += 1

        g_headers = ["Grade Classification", "SGPI Range", "Student Count", "Cohort Share (%)"]
        for c_idx, h in enumerate(g_headers, start=2):
            c = ws.cell(row=r_curr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_CENTER
            c.border = HEADER_BORDER
        r_curr += 1

        g_dist = self.overall_summary.get("grade_distribution", {})
        grade_rows = [
            ("Distinction (Outstanding)", "SGPI ≥ 7.50", g_dist.get("distinction", 0)),
            ("First Class", "6.75 ≤ SGPI < 7.50", g_dist.get("first_class", 0)),
            ("Second Class", "6.00 ≤ SGPI < 6.75", g_dist.get("second_class", 0)),
            ("Pass Class", "SGPI < 6.00", g_dist.get("pass_class", 0)),
        ]

        for g_name, g_rng, cnt in grade_rows:
            ws.cell(row=r_curr, column=2, value=g_name).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=3, value=g_rng).font = DATA_FONT
            ws.cell(row=r_curr, column=3).alignment = ALIGN_CENTER

            ws.cell(row=r_curr, column=4, value=cnt).font = DATA_FONT
            ws.cell(row=r_curr, column=4).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=4).number_format = "#,##0"

            pct = (cnt / tot) if tot else 0.0
            ws.cell(row=r_curr, column=5, value=pct).font = DATA_FONT
            ws.cell(row=r_curr, column=5).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=5).number_format = "0.0%"

            for c in range(2, 6):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER
                if r_curr % 2 == 1:
                    ws.cell(row=r_curr, column=c).fill = ZEBRA_FILL
            r_curr += 1

        auto_fit_columns(ws, min_width=14, max_width=45)

    # -----------------------------------------------------------------
    # SHEET 3: SEMESTER ANALYSIS
    # -----------------------------------------------------------------
    def _build_sheet_3_semester_analysis(self, ws):
        ws.title = "Semester Analysis"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="SEMESTER-WISE ACADEMIC PERFORMANCE AUDIT").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Detailed Pass/Fail, SGPI Range, and KT Counts per Semester").font = SUBTITLE_FONT

        headers = [
            "Semester", "Input Type", "Total Students", "Passed", "Failed / KT",
            "Pass Rate (%)", "Fail Rate (%)", "Average SGPI", "Highest SGPI",
            "Lowest Passing SGPI", "KT Students", "Total KT Subjects"
        ]

        r_hdr = 5
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER
        ws.row_dimensions[r_hdr].height = 28

        r_curr = 6
        for s_item in self.semester_analysis:
            ws.cell(row=r_curr, column=2, value=s_item["semester_name"]).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=3, value=s_item["input_type"]).font = DATA_FONT
            ws.cell(row=r_curr, column=3).alignment = ALIGN_CENTER

            # Counts
            ws.cell(row=r_curr, column=4, value=s_item["total_students"]).font = DATA_FONT
            ws.cell(row=r_curr, column=4).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=4).number_format = "#,##0"

            ws.cell(row=r_curr, column=5, value=s_item["passed"]).font = DATA_FONT
            ws.cell(row=r_curr, column=5).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=5).number_format = "#,##0"

            ws.cell(row=r_curr, column=6, value=s_item["failed"]).font = DATA_FONT
            ws.cell(row=r_curr, column=6).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=6).number_format = "#,##0"

            # Percentages
            ws.cell(row=r_curr, column=7, value=s_item["pass_percentage"] / 100.0).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=7).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=7).number_format = "0.0%"
            ws.cell(row=r_curr, column=7).fill = PASS_FILL

            ws.cell(row=r_curr, column=8, value=s_item["fail_percentage"] / 100.0).font = DATA_FONT
            ws.cell(row=r_curr, column=8).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=8).number_format = "0.0%"
            if s_item["fail_percentage"] > 25.0:
                ws.cell(row=r_curr, column=8).fill = FAIL_FILL

            # SGPIs
            for c_pos, key in [(9, "average_sgpi"), (10, "highest_sgpi"), (11, "lowest_sgpi")]:
                val = s_item.get(key)
                c_cell = ws.cell(row=r_curr, column=c_pos)
                if val is not None:
                    c_cell.value = val
                    c_cell.number_format = "0.00"
                    c_cell.alignment = ALIGN_RIGHT
                else:
                    c_cell.value = "N/A"
                    c_cell.alignment = ALIGN_CENTER
                c_cell.font = DATA_FONT

            # KT info
            ws.cell(row=r_curr, column=12, value=s_item["kt_students_count"]).font = DATA_FONT
            ws.cell(row=r_curr, column=12).alignment = ALIGN_RIGHT

            kt_sub = s_item.get("total_kt_subjects")
            c_sub = ws.cell(row=r_curr, column=13, value=kt_sub if kt_sub is not None else "N/A")
            c_sub.font = DATA_FONT
            c_sub.alignment = ALIGN_RIGHT if kt_sub is not None else ALIGN_CENTER

            for col in range(2, 14):
                ws.cell(row=r_curr, column=col).border = CELL_BORDER

            r_curr += 1

        auto_fit_columns(ws, min_width=12, max_width=30)

    # -----------------------------------------------------------------
    # SHEET 4: STUDENT PROGRESSION
    # -----------------------------------------------------------------
    def _build_sheet_4_student_progression(self, ws):
        ws.title = "Student Progression"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="CONSOLIDATED STUDENT PROGRESSION MATRIX (SEMESTER 1 TO 8)").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Individual Student Trajectory across all Academic Semesters").font = SUBTITLE_FONT

        # Dynamic Header Construction based on available semesters
        base_headers = ["Student ID", "Canonical Student Name", "PRN / Reg No"]
        sem_headers = []
        for s in range(1, 9):
            sem_headers.extend([f"Sem {s} Result", f"Sem {s} SGPI", f"Sem {s} KT"])
        stat_headers = ["Average SGPI", "Highest SGPI", "Lowest SGPI", "Progression Trend", "Final / Current Status"]

        all_headers = base_headers + sem_headers + stat_headers

        r_hdr = 5
        for c_idx, h in enumerate(all_headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER
        ws.row_dimensions[r_hdr].height = 28
        ws.freeze_panes = "E6"

        r_curr = 6
        for p in self.student_progression:
            ws.cell(row=r_curr, column=2, value=p["student_id"]).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

            ws.cell(row=r_curr, column=3, value=p["canonical_name"]).font = BOLD_DATA_FONT

            ws.cell(row=r_curr, column=4, value=p.get("prn") or "N/A").font = DATA_FONT
            ws.cell(row=r_curr, column=4).alignment = ALIGN_CENTER

            col_idx = 5
            for s in range(1, 9):
                rec = p["sem_records"].get(s) or p["sem_records"].get(str(s))
                c_res = ws.cell(row=r_curr, column=col_idx)
                c_sgpi = ws.cell(row=r_curr, column=col_idx + 1)
                c_kt = ws.cell(row=r_curr, column=col_idx + 2)

                if rec:
                    # Result
                    res_val = rec.get("result", "N/A")
                    c_res.value = res_val
                    c_res.font = BOLD_DATA_FONT
                    c_res.alignment = ALIGN_CENTER
                    if res_val == "PASS":
                        c_res.fill = PASS_FILL
                    elif res_val == "FAIL":
                        c_res.fill = FAIL_FILL

                    # SGPI
                    s_val = rec.get("sgpi")
                    s_float = None
                    if s_val is not None and s_val != "N/A":
                        try:
                            s_float = float(s_val)
                        except (ValueError, TypeError):
                            s_float = None

                    if s_float is not None:
                        c_sgpi.value = s_float
                        c_sgpi.number_format = "0.00"
                        c_sgpi.alignment = ALIGN_RIGHT
                        if s_float >= 8.50:
                            c_sgpi.fill = GOLD_FILL
                    else:
                        c_sgpi.value = "N/A"
                        c_sgpi.alignment = ALIGN_CENTER

                    # KT count
                    kt_val = rec.get("kt_count", 0)
                    c_kt.value = kt_val if res_val != "PASS" else 0
                    c_kt.alignment = ALIGN_RIGHT
                else:
                    c_res.value = "N/A"
                    c_sgpi.value = "N/A"
                    c_kt.value = "N/A"
                    c_res.alignment = ALIGN_CENTER
                    c_sgpi.alignment = ALIGN_CENTER
                    c_kt.alignment = ALIGN_CENTER

                for c in [c_res, c_sgpi, c_kt]:
                    c.font = DATA_FONT
                col_idx += 3

            # Summary Metrics
            c_avg = ws.cell(row=r_curr, column=col_idx)
            if p.get("average_sgpi") is not None:
                c_avg.value = p["average_sgpi"]
                c_avg.number_format = "0.00"
                c_avg.alignment = ALIGN_RIGHT
            else:
                c_avg.value = "N/A"
                c_avg.alignment = ALIGN_CENTER
            c_avg.font = BOLD_DATA_FONT

            c_max = ws.cell(row=r_curr, column=col_idx + 1)
            c_max.value = p.get("highest_sgpi", "N/A")
            c_max.number_format = "0.00" if p.get("highest_sgpi") else "@"
            c_max.alignment = ALIGN_RIGHT if p.get("highest_sgpi") else ALIGN_CENTER
            c_max.font = DATA_FONT

            c_min = ws.cell(row=r_curr, column=col_idx + 2)
            c_min.value = p.get("lowest_sgpi", "N/A")
            c_min.number_format = "0.00" if p.get("lowest_sgpi") else "@"
            c_min.alignment = ALIGN_RIGHT if p.get("lowest_sgpi") else ALIGN_CENTER
            c_min.font = DATA_FONT

            c_prog = ws.cell(row=r_curr, column=col_idx + 3, value=p.get("progression", "N/A"))
            c_prog.font = DATA_FONT
            c_prog.alignment = ALIGN_CENTER

            c_stat = ws.cell(row=r_curr, column=col_idx + 4, value=p.get("final_status", "N/A"))
            c_stat.font = BOLD_DATA_FONT
            c_stat.alignment = ALIGN_LEFT

            for c in range(2, col_idx + 5):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER

            r_curr += 1

        auto_fit_columns(ws, min_width=11, max_width=35)

    # -----------------------------------------------------------------
    # SHEET 5: KT ANALYSIS
    # -----------------------------------------------------------------
    def _build_sheet_5_kt_analysis(self, ws):
        ws.title = "KT Analysis"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="ACADEMIC BACKLOG & KT ANALYSIS").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Semester-Wise KT Rates and Repeat Backlog Tracking").font = SUBTITLE_FONT

        # Table 1: Semester KT Burden
        ws.cell(row=5, column=2, value="1. SEMESTER-WISE BACKLOG BURDEN").font = SECTION_FONT

        headers = ["Semester", "Appeared Students", "Students with KT", "KT Rate (%)", "KT Subjects Count", "Avg KT per Failed Student"]
        r_hdr = 6
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER

        r_curr = 7
        for k in self.kt_analysis:
            ws.cell(row=r_curr, column=2, value=k["semester_name"]).font = BOLD_DATA_FONT

            ws.cell(row=r_curr, column=3, value=k["total_students"]).font = DATA_FONT
            ws.cell(row=r_curr, column=3).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=3).number_format = "#,##0"

            ws.cell(row=r_curr, column=4, value=k["kt_students"]).font = DATA_FONT
            ws.cell(row=r_curr, column=4).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=4).number_format = "#,##0"

            ws.cell(row=r_curr, column=5, value=k["kt_percentage"] / 100.0).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=5).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=5).number_format = "0.0%"
            if k["kt_percentage"] > 25.0:
                ws.cell(row=r_curr, column=5).fill = FAIL_FILL

            ws.cell(row=r_curr, column=6, value=k["total_kt_subjects"]).font = DATA_FONT
            ws.cell(row=r_curr, column=6).alignment = ALIGN_RIGHT if isinstance(k["total_kt_subjects"], (int, float)) else ALIGN_CENTER

            ws.cell(row=r_curr, column=7, value=k["avg_kt_per_failed_student"]).font = DATA_FONT
            ws.cell(row=r_curr, column=7).alignment = ALIGN_RIGHT if isinstance(k["avg_kt_per_failed_student"], (int, float)) else ALIGN_CENTER

            for c in range(2, 8):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER

            r_curr += 1

        # Table 2: Repeated KT Occurrences
        r_curr += 2
        ws.cell(row=r_curr, column=2, value="2. REPEATED KT OCCURRENCES (STUDENTS WITH KT IN ≥ 2 SEMESTERS)").font = SECTION_FONT
        r_curr += 1

        rep_headers = ["Student ID", "Student Name", "Total KT Semesters", "Semesters with KT", "Latest Available Status"]
        for c_idx, h in enumerate(rep_headers, start=2):
            c = ws.cell(row=r_curr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER
        r_curr += 1

        if self.repeated_kt_students:
            for rep in self.repeated_kt_students:
                ws.cell(row=r_curr, column=2, value=rep["student_id"]).font = DATA_FONT
                ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=3, value=rep["canonical_name"]).font = BOLD_DATA_FONT

                ws.cell(row=r_curr, column=4, value=rep["repeat_count"]).font = BOLD_DATA_FONT
                ws.cell(row=r_curr, column=4).alignment = ALIGN_RIGHT

                sems_str = ", ".join([f"Sem {s}" for s in rep["failed_semesters"]])
                ws.cell(row=r_curr, column=5, value=sems_str).font = DATA_FONT

                stat_str = f"{rep['latest_result']} in Sem {rep['latest_sem']}"
                c_st = ws.cell(row=r_curr, column=6, value=stat_str)
                c_st.font = DATA_FONT
                c_st.alignment = ALIGN_CENTER
                if "FAIL" in stat_str:
                    c_st.fill = FAIL_FILL

                for c in range(2, 7):
                    ws.cell(row=r_curr, column=c).border = CELL_BORDER
                r_curr += 1
        else:
            ws.cell(row=r_curr, column=2, value="No students experienced repeated KT occurrences across the cohort.").font = ITALIC_NOTE_FONT
            r_curr += 1

        auto_fit_columns(ws, min_width=14, max_width=40)

    # -----------------------------------------------------------------
    # SHEET 6: TOPPER ANALYSIS
    # -----------------------------------------------------------------
    def _build_sheet_6_topper_analysis(self, ws):
        ws.title = "Topper Analysis"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="ACADEMIC MERIT & TOPPER ANALYSIS").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Top-Performing Candidates per Semester & Multi-Semester Consistent Honor Roll").font = SUBTITLE_FONT

        # Part 1: Multi-Semester Consistent Top Performers
        ws.cell(row=5, column=2, value="1. MULTI-SEMESTER CONSISTENT TOP PERFORMERS (APPEARED IN TOP 10 ≥ 2 TIMES)").font = SECTION_FONT

        multi_headers = ["Rank", "Candidate Name", "Primary Identifier", "Times in Top 10", "Semesters Ranked in Top 10", "Cumulative Average SGPI"]
        r_hdr = 6
        for c_idx, h in enumerate(multi_headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER

        r_curr = 7
        if self.multi_sem_toppers:
            for rank_i, m in enumerate(self.multi_sem_toppers, start=1):
                ws.cell(row=r_curr, column=2, value=rank_i).font = BOLD_DATA_FONT
                ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=3, value=m["canonical_name"]).font = BOLD_DATA_FONT

                ws.cell(row=r_curr, column=4, value=m["seat_no"]).font = DATA_FONT
                ws.cell(row=r_curr, column=4).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=5, value=m["appearances"]).font = BOLD_DATA_FONT
                ws.cell(row=r_curr, column=5).alignment = ALIGN_RIGHT

                sems_str = ", ".join([f"Sem {s}" for s in m["semesters"]])
                ws.cell(row=r_curr, column=6, value=sems_str).font = DATA_FONT

                c_avg = ws.cell(row=r_curr, column=7, value=m["average_sgpi"])
                c_avg.font = BOLD_DATA_FONT
                c_avg.alignment = ALIGN_RIGHT
                c_avg.number_format = "0.00"
                c_avg.fill = GOLD_FILL

                for c in range(2, 8):
                    ws.cell(row=r_curr, column=c).border = CELL_BORDER

                r_curr += 1
        else:
            ws.cell(row=r_curr, column=2, value="Insufficient multi-semester data to identify repeating toppers.").font = ITALIC_NOTE_FONT
            r_curr += 1

        # Part 2: Semester-wise Toppers Table
        r_curr += 2
        ws.cell(row=r_curr, column=2, value="2. TOP-PERFORMING STUDENTS BY SEMESTER").font = SECTION_FONT
        r_curr += 1

        sem_top_headers = ["Semester", "Merit Rank", "Student Name", "Seat / Identifier", "SGPI Scored"]
        for c_idx, h in enumerate(sem_top_headers, start=2):
            c = ws.cell(row=r_curr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER
        r_curr += 1

        for sem in self.available_sems:
            top_list = self.topper_by_semester.get(sem) or self.topper_by_semester.get(str(sem)) or []
            for item in top_list[:5]:  # Top 5 per semester for concise clarity
                ws.cell(row=r_curr, column=2, value=f"Semester {sem}").font = BOLD_DATA_FONT
                ws.cell(row=r_curr, column=3, value=f"Rank {item['rank']}").font = DATA_FONT
                ws.cell(row=r_curr, column=3).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=4, value=item["student_name"]).font = DATA_FONT

                ws.cell(row=r_curr, column=5, value=item["seat_no"]).font = DATA_FONT
                ws.cell(row=r_curr, column=5).alignment = ALIGN_CENTER

                c_s = ws.cell(row=r_curr, column=6, value=item["sgpi"])
                c_s.font = BOLD_DATA_FONT
                c_s.alignment = ALIGN_RIGHT
                c_s.number_format = "0.00"
                if item["rank"] == 1:
                    c_s.fill = GOLD_FILL

                for c in range(2, 7):
                    ws.cell(row=r_curr, column=c).border = CELL_BORDER

                r_curr += 1

        auto_fit_columns(ws, min_width=12, max_width=40)

    # -----------------------------------------------------------------
    # SHEET 7: RETENTION / PROGRESSION ANALYSIS
    # -----------------------------------------------------------------
    def _build_sheet_7_retention(self, ws):
        ws.title = "Retention Progression"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="STUDENT RETENTION & COHORT PROGRESSION FLOW").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Tracking Continuous Enrolment and Lateral Entry Trajectory across Semesters").font = SUBTITLE_FONT

        # Table 1: Transition Flow Matrix
        ws.cell(row=5, column=2, value="1. SEMESTER-TO-SEMESTER RETENTION & PROGRESSION TRANSITIONS").font = SECTION_FONT

        headers = ["Semester Transition", "From Semester", "To Semester", "Initial Count", "Next Count", "Continuing Students", "Lateral / New Entry", "Continuance Rate (%)"]
        r_hdr = 6
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER

        r_curr = 7
        for flow in self.retention_flow:
            ws.cell(row=r_curr, column=2, value=flow["transition"]).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=3, value=f"Sem {flow['from_semester']}").font = DATA_FONT
            ws.cell(row=r_curr, column=4, value=f"Sem {flow['to_semester']}").font = DATA_FONT

            for pos, key in [(5, "initial_count"), (6, "next_count"), (7, "continuing_students"), (8, "lateral_new_students")]:
                c_cell = ws.cell(row=r_curr, column=pos, value=flow[key])
                c_cell.font = DATA_FONT
                c_cell.alignment = ALIGN_RIGHT
                c_cell.number_format = "#,##0"

            ws.cell(row=r_curr, column=9, value=flow["retention_rate"] / 100.0).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=9).alignment = ALIGN_RIGHT
            ws.cell(row=r_curr, column=9).number_format = "0.0%"

            for c in range(2, 10):
                ws.cell(row=r_curr, column=c).border = CELL_BORDER

            r_curr += 1

        # Table 2: Multi-Semester Persistence Summary
        r_curr += 2
        ws.cell(row=r_curr, column=2, value="2. MULTI-SEMESTER PERSISTENCE SUMMARY").font = SECTION_FONT
        r_curr += 1

        summary_rows = [
            ("Students Appearing in All Available Semesters", self.cohort_counts.get("appearing_in_all_available", 0)),
            ("Students Appearing in ≥ 4 Semesters", self.cohort_counts.get("appearing_in_ge_4_sems", 0)),
            ("Students Appearing in ≥ 2 Semesters", self.cohort_counts.get("appearing_in_ge_2_sems", 0)),
            ("Estimated Lateral Entry (Diploma) Intake (Joined Sem 3)", self.cohort_counts.get("lateral_entry_students_est", 0))
        ]

        for label, val in summary_rows:
            ws.cell(row=r_curr, column=2, value=label).font = BOLD_DATA_FONT
            ws.cell(row=r_curr, column=2).fill = TABLE_HEADER_FILL
            ws.cell(row=r_curr, column=2).border = CELL_BORDER

            c_val = ws.cell(row=r_curr, column=3, value=val)
            c_val.font = DATA_FONT
            c_val.alignment = ALIGN_RIGHT
            c_val.number_format = "#,##0"
            c_val.border = CELL_BORDER
            r_curr += 1

        # Mandatory Academic Rule Footnote
        r_curr += 2
        rule_box = ws.cell(
            row=r_curr,
            column=2,
            value="MANDATORY RULE COMPLIANCE: Do not assume that a student missing from a semester dropped out. "
                  "Students may take academic gap years, appear in unrecorded ATKT-specific examination gazettes, or transfer."
        )
        rule_box.font = ITALIC_NOTE_FONT

        auto_fit_columns(ws, min_width=14, max_width=45)

    # -----------------------------------------------------------------
    # SHEET 8: STUDENT PERFORMANCE ANALYSIS
    # -----------------------------------------------------------------
    def _build_sheet_8_performance(self, ws):
        ws.title = "Student Performance"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="DESCRIPTIVE STUDENT PERFORMANCE ANALYSIS").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Analytical Classification into Cohort Segments Based on Empirical Data").font = SUBTITLE_FONT

        cats = [
            ("1. Consistently Strong Performance (Maintained Distinction, Zero Backlogs)", "consistently_strong", GOLD_FILL),
            ("2. Improving Performance (Positive Progression Trend Δ ≥ +0.50 SGPI)", "improving_performance", PASS_FILL),
            ("3. Declining Performance (Noticeable Performance Drop Δ ≤ -0.60 SGPI)", "declining_performance", FAIL_FILL),
            ("4. Fluctuating Performance (High Variance / Std Dev across Semesters)", "fluctuating_performance", None),
            ("5. Repeated Academic Difficulties (KT / Backlogs in ≥ 2 Semesters)", "repeated_difficulties", FAIL_FILL),
        ]

        r_curr = 5
        for cat_title, cat_key, fill_color in cats:
            ws.cell(row=r_curr, column=2, value=cat_title).font = SECTION_FONT
            r_curr += 1

            headers = ["Student ID", "Canonical Student Name", "Semesters Appeared", "Average SGPI", "Empirical Classification Justification"]
            for c_idx, h in enumerate(headers, start=2):
                c = ws.cell(row=r_curr, column=c_idx, value=h)
                c.font = NAVY_HEADER_FONT
                c.fill = NAVY_HEADER_FILL
                c.alignment = ALIGN_HEADER
                c.border = HEADER_BORDER
            r_curr += 1

            items = self.student_cats.get(cat_key, [])
            if items:
                for it in items[:15]:  # Display top 15 candidates per category
                    ws.cell(row=r_curr, column=2, value=it.get("student_id", "N/A")).font = DATA_FONT
                    ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

                    ws.cell(row=r_curr, column=3, value=it.get("canonical_name", "N/A")).font = BOLD_DATA_FONT

                    ws.cell(row=r_curr, column=4, value=it.get("appeared_count", "N/A")).font = DATA_FONT
                    ws.cell(row=r_curr, column=4).alignment = ALIGN_CENTER

                    avg_val = it.get("average_sgpi")
                    c_avg = ws.cell(row=r_curr, column=5, value=avg_val if avg_val is not None else "N/A")
                    c_avg.font = BOLD_DATA_FONT
                    c_avg.alignment = ALIGN_RIGHT if isinstance(avg_val, (int, float)) else ALIGN_CENTER
                    if isinstance(avg_val, (int, float)):
                        c_avg.number_format = "0.00"
                        if fill_color:
                            c_avg.fill = fill_color

                    ws.cell(row=r_curr, column=6, value=it.get("classification_reason", "Supported by records")).font = DATA_FONT

                    for c in range(2, 7):
                        ws.cell(row=r_curr, column=c).border = CELL_BORDER

                    r_curr += 1
            else:
                ws.cell(row=r_curr, column=2, value="No students met the criteria for this performance classification.").font = ITALIC_NOTE_FONT
                r_curr += 1

            r_curr += 2  # Spacing between categories

        auto_fit_columns(ws, min_width=14, max_width=45)

    # -----------------------------------------------------------------
    # SHEET 9: DATA QUALITY
    # -----------------------------------------------------------------
    def _build_sheet_9_data_quality(self, ws):
        ws.title = "Data Quality"
        ws.views.sheetView[0].showGridLines = True

        ws.cell(row=2, column=2, value="DATA INTEGRITY & QUALITY AUDIT LOG").font = TITLE_FONT
        ws.cell(row=3, column=2, value="Non-Destructive Anomaly Detection (No Silent Modifications Applied)").font = SUBTITLE_FONT

        headers = ["Issue Index", "Issue Category", "Semester Scope", "Student Identifier", "Candidate Name", "Anomaly Description", "Severity", "Action / Resolution Protocol"]
        r_hdr = 5
        for c_idx, h in enumerate(headers, start=2):
            c = ws.cell(row=r_hdr, column=c_idx, value=h)
            c.font = NAVY_HEADER_FONT
            c.fill = NAVY_HEADER_FILL
            c.alignment = ALIGN_HEADER
            c.border = HEADER_BORDER

        r_curr = 6
        if self.quality_issues:
            for idx, iss in enumerate(self.quality_issues, start=1):
                ws.cell(row=r_curr, column=2, value=idx).font = DATA_FONT
                ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=3, value=iss.get("category", "Data Anomaly")).font = BOLD_DATA_FONT

                ws.cell(row=r_curr, column=4, value=str(iss.get("semester", "N/A"))).font = DATA_FONT
                ws.cell(row=r_curr, column=4).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=5, value=str(iss.get("student_id", "N/A"))).font = DATA_FONT
                ws.cell(row=r_curr, column=5).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=6, value=iss.get("student_name", "N/A")).font = DATA_FONT

                ws.cell(row=r_curr, column=7, value=iss.get("description", "")).font = DATA_FONT

                # Severity Badge
                c_sev = ws.cell(row=r_curr, column=8, value=iss.get("severity", "Medium"))
                c_sev.font = BOLD_DATA_FONT
                c_sev.alignment = ALIGN_CENTER
                if iss.get("severity") == "High":
                    c_sev.fill = FAIL_FILL
                elif iss.get("severity") == "Medium":
                    c_sev.fill = GOLD_FILL

                ws.cell(row=r_curr, column=9, value=iss.get("action_taken", "Preserved as per data integrity rule")).font = DATA_FONT

                for c in range(2, 10):
                    ws.cell(row=r_curr, column=c).border = CELL_BORDER

                r_curr += 1
        else:
            ws.cell(row=r_curr, column=2, value="Zero anomalies detected. All extracted records satisfy strict university validation checks.").font = ITALIC_NOTE_FONT

        auto_fit_columns(ws, min_width=12, max_width=45)

    # -----------------------------------------------------------------
    # SHEET 10: SEMESTER DATA (Traceable Sheets)
    # -----------------------------------------------------------------
    def _build_sheet_10_semester_data(self, wb):
        """Preserves the processed data for each semester in individual traceable sheets."""
        for sem in self.available_sems:
            recs = self.semester_raw.get(sem) or self.semester_raw.get(str(sem)) or []
            sheet_title = f"Sem {sem} Data"
            ws = wb.create_sheet(sheet_title)
            ws.views.sheetView[0].showGridLines = True

            ws.cell(row=2, column=2, value=f"PROCESSED RECORD TRACEABILITY: SEMESTER {sem}").font = TITLE_FONT
            ws.cell(row=3, column=2, value=f"Complete Extracted Student Ledger for Semester {sem} ({len(recs)} records)").font = SUBTITLE_FONT

            headers = ["Sr No", "Seat No", "Student Name", "PRN / Reg No", "Result Status", "SGPI", "KT Count", "Total Marks", "Source File Trace"]
            r_hdr = 5
            for c_idx, h in enumerate(headers, start=2):
                c = ws.cell(row=r_hdr, column=c_idx, value=h)
                c.font = NAVY_HEADER_FONT
                c.fill = NAVY_HEADER_FILL
                c.alignment = ALIGN_HEADER
                c.border = HEADER_BORDER

            r_curr = 6
            for idx, r in enumerate(recs, start=1):
                ws.cell(row=r_curr, column=2, value=idx).font = DATA_FONT
                ws.cell(row=r_curr, column=2).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=3, value=r.get("seat_no", "N/A")).font = BOLD_DATA_FONT
                ws.cell(row=r_curr, column=3).alignment = ALIGN_CENTER

                ws.cell(row=r_curr, column=4, value=r.get("name", "N/A")).font = DATA_FONT

                ws.cell(row=r_curr, column=5, value=r.get("prn") or "N/A").font = DATA_FONT
                ws.cell(row=r_curr, column=5).alignment = ALIGN_CENTER

                c_res = ws.cell(row=r_curr, column=6, value=r.get("result", "N/A"))
                c_res.font = BOLD_DATA_FONT
                c_res.alignment = ALIGN_CENTER
                if r.get("result") == "PASS":
                    c_res.fill = PASS_FILL
                elif r.get("result") == "FAIL":
                    c_res.fill = FAIL_FILL

                s_val = r.get("sgpi")
                c_sgpi = ws.cell(row=r_curr, column=7)
                s_float = None
                if s_val is not None and s_val != "N/A":
                    try:
                        s_float = float(s_val)
                    except (ValueError, TypeError):
                        s_float = None

                if s_float is not None:
                    c_sgpi.value = s_float
                    c_sgpi.number_format = "0.00"
                    c_sgpi.alignment = ALIGN_RIGHT
                else:
                    c_sgpi.value = "N/A"
                    c_sgpi.alignment = ALIGN_CENTER
                c_sgpi.font = DATA_FONT

                ws.cell(row=r_curr, column=8, value=r.get("kt_count", 0)).font = DATA_FONT
                ws.cell(row=r_curr, column=8).alignment = ALIGN_RIGHT

                tot_m = r.get("total_marks")
                c_tm = ws.cell(row=r_curr, column=9, value=tot_m if tot_m is not None else "N/A")
                c_tm.font = DATA_FONT
                c_tm.alignment = ALIGN_RIGHT if tot_m is not None else ALIGN_CENTER

                ws.cell(row=r_curr, column=10, value=r.get("source_file", "N/A")).font = DATA_FONT

                for c in range(2, 11):
                    ws.cell(row=r_curr, column=c).border = CELL_BORDER
                    if r_curr % 2 == 1:
                        ws.cell(row=r_curr, column=c).fill = ZEBRA_FILL

                r_curr += 1

            auto_fit_columns(ws, min_width=11, max_width=35)
