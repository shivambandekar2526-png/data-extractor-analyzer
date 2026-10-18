"""
Authentication & User API Routes
================================
Endpoints for user registration, login, logout, session refresh,
and persistent per-user AI API key management.
"""

import logging
from typing import Dict, Any, Optional
from flask import Blueprint, request, jsonify, make_response

logger = logging.getLogger("AuthAPI")

from services.auth_service import get_auth_service

auth_bp = Blueprint("auth_api", __name__)

COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds (remember device at all times)


def extract_token_from_request() -> Optional[str]:
    """Helper to extract access token from Authorization header or cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.cookies.get("sb_access_token")


def get_current_user_from_request() -> Optional[Dict[str, Any]]:
    """Helper to get validated user dictionary for the active request."""
    token = extract_token_from_request()
    if not token:
        return None
    auth_svc = get_auth_service()
    return auth_svc.verify_token(token)


@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    """Register a new user in Supabase Auth."""
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    full_name = data.get("full_name", "").strip()
    initial_keys = data.get("api_keys", {})

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required."}), 400

    auth_svc = get_auth_service()
    ok, msg, res_data = auth_svc.register(
        email=email,
        password=password,
        full_name=full_name,
        initial_keys=initial_keys
    )

    if not ok:
        return jsonify({"success": False, "error": msg}), 400

    response = make_response(jsonify({
        "success": True,
        "message": msg,
        **res_data
    }))

    # If access token was returned (auto-login), set persistent cookie
    access_token = res_data.get("access_token")
    refresh_token = res_data.get("refresh_token")
    if access_token:
        response.set_cookie("sb_access_token", access_token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    if refresh_token:
        response.set_cookie("sb_refresh_token", refresh_token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")

    return response


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    """Authenticate user with Supabase Auth and retrieve their persistent keys."""
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    remember_me = data.get("remember_me", True)

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required."}), 400

    auth_svc = get_auth_service()
    ok, msg, res_data = auth_svc.login(email=email, password=password)

    if not ok:
        return jsonify({"success": False, "error": msg}), 401

    response = make_response(jsonify({
        "success": True,
        "message": msg,
        **res_data
    }))

    max_age = COOKIE_MAX_AGE if remember_me else None
    access_token = res_data.get("access_token")
    refresh_token = res_data.get("refresh_token")

    if access_token:
        response.set_cookie("sb_access_token", access_token, max_age=max_age, httponly=True, samesite="Lax")
    if refresh_token:
        response.set_cookie("sb_refresh_token", refresh_token, max_age=max_age, httponly=True, samesite="Lax")

    return response


@auth_bp.route("/api/auth/refresh", methods=["POST"])
def refresh():
    """Refresh session token using refresh token from request body or cookie."""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token") or request.cookies.get("sb_refresh_token")

    if not refresh_token:
        return jsonify({"success": False, "error": "Missing refresh token."}), 400

    auth_svc = get_auth_service()
    ok, msg, res_data = auth_svc.refresh_session(refresh_token)

    if not ok:
        # Clear invalid cookies
        resp = make_response(jsonify({"success": False, "error": msg}), 401)
        resp.delete_cookie("sb_access_token")
        resp.delete_cookie("sb_refresh_token")
        return resp

    response = make_response(jsonify({
        "success": True,
        "message": msg,
        **res_data
    }))

    access_token = res_data.get("access_token")
    new_refresh = res_data.get("refresh_token")
    if access_token:
        response.set_cookie("sb_access_token", access_token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    if new_refresh:
        response.set_cookie("sb_refresh_token", new_refresh, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")

    return response


@auth_bp.route("/api/auth/me", methods=["GET"])
def me():
    """Verify active authentication session and return profile + persistent keys."""
    user = get_current_user_from_request()
    if not user:
        return jsonify({"success": False, "authenticated": False, "error": "Not authenticated."}), 401

    auth_svc = get_auth_service()
    user_id = user.get("id", "")
    settings = auth_svc.get_user_settings(user_id)

    return jsonify({
        "success": True,
        "authenticated": True,
        "user": user,
        "api_keys": settings.get("api_keys", {}),
        "preferences": settings.get("preferences", {})
    })


@auth_bp.route("/api/auth/keys", methods=["GET", "POST"])
def manage_user_keys():
    """Retrieve or persist API keys for the current authenticated user."""
    user = get_current_user_from_request()
    if not user:
        return jsonify({"success": False, "error": "Authentication required."}), 401

    auth_svc = get_auth_service()
    user_id = user.get("id", "")

    if request.method == "GET":
        settings = auth_svc.get_user_settings(user_id)
        return jsonify({
            "success": True,
            "api_keys": settings.get("api_keys", {}),
            "preferences": settings.get("preferences", {})
        })

    # POST: Update user keys
    data = request.get_json(silent=True) or {}
    api_keys = data.get("api_keys", {})
    preferences = data.get("preferences")

    ok, msg = auth_svc.save_user_settings(user_id=user_id, api_keys=api_keys, preferences=preferences)
    if not ok:
        return jsonify({"success": False, "error": msg}), 500

    return jsonify({
        "success": True,
        "message": msg,
        "api_keys": api_keys,
        "preferences": preferences
    })


@auth_bp.route("/api/auth/logout", methods=["POST", "GET"])
def logout():
    """Clear session cookies and return success."""
    response = make_response(jsonify({"success": True, "message": "Signed out successfully."}))
    response.delete_cookie("sb_access_token")
    response.delete_cookie("sb_refresh_token")
    return response
