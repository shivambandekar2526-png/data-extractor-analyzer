"""
View Routes
===========
Handles HTML page serving and SPA entry points.
"""

from flask import Blueprint, render_template

views_bp = Blueprint("views", __name__)


@views_bp.route("/", methods=["GET"])
@views_bp.route("/extract", methods=["GET"])
@views_bp.route("/historical", methods=["GET"])
@views_bp.route("/analysis", methods=["GET"])
@views_bp.route("/analysis/subject", methods=["GET"])
@views_bp.route("/analysis/semester", methods=["GET"])
@views_bp.route("/reports", methods=["GET"])
@views_bp.route("/settings", methods=["GET"])
def index_route():
    return render_template("index.html")


@views_bp.app_errorhandler(404)
def handle_404(e):
    return render_template("index.html"), 200
