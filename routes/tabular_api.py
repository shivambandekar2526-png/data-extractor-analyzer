import os
import logging
import pandas as pd
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

logger = logging.getLogger("app")

from services.config import INPUT_DIR, DEPARTMENTS_DATA_DIR, OUTPUT_DIR
from services.state import tabular_ingester
from services.academic_service import resolve_tabular_file_path
from services.extraction_service import auto_sort_into_app_batch_store
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient
from core.data_cleaning_engine import DataCleaningEngine
from core.excel_csv_ingester import sanitize_dict_for_json

tabular_bp = Blueprint("tabular_api", __name__)

@tabular_bp.route("/api/tabular/inspect", methods=["POST"])
def inspect_tabular_file():
    """Inspect uploaded Excel/CSV file to evaluate formatting and structure."""
    target_path = None
    target_filename = None

    # Check for direct file upload
    for key in ["file", "excel_file", "csv_file", "pdf_file"]:
        if key in request.files:
            up = request.files[key]
            if up and up.filename:
                safe_name = secure_filename(up.filename)
                target_path = os.path.join(INPUT_DIR, safe_name)
                up.save(target_path)
                target_filename = safe_name
                break

    json_data = request.get_json(silent=True) or {}
    if not target_path:
        fname = (
            request.form.get("filename")
            or json_data.get("filename")
            or request.form.get("batch_file")
            or json_data.get("batch_file")
        )
        if fname:
            resolved = resolve_tabular_file_path(fname)
            if resolved:
                target_path, target_filename = resolved

    if not target_path or not os.path.isfile(target_path):
        return jsonify({"success": False, "error": "No valid tabular file uploaded or specified."}), 400

    sheet_name = request.form.get("sheet_name") or json_data.get("sheet_name")
    assessment = tabular_ingester.inspect_file(target_path, sheet_name=sheet_name)
    if assessment.get("success"):
        assessment["filename"] = target_filename
    return jsonify(sanitize_dict_for_json(assessment))


@tabular_bp.route("/api/tabular/import", methods=["POST"])
def import_tabular_direct():
    """Directly import Excel or CSV table without invoking AI (0 tokens)."""
    data = request.get_json(silent=True) or request.form.to_dict()
    filename = data.get("filename")
    sheet_name = data.get("sheet_name")
    header_row = data.get("header_row")
    if header_row is not None:
        try:
            header_row = int(header_row)
        except Exception:
            header_row = None

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    resolved = resolve_tabular_file_path(filename)
    if not resolved:
        return jsonify({"success": False, "error": f"Tabular file '{filename}' not found."}), 404
    target_path, target_filename = resolved

    result = tabular_ingester.direct_import(target_path, sheet_name=sheet_name, header_row=header_row)
    if result.get("success") and result.get("students"):
        try:
            auto_sort_into_app_batch_store(source_path=target_path, students=result.get("students"))
        except Exception:
            pass
    return jsonify(sanitize_dict_for_json(result))


@tabular_bp.route("/api/tabular/ai-fix", methods=["POST"])
def ai_fix_tabular():
    """Extract and restructure messy/unformatted Excel or CSV using AI."""
    data = request.get_json(silent=True) or request.form.to_dict()
    filename = data.get("filename")
    sheet_name = data.get("sheet_name")
    pipeline_mode = data.get("pipeline_mode", "local")
    model = data.get("model")

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    resolved = resolve_tabular_file_path(filename)
    if not resolved:
        return jsonify({"success": False, "error": f"Tabular file '{filename}' not found."}), 404
    target_path, target_filename = resolved

    oc = OllamaClient()
    cc = CloudClient()
    result = tabular_ingester.ai_fix_and_import(
        target_path,
        sheet_name=sheet_name,
        pipeline_mode=pipeline_mode,
        model=model,
        ollama_client=oc,
        cloud_client=cc
    )
    if result.get("success") and result.get("students"):
        try:
            auto_sort_into_app_batch_store(source_path=target_path, students=result.get("students"))
        except Exception:
            pass
    return jsonify(sanitize_dict_for_json(result))


@tabular_bp.route("/api/tabular/interactive-fix", methods=["POST"])
def interactive_fix_tabular():
    """Auto-repair formulas, totals, SGPI, and formatting for active spreadsheet records."""
    data = request.get_json(silent=True) or {}
    records = data.get("records", [])
    folder_name = data.get("folder_name", "")
    pipeline_mode = data.get("pipeline_mode", "local")

    if not records or not isinstance(records, list):
        return jsonify({"success": False, "error": "No records provided for AI fix."}), 400

    try:
        from core.data_cleaning_engine import DataCleaningEngine
        oc = OllamaClient() if pipeline_mode == "local" else None
        cc = CloudClient() if pipeline_mode == "cloud" else None
        if cc is None:
            inst_cc = CloudClient()
            if inst_cc.is_configured():
                cc = inst_cc
        cleaner = DataCleaningEngine(ollama_client=oc, cloud_client=cc)

        clean_res = cleaner.clean_dataset(records)
        cleaned_records = clean_res.get("cleaned_records", records)
        changes = clean_res.get("changes", [])

        # Re-run normalizer to verify all identifiers & SGPI are clean
        cleaned_records = tabular_ingester._normalize_student_records(cleaned_records)

        # Re-persist if folder exists on server
        if folder_name:
            safe_folder = secure_filename(folder_name)
            folder_path = os.path.join(OUTPUT_DIR, safe_folder)
            if os.path.isdir(folder_path):
                csv_path = os.path.join(folder_path, f"{safe_folder}_extracted_data.csv")
                excel_path = os.path.join(folder_path, f"{safe_folder}_extracted_data.xlsx")
                df = pd.DataFrame(cleaned_records)
                df.to_csv(csv_path, index=False, encoding="utf-8")
                try:
                    df.to_excel(excel_path, index=False)
                except Exception as ex:
                    logger.warning(f"Could not rewrite excel during interactive fix: {ex}")

        return jsonify(sanitize_dict_for_json({
            "success": True,
            "records": cleaned_records,
            "changes": [c.to_dict() if hasattr(c, "to_dict") else c for c in changes],
            "changes_count": len(changes),
            "summary": clean_res.get("summary", {})
        }))
    except Exception as e:
        logger.error(f"Error in interactive fix: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


