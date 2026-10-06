"""
Academic Dataset SQLite Database Engine
=======================================
Stores finalized examination datasets and student records into a persistent
structured SQLite database at:
  departments_data/academic_records.db

Integrates with the single AI classification metadata (Department, Batch, Semester).
"""

import os
import json
import sqlite3
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("DatasetDatabase")


def get_default_db_path(departments_dir: str) -> str:
    """Returns absolute path to SQLite database."""
    os.makedirs(departments_dir, exist_ok=True)
    return os.path.join(departments_dir, "academic_records.db")


def init_db(db_path: str) -> None:
    """Initialize database tables and indices if not already present."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department TEXT NOT NULL,
                batch TEXT NOT NULL,
                semester TEXT NOT NULL,
                pdf_path TEXT,
                excel_path TEXT,
                total_students INTEGER DEFAULT 0,
                columns_json TEXT,
                meta_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(department, batch, semester)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                seat_no TEXT,
                student_name TEXT,
                sgpi REAL,
                result_status TEXT,
                record_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_datasets_dept_batch ON datasets(department, batch, semester);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_students_dataset_seat ON students(dataset_id, seat_no);")
        conn.commit()
    finally:
        conn.close()


def save_dataset_to_db(
    db_path: str,
    department: str,
    batch: str,
    semester: str,
    students: List[Dict[str, Any]],
    columns: Optional[List[str]] = None,
    pdf_path: Optional[str] = None,
    excel_path: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    catalog: Optional[Dict[str, Any]] = None
) -> int:
    """
    Saves or updates the dataset and student records transactionally in SQLite.
    Returns the dataset ID.
    """
    if catalog and not meta:
        meta = catalog
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cols_str = json.dumps(columns or (list(students[0].keys()) if students else []))
        meta_str = json.dumps(meta or {})

        # Upsert dataset header
        cur.execute("""
            INSERT INTO datasets (department, batch, semester, pdf_path, excel_path, total_students, columns_json, meta_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(department, batch, semester) DO UPDATE SET
                pdf_path = COALESCE(excluded.pdf_path, datasets.pdf_path),
                excel_path = COALESCE(excluded.excel_path, datasets.excel_path),
                total_students = excluded.total_students,
                columns_json = excluded.columns_json,
                meta_json = excluded.meta_json,
                updated_at = CURRENT_TIMESTAMP;
        """, (department, batch, semester, pdf_path, excel_path, len(students), cols_str, meta_str))

        cur.execute("SELECT id FROM datasets WHERE department = ? AND batch = ? AND semester = ?", (department, batch, semester))
        row = cur.fetchone()
        dataset_id = row[0] if row else cur.lastrowid

        # Replace existing student records for this dataset
        cur.execute("DELETE FROM students WHERE dataset_id = ?", (dataset_id,))

        student_rows = []
        for s in students:
            seat = str(s.get("Seat_No") or s.get("seat_no") or s.get("Seat") or s.get("roll_no") or "")
            name = str(s.get("Student_Name") or s.get("student_name") or s.get("Name") or "")
            sgpi_val = None
            try:
                raw_sgpi = s.get("SGPI") or s.get("sgpi") or s.get("GPA")
                if raw_sgpi is not None and str(raw_sgpi).replace(".", "", 1).isdigit():
                    sgpi_val = float(raw_sgpi)
            except Exception:
                pass
            status = str(s.get("Result_Status") or s.get("result_status") or s.get("Status") or "")
            rec_json = json.dumps(s)
            student_rows.append((dataset_id, seat, name, sgpi_val, status, rec_json))

        if student_rows:
            cur.executemany("""
                INSERT INTO students (dataset_id, seat_no, student_name, sgpi, result_status, record_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, student_rows)

        conn.commit()
        return dataset_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to save dataset to database: {e}")
        raise e
    finally:
        conn.close()


def get_dataset_from_db(db_path: str, department: str, batch: str, semester: str) -> Optional[Dict[str, Any]]:
    """Fetch stored dataset header and student records from SQLite."""
    if not os.path.isfile(db_path):
        return None

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM datasets WHERE department = ? AND batch = ? AND semester = ?",
            (department, batch, semester)
        )
        d_row = cur.fetchone()
        if not d_row:
            return None

        dataset = dict(d_row)
        dataset_id = dataset["id"]

        cur.execute("SELECT record_json FROM students WHERE dataset_id = ? ORDER BY id ASC", (dataset_id,))
        s_rows = cur.fetchall()
        students = [json.loads(r["record_json"]) for r in s_rows]
        dataset["students"] = students
        return dataset
    finally:
        conn.close()


def list_datasets_from_db(db_path: str) -> List[Dict[str, Any]]:
    """List all saved datasets stored in SQLite database."""
    if not os.path.isfile(db_path):
        return []

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, department, batch, semester, pdf_path, excel_path, total_students, updated_at FROM datasets ORDER BY department, batch, semester")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
