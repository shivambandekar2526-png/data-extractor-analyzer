"""
Cloud & Model Settings Routes
=============================
Configuring API keys, zero-downtime rotation, model probing, and latency testing.
"""

import os
import logging
from typing import Dict, Any
from flask import Blueprint, request, jsonify

logger = logging.getLogger("app")

from core.cloud_client import CloudClient
from core.ollama_client import OllamaClient

settings_bp = Blueprint("settings_api", __name__)

@settings_bp.route("/api/cloud/config", methods=["GET"])
def get_cloud_config():
    """Get Cloud AI settings safe for frontend display."""
    client = CloudClient()
    return jsonify(client.get_safe_config())


@settings_bp.route("/api/cloud/config", methods=["POST"])
def save_cloud_config():
    """Save Cloud AI API keys and settings."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    ok, msg = client.save_config(data)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400

    # If auto-orchestration is on, pick and lock the best working model immediately
    if client.config.get("auto_orchestrate", True):
        best = client.get_best_working_model(force_refresh=True)
        msg += f" (Locked onto {best['provider'].upper()} - {best['model']})"

    return jsonify({"success": True, "message": msg, "config": client.get_safe_config()})


@settings_bp.route("/api/cloud/probe-all", methods=["POST"])
def probe_all_cloud_providers():
    """Probe all configured API keys across providers and return health report."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    keys_dict = data.get("api_keys")
    report = client.probe_all_providers(keys_dict=keys_dict)
    return jsonify({
        "success": True,
        "report": report,
        "config": client.get_safe_config()
    })


@settings_bp.route("/api/cloud/test", methods=["POST"])
def test_cloud_connection():
    """Test remote connection to the specified or configured Cloud AI."""
    data = request.get_json(silent=True) or {}
    client = CloudClient()
    provider = data.get("provider")
    api_key = data.get("api_key")
    model = data.get("model")

    ok, msg = client.test_connection(provider=provider, api_key=api_key, model=model)
    return jsonify({"success": ok, "message": msg})


@settings_bp.route("/api/cloud/models", methods=["GET", "POST"])
def get_cloud_models():
    """Fetch live or curated available models for a given provider and API key."""
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = {
            "provider": request.args.get("provider"),
            "api_key": request.args.get("api_key")
        }
    client = CloudClient()
    provider = data.get("provider") or client.config.get("provider", "gemini")
    api_key = (data.get("api_key") or client.config.get("api_key", "")).strip()

    models = client.fetch_live_models(provider=provider, api_key=api_key)
    return jsonify({
        "success": True,
        "provider": provider,
        "models": models
    })


@settings_bp.route("/api/models", methods=["GET"])
def get_available_models():
    """List locally downloaded Ollama models and currently active model."""
    client = OllamaClient()
    models = client.list_models() if client.is_available() else []
    return jsonify({
        "success": True,
        "active_model": client.model,
        "models": models
    })


@settings_bp.route("/api/model/switch", methods=["POST"])
def switch_model():
    """Switch active Ollama model dynamically."""
    data = request.get_json(silent=True) or {}
    new_model = data.get("model", "").strip()
    if not new_model:
        return jsonify({"success": False, "error": "Model name is required."}), 400
    os.environ["OLLAMA_MODEL"] = new_model
    return jsonify({"success": True, "active_model": new_model})


@settings_bp.route("/api/supabase/status", methods=["GET"])
def get_supabase_status():
    """Return Supabase Cloud connection status and diagnostics."""
    try:
        from services.supabase_service import get_supabase_service
        svc = get_supabase_service()
        status = svc.get_connection_status()
        return jsonify({"success": True, **status})
    except Exception as e:
        return jsonify({"success": False, "configured": False, "connected": False, "error": str(e)}), 500

