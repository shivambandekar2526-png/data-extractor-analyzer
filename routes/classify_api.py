import os
import re
import logging
import fitz
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import INPUT_DIR, DEPARTMENTS_DATA_DIR, OUTPUT_DIR
from services.state import DB_PATH
from core.pdf_classifier import (
    classify_pdf_first_page, classify_tabular_metadata, check_duplicate_dataset, store_classified_pdf,
    normalize_department, normalize_batch, normalize_semester
)
from core.dataset_database import save_dataset_to_db

classify_bp = Blueprint("classify_api", __name__)

@classify_bp.route("/api/classify", methods=["POST"])
def classify_uploaded_document():
    """
    Intelligent classification step for uploaded documents (PDF, Excel, CSV).
    Determines canonical Department, Batch, Semester.
    Checks if a dataset with this Department, Batch, Semester already exists.
    """
    target_file_path = None
    safe_name = None
    ext = None

    for key in ["pdf_file", "file", "excel_file", "csv_file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                ext = os.path.splitext(safe_name)[1].lower()
                if ext not in (".pdf", ".xlsx", ".xls", ".csv"):
                    return jsonify({"success": False, "error": "Supported formats for classification: .pdf, .xlsx, .xls, .csv"}), 400
                saved_path = os.path.join(INPUT_DIR, safe_name)
                up.save(saved_path)
                target_file_path = saved_path
                break

    json_data = request.get_json(silent=True) or {}
    if not target_file_path:
        existing = (
            request.form.get("input_file")
            or request.form.get("existing_input")
            or json_data.get("input_file")
            or json_data.get("pdf_file")
            or json_data.get("file")
        )
        if existing:
            safe_name = secure_filename(existing)
            ext = os.path.splitext(safe_name)[1].lower()
            cand = os.path.join(INPUT_DIR, safe_name)
            if os.path.isfile(cand):
                target_file_path = cand

    if not target_file_path or not os.path.isfile(target_file_path):
        return jsonify({"success": False, "error": "No valid document provided for classification."}), 400

    if os.path.getsize(target_file_path) == 0:
        return jsonify({"success": False, "error": "Uploaded file is empty (0 bytes)."}), 400

    try:
        if ext == ".pdf":
            try:
                import fitz
                test_doc = fitz.open(target_file_path)
                if len(test_doc) == 0:
                    test_doc.close()
                    return jsonify({"success": False, "error": "PDF file contains 0 pages or is empty."}), 400
                test_doc.close()
            except Exception:
                return jsonify({"success": False, "error": "Malformed or corrupted PDF file header."}), 400

            res = classify_pdf_first_page(target_file_path)
        else:
            # Excel or CSV file
            res = classify_tabular_metadata(target_file_path)

        dept = res["department"]
        batch = res["batch"]
        sem = res["semester"]

        dup_info = check_duplicate_dataset(dept, batch, sem, DEPARTMENTS_DATA_DIR)

        return jsonify({
            "success": True,
            "department": dept,
            "department_label": res.get("department_label", dept.replace("_", " ")),
            "batch": batch,
            "academic_year": res.get("academic_year", batch),
            "semester": sem,
            "semester_label": res.get("semester_label", sem.replace("_", " ")),
            "pdf_rel_path": res.get("pdf_rel_path"),
            "excel_rel_path": res.get("excel_rel_path"),
            "canonical_pdf_path": os.path.join(DEPARTMENTS_DATA_DIR, "pdf", dept, batch, f"{sem}.pdf").replace("\\", "/"),
            "canonical_excel_path": os.path.join(DEPARTMENTS_DATA_DIR, "excel", dept, batch, f"{sem}.xlsx").replace("\\", "/"),
            "is_duplicate": dup_info["duplicate"],
            "pdf_exists": dup_info["pdf_exists"],
            "excel_exists": dup_info["excel_exists"],
            "classification_method": res.get("classification_method"),
            "detection_reason": res.get("detection_reason", "Document Layout Analysis"),
            "file_name": safe_name or os.path.basename(target_file_path),
            "file_type": ext.lstrip(".") if ext else "pdf"
        })
    except Exception as e:
        logger.error(f"Classification failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@classify_bp.route("/api/archive-pdf", methods=["POST"])
def archive_classified_pdf():
    """Explicitly save/archive an uploaded PDF into departments_data/pdf/<Dept>/<Batch>/<Sem>.pdf."""
    target_pdf_path = None
    safe_name = None

    for key in ["pdf_file", "file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                saved_path = os.path.join(INPUT_DIR, safe_name)
                up.save(saved_path)
                target_pdf_path = saved_path
                break

    json_data = request.get_json(silent=True) or {}
    if not target_pdf_path:
        existing = (
            request.form.get("input_file")
            or request.form.get("file_name")
            or json_data.get("input_file")
            or json_data.get("file_name")
        )
        if existing:
            safe_name = secure_filename(existing)
            cand = os.path.join(INPUT_DIR, safe_name)
            if os.path.isfile(cand):
                target_pdf_path = cand

    if not target_pdf_path or not os.path.isfile(target_pdf_path):
        return jsonify({"success": False, "error": "No valid PDF file provided to archive."}), 400

    dept = request.form.get("department") or json_data.get("department")
    batch = request.form.get("batch") or json_data.get("batch")
    sem = request.form.get("semester") or json_data.get("semester")

    if not (dept and batch and sem):
        try:
            clf_res = classify_pdf_first_page(target_pdf_path)
            dept = dept or clf_res["department"]
            batch = batch or clf_res["batch"]
            sem = sem or clf_res["semester"]
        except Exception:
            dept = dept or "Artificial_Intelligence_and_Data_Science"
            batch = batch or "22-23"
            sem = sem or "Semester_1"

    dept = normalize_department(dept)
    sem = normalize_semester(sem)
    sem_num_val = int(re.search(r"\d+", sem).group(0)) if re.search(r"\d+", sem) else 1
    batch = normalize_batch(batch, sem_num=sem_num_val)

    try:
        canonical_pdf = store_classified_pdf(
            target_pdf_path, dept, batch, sem, DEPARTMENTS_DATA_DIR, overwrite=True
        )
        if canonical_pdf.startswith("pdf/") or canonical_pdf.startswith("excel/"):
            canonical_rel = canonical_pdf
        else:
            canonical_rel = os.path.relpath(canonical_pdf, DEPARTMENTS_DATA_DIR).replace("\\", "/")

        return jsonify({
            "success": True,
            "message": f"Successfully stored PDF in departments_data/{canonical_rel}",
            "canonical_pdf_path": canonical_pdf,
            "rel_path": canonical_rel,
            "department": dept,
            "batch": batch,
            "semester": sem
        })
    except Exception as e:
        logger.error(f"Archive PDF failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


