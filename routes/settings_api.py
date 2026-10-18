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

from routes.auth_api import get_current_user_from_request
from services.auth_service import get_auth_service

settings_bp = Blueprint("settings_api", __name__)

@settings_bp.route("/api/cloud/config", methods=["GET"])
def get_cloud_config():
    """Get Cloud AI settings safe for frontend display."""
    user = get_current_user_from_request()
    user_keys = {}
    if user:
        auth_svc = get_auth_service()
        settings = auth_svc.get_user_settings(user["id"])
        user_keys = settings.get("api_keys", {})

    client = CloudClient(config_override={"api_keys": user_keys} if user_keys else None)
    return jsonify(client.get_safe_config())


@settings_bp.route("/api/cloud/config", methods=["POST"])
def save_cloud_config():
    """Save Cloud AI API keys and settings."""
    data = request.get_json(silent=True) or {}
    user = get_current_user_from_request()

    # If authenticated, persist to user's Supabase account
    if user and "api_keys" in data and isinstance(data["api_keys"], dict):
        try:
            auth_svc = get_auth_service()
            existing_settings = auth_svc.get_user_settings(user["id"])
            merged_keys = existing_settings.get("api_keys", {})
            for p, k in data["api_keys"].items():
                if k == "__CLEAR__":
                    merged_keys.pop(p.lower(), None)
                elif isinstance(k, str) and k.strip():
                    merged_keys[p.lower()] = k.strip()
                elif isinstance(k, list) and k:
                    merged_keys[p.lower()] = k

            auth_svc.save_user_settings(
                user_id=user["id"],
                api_keys=merged_keys,
                preferences={
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "auto_orchestrate": data.get("auto_orchestrate", True),
                    "routing_mode": data.get("routing_mode", "auto_hybrid")
                }
            )
        except Exception as e:
            logger.warning(f"Failed to persist keys to user profile in Supabase: {e}")

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
    user = get_current_user_from_request()
    user_keys = {}
    if user:
        try:
            auth_svc = get_auth_service()
            settings = auth_svc.get_user_settings(user["id"])
            user_keys = settings.get("api_keys", {})
        except Exception:
            pass

    client = CloudClient(config_override={"api_keys": user_keys} if user_keys else None)
    keys_dict = data.get("api_keys") or user_keys
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
    user = get_current_user_from_request()
    user_keys = {}
    if user:
        try:
            auth_svc = get_auth_service()
            settings = auth_svc.get_user_settings(user["id"])
            user_keys = settings.get("api_keys", {})
        except Exception:
            pass

    input_keys = data.get("api_keys") or {}
    for k, v in input_keys.items():
        if v and isinstance(v, str) and v.strip():
            user_keys[k.lower()] = v.strip()

    client = CloudClient(config_override={"api_keys": user_keys} if user_keys else None)
    provider = data.get("provider")
    api_key = data.get("api_key")
    # If no inline key was passed, resolve from user's persistent keys
    if not api_key and provider and provider != "active":
        api_key = user_keys.get(provider.lower()) or client.get_api_key(provider.lower())
    elif not api_key and (not provider or provider == "active"):
        prov_cand = client.config.get("provider", "gemini").lower()
        if CloudClient._locked_model and CloudClient._locked_model.get("provider"):
            prov_cand = CloudClient._locked_model["provider"].lower()
        api_key = user_keys.get(prov_cand) or client.get_api_key(prov_cand)

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

