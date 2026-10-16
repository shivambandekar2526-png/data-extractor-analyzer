"""
Global Runtime State & Shared Services Singletons
=================================================
Thread-safe tracking for background jobs, controllers, and service singletons.
"""

import os
import threading
from typing import Any, Dict

from services.config import (
    APP_DEPARTMENTS_DATA_DIR,
    DEPARTMENTS_DATA_DIR,
    OUTPUT_DIR,
)
from core.dataset_database import get_default_db_path, init_db
from core.excel_csv_ingester import TabularIngester
from core.batch_analyzer import BatchAcademicAnalyzer
from core.folder_watcher import folder_watcher
from core.execution_controller import ExtractionController

# Thread-safe job states
jobs: Dict[str, Any] = {}
jobs_lock = threading.Lock()

batch_jobs: Dict[str, Any] = {}
batch_jobs_lock = threading.Lock()

ai_cc_jobs: Dict[str, Any] = {}
ai_cc_jobs_lock = threading.Lock()

job_controllers: Dict[str, ExtractionController] = {}
job_controllers_lock = threading.Lock()

# Database initialization
DB_PATH = get_default_db_path(DEPARTMENTS_DATA_DIR)
init_db(DB_PATH)

# Ingesters and analyzers
tabular_ingester = TabularIngester(OUTPUT_DIR)
batch_analyzer = BatchAcademicAnalyzer(
    OUTPUT_DIR,
    app_batch_dir=APP_DEPARTMENTS_DATA_DIR,
    root_batch_dir=DEPARTMENTS_DATA_DIR
)

# Folder watcher paths
folder_watcher.add_watch_path("departments_excel", os.path.join(DEPARTMENTS_DATA_DIR, "excel"))
folder_watcher.add_watch_path("departments_pdf", os.path.join(DEPARTMENTS_DATA_DIR, "pdf"))
folder_watcher.add_watch_path("output", OUTPUT_DIR)
