"""Local Ollama client for llama3.2:1b with timeout handling and deterministic fallback."""

import json
import logging
from typing import Optional, Dict, Any
import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Connects directly to local Ollama HTTP API with robust fallbacks."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = settings.ollama_timeout_seconds

    def check_connection(self) -> Dict[str, Any]:
        """Check if local Ollama server is running and model is available."""
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if res.status_code == 200:
                data = res.json()
                models = [m.get("name") for m in data.get("models", [])]
                model_present = any(self.model in m for m in models)
                return {
                    "available": True,
                    "model_present": model_present,
                    "models": models,
                    "error": None if model_present else f"Model '{self.model}' not found in Ollama.",
                }
            return {"available": False, "model_present": False, "models": [], "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"available": False, "model_present": False, "models": [], "error": str(e)}

    def generate_response(self, system_prompt: str, user_prompt: str, max_tokens: int = 500, temperature: float = 0.2) -> str:
        """Generate response from local llama3.2:1b."""
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,  # 0.2 for deterministic, grounded explanations
                "num_predict": max_tokens,
            },
            "keep_alive": "30m",
        }

        try:
            res = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("response", "").strip()
            logger.warning("Ollama API returned status code %s", res.status_code)
            return ""
        except Exception as e:
            logger.warning("Ollama generation failed or timed out: %s", e)
            return ""
