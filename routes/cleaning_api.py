import os
import csv
import json
import logging
from typing import Dict, Any
from flask import Blueprint, request, jsonify
import pandas as pd

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.extraction_service import safe_folder_name
from core.data_cleaning_engine import DataCleaningEngine
from core.excel_csv_ingester import sanitize_dict_for_json
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient
from exporter import write_csv_file, write_excel_file
from models import ExamMeta, CourseCatalog

cleaning_bp = Blueprint("cleaning_api", __name__)

pending_cleaning_sessions: Dict[str, Dict[str, Any]] = {}


@cleaning_bp.route("/api/clean/analyze", methods=["POST"])
def analyze_data_cleaning():
    """
    Run AI-guided parameter discovery and local deterministic cleaning.
    Returns proposed ChangeSet and summary statistics for interactive user review.
    """
    data = request.get_json(silent=True) or {}
    records = data.get("students", [])
    folder_name = data.get("folder_name") or ""
    session_key = folder_name or "active_session"

    fieldnames = []
    if not records and folder_name:
        clean_folder = safe_folder_name(folder_name)
        csv_path = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_extracted_data.csv")
        if os.path.isfile(csv_path):
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                records = list(reader)

    if not records:
        return jsonify({"success": False, "error": "No student records provided or found for cleaning."}), 400

    if not fieldnames and records:
        fieldnames = list(records[0].keys())

    ollama = OllamaClient()
    cloud = CloudClient()
    engine = DataCleaningEngine(ollama_client=ollama, cloud_client=cloud)

    clean_result = engine.clean_dataset(records)

    pending_cleaning_sessions[session_key] = {
        "original_records": records,
        "cleaned_records": clean_result["cleaned_records"],
        "changes": clean_result["changes"],
        "summary": clean_result["summary"],
        "rules": clean_result["rules"],
        "fieldnames": fieldnames,
        "folder_name": folder_name
    }

    return jsonify({
        "success": True,
        "summary": clean_result["summary"],
        "changes": clean_result["changes"],
        "rules": clean_result["rules"],
        "cleaned_records": clean_result["cleaned_records"]
    })


@cleaning_bp.route("/api/clean/apply", methods=["POST"])
def apply_data_cleaning():
    """
    Applies or discards the proposed data cleaning changes to promote the cleaned table to primary.
    """
    try:
        data = request.get_json(silent=True) or {}
        folder_name = data.get("folder_name") or ""
        action = data.get("action", "accept")
        accepted_ids = data.get("accepted_ids")
        session_key = folder_name or "active_session"

        session = pending_cleaning_sessions.get(session_key)

        if action == "discard":
            if session_key in pending_cleaning_sessions:
                del pending_cleaning_sessions[session_key]
            return jsonify({
                "success": True,
                "action": "discard",
                "message": "Data cleaning changes discarded. Primary dataset preserved in original state."
            })

        if not session:
            passed_cleaned = data.get("cleaned_students")
            passed_orig = data.get("original_records")
            passed_changes = data.get("changes")
            if passed_orig and passed_changes and accepted_ids is not None:
                engine = DataCleaningEngine()
                final_records = engine.apply_changes(
                    original_records=passed_orig,
                    changes=passed_changes,
                    accepted_ids=set(str(x) for x in accepted_ids)
                )
            elif passed_cleaned:
                final_records = passed_cleaned
            else:
                return jsonify({"success": False, "error": "No active cleaning session found to apply."}), 400
        else:
            if accepted_ids is not None:
                engine = DataCleaningEngine()
                final_records = engine.apply_changes(
                    original_records=session["original_records"],
                    changes=session["changes"],
                    accepted_ids=set(str(x) for x in accepted_ids)
                )
            else:
                final_records = session["cleaned_records"]

        fieldnames = session.get("fieldnames") if session else (list(final_records[0].keys()) if final_records else [])
        all_fieldnames = list(fieldnames) if fieldnames else []
        for r in final_records:
            for k in r.keys():
                if k not in all_fieldnames:
                    all_fieldnames.append(k)

        if folder_name:
            clean_folder = safe_folder_name(folder_name)
            target_dir = os.path.join(OUTPUT_DIR, clean_folder)
            csv_path = os.path.join(target_dir, f"{clean_folder}_extracted_data.csv")
            excel_path = os.path.join(target_dir, f"{clean_folder}_extracted_data.xlsx")
            cat_path = os.path.join(target_dir, f"{clean_folder}_catalog.json")

            if os.path.isdir(target_dir):
                try:
                    write_csv_file(final_records, all_fieldnames, csv_path)
                except Exception as e:
                    logger.warning(f"Could not overwrite CSV with cleaned data: {e}")

                try:
                    meta = ExamMeta(exam_title=clean_folder, semester="")
                    cat_obj = CourseCatalog()
                    if os.path.isfile(cat_path):
                        with open(cat_path, "r", encoding="utf-8") as f:
                            cat_data = json.load(f)
                        meta.exam_title = cat_data.get("exam_title", clean_folder)
                        meta.semester = cat_data.get("semester", "")
                        for c in cat_data.get("courses", []):
                            cat_obj.add_course(c.get("code", ""), c.get("name", ""), c.get("heads", []))
                        meta.catalog = cat_obj
                    write_excel_file(final_records, all_fieldnames, meta.catalog, meta, excel_path)
                except Exception as e:
                    logger.warning(f"Could not re-generate Excel with cleaned data: {e}")

        if session_key in pending_cleaning_sessions:
            del pending_cleaning_sessions[session_key]

        return jsonify({
            "success": True,
            "action": "accept",
            "message": f"Successfully promoted cleaned data ({len(final_records)} records) as the primary dataset!",
            "records": final_records,
            "total_records": len(final_records)
        })
    except Exception as exc:
        logger.exception("Unexpected error in apply_data_cleaning")
        return jsonify({"success": False, "error": str(exc)}), 500



