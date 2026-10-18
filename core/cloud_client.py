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
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

# Per-request timeouts are handled explicitly via urlopen(..., timeout=timeout)

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "cloud_config.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Curated, verified working models for each provider (no outdated/dead endpoints)
DEFAULT_PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "default_model": "gemini-flash-lite-latest",
        "available_models": [
            "gemini-flash-lite-latest",
            "gemini-2.0-flash-lite",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-flash-latest"
        ],
        "base_url": "https://generativelanguage.googleapis.com/v1beta"
    },
    "groq": {
        "name": "Groq",
        "default_model": "llama-3.3-70b-versatile",
        "available_models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it"
        ],
        "base_url": "https://api.groq.com/openai/v1"
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
    "anthropic": {
        "name": "Anthropic Claude",
        "default_model": "claude-3-5-sonnet-20241022",
        "available_models": [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229"
        ],
        "base_url": "https://api.anthropic.com/v1"
    },
    "deepseek": {
        "name": "DeepSeek",
        "default_model": "deepseek-chat",
        "available_models": [
            "deepseek-chat",
            "deepseek-reasoner"
        ],
        "base_url": "https://api.deepseek.com"
    },
    "openrouter": {
        "name": "OpenRouter",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "available_models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemma-2-9b-it:free",
            "mistralai/mistral-7b-instruct:free",
            "qwen/qwen-2.5-72b-instruct:free"
        ],
        "base_url": "https://openrouter.ai/api/v1"
    },
    "custom": {
        "name": "Custom OpenAI-Compatible",
        "default_model": "default",
        "available_models": [],
        "base_url": "http://localhost:8000/v1"
    }
}

# Hierarchy of candidates for automatic orchestration (ranked by speed, capability, & quota)
ORCHESTRATION_CANDIDATES = [
    # Top Multimodal Vision Candidates
    {"provider": "gemini", "model": "gemini-flash-lite-latest", "label": "Google Gemini Flash Lite", "supports_vision": True},
    {"provider": "gemini", "model": "gemini-2.0-flash-lite", "label": "Google Gemini 2.0 Flash Lite", "supports_vision": True},
    {"provider": "gemini", "model": "gemini-2.0-flash", "label": "Google Gemini 2.0 Flash", "supports_vision": True},
    {"provider": "gemini", "model": "gemini-1.5-flash", "label": "Google Gemini 1.5 Flash", "supports_vision": True},
    {"provider": "gemini", "model": "gemini-flash-latest", "label": "Google Gemini Flash", "supports_vision": True},
    {"provider": "gemini", "model": "gemini-1.5-pro", "label": "Google Gemini 1.5 Pro", "supports_vision": True},
    {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free", "label": "OpenRouter Llama 3.3 70B (Free)", "supports_vision": True},
    {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct:free", "label": "OpenRouter Llama 3.1 8B (Free)", "supports_vision": True},
    {"provider": "openrouter", "model": "google/gemma-2-9b-it:free", "label": "OpenRouter Gemma 2 9B (Free)", "supports_vision": True},
    {"provider": "openai", "model": "gpt-4o-mini", "label": "OpenAI GPT-4o-mini", "supports_vision": True},
    {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022", "label": "Anthropic Claude 3.5 Sonnet", "supports_vision": True},
    {"provider": "anthropic", "model": "claude-3-5-haiku-20241022", "label": "Anthropic Claude 3.5 Haiku", "supports_vision": True},
    {"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o Flagship", "supports_vision": True},
    # Ultra-Fast Text & Reasoning Candidates (Groq & DeepSeek)
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Groq Llama 3.3 70B", "supports_vision": False},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Groq Llama 3.1 8B Instant", "supports_vision": False},
    {"provider": "groq", "model": "mixtral-8x7b-32768", "label": "Groq Mixtral 8x7B", "supports_vision": False},
    {"provider": "deepseek", "model": "deepseek-chat", "label": "DeepSeek V3 (64k Context)", "supports_vision": False},
    {"provider": "deepseek", "model": "deepseek-reasoner", "label": "DeepSeek R1 Reasoner", "supports_vision": False}
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

    # Multi-Key Pool trackers: maps "provider:key_prefix" to expiration timestamp, and "provider" to active pool index
    _key_cooldowns: Dict[str, float] = {}
    _key_pool_indices: Dict[str, int] = {}

    # Adaptive Rate-Limit Backoff Level per provider (Level 0: 60s, Level 1: 120s, Level 2: 240s, Level 3+: 480s)
    _provider_backoff_level: Dict[str, int] = {}
    _provider_last_success: Dict[str, float] = {}

    # Global cumulative token metrics across all CloudClient instances in the process
    _global_token_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def __init__(self, config_override: Optional[Dict[str, Any]] = None):
        self.config = self.load_config()
        if config_override and isinstance(config_override, dict):
            if "api_keys" in config_override and isinstance(config_override["api_keys"], dict):
                for p, k in config_override["api_keys"].items():
                    if k:
                        self.config["api_keys"][str(p).lower()] = k
            for k in ["provider", "model", "routing_mode", "auto_orchestrate", "custom_base_url", "temperature"]:
                if k in config_override and config_override[k]:
                    self.config[k] = config_override[k]

        self.token_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.last_call_tokens: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        if CloudClient._locked_model is None and self.config.get("auto_orchestrate", True):
            # Attempt to set initial locked model from config or best candidate
            CloudClient._locked_model = {
                "provider": self.config.get("provider", "groq"),
                "model": self.config.get("model", "openai/gpt-oss-120b")
            }

    def get_token_usage(self) -> Dict[str, int]:
        """Return a copy of cumulative token metrics for this client instance."""
        return dict(self.token_usage)

    def reset_token_usage(self) -> None:
        """Reset token metrics for this client instance."""
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.last_call_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @classmethod
    def get_global_token_usage(cls) -> Dict[str, int]:
        """Return cumulative token metrics across all CloudClient instances."""
        return dict(cls._global_token_usage)

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Load multi-provider configuration from disk or return default."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        default_cfg = {
            "api_keys": {
                "groq": "",
                "gemini": "",
                "openrouter": "",
                "openai": "",
                "anthropic": "",
                "deepseek": "",
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

        # Clear specified keys
        if "clear_keys" in new_cfg and isinstance(new_cfg["clear_keys"], list):
            for p in new_cfg["clear_keys"]:
                self.config["api_keys"].pop(str(p).lower(), None)

        if "api_keys" in new_cfg and isinstance(new_cfg["api_keys"], dict):
            for p, k in new_cfg["api_keys"].items():
                p_key = p.lower()
                if k == "__CLEAR__" or (isinstance(k, str) and k == ""):
                    # Explicit clear
                    if p_key in new_cfg.get("clear_keys", []):
                        self.config["api_keys"].pop(p_key, None)
                elif isinstance(k, str) and k.strip():
                    self.config["api_keys"][p_key] = k.strip()
                elif isinstance(k, list):
                    cleaned = [str(item).strip() for item in k if str(item).strip()]
                    if cleaned:
                        self.config["api_keys"][p_key] = cleaned
                    elif p_key in new_cfg.get("clear_keys", []):
                        self.config["api_keys"].pop(p_key, None)

        # Support single api_key update
        if "api_key" in new_cfg and isinstance(new_cfg["api_key"], str):
            prov = new_cfg.get("provider", self.config.get("provider", "gemini")).lower()
            if new_cfg["api_key"].strip():
                self.config["api_keys"][prov] = new_cfg["api_key"].strip()

        for k in ["provider", "model", "routing_mode", "auto_orchestrate", "custom_base_url", "temperature", "enabled"]:
            if k in new_cfg:
                self.config[k] = new_cfg[k]

        # Sync primary api_key field for backward compatibility
        cur_prov = self.config.get("provider", "gemini").lower()
        self.config["api_key"] = self.get_api_key(cur_prov)

        # If user explicitly changed provider/model, update the locked model
        if "provider" in new_cfg or "model" in new_cfg:
            CloudClient._locked_model = {
                "provider": self.config.get("provider", "gemini"),
                "model": self.config.get("model", "gemini-flash-latest")
            }

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True, "Cloud AI configuration saved successfully."
        except Exception as e:
            return False, f"Failed to write config: {e}"

    def get_key_pool(self, provider: Optional[str] = None) -> List[str]:
        """Return list of valid, non-empty API keys for a provider (supports comma/newline/semicolon pools)."""
        prov = (provider or self.config.get("provider", "groq")).lower()
        keys_dict = self.config.get("api_keys", {})
        raw = keys_dict.get(prov, "")
        if not raw and prov == self.config.get("provider", "").lower():
            raw = self.config.get("api_key", "")
        if isinstance(raw, list):
            pool = []
            for item in raw:
                for token in re.split(r"[\n,;]+", str(item)):
                    if token.strip():
                        pool.append(token.strip())
            return pool
        if isinstance(raw, str):
            tokens = re.split(r"[\n,;]+", raw)
            return [t.strip() for t in tokens if t.strip()]
        return []

    def get_api_key(self, provider: Optional[str] = None) -> str:
        """Retrieve the currently active API key from the provider's key pool, respecting cooldowns."""
        prov = (provider or self.config.get("provider", "groq")).lower()
        pool = self.get_key_pool(prov)
        if not pool:
            return ""
        now = time.time()
        start_idx = CloudClient._key_pool_indices.get(prov, 0)
        # Search for first non-cooling key
        for i in range(len(pool)):
            k_candidate = pool[(start_idx + i) % len(pool)]
            cd = CloudClient._key_cooldowns.get(f"{prov}:{k_candidate[:12]}", 0.0)
            if now >= cd:
                CloudClient._key_pool_indices[prov] = (start_idx + i) % len(pool)
                return k_candidate
        # Fallback to current index if all cooling
        return pool[start_idx % len(pool)]

    def rotate_key(
        self,
        provider: Optional[str] = None,
        failed_key: Optional[str] = None,
        cooldown_seconds: int = 60
    ) -> Optional[str]:
        """
        Mark failed_key on cooldown and rotate pointer to next available key in the provider pool.
        Returns the new active key, or None if pool has <= 1 key or all are cooling.
        """
        prov = (provider or self.config.get("provider", "groq")).lower()
        pool = self.get_key_pool(prov)
        if len(pool) <= 1:
            return None

        if failed_key:
            CloudClient._key_cooldowns[f"{prov}:{failed_key[:12]}"] = time.time() + cooldown_seconds

        now = time.time()
        start_idx = (CloudClient._key_pool_indices.get(prov, 0) + 1) % len(pool)
        for i in range(len(pool)):
            k_candidate = pool[(start_idx + i) % len(pool)]
            cd = CloudClient._key_cooldowns.get(f"{prov}:{k_candidate[:12]}", 0.0)
            if now >= cd:
                CloudClient._key_pool_indices[prov] = (start_idx + i) % len(pool)
                return k_candidate
        return None

    def is_configured(self) -> bool:
        """Check if at least one AI provider has an API key configured and cloud AI is enabled."""
        if not self.config.get("enabled", True):
            return False
        for p in ["groq", "gemini", "openrouter", "openai", "anthropic", "deepseek", "custom"]:
            if len(self.get_key_pool(p)) > 0:
                return True
        return bool(self.config.get("api_key", "").strip())

    @staticmethod
    def mask_key(key: Any) -> str:
        """Return masked key or pool summary for safe UI display (e.g. gsk_7y4...ZR2V or '2 keys pool')."""
        if not key:
            return ""
        if isinstance(key, list):
            tokens = [str(k).strip() for k in key if str(k).strip()]
        else:
            tokens = [t.strip() for t in re.split(r"[\n,;]+", str(key)) if t.strip()]
        if not tokens:
            return ""
        if len(tokens) > 1:
            first_m = CloudClient._mask_single_key(tokens[0])
            return f"{len(tokens)} keys pool ({first_m})"
        return CloudClient._mask_single_key(tokens[0])

    @staticmethod
    def _mask_single_key(k: str) -> str:
        k = k.strip()
        if len(k) <= 8:
            return k[:2] + "•" * (len(k) - 2)
        return k[:6] + "•" * (len(k) - 10) + k[-4:]

    def get_safe_config(self) -> Dict[str, Any]:
        """Return configuration safe for frontend rendering (never exposes raw API keys)."""
        active_locked = CloudClient._locked_model or {
            "provider": self.config.get("provider", "groq"),
            "model": self.config.get("model", "openai/gpt-oss-120b")
        }

        all_provs = ["groq", "gemini", "openrouter", "openai", "anthropic", "deepseek", "custom"]
        keys_summary = {}
        for prov in all_provs:
            pool = self.get_key_pool(prov)
            keys_summary[prov] = {
                "count": len(pool),
                "is_configured": len(pool) > 0,
                "preview": self.mask_key(self.config.get("api_keys", {}).get(prov, ""))
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
                p: (len(self.get_key_pool(p)) > 0) for p in all_provs
            },
            "masked_keys": {
                p: self.mask_key(self.config.get("api_keys", {}).get(p, "")) for p in all_provs
            },
            "keys_summary": keys_summary,
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
        timeout: int = 8
    ) -> Tuple[bool, str, int]:
        """
        Sends a minimal test ping to check if a specific model endpoint is active and working.
        Returns: (success: bool, message: str, latency_ms: int)
        """
        prov = provider.lower()
        raw_key = (api_key or self.get_api_key(prov)).strip()
        key_pool = [t.strip() for t in re.split(r"[\n,;]+", raw_key) if t.strip()] if raw_key else []
        key = key_pool[0] if key_pool else ""
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

            elif prov == "anthropic":
                url = "https://api.anthropic.com/v1/messages"
                headers["x-api-key"] = key
                headers["anthropic-version"] = "2023-06-01"
                payload = {
                    "model": model,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "Respond with OK"}]
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    lat = round((time.time() - t0) * 1000)
                    if resp.status == 200:
                        return True, f"Verified ({lat}ms)", lat
                    return False, f"Unexpected HTTP status {resp.status}", lat

            else:  # Groq, OpenRouter, OpenAI, DeepSeek, or Custom
                base_url = self.config.get("custom_base_url") if prov == "custom" else DEFAULT_PROVIDERS.get(prov, {}).get("base_url", "https://api.openai.com/v1")
                url = f"{base_url.rstrip('/')}/chat/completions"
                headers["Authorization"] = f"Bearer {key}"
                if prov == "openrouter":
                    headers["HTTP-Referer"] = "https://github.com/shivambandekar2526-png/result-analysis-aids"
                    headers["X-Title"] = "Result Analysis AI"
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

    def get_best_working_model(self, force_refresh: bool = False, probe: bool = False, requires_vision: bool = False) -> Dict[str, str]:
        """
        Orchestration Core: Selects the best working model across all configured providers.
        PRIORITY ORDER: Highest tier models (e.g. Gemini) are given top priority.
        OPPORTUNISTIC RECOVERY: If a lower-priority fallback is active, checks if top-priority models
        have completed their rate-limit cooldown and restores them.
        ANTI-THRASHING STICKINESS: Once locked onto a model, sticks to it unless a higher-priority
        model becomes available or an error triggers failover.
        """
        now = time.time()

        # 1. Determine currently locked candidate priority index
        locked_priority = 999
        if not force_refresh and CloudClient._locked_model:
            locked_prov = CloudClient._locked_model.get("provider", "").lower()
            locked_mod = CloudClient._locked_model.get("model", "")
            cand_info = next((c for c in ORCHESTRATION_CANDIDATES if c["provider"] == locked_prov and c["model"] == locked_mod), None)
            vision_ok = (not requires_vision) or (cand_info and cand_info.get("supports_vision", False))
            if vision_ok:
                cooldown_key = f"{locked_prov}:{locked_mod}"
                if now >= CloudClient._provider_cooldowns.get(locked_prov, 0) and now >= CloudClient._model_cooldowns.get(cooldown_key, 0):
                    if self.get_api_key(locked_prov):
                        for idx, c in enumerate(ORCHESTRATION_CANDIDATES):
                            if c["provider"].lower() == locked_prov and c["model"] == locked_mod:
                                locked_priority = idx
                                break

        # 2. Iterate candidates in strict priority order
        for idx, cand in enumerate(ORCHESTRATION_CANDIDATES):
            if requires_vision and not cand.get("supports_vision", False):
                continue

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

            # If we reached the currently locked model and nothing higher-priority is available, stick to it!
            if not force_refresh and idx == locked_priority and CloudClient._locked_model:
                return CloudClient._locked_model

            if probe:
                ok, msg, _ = self.probe_candidate(prov, mod, key, timeout=5)
                if not ok:
                    continue

            # Higher-priority model is available or newly locked
            CloudClient._locked_model = {"provider": prov, "model": mod}
            self.config["provider"] = prov
            self.config["model"] = mod
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            except Exception:
                pass
            logger.info("Cloud Orchestrator selected model: %s (%s, vision=%s)", mod, prov, cand.get("supports_vision", False))
            return CloudClient._locked_model

        # Fallback: if all eligible candidates are in cooldown, pick the one with earliest cooldown expiry
        eligible_cands = [
            c for c in ORCHESTRATION_CANDIDATES
            if (not requires_vision or c.get("supports_vision", False)) and (self.get_api_key(c["provider"]) or c["provider"] == "custom")
        ]
        if eligible_cands:
            best_cand = min(eligible_cands, key=lambda c: CloudClient._model_cooldowns.get(f"{c['provider'].lower()}:{c['model']}", 0))
            return {"provider": best_cand["provider"].lower(), "model": best_cand["model"]}

        if requires_vision:
            return {
                "provider": "gemini",
                "model": "gemini-flash-latest"
            }
        return {
            "provider": self.config.get("provider", "groq"),
            "model": "openai/gpt-oss-120b"
        }

    @classmethod
    def record_provider_success(cls, provider: str) -> None:
        """Track successful execution to reset rate-limit backoff levels if operating stably."""
        prov = provider.lower()
        now = time.time()
        last_succ = cls._provider_last_success.get(prov, 0.0)
        # If successfully operating for > 90 seconds without rate-limits, reset backoff level
        if last_succ > 0 and (now - last_succ) > 90:
            cls._provider_backoff_level[prov] = 0
        cls._provider_last_success[prov] = now

    @classmethod
    def mark_provider_failed(cls, provider: str, cooldown_seconds: int = 30) -> None:
        """Mark an entire provider as temporarily unavailable (e.g. 401/403 invalid key or total provider outage)."""
        prov = provider.lower()
        cls._provider_cooldowns[prov] = time.time() + cooldown_seconds
        logger.warning("Provider '%s' entered rate-limit cooldown for %ds.", prov, cooldown_seconds)
        if cls._locked_model and cls._locked_model.get("provider", "").lower() == prov:
            cls._locked_model = None

    @classmethod
    def mark_model_failed(cls, provider: str, model: str, cooldown_seconds: int = 60, is_rate_limit: bool = False) -> None:
        """
        Mark a model as failed and place it into temporary cooldown.
        Applies exponential backoff on consecutive rate limits to prevent thrashing loops.
        """
        prov = provider.lower()
        cooldown_key = f"{prov}:{model}"
        now = time.time()

        if is_rate_limit:
            if cooldown_seconds >= 300:
                # Extended / daily quota exhaustion
                cls._provider_backoff_level[prov] = 3
                eff_cooldown = cooldown_seconds
            else:
                level = cls._provider_backoff_level.get(prov, 0)
                # Exponential backoff based on level: 60s, 120s, 240s, 480s
                backoff_factors = [60, 120, 240, 480]
                eff_cooldown = backoff_factors[min(level, len(backoff_factors) - 1)]
                cls._provider_backoff_level[prov] = level + 1
        else:
            eff_cooldown = cooldown_seconds

        target_expiry = now + eff_cooldown
        if target_expiry > cls._model_cooldowns.get(cooldown_key, 0):
            cls._model_cooldowns[cooldown_key] = target_expiry

        # If all candidates for this provider are cooling down, place provider in cooldown
        prov_models = [c["model"] for c in ORCHESTRATION_CANDIDATES if c["provider"] == prov]
        if prov_models and all(now < cls._model_cooldowns.get(f"{prov}:{m}", 0) for m in prov_models):
            min_expiry = min(cls._model_cooldowns.get(f"{prov}:{m}", 0) for m in prov_models)
            cls._provider_cooldowns[prov] = min_expiry

        logger.warning("Model %s (%s) cooling down for %ds (rate_limit=%s, backoff_lvl=%d)",
                       model, prov, eff_cooldown, is_rate_limit, cls._provider_backoff_level.get(prov, 0))

        # Clear locked model so orchestrator picks next working model
        if cls._locked_model and cls._locked_model.get("provider", "").lower() == prov and cls._locked_model.get("model") == model:
            cls._locked_model = None

    def probe_all_providers(self, keys_dict: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Test all configured or supplied keys across providers in parallel using ThreadPoolExecutor(max_workers=6)
        with a 5-second per-provider timeout, and select the best working model.
        Returns a comprehensive report for the UI.
        """
        keys_to_test = dict(self.config.get("api_keys", {}))
        if keys_dict:
            for p, k in keys_dict.items():
                if k is not None:
                    keys_to_test[p] = k
                    self.config.setdefault("api_keys", {})[p] = k

        report: Dict[str, Any] = {}
        providers_to_probe = [p for p in DEFAULT_PROVIDERS if p != "custom"]
        if "custom" in DEFAULT_PROVIDERS and (keys_to_test.get("custom") or self.config.get("custom_base_url")):
            providers_to_probe.append("custom")

        def _probe_provider(prov: str) -> Tuple[str, Dict[str, Any]]:
            p_info = DEFAULT_PROVIDERS.get(prov, {})
            k = (keys_to_test.get(prov) or self.get_api_key(prov)).strip()

            if not k and prov != "custom":
                return prov, {
                    "configured": False,
                    "success": False,
                    "model": p_info.get("default_model", ""),
                    "latency_ms": 0,
                    "key_count": 0,
                    "message": "No API key configured."
                }

            tokens = [t.strip() for t in re.split(r"[\n,;]+", k) if t.strip()]
            key_count = len(tokens)

            # Build prioritized list of candidate models for this provider
            candidates = []
            # 1. User's currently configured model if this is their selected provider
            if self.config.get("provider", "").lower() == prov and self.config.get("model"):
                candidates.append(self.config.get("model"))
            # 2. Provider's default model
            if p_info.get("default_model") and p_info.get("default_model") not in candidates:
                candidates.append(p_info.get("default_model"))
            # 3. Available curated models for this provider
            for m in p_info.get("available_models", []):
                if m not in candidates:
                    candidates.append(m)

            last_msg = "Probe failed"
            last_lat = 0
            last_mod = candidates[0] if candidates else p_info.get("default_model", "")

            for cand_mod in candidates:
                ok, msg, lat = self.probe_candidate(prov, cand_mod, k, timeout=8)
                if ok:
                    return prov, {
                        "configured": True,
                        "success": True,
                        "model": cand_mod,
                        "latency_ms": lat,
                        "key_count": key_count,
                        "message": f"Verified ({lat}ms)"
                    }
                last_msg = msg
                last_lat = lat
                last_mod = cand_mod

                # If authentication failed outright (invalid key), abort other models
                msg_lower = msg.lower()
                if "401" in msg or "invalid api key" in msg_lower or "unauthorized" in msg_lower or "api_key_invalid" in msg_lower:
                    break

            return prov, {
                "configured": True,
                "success": False,
                "model": last_mod,
                "latency_ms": last_lat,
                "key_count": key_count,
                "message": last_msg
            }

        with ThreadPoolExecutor(max_workers=min(len(providers_to_probe), 6)) as executor:
            future_to_prov = {executor.submit(_probe_provider, prov): prov for prov in providers_to_probe}
            for future in as_completed(future_to_prov):
                prov = future_to_prov[future]
                try:
                    p_name, result = future.result()
                    report[p_name] = result
                except Exception as e:
                    report[prov] = {
                        "configured": True,
                        "success": False,
                        "model": DEFAULT_PROVIDERS.get(prov, {}).get("default_model", ""),
                        "latency_ms": 0,
                        "key_count": 0,
                        "message": f"Probe error: {e}"
                    }

        # Select and lock best model based on existing probe report (probe=False to prevent 90s freeze)
        best = self.get_best_working_model(force_refresh=True, probe=False)
        report["best_locked_model"] = best
        return report

    def test_connection(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Test API key validity and remote connectivity for a given or currently active model."""
        # Auto-resolve active provider if None or 'active'
        if not provider or provider == "active":
            if CloudClient._locked_model and self.get_api_key(CloudClient._locked_model.get("provider")):
                prov = CloudClient._locked_model["provider"].lower()
                mod = model or CloudClient._locked_model.get("model")
            elif self.is_configured():
                best = self.get_best_working_model(force_refresh=False, probe=False)
                prov = best.get("provider", self.config.get("provider", "gemini")).lower()
                mod = model or best.get("model")
            else:
                prov = self.config.get("provider", "gemini").lower()
                mod = model or self.config.get("model")
        else:
            prov = provider.lower()
            if model:
                mod = model
            elif prov == self.config.get("provider", "").lower() and self.config.get("model"):
                mod = self.config.get("model")
            else:
                mod = DEFAULT_PROVIDERS.get(prov, {}).get("default_model", "")

        key = (api_key or self.get_api_key(prov)).strip()
        if not key and prov != "custom":
            return False, f"No API key provided or configured for {prov.upper()}. Please enter or save your key first."

        raw_pool = [t.strip() for t in re.split(r"[\n,;]+", key) if t.strip()]
        pool_str = f" (Key pool of {len(raw_pool)} keys active)" if len(raw_pool) > 1 else ""

        ok, msg, lat = self.probe_candidate(prov, mod, key, timeout=5)
        if ok:
            return True, f"Successfully connected to {prov.upper()} ({mod}) in {lat}ms!{pool_str}"
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

            elif prov == "deepseek" and key:
                url = "https://api.deepseek.com/models"
                headers["Authorization"] = f"Bearer {key}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        live_models.append(m.get("id", ""))

            elif prov == "anthropic" and key:
                url = "https://api.anthropic.com/v1/models"
                headers["x-api-key"] = key
                headers["anthropic-version"] = "2023-06-01"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            elif prov == "openrouter" and key:
                url = "https://openrouter.ai/api/v1/models"
                headers["Authorization"] = f"Bearer {key}"
                headers["HTTP-Referer"] = "https://github.com/shivambandekar2526-png/result-analysis-aids"
                headers["X-Title"] = "Result Analysis AI"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if ":free" in mid:
                            live_models.append(mid)
        except Exception:
            pass

        if live_models:
            priority_order = [
                "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
                "deepseek-chat", "deepseek-reasoner",
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

            # Token tracking
            usage_meta = resp_data.get("usageMetadata", {})
            in_tok = int(usage_meta.get("promptTokenCount", 0) or 0)
            out_tok = int(usage_meta.get("candidatesTokenCount", 0) or 0)
            tot_tok = int(usage_meta.get("totalTokenCount", 0) or (in_tok + out_tok))
            if in_tok == 0 and user_turn_text:
                in_tok = max(1, len(user_turn_text) // 4)
            if out_tok == 0 and reply_text:
                out_tok = max(1, len(reply_text) // 4)
            if tot_tok == 0:
                tot_tok = in_tok + out_tok
            self.token_usage["input_tokens"] += in_tok
            self.token_usage["output_tokens"] += out_tok
            self.token_usage["total_tokens"] += tot_tok
            self.last_call_tokens = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
            CloudClient._global_token_usage["input_tokens"] += in_tok
            CloudClient._global_token_usage["output_tokens"] += out_tok
            CloudClient._global_token_usage["total_tokens"] += tot_tok

            contents.append({"role": "model", "parts": [{"text": reply_text}]})
            if len(contents) > 24:
                contents = contents[:2] + contents[-16:]
            session["gemini_contents"] = contents
            return reply_text

        # --- Anthropic Claude ---
        elif provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"

            messages = list(session.get("messages", []))
            if is_new or len(messages) == 0:
                full_ctx = self._build_dataset_context(students, dataset_summary)
                messages = [
                    {
                        "role": "user",
                        "content": f"{system_instruction}\n\n{full_ctx}\n\n{user_turn_text}"
                    }
                ]
            else:
                messages.append({"role": "user", "content": user_turn_text})

            payload = {
                "model": model,
                "max_tokens": 4096,
                "temperature": temperature,
                "messages": messages
            }
            if system_instruction:
                payload["system"] = system_instruction

            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers)

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            content_blocks = resp_data.get("content", [])
            reply_text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            reply_text = re.sub(r"^```(?:json)?\s*", "", reply_text.strip(), flags=re.IGNORECASE)
            reply_text = re.sub(r"\s*```$", "", reply_text).strip()

            usage_meta = resp_data.get("usage", {})
            in_tok = int(usage_meta.get("input_tokens", 0) or 0)
            out_tok = int(usage_meta.get("output_tokens", 0) or 0)
            tot_tok = in_tok + out_tok
            if in_tok == 0 and user_turn_text:
                in_tok = max(1, len(user_turn_text) // 4)
            if out_tok == 0 and reply_text:
                out_tok = max(1, len(reply_text) // 4)
            if tot_tok == 0:
                tot_tok = in_tok + out_tok
            self.token_usage["input_tokens"] += in_tok
            self.token_usage["output_tokens"] += out_tok
            self.token_usage["total_tokens"] += tot_tok
            self.last_call_tokens = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
            CloudClient._global_token_usage["input_tokens"] += in_tok
            CloudClient._global_token_usage["output_tokens"] += out_tok
            CloudClient._global_token_usage["total_tokens"] += tot_tok

            messages.append({"role": "assistant", "content": reply_text})
            if len(messages) > 24:
                messages = messages[:2] + messages[-16:]
            session["messages"] = messages
            return reply_text

        # --- OpenAI / OpenRouter / Groq / DeepSeek / Custom ---
        else:
            base_url = self.config.get("custom_base_url") if provider == "custom" else DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
            url = f"{base_url.rstrip('/')}/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/shivambandekar2526-png/result-analysis-aids"
                headers["X-Title"] = "Result Analysis AI"

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

            # Token tracking
            usage_meta = resp_data.get("usage", {})
            in_tok = int(usage_meta.get("prompt_tokens", 0) or 0)
            out_tok = int(usage_meta.get("completion_tokens", 0) or 0)
            tot_tok = int(usage_meta.get("total_tokens", 0) or (in_tok + out_tok))
            if in_tok == 0 and user_turn_text:
                in_tok = max(1, len(user_turn_text) // 4)
            if out_tok == 0 and reply_text:
                out_tok = max(1, len(reply_text) // 4)
            if tot_tok == 0:
                tot_tok = in_tok + out_tok
            self.token_usage["input_tokens"] += in_tok
            self.token_usage["output_tokens"] += out_tok
            self.token_usage["total_tokens"] += tot_tok
            self.last_call_tokens = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
            CloudClient._global_token_usage["input_tokens"] += in_tok
            CloudClient._global_token_usage["output_tokens"] += out_tok
            CloudClient._global_token_usage["total_tokens"] += tot_tok

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
        eligible = [
            c for c in ORCHESTRATION_CANDIDATES
            if self.get_api_key(c["provider"]) or c["provider"] == "custom"
        ]
        max_attempts = max(len(eligible), 6) if auto_orchestrate else 1
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
        requires_vision = bool(image_bytes)
        attempts = 0
        eligible = [
            c for c in ORCHESTRATION_CANDIDATES
            if (not requires_vision or c.get("supports_vision", False)) and (self.get_api_key(c["provider"]) or c["provider"] == "custom")
        ]
        max_attempts = max(len(eligible), 6) if auto_orchestrate else 1
        last_error = None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT
        }

        while attempts < max_attempts:
            attempts += 1
            if auto_orchestrate:
                active_candidate = self.get_best_working_model(force_refresh=(attempts > 1), probe=False, requires_vision=requires_vision)
                provider = active_candidate["provider"]
                model = active_candidate["model"]
            else:
                provider = self.config.get("provider", "gemini" if requires_vision else "groq").lower()
                model = self.config.get("model", "gemini-flash-latest" if requires_vision else "openai/gpt-oss-120b")

            api_key = self.get_api_key(provider)
            if not api_key and provider != "custom":
                continue

            # Per-candidate in-place retry (at most 1 quick retry for brief blips, otherwise fail over immediately)
            max_rate_retries = 1
            rate_attempt = 0

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

                        usage_meta = resp_data.get("usageMetadata", {})
                        in_tok = int(usage_meta.get("promptTokenCount", 0) or 0)
                        out_tok = int(usage_meta.get("candidatesTokenCount", 0) or 0)
                        tot_tok = int(usage_meta.get("totalTokenCount", 0) or (in_tok + out_tok))

                    # 2. Anthropic Claude (with multimodal vision & structured JSON)
                    elif provider == "anthropic":
                        url = "https://api.anthropic.com/v1/messages"
                        req_headers = dict(headers)
                        req_headers["x-api-key"] = api_key
                        req_headers["anthropic-version"] = "2023-06-01"

                        user_content = []
                        if image_bytes:
                            b64_data = base64.b64encode(image_bytes).decode("utf-8")
                            user_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image_mime,
                                    "data": b64_data
                                }
                            })
                        user_content.append({"type": "text", "text": user_prompt})

                        payload = {
                            "model": model,
                            "max_tokens": 4096,
                            "temperature": temperature,
                            "messages": [{"role": "user", "content": user_content}]
                        }
                        sys_txt = system_instruction
                        if format_json:
                            sys_txt = (sys_txt + "\n" if sys_txt else "") + "Strict requirement: Return ONLY valid raw RFC-8259 JSON matching the required schema. Do NOT include markdown code blocks or backticks."
                        if sys_txt:
                            payload["system"] = sys_txt

                        data_bytes = json.dumps(payload).encode("utf-8")
                        req = urllib.request.Request(url, data=data_bytes, headers=req_headers)
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            resp_data = json.loads(resp.read().decode("utf-8"))

                        content_blocks = resp_data.get("content", [])
                        raw_reply = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                        reply_text = re.sub(r"^```(?:json)?\s*", "", raw_reply.strip(), flags=re.IGNORECASE)
                        reply_text = re.sub(r"\s*```$", "", reply_text).strip()

                        usage_meta = resp_data.get("usage", {})
                        in_tok = int(usage_meta.get("input_tokens", 0) or 0)
                        out_tok = int(usage_meta.get("output_tokens", 0) or 0)
                        tot_tok = in_tok + out_tok

                    # 3. OpenAI / OpenRouter / Groq / DeepSeek / Custom
                    else:
                        base_url = self.config.get("custom_base_url") if provider == "custom" else DEFAULT_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1")
                        url = f"{base_url.rstrip('/')}/chat/completions"
                        req_headers = dict(headers)
                        req_headers["Authorization"] = f"Bearer {api_key}"
                        if provider == "openrouter":
                            req_headers["HTTP-Referer"] = "https://github.com/shivambandekar2526-png/result-analysis-aids"
                            req_headers["X-Title"] = "Result Analysis AI"

                        messages = []
                        if system_instruction:
                            messages.append({"role": "system", "content": system_instruction})

                        if image_bytes and provider in ("openai", "openrouter", "custom"):
                            b64_data = base64.b64encode(image_bytes).decode("utf-8")
                            content = [
                                {"type": "text", "text": user_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64_data}"}}
                            ]
                            messages.append({"role": "user", "content": content})
                        else:
                            messages.append({"role": "user", "content": user_prompt})

                        payload = {
                            "model": model,
                            "messages": messages,
                            "temperature": temperature
                        }
                        if provider == "openrouter":
                            # Suppress excessive reasoning tokens (prevents 28k tokens runaway loop on Qwen/Nemotron)
                            payload["reasoning"] = {"effort": "none"}
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
                            if he.code == 400 and ("response_format" in payload or "reasoning" in payload):
                                logger.info("Direct call on %s returned 400 with reasoning/response_format, retrying without...", provider)
                                payload.pop("reasoning", None)
                                payload.pop("response_format", None)
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

                        usage_meta = resp_data.get("usage", {})
                        in_tok = int(usage_meta.get("prompt_tokens", 0) or 0)
                        out_tok = int(usage_meta.get("completion_tokens", 0) or 0)
                        tot_tok = int(usage_meta.get("total_tokens", 0) or (in_tok + out_tok))

                    dur = time.time() - t_start

                    # Token usage tracking with intelligent fallback heuristics
                    if in_tok == 0 and user_prompt:
                        in_tok = max(1, len(user_prompt) // 4)
                        if image_bytes:
                            in_tok += 500  # Vision image base weight
                    if out_tok == 0 and reply_text:
                        out_tok = max(1, len(reply_text) // 4)
                    if tot_tok == 0:
                        tot_tok = in_tok + out_tok

                    self.token_usage["input_tokens"] += in_tok
                    self.token_usage["output_tokens"] += out_tok
                    self.token_usage["total_tokens"] += tot_tok
                    self.last_call_tokens = {
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "total_tokens": tot_tok
                    }
                    CloudClient._global_token_usage["input_tokens"] += in_tok
                    CloudClient._global_token_usage["output_tokens"] += out_tok
                    CloudClient._global_token_usage["total_tokens"] += tot_tok

                    # Successful execution: update primary config, lock active model, record provider success, and return
                    self.record_provider_success(provider)
                    CloudClient._locked_model = {"provider": provider, "model": model}
                    self.config["provider"] = provider
                    self.config["model"] = model

                    if audit_cb:
                        try:
                            audit_cb({
                                "timestamp": time.strftime("%H:%M:%S"),
                                "type": "cloud_ai_success",
                                "badge": "CLOUD AI SUCCESS",
                                "title": f"{provider.upper()} ({model}) responded in {dur:.2f}s [Tokens: {in_tok:,} in / {out_tok:,} out ({tot_tok:,} tot)]",
                                "details": {
                                    "provider": provider,
                                    "model": model,
                                    "duration_sec": round(dur, 2),
                                    "tokens": {
                                        "input": in_tok,
                                        "output": out_tok,
                                        "total": tot_tok,
                                        "cumulative_input": self.token_usage["input_tokens"],
                                        "cumulative_output": self.token_usage["output_tokens"],
                                        "cumulative_total": self.token_usage["total_tokens"]
                                    },
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
                    is_server_busy = (he.code in (500, 502, 503, 504)) or any(k in err_body.lower() for k in ["high demand", "overloaded", "service unavailable", "backend error", "resource exhausted"])
                    is_model_missing = (he.code in (400, 404)) and any(k in err_body.lower() for k in ["decommissioned", "not found", "does not exist", "no access", "not support", "no longer available"])

                    if is_model_missing:
                        # Model is decommissioned or unavailable -> mark in cooldown for 1 hour and break to failover immediately
                        self.mark_model_failed(provider, model, cooldown_seconds=3600, is_rate_limit=False)
                        msg = err_body[:250] if err_body else str(he)
                        last_error = RuntimeError(f"HTTP {he.code} from {provider} ({model}): {msg}")
                        break

                    if is_payload_limit:
                        self.mark_model_failed(provider, model, cooldown_seconds=120, is_rate_limit=True)
                        msg = err_body[:250] if err_body else str(he)
                        last_error = RuntimeError(f"HTTP 413 from {provider} ({model}): {msg}")
                        break

                    if is_rate_limit:
                        # Check multi-key pool rotation first
                        rotated_key = self.rotate_key(provider, failed_key=api_key, cooldown_seconds=60)
                        if rotated_key:
                            api_key = rotated_key
                            pool = self.get_key_pool(provider)
                            active_idx = CloudClient._key_pool_indices.get(provider, 0) + 1
                            if audit_cb:
                                try:
                                    audit_cb({
                                        "timestamp": time.strftime("%H:%M:%S"),
                                        "type": "key_rotation",
                                        "badge": "KEY POOL ROTATE",
                                        "title": f"Rate limit on {provider.upper()}. Rotated to Key #{active_idx} of {len(pool)} in pool",
                                        "details": {
                                            "provider": provider,
                                            "active_key_index": active_idx,
                                            "pool_size": len(pool),
                                            "model": model
                                        }
                                    })
                                except Exception:
                                    pass
                            continue

                        # Check if quota is exhausted for an extended duration (e.g. daily free tier exhausted)
                        is_quota_exhausted = any(k in err_body.lower() for k in ["quota exceeded", "exceeded your current quota", "limit: 0", "limit: 20"]) or bool(re.search(r"retry in\s*[0-9]+[hm]", err_body, re.I))
                        if is_quota_exhausted:
                            # Quota exhausted -> fail over to next model immediately without blocking sleeps
                            self.mark_model_failed(provider, model, cooldown_seconds=600, is_rate_limit=True)
                            msg = err_body[:250] if err_body else str(he)
                            last_error = RuntimeError(f"Quota exhausted on {provider} ({model}): {msg}")
                            break

                        # Quick rate blip: at most 1 pacing retry if wait time is short (<= 4s)
                        if rate_attempt <= max_rate_retries:
                            m_wait = re.search(r"(?:try again in|please retry in|retry after|wait)\s*([0-9]+(?:\.[0-9]+)?)\s*s\b", err_body, re.IGNORECASE)
                            wait_time = float(m_wait.group(1)) + 0.5 if m_wait else 2.0
                            if wait_time <= 4.0:
                                if audit_cb:
                                    try:
                                        audit_cb({
                                            "timestamp": time.strftime("%H:%M:%S"),
                                            "type": "cloud_ai_pacing",
                                            "badge": "RATE LIMIT PACING",
                                            "title": f"⏳ Rate limit on {provider.upper()} ({model}). Pacing request for {wait_time:.1f}s before 1 retry...",
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
                                time.sleep(wait_time)
                                continue

                        self.mark_model_failed(provider, model, cooldown_seconds=60, is_rate_limit=True)
                        msg = err_body[:250] if err_body else str(he)
                        last_error = RuntimeError(f"HTTP 429 from {provider} ({model}): {msg}")
                        break

                    if is_server_busy:
                        if rate_attempt <= max_rate_retries:
                            time.sleep(1.5)
                            continue
                        self.mark_model_failed(provider, model, cooldown_seconds=45, is_rate_limit=False)
                        msg = err_body[:250] if err_body else str(he)
                        last_error = RuntimeError(f"HTTP {he.code} (Server busy) from {provider}: {msg}")
                        break

                    # Format unexpected HTTP error and break to outer failover
                    msg = err_body[:250] if err_body else str(he)
                    try:
                        err_json = json.loads(err_body)
                        msg = err_json.get("error", {}).get("message", msg)
                    except Exception:
                        pass
                    self.mark_model_failed(provider, model, cooldown_seconds=60, is_rate_limit=False)
                    last_error = RuntimeError(f"HTTP {he.code} from {provider} ({model}): {msg}")
                    break

                except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as net_err:
                    last_error = net_err
                    # Timeout / Network error -> break to outer failover immediately to try next candidate
                    self.mark_model_failed(provider, model, cooldown_seconds=60, is_rate_limit=False)
                    break

                except Exception as e:
                    last_error = e
                    self.mark_model_failed(provider, model, cooldown_seconds=60, is_rate_limit=False)
                    break

            # Outer failover handling
            err_str = str(last_error).lower()
            is_rate_limit = any(k in err_str for k in ["429", "quota", "too many requests", "rate limit", "resource exhausted"])
            logger.warning(
                "Direct Cloud AI candidate failed on %s (%s): %s. Orchestrating failover...",
                provider, model, last_error
            )
            cooldown_sec = 60 if is_rate_limit else 45
            self.mark_model_failed(provider, model, cooldown_seconds=cooldown_sec, is_rate_limit=is_rate_limit)

            if auto_orchestrate:
                next_cand = self.get_best_working_model(force_refresh=True, probe=False, requires_vision=requires_vision)
                if audit_cb and next_cand and (next_cand["model"] != model or next_cand["provider"] != provider):
                    try:
                        audit_cb({
                            "timestamp": time.strftime("%H:%M:%S"),
                            "type": "cloud_ai_active",
                            "badge": "MODEL FAILOVER",
                            "title": f"🔄 Failover: Switching from {provider.upper()} ({model}) to {next_cand['provider'].upper()} ({next_cand['model']})",
                            "details": {
                                "new_provider": next_cand["provider"],
                                "new_model": next_cand["model"],
                                "previous_provider": provider,
                                "previous_model": model,
                                "reason": str(last_error)[:200]
                            }
                        })
                    except Exception:
                        pass

            if not auto_orchestrate:
                raise last_error

        raise RuntimeError(f"All configured cloud AI models encountered errors. Last error: {last_error}")
