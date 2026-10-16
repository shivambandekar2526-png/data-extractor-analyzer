import os
import csv
import json
import logging
from flask import Blueprint, request, jsonify
import pandas as pd

logger = logging.getLogger("app")

from services.config import OUTPUT_DIR
from services.extraction_service import safe_folder_name
from core.eda_engine import EDAEngine
from core.ai_insights import AIInsightsSynthesizer
from core.ollama_client import OllamaClient
from core.cloud_client import CloudClient

eda_bp = Blueprint("eda_api", __name__)

@eda_bp.route("/api/eda", methods=["GET", "POST"])
def run_dataset_eda():
    """Run deterministic statistical EDA on active or saved extraction."""
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or {}

    records = data.get("students", [])
    folder_name = data.get("folder_name") or request.args.get("folder_name", "")

    # If no records provided directly, load from saved folder in output
    if not records and folder_name:
        clean_folder = safe_folder_name(folder_name)
        csv_path = os.path.join(OUTPUT_DIR, clean_folder, f"{clean_folder}_extracted_data.csv")
        if os.path.isfile(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                records = list(csv.DictReader(f))

    if not records:
        return jsonify({"success": False, "error": "No student records provided or found for analysis."}), 400

    ollama = OllamaClient()
    cloud = CloudClient()
    eda_engine = EDAEngine()
    eda_results = eda_engine.run_analysis(
        data=records,
        ollama_client=ollama,
        cloud_client=cloud
    )

    return jsonify({
        "success": True,
        "eda": eda_results
    })


@eda_bp.route("/api/eda/insights", methods=["POST"])
def generate_eda_insights():
    """Generate executive AI insights and NAAC recommendations from compact EDA summary."""
    data = request.get_json(silent=True) or {}
    eda_data = data.get("eda", {})
    if not eda_data:
        return jsonify({"success": False, "error": "EDA metrics payload is required."}), 400

    ollama = OllamaClient()
    cloud = CloudClient()
    synthesizer = AIInsightsSynthesizer(ollama_client=ollama, cloud_client=cloud)
    insights = synthesizer.generate_narrative(eda_data)

    return jsonify({
        "success": True,
        "insights": insights
    })


