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


class SupabaseService:
    """Thread-safe service to interact with Supabase Storage and PostgreSQL."""

    def __init__(self):
        self._lock = threading.RLock()
        self._client: Optional[Client] = None
        self._config: Dict[str, Any] = self._load_config()
        self._realtime_started = False
        self._periodic_listener_started = False
        self._init_client()

        # In-memory thread-safe cache (single source of truth in RAM)
        self._catalog_cache: Optional[Dict[str, Any]] = None
        self._catalog_timestamp: float = 0.0
        self._cache_ttl: float = 60.0  # seconds (1 minute live sync)

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
                if not self._realtime_started:
                    self._start_realtime_listener()
                if not self._periodic_listener_started:
                    self._start_periodic_cloud_sync_listener()
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
    # Real-Time Push Synchronization (Zero Polling Queries)
    # ---------------------------------------------------------
    def _start_realtime_listener(self):
        """
        Runs a persistent, push-based WebSocket connection to Supabase Realtime
        to receive instant storage.objects notifications.
        While idle, consumes ZERO database calls and ZERO HTTP requests.
        """
        url = self._config.get("supabase_url", "")
        key = self._config.get("supabase_key", "")
        if not url or not key:
            return

        self._realtime_started = True

        def _run_realtime_loop():
            import asyncio
            try:
                import websockets
            except ImportError:
                logger.warning("websockets not installed; Supabase Realtime push listener disabled.")
                return

            base_ws = url.replace("https://", "wss://").replace("http://", "ws://")
            ws_url = f"{base_ws}/realtime/v1/websocket?apikey={key}&vsn=1.0.0"

            async def _listen():
                retry_delay = 3.0
                while True:
                    try:
                        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                            logger.info("[Supabase Realtime] Connected to push stream. Listening for storage events.")
                            retry_delay = 3.0
                            # Join storage changes channel
                            join_msg = {
                                "topic": "realtime:storage_channel",
                                "event": "phx_join",
                                "payload": {
                                    "config": {
                                        "postgres_changes": [
                                            {"event": "*", "schema": "storage", "table": "objects"}
                                        ]
                                    }
                                },
                                "ref": "1"
                            }
                            await ws.send(json.dumps(join_msg))

                            while True:
                                raw = await ws.recv()
                                msg = json.loads(raw)
                                evt = msg.get("event")
                                payload = msg.get("payload", {})

                                if evt == "postgres_changes":
                                    data = payload.get("data", {})
                                    change_type = data.get("type", "")
                                    record = data.get("record") or {}
                                    old_record = data.get("old_record") or {}
                                    path = record.get("name") or old_record.get("name") or ""
                                    bucket = record.get("bucket_id") or old_record.get("bucket_id") or ""
                                    if path and (not bucket or bucket == self.bucket_name):
                                        logger.info(f"[Supabase Realtime] Pushed change: {change_type} on {path}")
                                        self.handle_storage_event(change_type, path)
                    except Exception as e:
                        logger.debug(f"[Supabase Realtime] Reconnecting in {retry_delay:.1f}s... ({e})")
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.5, 60.0)

            try:
                asyncio.run(_listen())
            except Exception as loop_err:
                logger.debug(f"[Supabase Realtime] Loop exited: {loop_err}")

        t = threading.Thread(target=_run_realtime_loop, daemon=True, name="SupabaseRealtimeThread")
        t.start()
        logger.info("Supabase Realtime push listener thread launched.")

    def _start_periodic_cloud_sync_listener(self):
        """
        Runs a background synchronization listener timed at 1 minute (60 seconds).
        Reconciles Supabase Storage files against memory. If any file was deleted
        or added in the Supabase dashboard, it immediately pushes an SSE update
        to the browser UI.
        """
        self._periodic_listener_started = True

        def _loop():
            # Initial wait of 60 seconds before first periodic check
            time.sleep(60.0)
            while True:
                try:
                    if self.is_configured:
                        old_cache = self._catalog_cache or {}
                        old_datasets = {
                            (d.get("department", ""), d.get("batch", ""), d.get("semester", "")): (d.get("excel_path"), d.get("pdf_path"))
                            for d in old_cache.get("datasets", [])
                        }

                        # Reconcile fresh from storage
                        new_cat = self.reconcile_catalog_with_storage(force_save=False)
                        new_datasets = {
                            (d.get("department", ""), d.get("batch", ""), d.get("semester", "")): (d.get("excel_path"), d.get("pdf_path"))
                            for d in new_cat.get("datasets", [])
                        }

                        if old_datasets != new_datasets:
                            logger.info("[1-Min Cloud Listener] Storage changes detected! Syncing UI...")
                            try:
                                from core.folder_watcher import folder_watcher
                                folder_watcher.notify_event({
                                    "event": "storage_reconciled",
                                    "category": "supabase_storage",
                                    "timestamp": time.time()
                                })
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug(f"[1-Min Cloud Listener] Error: {e}")
                time.sleep(60.0)

        t = threading.Thread(target=_loop, daemon=True, name="Supabase1MinListenerThread")
        t.start()
        logger.info("Supabase 1-minute cloud sync listener launched.")

    def handle_storage_event(self, event_type: str, file_path: str) -> bool:
        """
        Directly mutates the in-memory catalog cache when a file is created,
        updated, or deleted in Supabase Storage.
        Requires ZERO calls to Supabase.
        """
        if not file_path:
            return False

        clean_path = file_path.replace("\\", "/").strip("/")
        parts = clean_path.split("/")
        # We track pdf/<dept>/<batch>/<file> and excel/<dept>/<batch>/<file>
        if len(parts) < 4 or parts[0] not in ("pdf", "excel"):
            return False

        fmt = parts[0]  # "pdf" or "excel"
        dept = parts[1]
        batch = parts[2]
        filename = parts[3]
        sem = os.path.splitext(filename)[0]
        event_upper = event_type.upper()

        with self._lock:
            if not self._catalog_cache:
                # If cache is not yet built, it will compile fresh on first query
                return True

            datasets = self._catalog_cache.get("datasets", [])
            key = (dept.lower(), batch.lower(), sem.lower())
            matched = next(
                (d for d in datasets
                 if d.get("department", "").lower() == key[0]
                 and d.get("batch", "").lower() == key[1]
                 and d.get("semester", "").lower() == key[2]),
                None
            )

            if "DELETE" in event_upper:
                if matched:
                    if fmt == "excel" and matched.get("excel_path") == clean_path:
                        matched["excel_path"] = None
                    elif fmt == "pdf" and matched.get("pdf_path") == clean_path:
                        matched["pdf_path"] = None

                    # If neither excel nor pdf exists anymore, remove the dataset record entirely
                    if not matched.get("excel_path") and not matched.get("pdf_path"):
                        datasets.remove(matched)

                    self._catalog_cache["datasets"] = datasets
                    self._catalog_cache["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[LiveSync] In-memory catalog pruned deleted file: {clean_path}")
            elif "INSERT" in event_upper or "UPDATE" in event_upper:
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                if matched:
                    if fmt == "excel":
                        matched["excel_path"] = clean_path
                    elif fmt == "pdf":
                        matched["pdf_path"] = clean_path
                    matched["updated_at"] = now_str
                else:
                    datasets.append({
                        "department": dept,
                        "batch": batch,
                        "semester": sem,
                        "excel_path": clean_path if fmt == "excel" else None,
                        "pdf_path": clean_path if fmt == "pdf" else None,
                        "total_students": 0,
                        "meta_json": {},
                        "created_at": now_str,
                        "updated_at": now_str
                    })
                self._catalog_cache["datasets"] = datasets
                self._catalog_cache["updated_at"] = now_str
                logger.info(f"[LiveSync] In-memory catalog registered new file: {clean_path}")

        # Broadcast change event to folder_watcher (triggers SSE stream to browser UI)
        try:
            from core.folder_watcher import folder_watcher
            folder_watcher.notify_event({
                "event": "file_deleted" if "DELETE" in event_upper else "file_created",
                "category": "supabase_storage",
                "file": clean_path,
                "filename": filename,
                "department": dept,
                "batch": batch,
                "semester": sem,
                "file_type": fmt,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.debug(f"Could not notify folder_watcher: {e}")

        return True

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
                self.handle_storage_event("INSERT", clean_path)
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
                self.handle_storage_event("DELETE", clean_path)
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
        Retrieves the unified department catalog directly from the verified in-memory cache.
        Cached in memory to eliminate useless database calls.
        Only performs a live bucket scan on initial startup or explicit force_refresh.
        """
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._catalog_timestamp) < self._cache_ttl:
            return self._catalog_cache

        with self._lock:
            if not force_refresh and self._catalog_cache and (now - self._catalog_timestamp) < self._cache_ttl:
                return self._catalog_cache

            # Reconcile directly with real storage objects (no static catalog JSON)
            cat_data = self.reconcile_catalog_with_storage(force_save=False)
            self._catalog_cache = cat_data
            self._catalog_timestamp = now
            return cat_data

    def _list_storage_files_recursive(self, prefix: str = "") -> List[str]:
        """Recursively lists all object file paths in a given Supabase Storage prefix."""
        if not self.is_configured:
            return []
        results = []
        try:
            items = self.client.storage.from_(self.bucket_name).list(prefix)
            for item in items:
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
                if not name or name.startswith("."):
                    continue
                path = f"{prefix}/{name}" if prefix else name
                # If metadata is missing or id is None, it is a folder
                is_folder = False
                if isinstance(item, dict):
                    is_folder = item.get("id") is None or not item.get("metadata")
                else:
                    is_folder = getattr(item, "id", None) is None or not getattr(item, "metadata", None)

                if is_folder:
                    results.extend(self._list_storage_files_recursive(path))
                else:
                    results.append(path.replace("\\", "/"))
        except Exception as e:
            logger.debug(f"Storage listing warning for prefix '{prefix}': {e}")
        return results

    def reconcile_catalog_with_storage(self, force_save: bool = False) -> Dict[str, Any]:
        """
        Builds the active catalog directly from real Supabase Storage files:
        - Scans all verified files in pdf/ and excel/.
        - Purges non-existent (phantom) PDF/Excel paths.
        - Preserves existing student counts and metadata in memory.
        - Zero reliance on static catalog JSON files in cloud storage.
        """
        if not self.is_configured:
            return {"datasets": [], "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}

        logger.info("[Reconciliation] Scanning Supabase Storage for actual academic files...")
        real_pdfs = set(self._list_storage_files_recursive("pdf"))
        real_excels = set(self._list_storage_files_recursive("excel"))

        # Fetch existing in-memory metadata to preserve student counts
        existing_meta = {}
        if self._catalog_cache and "datasets" in self._catalog_cache:
            for d in self._catalog_cache.get("datasets", []):
                k = (d.get("department", "").lower(), d.get("batch", "").lower(), d.get("semester", "").lower())
                existing_meta[k] = d

        datasets_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Process all verified Excels in Storage
        for fpath in sorted(real_excels):
            parts = fpath.split("/")
            if len(parts) >= 4:
                dept = parts[1]
                batch = parts[2]
                sem = os.path.splitext(parts[3])[0]
                key = (dept.lower(), batch.lower(), sem.lower())
                prev = existing_meta.get(key, {})
                datasets_map[key] = {
                    "department": dept,
                    "batch": batch,
                    "semester": sem,
                    "excel_path": fpath,
                    "pdf_path": None,
                    "total_students": prev.get("total_students", 0),
                    "meta_json": prev.get("meta_json", {}),
                    "created_at": prev.get("created_at", now_str),
                    "updated_at": now_str
                }

        # 2. Process all verified PDFs in Storage
        for fpath in sorted(real_pdfs):
            parts = fpath.split("/")
            if len(parts) >= 4:
                dept = parts[1]
                batch = parts[2]
                sem = os.path.splitext(parts[3])[0]
                key = (dept.lower(), batch.lower(), sem.lower())
                if key in datasets_map:
                    datasets_map[key]["pdf_path"] = fpath
                else:
                    prev = existing_meta.get(key, {})
                    datasets_map[key] = {
                        "department": dept,
                        "batch": batch,
                        "semester": sem,
                        "excel_path": None,
                        "pdf_path": fpath,
                        "total_students": prev.get("total_students", 0),
                        "meta_json": prev.get("meta_json", {}),
                        "created_at": prev.get("created_at", now_str),
                        "updated_at": now_str
                    }

        clean_datasets = list(datasets_map.values())
        clean_catalog = {
            "datasets": clean_datasets,
            "updated_at": now_str,
            "verified_storage_sync": True
        }

        self._catalog_cache = clean_catalog
        self._catalog_timestamp = time.time()
        logger.info(f"[Reconciliation] In-memory catalog synchronized: {len(clean_datasets)} verified datasets ({len(real_pdfs)} PDFs, {len(real_excels)} Excels).")
        return clean_catalog

    def _build_fresh_catalog(self) -> Dict[str, Any]:
        """Compiles catalog directly from Supabase Storage reconciliation."""
        return self.reconcile_catalog_with_storage(force_save=False)

    def upsert_dataset_record(
        self,
        department: str,
        batch: str,
        semester: str,
        excel_path: Optional[str] = None,
        pdf_path: Optional[str] = None,
        total_students: int = 0,
        meta_json: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Upserts a dataset record directly into in-memory catalog (and Database if table exists)."""
        cat_data = self.get_cloud_catalog(force_refresh=False)
        datasets = cat_data.get("datasets", [])

        # Update or append
        existing = next((d for d in datasets if d["department"].lower() == department.lower() and d["batch"].lower() == batch.lower() and d["semester"].lower() == semester.lower()), None)
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            if excel_path:
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
                "excel_path": excel_path.replace("\\", "/") if excel_path else None,
                "pdf_path": pdf_path.replace("\\", "/") if pdf_path else None,
                "total_students": total_students,
                "meta_json": meta_json or {},
                "created_at": now_str,
                "updated_at": now_str
            })

        cat_data["datasets"] = datasets
        cat_data["updated_at"] = now_str
        self._catalog_cache = cat_data
        self._catalog_timestamp = time.time()

        # Also try PostgreSQL table if configured
        try:
            row_data = {
                "department": department,
                "batch": batch,
                "semester": semester,
                "total_students": total_students,
                "meta_json": meta_json or {}
            }
            if excel_path:
                row_data["excel_path"] = excel_path.replace("\\", "/")
            if pdf_path:
                row_data["pdf_path"] = pdf_path.replace("\\", "/")
            self.client.table("department_datasets").upsert(row_data, on_conflict="department,batch,semester").execute()
        except Exception:
            pass

        return True

    def list_datasets_from_catalog(self) -> List[Dict[str, Any]]:
        """Returns all registered datasets from Cloud Catalog."""
        cat_data = self.get_cloud_catalog()
        return cat_data.get("datasets", [])

    def delete_dataset_record(self, department: str, batch: str, semester: str) -> bool:
        """Deletes dataset entry from Cloud Catalog and Storage."""
        cat_data = self.get_cloud_catalog(force_refresh=False)
        datasets = cat_data.get("datasets", [])
        matched = next((d for d in datasets if d["department"].lower() == department.lower() and d["batch"].lower() == batch.lower() and d["semester"].lower() == semester.lower()), None)

        if matched:
            datasets.remove(matched)
            cat_data["datasets"] = datasets
            cat_data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._catalog_cache = cat_data
            self._catalog_timestamp = time.time()

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
            excel_path = ds.get("excel_path")
            pdf_path = ds.get("pdf_path")

            # 1. Excel item (only if Excel actually exists in storage)
            if excel_path:
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

            # 2. PDF item (only if PDF actually exists in storage)
            if pdf_path:
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
