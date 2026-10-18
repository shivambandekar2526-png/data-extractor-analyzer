"""
Authentication & Multi-Tenant User Management Service
=====================================================
Thread-safe interface for Supabase Authentication, JWT session verification,
and persistent per-user AI API keys and model configuration storage.
"""

import os
import json
import logging
import threading
from typing import Dict, Any, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("AuthService")

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = Any


class AuthService:
    """Thread-safe service to handle Supabase Auth & per-user key persistence."""

    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._singleton_lock:
                if not cls._instance:
                    cls._instance = super(AuthService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.Lock()
        self._supabase_url = os.getenv("SUPABASE_URL", "").strip()
        self._supabase_key = os.getenv("SUPABASE_KEY", "").strip()
        self._supabase_pub_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        self._admin_client: Optional[Client] = None
        self._pub_client: Optional[Client] = None
        self._init_clients()
        self._initialized = True

    def _init_clients(self):
        if not self._supabase_url:
            logger.warning("[!] SUPABASE_URL not configured in environment.")
            return

        if create_client:
            try:
                # Service role / secret key client for admin operations
                if self._supabase_key:
                    self._admin_client = create_client(self._supabase_url, self._supabase_key)
                # Public / anon key client for standard client auth if available
                pub_key = self._supabase_pub_key or self._supabase_key
                if pub_key:
                    self._pub_client = create_client(self._supabase_url, pub_key)
                logger.info("AuthService Supabase clients initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize AuthService Supabase clients: {e}")

    @property
    def is_configured(self) -> bool:
        return bool(self._supabase_url and (self._admin_client or self._pub_client))

    @property
    def client(self) -> Optional[Client]:
        return self._admin_client or self._pub_client

    def register(self, email: str, password: str, full_name: str = "", initial_keys: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Register a new user in Supabase Auth.
        Auto-confirms email so user can immediately sign in.
        Initializes user_metadata with name and default/initial API keys.
        """
        if not self.is_configured:
            return False, "Supabase authentication is not configured on this server.", {}

        email = (email or "").strip().lower()
        password = (password or "").strip()
        full_name = (full_name or "").strip()
        initial_keys = initial_keys or {}

        if not email or "@" not in email:
            return False, "Please enter a valid email address.", {}
        if len(password) < 6:
            return False, "Password must be at least 6 characters long.", {}

        user_metadata = {
            "full_name": full_name or email.split("@")[0].capitalize(),
            "api_keys": initial_keys,
            "preferences": {
                "provider": "gemini",
                "model": "gemini-flash-lite-latest",
                "auto_orchestrate": True,
                "routing_mode": "auto_hybrid"
            }
        }

        with self._lock:
            # First try admin create_user to bypass email confirmation
            if self._admin_client and hasattr(self._admin_client.auth, "admin"):
                try:
                    admin_res = self._admin_client.auth.admin.create_user({
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": user_metadata
                    })
                    user = getattr(admin_res, "user", admin_res)
                    user_dict = self._user_to_dict(user)
                    logger.info(f"User registered successfully via admin: {email}")

                    # Automatically sign in to get tokens
                    login_ok, login_msg, session_data = self.login(email, password)
                    if login_ok:
                        return True, "Account created and signed in successfully.", session_data
                    return True, "Account registered successfully. Please sign in.", {"user": user_dict}
                except Exception as admin_err:
                    err_msg = str(admin_err)
                    # Check if already registered
                    if "already" in err_msg.lower() or "unique" in err_msg.lower() or "exists" in err_msg.lower():
                        return False, "An account with this email already exists. Please sign in.", {}
                    logger.warning(f"Admin user creation failed, trying standard sign_up: {admin_err}")

            # Fallback to standard sign_up
            try:
                client = self._pub_client or self._admin_client
                res = client.auth.sign_up({
                    "email": email,
                    "password": password,
                    "options": {"data": user_metadata}
                })
                user = getattr(res, "user", None) or getattr(res, "data", None)
                session = getattr(res, "session", None)
                user_dict = self._user_to_dict(user) if user else {}
                
                result_data = {
                    "user": user_dict,
                    "access_token": session.access_token if session else "",
                    "refresh_token": session.refresh_token if session else "",
                    "api_keys": initial_keys,
                    "preferences": user_metadata["preferences"]
                }
                return True, "Account created successfully.", result_data
            except Exception as e:
                logger.error(f"Error during registration: {e}")
                err_str = str(e)
                if "already registered" in err_str.lower() or "user already exists" in err_str.lower():
                    return False, "An account with this email already exists. Please sign in.", {}
                return False, f"Registration failed: {err_str}", {}

    def login(self, email: str, password: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Authenticate user with Supabase Auth and load their persistent keys.
        Returns tokens and user profile.
        """
        if not self.is_configured:
            return False, "Supabase authentication is not configured on this server.", {}

        email = (email or "").strip().lower()
        password = (password or "").strip()

        if not email or not password:
            return False, "Email and password are required.", {}

        client = self._pub_client or self._admin_client
        try:
            res = client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            user = getattr(res, "user", None)
            session = getattr(res, "session", None)

            if not session or not session.access_token:
                return False, "Invalid login credentials.", {}

            user_id = str(user.id) if hasattr(user, "id") else (user.get("id") if isinstance(user, dict) else "")
            user_dict = self._user_to_dict(user)

            # Retrieve user's persistent keys and preferences
            settings = self.get_user_settings(user_id, user_obj=user)

            data = {
                "user": user_dict,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": getattr(session, "expires_at", None),
                "api_keys": settings.get("api_keys", {}),
                "preferences": settings.get("preferences", {})
            }
            logger.info(f"User logged in successfully: {email} (ID: {user_id})")
            return True, "Login successful.", data

        except Exception as e:
            logger.warning(f"Login failed for {email}: {e}")
            err_str = str(e).lower()
            if "invalid" in err_str or "credentials" in err_str or "password" in err_str:
                return False, "Invalid email or password. Please check your credentials.", {}
            if "email not confirmed" in err_str:
                return False, "Email confirmation is required. Please check your inbox or contact administrator.", {}
            return False, f"Login error: {str(e)}", {}

    def refresh_session(self, refresh_token: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Refresh an expired access token using the long-lived refresh token."""
        if not self.is_configured or not refresh_token:
            return False, "Missing refresh token or unconfigured service.", {}

        client = self._pub_client or self._admin_client
        try:
            res = client.auth.refresh_session(refresh_token)
            session = getattr(res, "session", None)
            user = getattr(res, "user", None)

            if not session or not session.access_token:
                return False, "Session refresh failed.", {}

            user_id = str(user.id) if hasattr(user, "id") else ""
            settings = self.get_user_settings(user_id, user_obj=user)

            data = {
                "user": self._user_to_dict(user),
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": getattr(session, "expires_at", None),
                "api_keys": settings.get("api_keys", {}),
                "preferences": settings.get("preferences", {})
            }
            return True, "Session refreshed.", data
        except Exception as e:
            logger.warning(f"Could not refresh session: {e}")
            return False, f"Refresh failed: {e}", {}

    def verify_token(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Verify Supabase JWT access token and return user dictionary.
        Returns None if token is invalid or expired.
        """
        if not self.is_configured or not access_token:
            return None

        clean_token = access_token.replace("Bearer ", "").strip()
        client = self._pub_client or self._admin_client
        try:
            user_res = client.auth.get_user(clean_token)
            user = getattr(user_res, "user", user_res)
            if user and (hasattr(user, "id") or isinstance(user, dict) and "id" in user):
                return self._user_to_dict(user)
            return None
        except Exception as e:
            logger.debug(f"Token verification failed: {e}")
            return None

    def get_user_settings(self, user_id: str, user_obj: Optional[Any] = None) -> Dict[str, Any]:
        """
        Retrieve user's persistent API keys and preferences.
        Tries Supabase public.user_settings table first, falls back to Supabase auth user_metadata.
        """
        if not user_id:
            return {"api_keys": {}, "preferences": {}}

        # 1. Check user_settings table
        if self._admin_client:
            try:
                res = self._admin_client.table("user_settings").select("*").eq("user_id", user_id).limit(1).execute()
                if res.data and len(res.data) > 0:
                    row = res.data[0]
                    return {
                        "api_keys": row.get("api_keys") or {},
                        "preferences": row.get("preferences") or {}
                    }
            except Exception:
                pass

        # 2. Check user_metadata from user_obj or fetch via admin
        metadata = {}
        if user_obj:
            if hasattr(user_obj, "user_metadata") and isinstance(user_obj.user_metadata, dict):
                metadata = user_obj.user_metadata
            elif isinstance(user_obj, dict) and isinstance(user_obj.get("user_metadata"), dict):
                metadata = user_obj["user_metadata"]

        if not metadata and self._admin_client and hasattr(self._admin_client.auth, "admin"):
            try:
                u_res = self._admin_client.auth.admin.get_user_by_id(user_id)
                u = getattr(u_res, "user", u_res)
                if hasattr(u, "user_metadata") and isinstance(u.user_metadata, dict):
                    metadata = u.user_metadata
            except Exception as e:
                logger.debug(f"Could not fetch user metadata for {user_id}: {e}")

        api_keys = metadata.get("api_keys", {}) if isinstance(metadata, dict) else {}
        preferences = metadata.get("preferences", {}) if isinstance(metadata, dict) else {}

        return {
            "api_keys": api_keys or {},
            "preferences": preferences or {
                "provider": "gemini",
                "model": "gemini-flash-lite-latest",
                "auto_orchestrate": True,
                "routing_mode": "auto_hybrid"
            }
        }

    def save_user_settings(self, user_id: str, api_keys: Dict[str, Any], preferences: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        Persist user API keys and preferences to both:
        1. Supabase public.user_settings table (if available)
        2. Supabase Auth user_metadata (guaranteed persistence across all sessions)
        """
        if not self.is_configured or not user_id:
            return False, "Authentication service unavailable."

        # Filter and clean keys
        clean_keys = {}
        if isinstance(api_keys, dict):
            for p, k in api_keys.items():
                p_clean = str(p).lower().strip()
                if isinstance(k, str) and k.strip():
                    clean_keys[p_clean] = k.strip()
                elif isinstance(k, list):
                    clean_items = [str(x).strip() for x in k if str(x).strip()]
                    if clean_items:
                        clean_keys[p_clean] = clean_items

        prefs = preferences or {
            "provider": "gemini",
            "model": "gemini-flash-lite-latest",
            "auto_orchestrate": True,
            "routing_mode": "auto_hybrid"
        }

        saved_table = False
        saved_metadata = False

        # Attempt 1: Upsert to user_settings table
        if self._admin_client:
            try:
                self._admin_client.table("user_settings").upsert({
                    "user_id": user_id,
                    "api_keys": clean_keys,
                    "preferences": prefs,
                    "updated_at": "now()"
                }).execute()
                saved_table = True
            except Exception as e:
                logger.debug(f"Could not upsert into user_settings table (table might not exist yet): {e}")

        # Attempt 2: Update Supabase Auth user_metadata via admin
        if self._admin_client and hasattr(self._admin_client.auth, "admin"):
            try:
                # Fetch existing metadata to merge
                existing_meta = {}
                try:
                    u_res = self._admin_client.auth.admin.get_user_by_id(user_id)
                    u = getattr(u_res, "user", u_res)
                    if hasattr(u, "user_metadata") and isinstance(u.user_metadata, dict):
                        existing_meta = u.user_metadata
                except Exception:
                    pass

                existing_meta["api_keys"] = clean_keys
                existing_meta["preferences"] = prefs

                self._admin_client.auth.admin.update_user_by_id(
                    user_id,
                    {"user_metadata": existing_meta}
                )
                saved_metadata = True
                logger.info(f"User metadata updated with persistent keys for user {user_id}")
            except Exception as e:
                logger.error(f"Error updating user_metadata in Supabase Auth: {e}")

        if saved_table or saved_metadata:
            return True, "API keys and preferences saved persistently to your account."
        return False, "Failed to persist user settings to Supabase."

    def _user_to_dict(self, user: Any) -> Dict[str, Any]:
        """Normalize Supabase User object or dict to standard dictionary."""
        if not user:
            return {}
        if isinstance(user, dict):
            return {
                "id": user.get("id", ""),
                "email": user.get("email", ""),
                "user_metadata": user.get("user_metadata", {})
            }
        
        user_meta = getattr(user, "user_metadata", {})
        if not isinstance(user_meta, dict):
            user_meta = {}

        return {
            "id": getattr(user, "id", ""),
            "email": getattr(user, "email", ""),
            "user_metadata": user_meta
        }


# Singleton accessor
def get_auth_service() -> AuthService:
    return AuthService()
