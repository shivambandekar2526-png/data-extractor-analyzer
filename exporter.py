"""
Exporter Module
===============
Exports student records into structured CSV, formatted Excel (.xlsx),
and Course Catalog JSON metadata files.
"""

import csv
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from .models import CourseCatalog, ExamMeta, StudentRecord
except Exception:
    try:
        from data_extractor_AI.models import CourseCatalog, ExamMeta, StudentRecord
    except Exception:
        from models import CourseCatalog, ExamMeta, StudentRecord

try:
    from core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
except Exception:
    try:
        from .core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename
    except Exception:
        from data_extractor_AI.core.excel_report_generator import AcademicExcelReportGenerator, get_academic_filename


def sanitize_folder_name(name: str, max_len: int = 50) -> str:
    """Sanitize filename/stem for directory naming."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = clean.strip().replace(" ", "_")
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip("_.-")
    return clean or "ai_extracted_results"


def write_csv_file(data: List[Dict[str, Any]], fieldnames: List[str], output_path: str) -> None:
    """Write list of row dictionaries to UTF-8 CSV with Windows long-path safety."""
    abs_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    win_path = abs_path
    if os.name == "nt" and not abs_path.startswith("\\\\?\\") and len(abs_path) >= 240:
        win_path = "\\\\?\\" + abs_path
    with open(win_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)


def write_excel_file(
    data: List[Dict[str, Any]],
    fieldnames: List[str],
    catalog: Optional[CourseCatalog],
    meta: ExamMeta,
    output_path: str
) -> None:
    """Write formatted Excel workbook with Extracted Data and Course Catalog sheets."""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        return

    wb = openpyxl.Workbook()

    # --- Sheet 1: Extracted Data ---
    ws = wb.active
    ws.title = "Extracted Data"

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=False)
    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    ws.append(fieldnames)
    for col_idx in range(1, len(fieldnames) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = thin_border

    for row_dict in data:
        row_vals = [row_dict.get(col, "") for col in fieldnames]
        ws.append(row_vals)

    ws.freeze_panes = "A2"

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 45)

    # --- Sheet 2: Course Catalog Metadata ---
    ws_cat = wb.create_sheet("Course Catalog")
    cat_headers = ["Course Code", "Official Course Name", "Component Heads", "Max Marks", "Credits"]
    ws_cat.append(cat_headers)

    cat_header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    for col_idx in range(1, len(cat_headers) + 1):
        cell = ws_cat.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = cat_header_fill
        cell.alignment = center_align
        cell.border = thin_border

    courses_list = catalog.courses if catalog else (meta.catalog.courses if meta and meta.catalog else [])
    for c in courses_list:
        heads_str = ", ".join(c.heads) if c.heads else "N/A"
        max_m_str = ", ".join(f"{h}:{m}" for h, m in c.max_marks.items()) if c.max_marks else "N/A"
        ws_cat.append([c.code, c.name, heads_str, max_m_str, c.credits or ""])

    for col in ws_cat.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = col[0].column_letter
        ws_cat.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 60)

    abs_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    wb.save(abs_path)


def export_ai_extracted_data(
    meta: ExamMeta,
    students: List[StudentRecord],
    input_pdf_path: str,
    base_output_dir: str,
    archetype: str = "tabulation",
    checksum_data: Optional[Dict[str, Any]] = None,
    flat_rows: Optional[List[Dict[str, Any]]] = None
) -> Tuple[str, str]:
    """
    Export extracted StudentRecord objects to CSV, Excel, and JSON catalog.
    Returns (designated_folder_path, output_csv_path).
    """
    pdf_filename = os.path.basename(input_pdf_path)
    pdf_stem = os.path.splitext(pdf_filename)[0]
    folder_name = sanitize_folder_name(pdf_stem) or "ai_extracted_results"

    designated_dir = os.path.join(base_output_dir, folder_name)
    os.makedirs(designated_dir, exist_ok=True)

    csv_path = os.path.join(designated_dir, f"{folder_name}_extracted_data.csv")
    excel_path = os.path.join(designated_dir, f"{folder_name}_extracted_data.xlsx")
    json_path = os.path.join(designated_dir, f"{folder_name}_catalog.json")

    # Flatten students to dicts if not provided
    if flat_rows is None:
        flat_rows = [s.to_flat_dict(meta.catalog) for s in students]

    # Collect union of field names
    seen_fields = set()
    fieldnames = []
    for r in flat_rows:
        for k in r.keys():
            if k not in seen_fields:
                seen_fields.add(k)
                fieldnames.append(k)

    # 1. Export CSV
    write_csv_file(flat_rows, fieldnames, csv_path)

    # 2. Export Excel
    write_excel_file(
        data=flat_rows,
        fieldnames=fieldnames,
        catalog=meta.catalog,
        meta=meta,
        output_path=excel_path
    )

    # 3. Export JSON Metadata Catalog
    cat_dict = meta.catalog.to_dict() if meta.catalog else []
    payload = {
        "exam_title": meta.exam_title,
        "semester": meta.semester,
        "college_name": meta.college_name,
        "result_date": meta.result_date,
        "archetype": meta.archetype or archetype,
        "total_students": len(students),
        "courses": cat_dict,
        "checksum_verification": checksum_data or {}
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # 4. Export Academic Analysis 6-Sheet Workbook (Target Google Sheet format)
    try:
        meta_dict = {
            "academic_year": getattr(meta, "academic_year", ""),
            "semester": getattr(meta, "semester", ""),
            "exam_title": getattr(meta, "exam_title", ""),
            "college_name": getattr(meta, "college_name", "")
        }
        academic_filename = get_academic_filename(folder_name, meta_dict)
        academic_path = os.path.join(designated_dir, academic_filename)
        generator = AcademicExcelReportGenerator(
            students=flat_rows,
            catalog={"courses": cat_dict},
            meta=meta_dict,
            folder_name=folder_name
        )
        generator.generate_workbook(academic_path)
    except Exception as e:
        print(f"Warning: Academic analysis excel generation skipped: {e}")

    return designated_dir, csv_path
