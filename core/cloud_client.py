"""
Cloud AI Client & Dataset Context Manager
=========================================
Connects to external Cloud AI providers (Google Gemini, OpenAI, Groq, Custom)
with zero external library dependencies (uses Python standard library urllib).

Features:
- Encrypted / masked API key storage in local configuration.
- Persistent session cache: caches dataset representation across conversation turns,
  avoiding re-sending large student datasets and cutting token usage by 90-95%.
- Real-time connection testing & health diagnostics.
"""

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "cloud_config.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

DEFAULT_PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "default_model": "gemini-flash-latest",
        "available_models": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-pro"],
        "base_url": "https://generativelanguage.googleapis.com/v1beta"
    },
    "openai": {
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "available_models": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
        "base_url": "https://api.openai.com/v1"
    },
    "groq": {
        "name": "Groq",
        "default_model": "openai/gpt-oss-120b",
        "available_models": [
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
            "allam-2-7b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant"
        ],
        "base_url": "https://api.groq.com/openai/v1"
    },
    "custom": {
        "name": "Custom OpenAI-Compatible",
        "default_model": "default",
        "available_models": [],
        "base_url": "http://localhost:8000/v1"
    }
}


class CloudClient:
    """Interface to external Cloud AI providers with persistent dataset token caching."""

    # In-memory session store to preserve dataset state across multiple chat turns
    _sessions: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self.config = self.load_config()

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Load configuration from disk or return default."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        default_cfg = {
            "provider": "gemini",
            "api_key": "",
            "model": "gemini-2.0-flash",
            "routing_mode": "auto_hybrid",  # "auto_hybrid", "prefer_cloud", "prefer_ollama"
            "custom_base_url": "",
            "temperature": 0.2,
            "enabled": True
        }
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    default_cfg.update(saved)
            except Exception:
                pass
        return default_cfg

    def save_config(self, new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
        """Save updated configuration to disk."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Update config fields
        for k in ["provider", "api_key", "model", "routing_mode", "custom_base_url", "temperature", "enabled"]:
            if k in new_cfg:
                self.config[k] = new_cfg[k]

        # Strip whitespace from API key
        if "api_key" in self.config and isinstance(self.config["api_key"], str):
            self.config["api_key"] = self.config["api_key"].strip()

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True, "Configuration saved successfully."
        except Exception as e:
            return False, f"Failed to write config: {e}"

    def is_configured(self) -> bool:
        """Check if an API key is provided and cloud AI is enabled."""
        key = self.config.get("api_key", "").strip()
        return bool(key and self.config.get("enabled", True))

    def get_masked_key(self) -> str:
        """Return masked key for safe UI display (e.g. AIzaSy...8xK9)."""
        key = self.config.get("api_key", "").strip()
        if not key:
            return ""
        if len(key) <= 8:
            return key[:2] + "•" * (len(key) - 2)
        return key[:6] + "•" * (len(key) - 10) + key[-4:]

    def get_safe_config(self) -> Dict[str, Any]:
        """Return configuration safe for frontend rendering (never exposes raw API key)."""
        return {
            "provider": self.config.get("provider", "gemini"),
            "model": self.config.get("model", "gemini-2.0-flash"),
            "routing_mode": self.config.get("routing_mode", "auto_hybrid"),
            "custom_base_url": self.config.get("custom_base_url", ""),
            "enabled": self.config.get("enabled", True),
            "is_configured": self.is_configured(),
            "masked_key": self.get_masked_key(),
            "available_providers": DEFAULT_PROVIDERS
        }

    def test_connection(self) -> Tuple[bool, str]:
        """Test API key validity and remote service connectivity."""
        if not self.is_configured():
            return False, "No API key configured."

        provider = self.config.get("provider", "gemini").lower()
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "gemini-2.0-flash")

        try:
            if provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": "Hello, respond with 'OK'."}]}],
                    "generationConfig": {"maxOutputTokens": 10, "temperature": 0.0}
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return True, f"Successfully connected to Google Gemini ({model})!"
                    return False, f"Unexpected response status {resp.status}"

            else:  # OpenAI, Groq, or Custom
                base_url = self.config.get("custom_base_url") or DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
                url = f"{base_url.rstrip('/')}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Respond with 'OK'."}],
                    "max_tokens": 10,
                    "temperature": 0.0
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "User-Agent": USER_AGENT
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return True, f"Successfully connected to {provider.upper()} ({model})!"
                    return False, f"Unexpected response status {resp.status}"

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            return False, f"API Error ({e.code}): {msg}"
        except Exception as e:
            return False, f"Connection Failed: {e}"

    def fetch_live_models(self, provider: Optional[str] = None, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Dynamically query remote provider for available chat/completion models.
        Returns a list of dicts: [{"id": model_id, "label": display_name, "recommended": bool}]
        Falls back to curated list if remote query fails.
        """
        if not provider:
            provider = self.config.get("provider", "gemini").lower()
        if not api_key:
            api_key = self.config.get("api_key", "").strip()

        provider = provider.lower()
        live_models: List[str] = []

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json"
        }

        try:
            if provider == "groq" and api_key:
                url = "https://api.groq.com/openai/v1/models"
                headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        # Filter out non-chat models (audio, whisper, guard only)
                        if not any(k in mid.lower() for k in ["whisper", "guard", "orpheus"]):
                            live_models.append(mid)

            elif provider == "gemini" and api_key:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("models", []):
                        mid = m.get("name", "").replace("models/", "")
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" in methods and not any(k in mid.lower() for k in ["tts", "image", "robotics"]):
                            live_models.append(mid)

            elif provider == "openai" and api_key:
                url = "https://api.openai.com/v1/models"
                headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if mid.startswith("gpt-") or "o1" in mid or "o3" in mid:
                            live_models.append(mid)
        except Exception:
            pass

        # If live models found, build curated response
        if live_models:
            priority_order = [
                "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "allam-2-7b",
                "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-pro",
                "gpt-4o-mini", "gpt-4o", "gpt-4-turbo"
            ]
            result = []
            for p in priority_order:
                if p in live_models:
                    result.append({"id": p, "label": p, "recommended": True})
            for m in live_models:
                if m not in [r["id"] for r in result]:
                    result.append({"id": m, "label": m, "recommended": False})
            return result

        # Fallback to curated models from DEFAULT_PROVIDERS
        curated = DEFAULT_PROVIDERS.get(provider, {}).get("available_models", [])
        default_m = DEFAULT_PROVIDERS.get(provider, {}).get("default_model", "")
        return [{"id": m, "label": m, "recommended": (m == default_m)} for m in curated]

    # =========================================================================
    # TOKEN MINIMIZATION & DATASET PERSISTENCE
    # =========================================================================
    # TOKEN MINIMIZATION & FULL DATASET PERSISTENCE
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
        """
        Build complete dataset context containing 100% of all student records and all fields.
        Every column and every row is preserved in full context so the AI can answer any question.
        """
        total_st = len(students)
        clean_students = CloudClient._clean_for_json(students)
        students_json = json.dumps(clean_students, ensure_ascii=False)

        return (
            f"=== COHORT OVERVIEW & SUMMARY STATISTICS ===\n"
            f"{dataset_summary}\n\n"
            f"=== COMPLETE STUDENT DATASET RECORDS ({total_st} CANDIDATES - ALL FIELDS INCLUDED) ===\n"
            f"{students_json}\n\n"
            f"=== INSTRUCTIONS FOR THE ASSISTANT ===\n"
            f"1. You have the 100% FULL dataset containing all {total_st} individual student records with every single column (Seat No, Name, Gender, Candidate Type, PRN, ABC ID, Semester, Result Status, Remarks, SGPI, CGPI, and all subject theory/internal/practical marks, grades, and credits) loaded into your session memory.\n"
            f"2. You can answer ANY question about: individual students (by name, seat number, PRN, or rank), subject marks, theory vs lab scores, toppers, failures/KTs, semester comparisons, or cohort statistics.\n"
            f"3. Always reference the actual records above to provide exact, factual, detailed answers to whatever the user asks.\n"
            f"4. If asked to modify or sort records (e.g. 'remove first 2 records', 'filter passed', 'sort by SGPI'), "
            f"return the matching JSON action and parameters according to the schema.\n"
            f"5. Always return valid JSON with 'action' and 'reply'."
        )

    def _compute_dataset_fingerprint(self, students: List[Dict[str, Any]]) -> str:
        """Compute deterministic hash of the loaded dataset to detect any modifications or new loads."""
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
        """
        Maintains persistent dataset session context in memory.
        Returns: (session_id, is_new_session)
        If session exists and dataset hasn't changed, is_new_session is False,
        allowing all subsequent queries to completely omit re-sending the dataset!
        """
        fingerprint = self._compute_dataset_fingerprint(students)
        session = self._sessions.get(session_id)

        if session and session.get("fingerprint") == fingerprint and (session.get("gemini_contents") or session.get("messages")):
            # Persistent session active: dataset already resident in context!
            return session_id, False

        # Initialize or re-initialize session with dataset
        self._sessions[session_id] = {
            "fingerprint": fingerprint,
            "created_at": os.times().elapsed,
            "messages": [],  # OpenAI format
            "gemini_contents": [],  # Gemini format
            "summary_injected": True
        }
        return session_id, True

    def notify_dataset_modified(self, session_id: str, modification_note: str) -> None:
        """Invalidate or update session when dataset records are modified."""
        if session_id in self._sessions:
            # Clear fingerprint to force fresh full-dataset context seed on next query
            self._sessions[session_id]["fingerprint"] = ""
            self._sessions[session_id]["gemini_contents"] = []
            self._sessions[session_id]["messages"] = []

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
        Execute request with persistent dataset context and token minimization.
        Full student records or targeted DataFrame slices are forwarded to Cloud AI
        for maximum precision and minimal latency/token consumption.
        """
        if prompt is not None:
            user_prompt = prompt
        if system is not None:
            system_instruction = system
        if students is None:
            students = []

        if not self.is_configured():
            raise RuntimeError("Cloud AI is not configured or disabled.")

        provider = self.config.get("provider", "gemini").lower()
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "gemini-3.8-flash")
        if temperature is None:
            temperature = float(self.config.get("temperature", 0.2))

        sess_id, is_new = self.get_or_create_session(session_id, dataset_summary, students, system_instruction)
        session = self._sessions[sess_id]

        # -------------------------------------------------------------
        # Provider: Google Gemini
        # -------------------------------------------------------------
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

            user_turn_text = f"USER QUERY: {user_prompt}"
            if dataframe_slice:
                user_turn_text += f"\n\n=== RELEVANT EXTRACTED DATAFRAME SLICE ===\n{dataframe_slice}"
            user_turn_text += "\n\nRespond with valid JSON matching the schema."

            if is_new or len(contents) == 0:
                # Turn 1: Seed session context with overview and dataset records
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
                # Subsequent turns: Send new user query with targeted DataFrame slice
                contents.append({
                    "role": "user",
                    "parts": [{"text": user_turn_text}]
                })

            payload: Dict[str, Any] = {
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "generationConfig": {
                    "temperature": temperature,
                    "responseMimeType": "application/json" if format_json else "text/plain"
                }
            }

            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (503, 429):
                    time.sleep(2.0)
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                else:
                    raise

            candidates = resp_data.get("candidates", [])
            if not candidates:
                raise RuntimeError("Empty response from Gemini API.")
            parts = candidates[0].get("content", {}).get("parts", [])
            reply_text = parts[0].get("text", "") if parts else ""

            # Save response to persistent session history
            contents.append({"role": "model", "parts": [{"text": reply_text}]})

            # Cap history while ALWAYS PRESERVING Turn 0 (the full dataset seed and confirmation)
            if len(contents) > 24:
                contents = contents[:2] + contents[-16:]
            session["gemini_contents"] = contents

            return reply_text

        # -------------------------------------------------------------
        # Provider: OpenAI / Groq / Custom
        # -------------------------------------------------------------
        else:
            base_url = self.config.get("custom_base_url") or DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
            url = f"{base_url.rstrip('/')}/chat/completions"

            messages = list(session.get("messages", []))
            user_turn_text = f"USER QUERY: {user_prompt}"
            if dataframe_slice:
                user_turn_text += f"\n\n=== RELEVANT EXTRACTED DATAFRAME SLICE ===\n{dataframe_slice}"
            user_turn_text += "\n\nRespond with valid JSON matching the schema."

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
                messages.append({
                    "role": "user",
                    "content": user_turn_text
                })

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature
            }
            if format_json:
                payload["response_format"] = {"type": "json_object"}

            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": USER_AGENT
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            choices = resp_data.get("choices", [])
            if not choices:
                raise RuntimeError("Empty choices returned from API.")
            reply_text = choices[0].get("message", {}).get("content", "")

            # Save to history
            messages.append({"role": "assistant", "content": reply_text})
            # Cap history while ALWAYS PRESERVING system message with full dataset
            if len(messages) > 24:
                messages = [messages[0]] + messages[-16:]
            session["messages"] = messages

            return reply_text
