"""
Application Configuration and Directory Paths
============================================
Central configuration paths for the data extractor analyzer.
"""

import os
import sys

# Base directories
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, ".."))
DEPARTMENTS_DATA_DIR = os.path.join(APP_DIR, "departments_data")
APP_DEPARTMENTS_DATA_DIR = DEPARTMENTS_DATA_DIR
BATCH_DATA_DIR = DEPARTMENTS_DATA_DIR
APP_BATCH_DATA_DIR = DEPARTMENTS_DATA_DIR
INPUT_DIR = os.path.join(APP_DIR, "input")
OUTPUT_DIR = os.path.join(APP_DIR, "output")
KB_DIR = os.path.join(APP_DIR, "knowledge_base")

# Ensure required runtime directories exist
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(KB_DIR, exist_ok=True)
os.makedirs(os.path.join(DEPARTMENTS_DATA_DIR, "pdf"), exist_ok=True)
os.makedirs(os.path.join(DEPARTMENTS_DATA_DIR, "excel"), exist_ok=True)

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
