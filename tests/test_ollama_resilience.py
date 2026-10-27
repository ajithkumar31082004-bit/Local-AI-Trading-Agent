"""Unit tests for Ollama client resilience and fallback mechanisms."""

import pytest
from agents.ollama_client import OllamaClient


def test_ollama_invalid_url_graceful_fallback():
    # Point to an invalid unreachable port
    client = OllamaClient(base_url="http://localhost:59999", model="non_existent_model")
    status = client.check_connection()
    assert status["available"] is False
    assert status["error"] is not None

    # generate_response should return an empty string and not crash
    resp = client.generate_response("System prompt", "User prompt")
    assert resp == ""
