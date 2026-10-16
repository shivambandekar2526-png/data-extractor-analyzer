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
    classify_pdf_first_page, check_duplicate_dataset, store_classified_pdf,
    normalize_department, normalize_batch, normalize_semester
)
from core.dataset_database import save_dataset_to_db

classify_bp = Blueprint("classify_api", __name__)

@classify_bp.route("/api/classify", methods=["POST"])
def classify_uploaded_document():
    """
    Vision AI classification step for uploaded PDF on Page 1.
    Determines canonical Department, Batch, Semester.
    Checks if a dataset with this Department, Batch, Semester already exists.
    """
    target_pdf_path = None
    safe_name = None

    for key in ["pdf_file", "file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                ext = os.path.splitext(safe_name)[1].lower()
                if ext != ".pdf":
                    return jsonify({"success": False, "error": "Classification requires a PDF file."}), 400
                saved_path = os.path.join(INPUT_DIR, safe_name)
                up.save(saved_path)
                target_pdf_path = saved_path
                break

    json_data = request.get_json(silent=True) or {}
    if not target_pdf_path:
        existing = request.form.get("input_file") or request.form.get("existing_input") or json_data.get("input_file") or json_data.get("pdf_file")
        if existing:
            safe_name = secure_filename(existing)
            cand = os.path.join(INPUT_DIR, safe_name)
            if os.path.isfile(cand):
                target_pdf_path = cand

    if not target_pdf_path or not os.path.isfile(target_pdf_path):
        return jsonify({"success": False, "error": "No valid PDF file provided for classification."}), 400

    if os.path.getsize(target_pdf_path) == 0:
        return jsonify({"success": False, "error": "PDF file is empty (0 bytes)."}), 400

    try:
        import fitz
        test_doc = fitz.open(target_pdf_path)
        if len(test_doc) == 0:
            test_doc.close()
            return jsonify({"success": False, "error": "PDF file contains 0 pages or is empty."}), 400
        test_doc.close()
    except Exception:
        return jsonify({"success": False, "error": "Malformed or corrupted PDF file header."}), 400

    try:
        res = classify_pdf_first_page(target_pdf_path)
        dept = res["department"]
        batch = res["batch"]
        sem = res["semester"]

        dup_info = check_duplicate_dataset(dept, batch, sem, DEPARTMENTS_DATA_DIR)

        return jsonify({
            "success": True,
            "department": dept,
            "batch": batch,
            "semester": sem,
            "pdf_rel_path": res.get("pdf_rel_path"),
            "excel_rel_path": res.get("excel_rel_path"),
            "canonical_pdf_path": os.path.join(DEPARTMENTS_DATA_DIR, "pdf", dept, batch, f"{sem}.pdf").replace("\\", "/"),
            "is_duplicate": dup_info["duplicate"],
            "pdf_exists": dup_info["pdf_exists"],
            "excel_exists": dup_info["excel_exists"],
            "classification_method": res.get("classification_method"),
            "file_name": safe_name or os.path.basename(target_pdf_path)
        })
    except Exception as e:
        logger.error(f"Classification failed: {e}")
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


