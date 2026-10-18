"""
Data Extractor AI Web Application Instance
==========================================
Interactive Web Server & API for AI-Powered Multi-Archetype Academic Result Extraction.
Modular Flask Architecture with Service-Oriented Blueprint Routing.
Runs on Port 5001.
"""

import argparse
import logging
import os
import sys
import threading
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask

# Base directory configuration
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Configure logger
logger = logging.getLogger("app")

# Import central configuration and runtime state singletons
from services.config import (
    PROJECT_ROOT,
    DEPARTMENTS_DATA_DIR,
    APP_DEPARTMENTS_DATA_DIR,
    BATCH_DATA_DIR,
    APP_BATCH_DATA_DIR,
    INPUT_DIR,
    OUTPUT_DIR,
    KB_DIR,
)
from services.state import (
    jobs,
    jobs_lock,
    batch_jobs,
    batch_jobs_lock,
    ai_cc_jobs,
    ai_cc_jobs_lock,
    job_controllers,
    job_controllers_lock,
    DB_PATH,
    tabular_ingester,
    batch_analyzer,
    folder_watcher,
)
from services.extraction_service import (
    safe_folder_name,
    auto_sort_into_app_batch_store,
    update_job_progress,
    _run_ai_extraction_worker,
)
from services.batch_service import (
    update_batch_job_progress,
    _run_batch_analysis_worker,
    build_batch_analysis_excel_hierarchy,
)
from services.academic_service import (
    _standardize_academic_year,
    _standardize_semester_label,
    get_dept_excel_base_dir,
    get_departments_data_excel_hierarchy,
    resolve_subject_dataset,
    resolve_tabular_file_path,
)
from services.crosscheck_service import (
    update_ai_cc_progress,
    _find_pdf_for_folder,
    _run_post_crosscheck_worker,
)
from routes import register_blueprints


def create_app() -> Flask:
    """Application factory for Data Extractor AI."""
    application = Flask(
        __name__,
        template_folder=os.path.join(APP_DIR, "templates"),
        static_folder=os.path.join(APP_DIR, "static"),
    )
    application.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024  # 128 MB max upload
    application.config["JSON_SORT_KEYS"] = False
    application.json.sort_keys = False

    # Start background live folder watcher
    try:
        folder_watcher.start()
    except Exception as e:
        logger.warning(f"Could not start folder_watcher: {e}")

    # Register modular blueprints
    register_blueprints(application)

    return application


# Global application instance
app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Extractor AI Server")
    parser.add_argument("pdf_path", nargs="?", default=None, help="Optional PDF path to extract directly")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: 5001)")
    parser.add_argument("--host", type=str, default=None, help="Server host (default: 127.0.0.1 or 0.0.0.0 on cloud)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    port = args.port or int(os.environ.get("PORT", 5001))
    is_cloud = bool(os.environ.get("RENDER") or os.environ.get("PORT"))
    host = args.host or os.environ.get("HOST", "0.0.0.0" if is_cloud else "127.0.0.1")

    print(f"Starting Data Extractor AI on http://{host}:{port}")
    if not args.no_browser and not is_cloud:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    app.run(host=host, port=port, debug=False)
