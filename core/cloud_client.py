"""
Cloud AI Engine Client & Multi-Provider Auto-Orchestrator
==========================================================
Unified interface for Google Gemini, Groq, OpenAI, and custom OpenAI-compatible APIs.
Features:
- Multi-provider API keys storage (Groq, Gemini, OpenAI, Custom).
- Intelligent auto-orchestration: Probes and locks onto the best available working model.
- Model Stickiness: Remains locked to the active working model across turns.
- Self-Healing Real-Time Failover: Automatically switches to the next best model on any error (429, 503, 404, timeout).
- Persistent dataset context token caching to minimize prompt payload and costs.
"""

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "cloud_config.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Curated, verified working models for each provider (no outdated/dead endpoints)
DEFAULT_PROVIDERS = {
    "groq": {
        "name": "Groq",
        "default_model": "llama-3.3-70b-versatile",
        "available_models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b"
        ],
        "base_url": "https://api.groq.com/openai/v1"
    },
    "gemini": {
        "name": "Google Gemini",
        "default_model": "gemini-flash-latest",
        "available_models": [
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash",
            "gemini-2.5-pro"
        ],
        "base_url": "https://generativelanguage.googleapis.com/v1beta"
    },
    "openai": {
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "available_models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-3.5-turbo"
        ],
        "base_url": "https://api.openai.com/v1"
    },
    "custom": {
        "name": "Custom OpenAI-Compatible",
        "default_model": "default",
        "available_models": [],
        "base_url": "http://localhost:8000/v1"
    }
}

# Hierarchy of candidates for automatic orchestration (ranked by speed, capability, & quota)
# Uses high-TPM models (e.g. Llama 3.3 70B: 30k TPM, Gemini Flash: 250k TPM) to prevent HTTP 413/429
ORCHESTRATION_CANDIDATES = [
    {"provider": "gemini", "model": "gemini-flash-latest", "label": "Google Gemini Flash (250k TPM)"},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq Llama 3.3 70B (30k TPM)"},
    {"provider": "gemini", "model": "gemini-2.5-flash", "label": "Google Gemini 2.5 Flash"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Groq Llama 3.1 8B Instant (30k TPM)"},
    {"provider": "openai", "model": "gpt-4o-mini", "label": "OpenAI GPT-4o-mini"},
    {"provider": "groq", "model": "qwen/qwen3.8-27b", "label": "Groq Fast Reasoning (27B)"},
    {"provider": "gemini", "model": "gemini-flash-lite-latest", "label": "Google Gemini Flash Lite"},
    {"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o Flagship"},
    {"provider": "groq", "model": "openai/gpt-oss-120b", "label": "Groq Flagship (120B, 8k TPM)"}
]


class CloudClient:
    """Interface to external Cloud AI providers with multi-key auto-orchestration and failover."""

    # In-memory session store to preserve dataset state across multiple chat turns
    _sessions: Dict[str, Dict[str, Any]] = {}

    # Currently locked-in working model across the application lifetime
    _locked_model: Optional[Dict[str, str]] = None

    # Model cooldown tracker: maps "provider:model" to expiration timestamp
    _model_cooldowns: Dict[str, float] = {}

    # Provider cooldown tracker: maps "provider" to expiration timestamp (for 429 rate limits)
    _provider_cooldowns: Dict[str, float] = {}

    def __init__(self):
        self.config = self.load_config()
        if CloudClient._locked_model is None and self.config.get("auto_orchestrate", True):
            # Attempt to set initial locked model from config or best candidate
            CloudClient._locked_model = {
                "provider": self.config.get("provider", "groq"),
                "model": self.config.get("model", "openai/gpt-oss-120b")
            }

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Load multi-provider configuration from disk or return default."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        default_cfg = {
            "api_keys": {
                "groq": "",
                "gemini": "",
                "openai": "",
                "custom": ""
            },
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "routing_mode": "auto_hybrid",  # "auto_hybrid", "prefer_cloud", "prefer_ollama"
            "auto_orchestrate": True,       # Auto-detect best working model and stick until error
            "custom_base_url": "",
            "temperature": 0.2,
            "enabled": True
        }

        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)

                # Migration: if legacy single api_key was saved, copy into api_keys dict
                if "api_key" in saved and "api_keys" not in saved:
                    prov = saved.get("provider", "groq").lower()
                    default_cfg["api_keys"][prov] = saved["api_key"]
                elif "api_keys" in saved and isinstance(saved["api_keys"], dict):
                    default_cfg["api_keys"].update(saved["api_keys"])

                for k in ["provider", "model", "routing_mode", "auto_orchestrate", "custom_base_url", "temperature", "enabled"]:
                    if k in saved:
                        default_cfg[k] = saved[k]
            except Exception:
                pass

        return default_cfg

    def save_config(self, new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
        """Save updated multi-provider configuration to disk."""
        os.makedirs(CONFIG_DIR, exist_ok=True)

        if "api_keys" in new_cfg and isinstance(new_cfg["api_keys"], dict):
            for p, k in new_cfg["api_keys"].items():
                if isinstance(k, str) and k.strip():
                    self.config["api_keys"][p.lower()] = k.strip()

        # Support single api_key update
        if "api_key" in new_cfg and isinstance(new_cfg["api_key"], str):
            prov = new_cfg.get("provider", self.config.get("provider", "groq")).lower()
            if new_cfg["api_key"].strip():
                self.config["api_keys"][prov] = new_cfg["api_key"].strip()

        for k in ["provider", "model", "routing_mode", "auto_orchestrate", "custom_base_url", "temperature", "enabled"]:
            if k in new_cfg:
                self.config[k] = new_cfg[k]

        # Sync primary api_key field for backward compatibility
        cur_prov = self.config.get("provider", "groq").lower()
        self.config["api_key"] = self.config.get("api_keys", {}).get(cur_prov, "")

        # If user explicitly changed provider/model, update the locked model
        if "provider" in new_cfg or "model" in new_cfg:
            CloudClient._locked_model = {
                "provider": self.config.get("provider", "groq"),
                "model": self.config.get("model", "openai/gpt-oss-120b")
            }

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True, "Cloud AI configuration saved successfully."
        except Exception as e:
            return False, f"Failed to write config: {e}"

    def get_api_key(self, provider: Optional[str] = None) -> str:
        """Retrieve the API key for the requested provider."""
        prov = (provider or self.config.get("provider", "groq")).lower()
        keys_dict = self.config.get("api_keys", {})
        key = keys_dict.get(prov, "").strip()
        if not key and prov == self.config.get("provider", "").lower():
            key = self.config.get("api_key", "").strip()
        return key

    def is_configured(self) -> bool:
        """Check if at least one AI provider has an API key configured and cloud AI is enabled."""
        if not self.config.get("enabled", True):
            return False
        keys_dict = self.config.get("api_keys", {})
        if any(bool(k.strip()) for k in keys_dict.values()):
            return True
        return bool(self.config.get("api_key", "").strip())

    @staticmethod
    def mask_key(key: str) -> str:
        """Return masked key for safe UI display (e.g. gsk_7y4...ZR2V)."""
        k = (key or "").strip()
        if not k:
            return ""
        if len(k) <= 8:
            return k[:2] + "•" * (len(k) - 2)
        return k[:6] + "•" * (len(k) - 10) + k[-4:]

    def get_safe_config(self) -> Dict[str, Any]:
        """Return configuration safe for frontend rendering (never exposes raw API keys)."""
        keys_dict = self.config.get("api_keys", {})
        active_locked = CloudClient._locked_model or {
            "provider": self.config.get("provider", "groq"),
            "model": self.config.get("model", "openai/gpt-oss-120b")
        }

        return {
            "provider": self.config.get("provider", "groq"),
            "model": self.config.get("model", "openai/gpt-oss-120b"),
            "routing_mode": self.config.get("routing_mode", "auto_hybrid"),
            "auto_orchestrate": self.config.get("auto_orchestrate", True),
            "custom_base_url": self.config.get("custom_base_url", ""),
            "enabled": self.config.get("enabled", True),
            "is_configured": self.is_configured(),
            "locked_model": active_locked,
            "api_keys_status": {
                p: bool(self.get_api_key(p)) for p in ["groq", "gemini", "openai", "custom"]
            },
            "masked_keys": {
                p: self.mask_key(self.get_api_key(p)) for p in ["groq", "gemini", "openai", "custom"]
            },
            "available_providers": DEFAULT_PROVIDERS
        }

    # =========================================================================
    # MODEL PROBING & ORCHESTRATION ENGINE ("LOCK & STICK")
    # =========================================================================

    def probe_candidate(
        self,
        provider: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: int = 5
    ) -> Tuple[bool, str, int]:
        """
        Sends a minimal test ping to check if a specific model endpoint is active and working.
        Returns: (success: bool, message: str, latency_ms: int)
        """
        prov = provider.lower()
        key = (api_key or self.get_api_key(prov)).strip()
        if not key and prov != "custom":
            return False, "No API key configured for this provider.", 0

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json"
        }

        t0 = time.time()
        try:
            if prov == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": "Respond with OK"}]}],
                    "generationConfig": {"maxOutputTokens": 5, "temperature": 0.0}
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    lat = round((time.time() - t0) * 1000)
                    if resp.status == 200:
                        return True, f"Verified ({lat}ms)", lat
                    return False, f"Unexpected HTTP status {resp.status}", lat

            else:  # Groq, OpenAI, or Custom
                base_url = self.config.get("custom_base_url") if prov == "custom" else DEFAULT_PROVIDERS.get(prov, {}).get("base_url", "https://api.openai.com/v1")
                url = f"{base_url.rstrip('/')}/chat/completions"
                headers["Authorization"] = f"Bearer {key}"
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Respond with OK"}],
                    "max_tokens": 5,
                    "temperature": 0.0
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    lat = round((time.time() - t0) * 1000)
                    if resp.status == 200:
                        return True, f"Verified ({lat}ms)", lat
                    return False, f"Unexpected HTTP status {resp.status}", lat

        except urllib.error.HTTPError as e:
            lat = round((time.time() - t0) * 1000)
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            return False, f"HTTP {e.code}: {msg}", lat
        except Exception as e:
            lat = round((time.time() - t0) * 1000)
            return False, f"Connection Failed: {e}", lat

    def get_best_working_model(self, force_refresh: bool = False, probe: bool = False) -> Dict[str, str]:
        """
        Orchestration Core: Selects the best working model across all configured providers.
        STRICT STICKINESS: Once locked onto a working model, sticks to it until an error occurs.
        If probe=False (default during execution failover), selects candidate without burning HTTP calls.
        """
        now = time.time()

        # If not forcing refresh, check if currently locked model is still valid and not in cooldown
        if not force_refresh and CloudClient._locked_model:
            locked_prov = CloudClient._locked_model.get("provider", "").lower()
            locked_mod = CloudClient._locked_model.get("model", "")
            cooldown_key = f"{locked_prov}:{locked_mod}"
            if now >= CloudClient._provider_cooldowns.get(locked_prov, 0) and now >= CloudClient._model_cooldowns.get(cooldown_key, 0):
                # Has key and not in cooldown -> stick to it!
                if self.get_api_key(locked_prov):
                    return CloudClient._locked_model

        # Probe or select candidates in order of capability / speed
        for cand in ORCHESTRATION_CANDIDATES:
            prov = cand["provider"].lower()
            mod = cand["model"]
            key = self.get_api_key(prov)
            if not key and prov != "custom":
                continue

            # Check if entire provider is in rate-limit cooldown
            if now < CloudClient._provider_cooldowns.get(prov, 0):
                continue

            cooldown_key = f"{prov}:{mod}"
            if now < CloudClient._model_cooldowns.get(cooldown_key, 0):
                continue  # In temporary cooldown due to recent error

            if probe:
                ok, msg, _ = self.probe_candidate(prov, mod, key, timeout=5)
                if not ok:
                    continue

            CloudClient._locked_model = {"provider": prov, "model": mod}
            self.config["provider"] = prov
            self.config["model"] = mod
            # Persist updated locked provider/model
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            except Exception:
                pass
            logger.info("Cloud Orchestrator locked onto model: %s (%s)", mod, prov)
            return CloudClient._locked_model

        # Fallback to configured model if all candidates failed or no candidates matched
        return {
            "provider": self.config.get("provider", "groq"),
            "model": self.config.get("model", "openai/gpt-oss-120b")
        }

    @classmethod
    def mark_provider_failed(cls, provider: str, cooldown_seconds: int = 30) -> None:
        """Mark an entire provider as temporarily unavailable (e.g. 429 rate limit or quota exceeded)."""
        prov = provider.lower()
        cls._provider_cooldowns[prov] = time.time() + cooldown_seconds
        logger.warning("Provider '%s' entered rate-limit cooldown for %ds.", prov, cooldown_seconds)
        if cls._locked_model and cls._locked_model.get("provider", "").lower() == prov:
            cls._locked_model = None

    @classmethod
    def mark_model_failed(cls, provider: str, model: str, cooldown_seconds: int = 60, is_rate_limit: bool = False) -> None:
        """Mark a model as failed and place it into temporary cooldown."""
        prov = provider.lower()
        cooldown_key = f"{prov}:{model}"
        cls._model_cooldowns[cooldown_key] = time.time() + cooldown_seconds
        # Clear locked model so orchestrator picks next working model
        if cls._locked_model and cls._locked_model.get("provider", "").lower() == prov and cls._locked_model.get("model") == model:
            cls._locked_model = None
        if is_rate_limit:
            cls.mark_provider_failed(prov, cooldown_seconds=min(cooldown_seconds, 30))

    def probe_all_providers(self, keys_dict: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Test all configured or supplied keys across providers and select the best working model.
        Returns a comprehensive report for the UI.
        """
        keys_to_test = keys_dict if keys_dict is not None else self.config.get("api_keys", {})
        report: Dict[str, Any] = {}

        for prov, p_info in DEFAULT_PROVIDERS.items():
            k = (keys_to_test.get(prov) or self.get_api_key(prov)).strip()
            if not k:
                report[prov] = {
                    "configured": False,
                    "success": False,
                    "model": p_info.get("default_model", ""),
                    "message": "No API key configured."
                }
                continue

            # Probe default model for this provider
            test_mod = p_info.get("default_model", "")
            ok, msg, lat = self.probe_candidate(prov, test_mod, k, timeout=6)
            report[prov] = {
                "configured": True,
                "success": ok,
                "model": test_mod,
                "latency_ms": lat,
                "message": msg
            }

        # Select and lock best model
        best = self.get_best_working_model(force_refresh=True, probe=True)
        report["best_locked_model"] = best
        return report

    def test_connection(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Test API key validity and remote connectivity for a given or currently active model."""
        prov = (provider or self.config.get("provider", "groq")).lower()
        key = (api_key or self.get_api_key(prov)).strip()
        mod = model or self.config.get("model", DEFAULT_PROVIDERS.get(prov, {}).get("default_model", ""))

        ok, msg, lat = self.probe_candidate(prov, mod, key, timeout=10)
        if ok:
            return True, f"Successfully connected to {prov.upper()} ({mod}) in {lat}ms!"
        return False, f"API Error: {msg}"

    def fetch_live_models(self, provider: Optional[str] = None, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Dynamically query remote provider for available chat/completion models."""
        prov = (provider or self.config.get("provider", "groq")).lower()
        key = (api_key or self.get_api_key(prov)).strip()

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json"
        }

        live_models: List[str] = []
        try:
            if prov == "groq" and key:
                url = "https://api.groq.com/openai/v1/models"
                headers["Authorization"] = f"Bearer {key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if not any(k in mid.lower() for k in ["whisper", "guard", "orpheus"]):
                            live_models.append(mid)

            elif prov == "gemini" and key:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("models", []):
                        mid = m.get("name", "").replace("models/", "")
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" in methods and not any(k in mid.lower() for k in ["tts", "image", "robotics"]):
                            live_models.append(mid)

            elif prov == "openai" and key:
                url = "https://api.openai.com/v1/models"
                headers["Authorization"] = f"Bearer {key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if mid.startswith("gpt-") or "o1" in mid or "o3" in mid:
                            live_models.append(mid)
        except Exception:
            pass

        if live_models:
            priority_order = [
                "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "allam-2-7b",
                "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-pro",
                "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"
            ]
            result = []
            for p in priority_order:
                if p in live_models:
                    result.append({"id": p, "label": p, "recommended": True})
            for m in live_models:
                if m not in [r["id"] for r in result]:
                    result.append({"id": m, "label": m, "recommended": False})
            return result

        curated = DEFAULT_PROVIDERS.get(prov, {}).get("available_models", [])
        default_m = DEFAULT_PROVIDERS.get(prov, {}).get("default_model", "")
        return [{"id": m, "label": m, "recommended": (m == default_m)} for m in curated]

    # =========================================================================
    # TOKEN MINIMIZATION & DATASET PERSISTENCE
    # =========================================================================

    @staticmethod
    def _clean_for_json(obj: Any) -> Any:
        """Sanitize floats, NaNs, and numpy values for strict JSON serialization."""
        if isinstance(obj, dict):
            return {str(k): CloudClient._clean_for_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [CloudClient._clean_for_json(x) for x in obj]
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return round(obj, 2)
        if hasattr(obj, "item"):
            return obj.item()
        return obj

    @staticmethod
    def _build_dataset_context(students: List[Dict[str, Any]], dataset_summary: str) -> str:
        """Build dataset summary for session context."""
        total_st = len(students)
        clean_students = CloudClient._clean_for_json(students)
        students_json = json.dumps(clean_students, ensure_ascii=False)

        return (
            f"=== COHORT OVERVIEW & SUMMARY STATISTICS ===\n"
            f"{dataset_summary}\n\n"
            f"=== COMPLETE STUDENT DATASET RECORDS ({total_st} CANDIDATES) ===\n"
            f"{students_json}\n\n"
            f"=== INSTRUCTIONS FOR THE ASSISTANT ===\n"
            f"1. You have the examination dataset with all student records.\n"
            f"2. You can answer ANY question about: individual students (by name, seat number, PRN, or rank), subject marks, theory vs lab scores, toppers, failures/KTs, semester comparisons, or cohort statistics.\n"
            f"3. Always reference the actual records above to provide exact, factual, detailed answers.\n"
            f"4. Always return valid JSON with 'action' and 'reply'."
        )

    def _compute_dataset_fingerprint(self, students: List[Dict[str, Any]]) -> str:
        """Compute deterministic hash of the loaded dataset to detect any modifications."""
        if not students:
            return "empty"
        total = len(students)
        first_seat = str(students[0].get("Seat_No", students[0].get("seat_no", "")))
        mid_seat = str(students[total // 2].get("Seat_No", students[total // 2].get("seat_no", "")))
        last_seat = str(students[-1].get("Seat_No", students[-1].get("seat_no", "")))
        keys = "-".join(sorted(str(k) for k in students[0].keys()))
        sig = f"{total}:{first_seat}:{mid_seat}:{last_seat}:{keys}"
        return hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]

    def get_or_create_session(
        self,
        session_id: str,
        dataset_summary: str,
        students: List[Dict[str, Any]],
        system_instruction: str
    ) -> Tuple[str, bool]:
        """Maintains persistent dataset session context in memory."""
        fingerprint = self._compute_dataset_fingerprint(students)
        session = self._sessions.get(session_id)

        if session and session.get("fingerprint") == fingerprint and (session.get("gemini_contents") or session.get("messages")):
            return session_id, False

        self._sessions[session_id] = {
            "fingerprint": fingerprint,
            "created_at": os.times().elapsed,
            "messages": [],
            "gemini_contents": [],
            "summary_injected": True
        }
        return session_id, True

    def notify_dataset_modified(self, session_id: str, modification_note: str) -> None:
        """Invalidate session when dataset records are modified."""
        if session_id in self._sessions:
            self._sessions[session_id]["fingerprint"] = ""
            self._sessions[session_id]["gemini_contents"] = []
            self._sessions[session_id]["messages"] = []

    # =========================================================================
    # SINGLE PROVIDER CALL EXECUTION
    # =========================================================================

    def _execute_provider_call(
        self,
        provider: str,
        model: str,
        api_key: str,
        user_prompt: str,
        system_instruction: str,
        dataset_summary: str,
        students: List[Dict[str, Any]],
        dataframe_slice: Optional[str],
        session_id: str,
        format_json: bool,
        timeout: int,
        temperature: float
    ) -> str:
        """Low-level execution of a single request to either Google Gemini or OpenAI/Groq."""
        sess_id, is_new = self.get_or_create_session(session_id, dataset_summary, students, system_instruction)
        session = self._sessions[sess_id]

        user_turn_text = f"USER QUERY: {user_prompt}"
        if dataframe_slice:
            user_turn_text += f"\n\n=== RELEVANT EXTRACTED DATAFRAME SLICE ===\n{dataframe_slice}"
        user_turn_text += (
            "\n\nInstructions:\n"
            "- For questions about cohort averages, totals, counts, pass rates, or distributions, always use the complete cohort data and DATASET FACTS above.\n"
            "- If a targeted slice is shown above (e.g. specific course or top N), answer based on the query context.\n"
            "- Respond with valid JSON matching the schema."
        )

        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT
        }

        # --- Google Gemini ---
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            contents = list(session.get("gemini_contents", []))

            if is_new or len(contents) == 0:
                full_ctx = self._build_dataset_context(students, dataset_summary)
                contents = [
                    {
                        "role": "user",
                        "parts": [{
                            "text": (
                                f"You are an expert academic data assistant analyzing a student result dataset.\n\n"
                                f"{full_ctx}"
                            )
                        }]
                    },
                    {
                        "role": "model",
                        "parts": [{
                            "text": json.dumps({
                                "action": "answer",
                                "reply": f"Complete examination dataset loaded and indexed successfully with all {len(students)} student records. I have full context of every student and all cohort statistics. How can I assist you with this dataset?"
                            })
                        }]
                    },
                    {
                        "role": "user",
                        "parts": [{"text": user_turn_text}]
                    }
                ]
            else:
                contents.append({"role": "user", "parts": [{"text": user_turn_text}]})

            payload = {
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "generationConfig": {
                    "temperature": temperature,
                    "responseMimeType": "application/json" if format_json else "text/plain"
                }
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers)

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            candidates = resp_data.get("candidates", [])
            if not candidates:
                raise RuntimeError("Empty response from Gemini API.")
            parts = candidates[0].get("content", {}).get("parts", [])
            reply_text = parts[0].get("text", "") if parts else ""

            contents.append({"role": "model", "parts": [{"text": reply_text}]})
            if len(contents) > 24:
                contents = contents[:2] + contents[-16:]
            session["gemini_contents"] = contents
            return reply_text

        # --- OpenAI / Groq / Custom ---
        else:
            base_url = self.config.get("custom_base_url") if provider == "custom" else DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
            url = f"{base_url.rstrip('/')}/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"

            messages = list(session.get("messages", []))
            if is_new or len(messages) == 0:
                full_ctx = self._build_dataset_context(students, dataset_summary)
                messages = [
                    {
                        "role": "system",
                        "content": f"{system_instruction}\n\n{full_ctx}"
                    },
                    {
                        "role": "user",
                        "content": user_turn_text
                    }
                ]
            else:
                messages.append({"role": "user", "content": user_turn_text})

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature
            }
            if format_json:
                payload["response_format"] = {"type": "json_object"}

            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers)

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                if he.code == 400 and format_json and "response_format" in payload:
                    logger.info("Provider %s returned 400 with response_format, retrying without it...", provider)
                    del payload["response_format"]
                    data_bytes = json.dumps(payload).encode("utf-8")
                    req_retry = urllib.request.Request(url, data=data_bytes, headers=headers)
                    with urllib.request.urlopen(req_retry, timeout=timeout) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                else:
                    raise

            choices = resp_data.get("choices", [])
            if not choices:
                raise RuntimeError(f"Empty choices returned from {provider} API.")
            reply_text = choices[0].get("message", {}).get("content", "")

            messages.append({"role": "assistant", "content": reply_text})
            if len(messages) > 24:
                messages = messages[:2] + messages[-16:]
            session["messages"] = messages
            return reply_text

    # =========================================================================
    # SELF-HEALING GENERATION WITH AUTOMATIC FAILOVER
    # =========================================================================

    def generate(
        self,
        user_prompt: str = "",
        system_instruction: str = "",
        dataset_summary: str = "",
        students: Optional[List[Dict[str, Any]]] = None,
        dataframe_slice: Optional[str] = None,
        session_id: str = "default_session",
        format_json: bool = True,
        timeout: int = 40,
        prompt: Optional[str] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Execute request with persistent dataset context and automatic multi-model failover.
        Sticks to the active locked model, and seamlessly switches to the next best model if an error occurs.
        """
        if prompt is not None:
            user_prompt = prompt
        if system is not None:
            system_instruction = system
        if students is None:
            students = []

        if not self.is_configured():
            raise RuntimeError("Cloud AI is not configured or disabled.")

        if temperature is None:
            temperature = float(self.config.get("temperature", 0.2))

        auto_orchestrate = self.config.get("auto_orchestrate", True)
        attempts = 0
        max_attempts = 4 if auto_orchestrate else 1
        last_error = None

        while attempts < max_attempts:
            attempts += 1
            if auto_orchestrate:
                active_candidate = self.get_best_working_model(force_refresh=(attempts > 1), probe=False)
                provider = active_candidate["provider"]
                model = active_candidate["model"]
            else:
                provider = self.config.get("provider", "groq").lower()
                model = self.config.get("model", "openai/gpt-oss-120b")

            api_key = self.get_api_key(provider)
            if not api_key and provider != "custom":
                continue

            try:
                reply = self._execute_provider_call(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    user_prompt=user_prompt,
                    system_instruction=system_instruction,
                    dataset_summary=dataset_summary,
                    students=students,
                    dataframe_slice=dataframe_slice,
                    session_id=session_id,
                    format_json=format_json,
                    timeout=timeout,
                    temperature=temperature
                )
                # Successful execution: update primary config so locked model is persisted
                self.config["provider"] = provider
                self.config["model"] = model
                return reply

            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                is_rate_limit = any(k in err_str for k in ["429", "quota", "too many requests", "rate limit", "resource exhausted"])
                logger.warning(
                    "Cloud AI request failed on %s (%s): %s (rate_limit=%s). Orchestrating failover...",
                    provider, model, e, is_rate_limit
                )
                self.mark_model_failed(provider, model, cooldown_seconds=60, is_rate_limit=is_rate_limit)
                if is_rate_limit:
                    time.sleep(min(2.0 * attempts, 4.0))
                if not auto_orchestrate:
                    raise

        raise RuntimeError(f"All configured cloud AI models encountered errors. Last error: {last_error}")

    def generate_direct(
        self,
        user_prompt: str,
        system_instruction: str = "",
        format_json: bool = True,
        timeout: int = 40,
        temperature: float = 0.1,
        audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        image_bytes: Optional[bytes] = None,
        image_mime: str = "image/jpeg"
    ) -> str:
        """
        Execute a stateless prompt completion with automatic multi-provider failover.
        Supports multimodal image input (Gemini inlineData and OpenAI image_url).
        """
        if not self.is_configured():
            raise RuntimeError("Cloud AI is not configured or disabled.")

        auto_orchestrate = self.config.get("auto_orchestrate", True)
        attempts = 0
        max_attempts = 4 if auto_orchestrate else 1
        last_error = None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT
        }

        while attempts < max_attempts:
            attempts += 1
            if auto_orchestrate:
                active_candidate = self.get_best_working_model(force_refresh=(attempts > 1), probe=False)
                provider = active_candidate["provider"]
                model = active_candidate["model"]
            else:
                provider = self.config.get("provider", "groq").lower()
                model = self.config.get("model", "openai/gpt-oss-120b")

            api_key = self.get_api_key(provider)
            if not api_key and provider != "custom":
                continue

            # Per-candidate in-place rate limit pacing retry (up to 2 retries on HTTP 429 before switching models)
            max_rate_retries = 2
            rate_attempt = 0
            candidate_success = False

            while rate_attempt <= max_rate_retries:
                rate_attempt += 1
                t_start = time.time()
                if audit_cb:
                    try:
                        audit_cb({
                            "timestamp": time.strftime("%H:%M:%S"),
                            "type": "cloud_ai_call",
                            "badge": "CLOUD AI CALL",
                            "title": f"Dispatching prompt to {provider.upper()} ({model}) [Candidate {attempts}/{max_attempts}, Run {rate_attempt}]",
                            "details": {
                                "provider": provider,
                                "model": model,
                                "candidate_attempt": attempts,
                                "rate_retry": rate_attempt,
                                "timeout_seconds": timeout,
                                "prompt_chars": len(user_prompt),
                                "has_image": bool(image_bytes),
                                "prompt_preview": user_prompt[:260] + "..."
                            }
                        })
                    except Exception:
                        pass

                try:
                    # 1. Google Gemini
                    if provider == "gemini":
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                        gen_cfg = {
                            "temperature": temperature,
                            "responseMimeType": "application/json" if format_json else "text/plain"
                        }
                        if format_json and response_schema:
                            gen_cfg["responseSchema"] = response_schema

                        parts = []
                        if image_bytes:
                            b64_data = base64.b64encode(image_bytes).decode("utf-8")
                            parts.append({
                                "inlineData": {
                                    "mimeType": image_mime,
                                    "data": b64_data
                                }
                            })
                        parts.append({"text": user_prompt})

                        payload = {
                            "contents": [{"role": "user", "parts": parts}],
                            "generationConfig": gen_cfg
                        }
                        if system_instruction:
                            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

                        data_bytes = json.dumps(payload).encode("utf-8")
                        req = urllib.request.Request(url, data=data_bytes, headers=headers)
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            resp_data = json.loads(resp.read().decode("utf-8"))

                        candidates = resp_data.get("candidates", [])
                        if not candidates:
                            raise RuntimeError("Empty response from Gemini API.")
                        parts = candidates[0].get("content", {}).get("parts", [])
                        reply_text = parts[0].get("text", "") if parts else ""

                    # 2. OpenAI / Groq / Custom
                    else:
                        base_url = self.config.get("custom_base_url") if provider == "custom" else DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
                        url = f"{base_url.rstrip('/')}/chat/completions"
                        req_headers = dict(headers)
                        req_headers["Authorization"] = f"Bearer {api_key}"

                        messages = []
                        if system_instruction:
                            messages.append({"role": "system", "content": system_instruction})

                        if image_bytes and provider == "openai":
                            b64_data = base64.b64encode(image_bytes).decode("utf-8")
                            content = [
                                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64_data}"}},
                                {"type": "text", "text": user_prompt}
                            ]
                            messages.append({"role": "user", "content": content})
                        else:
                            messages.append({"role": "user", "content": user_prompt})

                        payload = {
                            "model": model,
                            "messages": messages,
                            "temperature": temperature
                        }
                        if format_json:
                            if response_schema and provider in ("openai", "groq"):
                                payload["response_format"] = {
                                    "type": "json_schema",
                                    "json_schema": {
                                        "name": "structured_academic_extraction",
                                        "strict": True,
                                        "schema": response_schema
                                    }
                                }
                            else:
                                payload["response_format"] = {"type": "json_object"}

                        data_bytes = json.dumps(payload).encode("utf-8")
                        req = urllib.request.Request(url, data=data_bytes, headers=req_headers)
                        try:
                            with urllib.request.urlopen(req, timeout=timeout) as resp:
                                resp_data = json.loads(resp.read().decode("utf-8"))
                        except urllib.error.HTTPError as he:
                            if he.code == 400 and format_json and "response_format" in payload:
                                logger.info("Direct call on %s returned 400 with response_format, retrying without it...", provider)
                                del payload["response_format"]
                                data_bytes = json.dumps(payload).encode("utf-8")
                                req_retry = urllib.request.Request(url, data=data_bytes, headers=req_headers)
                                with urllib.request.urlopen(req_retry, timeout=timeout) as resp:
                                    resp_data = json.loads(resp.read().decode("utf-8"))
                            else:
                                raise

                        choices = resp_data.get("choices", [])
                        if not choices:
                            raise RuntimeError(f"Empty choices returned from {provider} API.")
                        reply_text = choices[0].get("message", {}).get("content", "")

                    dur = time.time() - t_start
                    # Successful execution: update primary config and return
                    self.config["provider"] = provider
                    self.config["model"] = model

                    if audit_cb:
                        try:
                            audit_cb({
                                "timestamp": time.strftime("%H:%M:%S"),
                                "type": "cloud_ai_success",
                                "badge": "CLOUD AI SUCCESS",
                                "title": f"{provider.upper()} ({model}) responded in {dur:.2f}s",
                                "details": {
                                    "provider": provider,
                                    "model": model,
                                    "duration_sec": round(dur, 2),
                                    "response_preview": reply_text[:280] + "..."
                                }
                            })
                        except Exception:
                            pass

                    return reply_text

                except urllib.error.HTTPError as he:
                    last_error = he
                    err_body = ""
                    try:
                        err_body = he.read().decode("utf-8", errors="ignore")
                    except Exception:
                        pass

                    is_payload_limit = (he.code == 413) or ("tokens per minute" in err_body.lower())
                    is_rate_limit = is_payload_limit or (he.code == 429) or any(k in err_body.lower() for k in ["rate limit", "quota", "resource_exhausted", "too many requests"])

                    if is_payload_limit:
                        # Prompt exceeded model's TPM limit (e.g. Groq 8k TPM on 120B) -> switch to next model immediately
                        msg = err_body[:250] if err_body else str(he)
                        last_error = RuntimeError(f"HTTP 413 from {provider}: {msg}")
                        break

                    if is_rate_limit and rate_attempt <= max_rate_retries:
                        # Extract wait time from Retry-After header or response body
                        wait_time = 4.0
                        retry_hdr = he.headers.get("Retry-After") if hasattr(he, "headers") else None
                        if retry_hdr:
                            try:
                                wait_time = max(float(retry_hdr) + 0.5, 2.0)
                            except Exception:
                                pass
                        else:
                            m = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)s", err_body, re.IGNORECASE)
                            if m:
                                try:
                                    wait_time = float(m.group(1)) + 0.5
                                except Exception:
                                    pass
                            else:
                                wait_time = min(2.5 * rate_attempt + 1.5, 6.0)

                        wait_time = min(wait_time, 10.0)

                        if audit_cb:
                            try:
                                audit_cb({
                                    "timestamp": time.strftime("%H:%M:%S"),
                                    "type": "cloud_ai_pacing",
                                    "badge": "RATE LIMIT PACING",
                                    "title": f"⏳ Rate limit on {provider.upper()} ({model}). Pacing request for {wait_time:.1f}s before retry ({rate_attempt}/{max_rate_retries})...",
                                    "details": {
                                        "provider": provider,
                                        "model": model,
                                        "wait_seconds": round(wait_time, 1),
                                        "rate_retry": rate_attempt,
                                        "max_retries": max_rate_retries,
                                        "error_summary": err_body[:250] if err_body else str(he)
                                    }
                                })
                            except Exception:
                                pass

                        logger.info("Rate limit on %s (%s). Pacing for %.1fs (retry %d/%d)...", provider, model, wait_time, rate_attempt, max_rate_retries)
                        time.sleep(wait_time)
                        continue  # Retry same candidate model

                    # If not rate limit or retries exhausted, format error and break to outer failover
                    msg = err_body[:250] if err_body else str(he)
                    try:
                        err_json = json.loads(err_body)
                        msg = err_json.get("error", {}).get("message", msg)
                    except Exception:
                        pass
                    last_error = RuntimeError(f"HTTP {he.code} from {provider}: {msg}")
                    break

                except Exception as e:
                    last_error = e
                    # Non-HTTPError (e.g. socket timeout, network disconnection) -> break to failover
                    break

            # Outer failover handling
            err_str = str(last_error).lower()
            is_rate_limit = any(k in err_str for k in ["429", "quota", "too many requests", "rate limit", "resource exhausted"])
            logger.warning(
                "Direct Cloud AI candidate failed on %s (%s): %s (rate_limit=%s). Orchestrating failover...",
                provider, model, last_error, is_rate_limit
            )
            cooldown_sec = 45 if is_rate_limit else 60
            self.mark_model_failed(provider, model, cooldown_seconds=cooldown_sec, is_rate_limit=is_rate_limit)

            if audit_cb:
                try:
                    err_label = "HTTP 429 Quota Exceeded / Rate Limit" if is_rate_limit else f"Error: {last_error}"
                    audit_cb({
                        "timestamp": time.strftime("%H:%M:%S"),
                        "type": "ai_error",
                        "badge": "MODEL FAILOVER",
                        "title": f"⚠️ {err_label} on {provider.upper()} ({model}). Auto-switching models...",
                        "details": {
                            "failed_provider": provider,
                            "failed_model": model,
                            "error": str(last_error),
                            "is_rate_limit": is_rate_limit,
                            "cooldown_seconds": cooldown_sec,
                            "attempt": attempts
                        }
                    })
                except Exception:
                    pass

            if auto_orchestrate:
                next_cand = self.get_best_working_model(force_refresh=True, probe=False)
                if audit_cb and next_cand and (next_cand["model"] != model or next_cand["provider"] != provider):
                    try:
                        audit_cb({
                            "timestamp": time.strftime("%H:%M:%S"),
                            "type": "cloud_ai_active",
                            "badge": "NEW ACTIVE MODEL",
                            "title": f"🔄 Switched active model: Now using {next_cand['provider'].upper()} ({next_cand['model']})",
                            "details": {
                                "new_provider": next_cand["provider"],
                                "new_model": next_cand["model"],
                                "previous_provider": provider,
                                "previous_model": model
                            }
                        })
                    except Exception:
                        pass

            if is_rate_limit:
                time.sleep(1.5)
            if not auto_orchestrate:
                raise last_error

        raise RuntimeError(f"All configured cloud AI models encountered errors. Last error: {last_error}")
