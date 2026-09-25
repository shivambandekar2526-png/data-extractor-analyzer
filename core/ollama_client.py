"""
Local AI Engine Client
======================
Communicates with local AI servers (Ollama at http://localhost:11434, LM Studio, etc.)
using Python standard library urllib (zero external HTTP package dependencies).
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaClient:
    """Interface to local Ollama instance with JSON schema enforcement."""

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL):
        self.model = os.environ.get("OLLAMA_MODEL", model).strip()
        self.base_url = os.environ.get("OLLAMA_BASE_URL", base_url).rstrip("/")

    def is_available(self) -> bool:
        """Check if local Ollama daemon is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """List locally downloaded Ollama models."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        format_json: bool = True,
        timeout: int = 180
    ) -> str:
        """
        Send an extraction prompt to local Ollama and retrieve output.
        Enforces format='json' for deterministic structured output.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        if format_json:
            if isinstance(format_json, dict):
                payload["format"] = format_json
            else:
                payload["format"] = "json"
        if system:
            payload["system"] = system

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data_bytes,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return (result.get("response") or "").strip()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama local error ({e.code}): {err_body}")
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot connect to local Ollama at {self.base_url}.\n"
                "Please verify that Ollama is installed and running (run 'ollama serve' in terminal).\n"
                f"Underlying error: {e.reason}"
            )
