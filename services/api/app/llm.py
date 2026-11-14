"""LLM providers behind one small interface, so the chat code never knows which model it talks to.

Today: Ollama (free, local, runs on your GPU). Later: Claude, by adding one class and
changing LLM_PROVIDER in .env. Nothing in chat.py has to change.
"""

import json
import os
from collections.abc import Iterator
from typing import Any, Protocol

import httpx


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Send one system + user message; return the reply parsed as JSON matching `schema`."""
        ...

    def stream_text(self, system: str, user: str) -> Iterator[str]:
        """Send one system + user message; yield the reply in small pieces as it is generated."""
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

    def stream_text(self, system: str, user: str) -> Iterator[str]:
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192},
        }
        # Ollama streams one JSON object per line: {"message": {"content": "..."}, "done": false}
        with self.client.stream("POST", "/api/chat", json=request) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                part = json.loads(line)
                if piece := part.get("message", {}).get("content"):
                    yield piece
                if part.get("done"):
                    break

    def close(self) -> None:
        self.client.close()


def get_provider() -> LLMProvider:
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    if provider == "ollama":
        # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 (::1) first, and Ollama only
        # listens on IPv4, so every new connection to "localhost" waits ~2 s before falling back.
        return OllamaProvider(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                              os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"))
    raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r} (supported: ollama)")