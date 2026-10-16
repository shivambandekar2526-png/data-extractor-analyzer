import os
import csv
import json
import logging
from flask import Blueprint, request, jsonify
import pandas as pd

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.extraction_service import safe_folder_name
from core.chat_assistant import process_chat_command
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient

chat_bp = Blueprint("chat_api", __name__)

@chat_bp.route("/api/chat", methods=["POST"])
def chat_with_dataset():
    """Interactive Chat with Hybrid Routing (Ollama + Cloud AI + Persistent Token Cache)."""
    data = request.get_json(silent=True) or {}
    user_prompt = data.get("prompt", "").strip()
    students = data.get("students", [])
    folder_name = data.get("folder_name", "")
    session_id = data.get("session_id", folder_name or "active_session")

    if not user_prompt:
        return jsonify({"success": False, "error": "Prompt cannot be empty."}), 400

    catalog_courses = []
    if folder_name:
        clean_folder = safe_folder_name(folder_name)
        cat_file = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_catalog.json")
        if os.path.isfile(cat_file):
            try:
                with open(cat_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    catalog_courses = cdata.get("courses", [])
            except Exception:
                pass

    client = OllamaClient()
    cloud_client = CloudClient()
    result = process_chat_command(
        user_prompt=user_prompt,
        students=students,
        catalog=catalog_courses,
        client=client,
        cloud_client=cloud_client,
        session_id=session_id,
        folder_name=folder_name
    )

    # Persist dataset modification to disk if folder_name provided
    if result.get("success") and result.get("action") not in ("answer", "unrecognized", "none"):
        updated = result.get("students", [])
        if folder_name and updated:
            try:
                clean_folder = safe_folder_name(folder_name)
                folder_path = os.path.join(OUTPUT_DIR, clean_folder)
                if os.path.isdir(folder_path):
                    csv_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.csv")
                    excel_path = os.path.join(folder_path, f"{clean_folder}_extracted_data.xlsx")
                    cat_path = os.path.join(folder_path, f"{clean_folder}_catalog.json")
                    keys = list(updated[0].keys())
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=keys)
                        writer.writeheader()
                        writer.writerows(updated)
                    try:
                        import pandas as pd
                        df = pd.DataFrame(updated)
                        df.to_excel(excel_path, index=False)
                    except Exception:
                        pass
                    # Sync total_students in catalog JSON
                    if os.path.isfile(cat_path):
                        try:
                            with open(cat_path, "r", encoding="utf-8") as f:
                                cdata = json.load(f)
                            cdata["total_students"] = len(updated)
                            with open(cat_path, "w", encoding="utf-8") as f:
                                json.dump(cdata, f, indent=2)
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error persisting modified records: {e}")

    return jsonify(result)



