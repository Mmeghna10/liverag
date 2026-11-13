"""LLM providers behind one small interface, so the chat code never knows which model it talks to.

Today: Ollama (free, local, runs on your GPU). Later: Claude, by adding one class and
changing LLM_PROVIDER in .env. Nothing in chat.py has to change.
"""

import json
import os
from typing import Any, Protocol

import httpx


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Send one system + user message; return the reply parsed as JSON matching `schema`."""
        ...

    def close(self) -> None: ...


class OllamaProvider:
    def __init__(self, base_url: str, model: str) -> None:
        self.name = f"ollama:{model}"
        self.model = model
        # One client for the app's lifetime: reuses the TCP connection between requests.
        # Generous read timeout: the first call after a pause also loads the model into the GPU.
        self.client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(120.0, connect=5.0))

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("/api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            # Structured output: Ollama forces the reply to be JSON matching this schema.
            "format": schema,
            "stream": False,
            "keep_alive": "30m",  # keep the model in GPU memory between questions
            "options": {"temperature": 0, "num_ctx": 8192},  # deterministic; room for 5 chunks
        })
        response.raise_for_status()
        return json.loads(response.json()["message"]["content"])

    def close(self) -> None:
        self.client.close()


def get_provider() -> LLMProvider:
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    if provider == "ollama":
        return OllamaProvider(os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                              os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"))
    raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r} (supported: ollama)")