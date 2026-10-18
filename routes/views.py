"""
View Routes
===========
Handles HTML page serving and SPA entry points.
"""

from flask import Blueprint, render_template, request, redirect, url_for
from services.auth_service import get_auth_service

views_bp = Blueprint("views", __name__)


@views_bp.route("/login", methods=["GET"])
def login_route():
    """Render login and registration page."""
    # If already logged in via cookie, redirect to dashboard
    token = request.cookies.get("sb_access_token")
    if token:
        user = get_auth_service().verify_token(token)
        if user:
            return redirect(url_for("views.index_route"))
    return render_template("login.html")


@views_bp.route("/", methods=["GET"])
@views_bp.route("/extract", methods=["GET"])
@views_bp.route("/historical", methods=["GET"])
@views_bp.route("/analysis", methods=["GET"])
@views_bp.route("/analysis/subject", methods=["GET"])
@views_bp.route("/analysis/semester", methods=["GET"])
@views_bp.route("/reports", methods=["GET"])
@views_bp.route("/settings", methods=["GET"])
def index_route():
    """Main SPA entry point guarded by authentication."""
    token = request.cookies.get("sb_access_token")
    # If cookie is present, verify it
    if token:
        user = get_auth_service().verify_token(token)
        if user:
            return render_template("index.html")
    # If no valid cookie, serve index.html with client-side auth checker
    # so localStorage-cached sessions can auto-login without flashing.
    # If localStorage also has no session, client redirects to /login.
    return render_template("index.html")


@views_bp.app_errorhandler(404)
def handle_404(e):
    return render_template("index.html"), 200
