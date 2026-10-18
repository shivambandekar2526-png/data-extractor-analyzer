"""
Supabase Cloud Storage & Database Service
=========================================
Centralized, thread-safe interface for storing, retrieving, and cataloging
departmental academic records, clean Excel files, and PDFs directly in Supabase Cloud.
"""

import os
import io
import time
import json
import logging
import threading
from typing import Dict, List, Any, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("SupabaseService")

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config", "supabase_config.json")
CATALOG_STORAGE_PATH = "catalog/departments_catalog.json"


class SupabaseService:
    """Thread-safe service to interact with Supabase Storage and PostgreSQL."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client: Optional[Client] = None
        self._config: Dict[str, Any] = self._load_config()
        self._init_client()

        # In-memory thread-safe cache
        self._catalog_cache: Optional[Dict[str, Any]] = None
        self._catalog_timestamp: float = 0.0
        self._cache_ttl: float = 30.0  # seconds

    def _load_config(self) -> Dict[str, Any]:
        cfg = {
            "supabase_url": os.getenv("SUPABASE_URL", ""),
            "supabase_key": os.getenv("SUPABASE_KEY", ""),
            "supabase_publishable_key": os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
            "storage_bucket": os.getenv("SUPABASE_STORAGE_BUCKET", "departments_data"),
            "enabled": True
        }

        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    for k, v in file_cfg.items():
                        if v and not cfg.get(k):
                            cfg[k] = v
            except Exception as e:
                logger.warning(f"Could not load supabase_config.json: {e}")

        return cfg

    def _init_client(self):
        url = self._config.get("supabase_url")
        key = self._config.get("supabase_key")
        if url and key and create_client:
            try:
                self._client = create_client(url, key)
                logger.info(f"Supabase client initialized successfully for {url}")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                self._client = None
        else:
            self._client = None
            logger.warning(
                "[!] Supabase credentials not found in environment. "
                "Cloud departments and storage will be unavailable until SUPABASE_URL and SUPABASE_KEY are set."
            )

    @property
    def is_configured(self) -> bool:
        return self._client is not None and bool(self._config.get("supabase_url")) and bool(self._config.get("supabase_key"))

    @property
    def bucket_name(self) -> str:
        return self._config.get("storage_bucket", "departments_data")

    @property
    def client(self) -> Optional[Client]:
        if not self._client:
            with self._lock:
                if not self._client:
                    self._init_client()
        return self._client

    def configure(self, url: str, key: str, bucket: Optional[str] = None) -> bool:
        """Update Supabase configuration dynamically at runtime."""
        with self._lock:
            self._config["supabase_url"] = (url or "").strip()
            self._config["supabase_key"] = (key or "").strip()
            if bucket:
                self._config["storage_bucket"] = bucket.strip()
            self._client = None
            self._catalog_cache = None
            self._init_client()
            return self.is_configured

    def get_connection_status(self) -> Dict[str, Any]:
        """Returns connection health and summary for diagnostics."""
        url = self._config.get("supabase_url", "")
        masked_url = url[:22] + "..." if len(url) > 22 else url
        has_key = bool(self._config.get("supabase_key"))
        configured = self.is_configured

        connected = False
        dataset_count = 0
        error_msg = None

        if configured:
            try:
                cat = self.get_cloud_catalog()
                dataset_count = len(cat.get("datasets", []))
                connected = True
            except Exception as e:
                error_msg = str(e)

        return {
            "configured": configured,
            "connected": connected,
            "url": masked_url,
            "bucket": self.bucket_name,
            "has_key": has_key,
            "total_datasets": dataset_count,
            "error": error_msg
        }

    # ---------------------------------------------------------
    # Storage Operations
    # ---------------------------------------------------------
    def ensure_bucket_exists(self) -> bool:
        """Verifies or creates the departments_data bucket."""
        if not self.is_configured:
            return False
        with self._lock:
            try:
                buckets = self.client.storage.list_buckets()
                names = [b.name for b in buckets]
                if self.bucket_name not in names:
                    self.client.storage.create_bucket(self.bucket_name, options={"public": True})
                    logger.info(f"Created Supabase bucket: {self.bucket_name}")
                return True
            except Exception as e:
                logger.warning(f"Could not check/create bucket: {e}")
                return False

    def upload_file_bytes(self, data: bytes, remote_path: str, content_type: Optional[str] = None) -> bool:
        """Uploads raw bytes to the Supabase Storage bucket."""
        if not self.is_configured:
            logger.error("Supabase not configured for upload.")
            return False

        clean_path = remote_path.replace("\\", "/").lstrip("/")
        opts = {"upsert": "true"}
        if content_type:
            opts["contentType"] = content_type

        with self._lock:
            try:
                self.client.storage.from_(self.bucket_name).upload(
                    path=clean_path,
                    file=data,
                    file_options=opts
                )
                return True
            except Exception as e:
                logger.error(f"Failed to upload {clean_path} to Supabase Storage: {e}")
                return False

    def upload_local_file(self, local_path: str, remote_path: str) -> bool:
        """Reads local file and uploads it to Supabase Storage."""
        if not os.path.isfile(local_path):
            logger.error(f"Local file not found for upload: {local_path}")
            return False

        ext = os.path.splitext(local_path)[1].lower()
        content_type = "application/pdf" if ext == ".pdf" else (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext in (".xlsx", ".xls") else "application/octet-stream"
        )

        try:
            with open(local_path, "rb") as f:
                data = f.read()
            return self.upload_file_bytes(data, remote_path, content_type=content_type)
        except Exception as e:
            logger.error(f"Error reading local file {local_path}: {e}")
            return False

    def download_file_bytes(self, remote_path: str) -> Optional[bytes]:
        """Downloads file bytes from Supabase Storage."""
        if not self.is_configured:
            return None

        clean_path = remote_path.replace("\\", "/").lstrip("/")
        with self._lock:
            try:
                data = self.client.storage.from_(self.bucket_name).download(clean_path)
                return data
            except Exception as e:
                logger.error(f"Failed downloading {clean_path} from Supabase: {e}")
                return None

    def get_public_url(self, remote_path: str) -> str:
        clean_path = remote_path.replace("\\", "/").lstrip("/")
        if not self.is_configured:
            return ""
        try:
            return self.client.storage.from_(self.bucket_name).get_public_url(clean_path)
        except Exception:
            return ""

    def get_signed_url(self, remote_path: str, expires_in: int = 3600) -> Optional[str]:
        clean_path = remote_path.replace("\\", "/").lstrip("/")
        if not self.is_configured:
            return None
        with self._lock:
            try:
                res = self.client.storage.from_(self.bucket_name).create_signed_url(clean_path, expires_in)
                if isinstance(res, dict):
                    return res.get("signedURL") or res.get("signed_url")
                return getattr(res, "signed_url", None) or str(res)
            except Exception as e:
                logger.warning(f"Could not generate signed URL for {clean_path}: {e}")
                return None

    def delete_file(self, remote_path: str) -> bool:
        clean_path = remote_path.replace("\\", "/").lstrip("/")
        if not self.is_configured:
            return False
        with self._lock:
            try:
                self.client.storage.from_(self.bucket_name).remove([clean_path])
                return True
            except Exception as e:
                logger.error(f"Error deleting file {clean_path} from Supabase: {e}")
                return False

    def list_files_in_folder(self, folder: str = "") -> List[Dict[str, Any]]:
        if not self.is_configured:
            return []
        clean_folder = folder.replace("\\", "/").strip("/")
        with self._lock:
            try:
                res = self.client.storage.from_(self.bucket_name).list(path=clean_folder)
                return res or []
            except Exception as e:
                logger.warning(f"Error listing folder '{clean_folder}' in Supabase: {e}")
                return []

    # ---------------------------------------------------------
    # Cloud Catalog Management (Stored on Supabase Cloud)
    # ---------------------------------------------------------
    def get_cloud_catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieves the unified department catalog directly from Supabase Cloud.
        Cached in memory to avoid repetitive network calls and socket exhaustion.
        """
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._catalog_timestamp) < self._cache_ttl:
            return self._catalog_cache

        with self._lock:
            if not force_refresh and self._catalog_cache and (now - self._catalog_timestamp) < self._cache_ttl:
                return self._catalog_cache

            # 1. Try reading catalog/departments_catalog.json from Supabase Storage
            try:
                cat_bytes = self.client.storage.from_(self.bucket_name).download(CATALOG_STORAGE_PATH)
                if cat_bytes:
                    cat_data = json.loads(cat_bytes.decode("utf-8"))
                    if isinstance(cat_data, dict) and "datasets" in cat_data:
                        self._catalog_cache = cat_data
                        self._catalog_timestamp = now
                        return cat_data
            except Exception:
                pass

            # 2. If catalog.json does not exist in cloud storage yet, build it
            cat_data = self._build_fresh_catalog()
            self._save_catalog_to_cloud(cat_data)
            self._catalog_cache = cat_data
            self._catalog_timestamp = now
            return cat_data

    def _save_catalog_to_cloud(self, catalog_data: Dict[str, Any]) -> bool:
        """Uploads catalog JSON to Supabase Storage."""
        if not self.is_configured:
            return False
        try:
            payload = json.dumps(catalog_data, indent=2).encode("utf-8")
            self.client.storage.from_(self.bucket_name).upload(
                path=CATALOG_STORAGE_PATH,
                file=payload,
                file_options={"upsert": "true", "contentType": "application/json"}
            )
            return True
        except Exception as e:
            logger.warning(f"Could not save catalog.json to Supabase: {e}")
            return False

    def _build_fresh_catalog(self) -> Dict[str, Any]:
        """Compiles catalog from backup SQLite or initial scanned data."""
        datasets = []
        db_path = os.path.join(PROJECT_ROOT, "departments_data_migrated_backup", "academic_records.db")
        if not os.path.isfile(db_path):
            db_path = os.path.join(PROJECT_ROOT, "departments_data", "academic_records.db")

        if os.path.isfile(db_path):
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT department, batch, semester, total_students, meta_json, created_at, updated_at FROM datasets;")
                for r in cur.fetchall():
                    dept = r["department"]
                    batch = r["batch"]
                    sem = r["semester"]
                    meta_p = {}
                    if r["meta_json"]:
                        try:
                            meta_p = json.loads(r["meta_json"])
                        except Exception:
                            pass
                    datasets.append({
                        "department": dept,
                        "batch": batch,
                        "semester": sem,
                        "excel_path": f"excel/{dept}/{batch}/{sem}.xlsx",
                        "pdf_path": f"pdf/{dept}/{batch}/{sem}.pdf",
                        "total_students": r["total_students"] or 0,
                        "meta_json": meta_p,
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"]
                    })
                conn.close()
            except Exception as e:
                logger.warning(f"Could not read backup sqlite: {e}")

        # If no sqlite data, scan storage root
        if not datasets:
            try:
                dept_folders = self.client.storage.from_(self.bucket_name).list(path="excel")
                for d_item in dept_folders:
                    d_name = d_item.get("name") if isinstance(d_item, dict) else getattr(d_item, "name", "")
                    if not d_name or d_name.startswith("."):
                        continue
                    b_folders = self.client.storage.from_(self.bucket_name).list(path=f"excel/{d_name}")
                    for b_item in b_folders:
                        b_name = b_item.get("name") if isinstance(b_item, dict) else getattr(b_item, "name", "")
                        if not b_name or b_name.startswith("."):
                            continue
                        f_files = self.client.storage.from_(self.bucket_name).list(path=f"excel/{d_name}/{b_name}")
                        for f_item in f_files:
                            fname = f_item.get("name") if isinstance(f_item, dict) else getattr(f_item, "name", "")
                            if not (fname.endswith(".xlsx") or fname.endswith(".xls")):
                                continue
                            sem_name = os.path.splitext(fname)[0]
                            datasets.append({
                                "department": d_name,
                                "batch": b_name,
                                "semester": sem_name,
                                "excel_path": f"excel/{d_name}/{b_name}/{fname}",
                                "pdf_path": f"pdf/{d_name}/{b_name}/{sem_name}.pdf",
                                "total_students": 0,
                                "meta_json": {},
                                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
                            })
            except Exception as e:
                logger.warning(f"Could not compile datasets from storage: {e}")

        return {
            "datasets": datasets,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    def upsert_dataset_record(
        self,
        department: str,
        batch: str,
        semester: str,
        excel_path: str,
        pdf_path: Optional[str] = None,
        total_students: int = 0,
        meta_json: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Upserts a dataset record into Cloud Catalog and Database."""
        cat_data = self.get_cloud_catalog(force_refresh=True)
        datasets = cat_data.get("datasets", [])

        # Update or append
        existing = next((d for d in datasets if d["department"].lower() == department.lower() and d["batch"].lower() == batch.lower() and d["semester"].lower() == semester.lower()), None)
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            existing["excel_path"] = excel_path.replace("\\", "/")
            if pdf_path:
                existing["pdf_path"] = pdf_path.replace("\\", "/")
            if total_students:
                existing["total_students"] = total_students
            if meta_json:
                existing["meta_json"] = meta_json
            existing["updated_at"] = now_str
        else:
            datasets.append({
                "department": department,
                "batch": batch,
                "semester": semester,
                "excel_path": excel_path.replace("\\", "/"),
                "pdf_path": pdf_path.replace("\\", "/") if pdf_path else None,
                "total_students": total_students,
                "meta_json": meta_json or {},
                "created_at": now_str,
                "updated_at": now_str
            })

        cat_data["datasets"] = datasets
        cat_data["updated_at"] = now_str
        self._save_catalog_to_cloud(cat_data)
        self._catalog_cache = cat_data

        # Also try PostgreSQL table if it exists
        try:
            self.client.table("department_datasets").upsert({
                "department": department,
                "batch": batch,
                "semester": semester,
                "excel_path": excel_path.replace("\\", "/"),
                "pdf_path": pdf_path.replace("\\", "/") if pdf_path else None,
                "total_students": total_students,
                "meta_json": meta_json or {}
            }, on_conflict="department,batch,semester").execute()
        except Exception:
            pass

        return True

    def list_datasets_from_catalog(self) -> List[Dict[str, Any]]:
        """Returns all registered datasets from Cloud Catalog."""
        cat_data = self.get_cloud_catalog()
        return cat_data.get("datasets", [])

    def delete_dataset_record(self, department: str, batch: str, semester: str) -> bool:
        """Deletes dataset entry from Cloud Catalog and Storage."""
        cat_data = self.get_cloud_catalog(force_refresh=True)
        datasets = cat_data.get("datasets", [])
        matched = next((d for d in datasets if d["department"].lower() == department.lower() and d["batch"].lower() == batch.lower() and d["semester"].lower() == semester.lower()), None)

        if matched:
            datasets.remove(matched)
            cat_data["datasets"] = datasets
            cat_data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_catalog_to_cloud(cat_data)
            self._catalog_cache = cat_data

            if matched.get("excel_path"):
                self.delete_file(matched["excel_path"])
            if matched.get("pdf_path"):
                self.delete_file(matched["pdf_path"])

        try:
            self.client.table("department_datasets").delete().match({
                "department": department,
                "batch": batch,
                "semester": semester
            }).execute()
        except Exception:
            pass

        return True

    # ---------------------------------------------------------
    # Hierarchy & Batches View (Used by Frontend UI Dropdowns)
    # ---------------------------------------------------------
    def get_cloud_departments_hierarchy(self) -> List[Dict[str, Any]]:
        """Reconstructs the 3-tier hierarchy instantly from the cached Cloud Catalog."""
        datasets = self.list_datasets_from_catalog()
        dept_map = {}

        for r in datasets:
            dept = r.get("department", "Unknown")
            batch = r.get("batch", "Unknown")
            sem = r.get("semester", "Unknown")
            excel_p = r.get("excel_path", "")
            fname = os.path.basename(excel_p) if excel_p else f"{sem}.xlsx"

            if dept not in dept_map:
                dept_map[dept] = {}
            if batch not in dept_map[dept]:
                dept_map[dept][batch] = []

            dept_map[dept][batch].append({
                "file_name": fname,
                "display_name": f"{sem.replace('_', ' ')} ({fname})",
                "semester_name": sem.replace("_", " "),
                "rel_path": f"{dept}/{batch}/{fname}",
                "storage_path": excel_p or f"excel/{dept}/{batch}/{fname}",
                "pdf_storage_path": r.get("pdf_path") or f"pdf/{dept}/{batch}/{sem}.pdf",
                "dataset_id": f"supabase_excel_{dept}/{batch}/{fname}",
                "total_students": r.get("total_students", 0),
                "created_at": r.get("created_at")
            })

        hierarchy = []
        for dept_name, b_dict in sorted(dept_map.items()):
            batches = []
            for batch_name, s_list in sorted(b_dict.items()):
                batches.append({
                    "id": batch_name,
                    "folder": batch_name,
                    "name": batch_name,
                    "academic_year": batch_name,
                    "semesters": s_list
                })
            hierarchy.append({
                "id": dept_name,
                "folder": dept_name,
                "name": dept_name.replace("_", " "),
                "batches": batches
            })

        return hierarchy

    def get_cloud_batches_map(self) -> Dict[str, Any]:
        """
        Builds the exact `batches` and `structured` dictionaries expected by
        the main frontend dropdowns (/api/batches).
        """
        datasets = self.list_datasets_from_catalog()
        batches = {}
        structured = {
            "pdf": {},
            "excel": {}
        }

        for ds in datasets:
            dept = ds["department"]
            dept_label = dept.replace("_", " ")
            batch = ds["batch"]
            sem_name = ds["semester"]
            excel_path = ds.get("excel_path") or f"excel/{dept}/{batch}/{sem_name}.xlsx"
            pdf_path = ds.get("pdf_path") or f"pdf/{dept}/{batch}/{sem_name}.pdf"

            # 1. Excel item
            sem_excel_file = os.path.basename(excel_path)
            excel_group = f"📁 {dept_label} ({batch}) - EXCEL"
            excel_meta = {
                "rel_path": excel_path,
                "filename": f"{sem_excel_file} (EXCEL)",
                "display_name": f"{sem_name.replace('_', ' ')} ({sem_excel_file})",
                "file_type": "excel",
                "full_path": excel_path,
                "department": dept,
                "department_label": dept_label,
                "batch": batch,
                "semester": sem_name
            }
            batches.setdefault(excel_group, []).append(excel_meta)

            structured["excel"].setdefault(dept, {
                "department": dept,
                "label": dept_label,
                "batches": {}
            })
            structured["excel"][dept]["batches"].setdefault(batch, []).append({
                "rel_path": excel_path,
                "filename": sem_excel_file,
                "display_name": f"{sem_name.replace('_', ' ')} ({sem_excel_file})",
                "semester": sem_name,
                "batch": batch,
                "department": dept,
                "file_type": "excel"
            })

            # 2. PDF item
            sem_pdf_file = os.path.basename(pdf_path)
            pdf_group = f"📁 {dept_label} ({batch}) - PDF"
            pdf_meta = {
                "rel_path": pdf_path,
                "filename": f"{sem_pdf_file} (PDF)",
                "display_name": f"{sem_name.replace('_', ' ')} ({sem_pdf_file})",
                "file_type": "pdf",
                "full_path": pdf_path,
                "department": dept,
                "department_label": dept_label,
                "batch": batch,
                "semester": sem_name
            }
            batches.setdefault(pdf_group, []).append(pdf_meta)

            structured["pdf"].setdefault(dept, {
                "department": dept,
                "label": dept_label,
                "batches": {}
            })
            structured["pdf"][dept]["batches"].setdefault(batch, []).append({
                "rel_path": pdf_path,
                "filename": sem_pdf_file,
                "display_name": f"{sem_name.replace('_', ' ')} ({sem_pdf_file})",
                "semester": sem_name,
                "batch": batch,
                "department": dept,
                "file_type": "pdf"
            })

        return {
            "batches": batches,
            "structured": structured
        }


# Global singleton instance
_supabase_instance: Optional[SupabaseService] = None

def get_supabase_service() -> SupabaseService:
    global _supabase_instance
    if _supabase_instance is None:
        _supabase_instance = SupabaseService()
    return _supabase_instance
