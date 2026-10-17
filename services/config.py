"""
Application Configuration and Directory Paths
============================================
Central configuration paths for the data extractor analyzer.
"""

import os
import sys

import tempfile

# Base directories
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, ".."))
KB_DIR = os.path.join(APP_DIR, "knowledge_base")

# Ephemeral runtime working directories in OS temp space
# (Eliminates all local data folders from this project repository)
RUNTIME_TEMP_DIR = os.path.join(tempfile.gettempdir(), "data_extractor_runtime")
INPUT_DIR = os.path.join(RUNTIME_TEMP_DIR, "input")
OUTPUT_DIR = os.path.join(RUNTIME_TEMP_DIR, "output")
DEPARTMENTS_DATA_DIR = os.path.join(RUNTIME_TEMP_DIR, "departments_data")
APP_DEPARTMENTS_DATA_DIR = DEPARTMENTS_DATA_DIR
BATCH_DATA_DIR = DEPARTMENTS_DATA_DIR
APP_BATCH_DATA_DIR = DEPARTMENTS_DATA_DIR

# Ensure required runtime working directories exist in OS temp space
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(KB_DIR, exist_ok=True)

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
