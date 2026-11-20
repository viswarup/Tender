"""
Ollama HTTP API client wrapper.
Handles text generation, vision inference, and embedding requests
to the local Ollama server at localhost:11434.
"""

import httpx
import base64
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 600.0  # 10 minutes for large VLM inference on complex pages


class OllamaClient:
    """Async HTTP client for Ollama API."""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def close(self):
        if hasattr(self, '_client') and self._client:
            try:
                self._client.close()
            except Exception:
                pass

    def __del__(self):
        self.close()

    def _make_client(self) -> httpx.Client:
        # Kept for backward compatibility, but we reuse self._client internally
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def generate(
        self,
        model: str,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
        num_ctx: int = 8192,
        images: Optional[list[str]] = None,
        format_json: bool = False,
    ) -> str:
        """
        Generate a text completion from Ollama.

        Args:
            model: Model name (e.g., 'gpt-oss:20b')
            prompt: The user prompt
            system: Optional system prompt
            temperature: Sampling temperature (lower = more deterministic)
            num_ctx: Context window size
            images: Optional list of base64-encoded images (for vision models)
            format_json: If True, request JSON output format

        Returns:
            The generated text response.
        """
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        if system:
            payload["system"] = system

        if images:
            payload["images"] = images

        if format_json:
            payload["format"] = "json"

        try:
            response = self._client.post("/api/generate", json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama API error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.ConnectError:
            logger.error("Cannot connect to Ollama. Is it running? Start with: ollama serve")
            raise ConnectionError("Ollama server not reachable at " + self.base_url)

    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        num_ctx: int = 8192,
        images: Optional[list[str]] = None,
        format_json: bool = False,
    ) -> str:
        """
        Chat completion via Ollama /api/chat endpoint.

        Args:
            model: Model name
            messages: List of {"role": "user"|"assistant"|"system", "content": "..."}
            temperature: Sampling temperature
            num_ctx: Context window size
            images: Optional base64-encoded images (attached to last user message)
            format_json: If True, request JSON output format

        Returns:
            The assistant's response text.
        """
        if images and messages:
            # Attach images to the last user message
            last_msg = messages[-1]
            if last_msg.get("role") == "user":
                last_msg["images"] = images

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        if format_json:
            payload["format"] = "json"

        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("message", {}).get("content", "")
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama chat API error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.ConnectError:
            logger.error("Cannot connect to Ollama.")
            raise ConnectionError("Ollama server not reachable at " + self.base_url)

    def embed(self, model: str, text: str) -> list[float]:
        """
        Generate an embedding vector for the given text.

        Args:
            model: Embedding model name (e.g., 'nomic-embed-text:latest')
            text: Text to embed

        Returns:
            List of floats representing the embedding vector.
        """
        payload = {
            "model": model,
            "input": text,
        }

        try:
            response = self._client.post("/api/embed", json=payload)
            response.raise_for_status()
            result = response.json()
            embeddings = result.get("embeddings", [[]])
            return embeddings[0] if embeddings else []
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama embed API error: {e.response.status_code} - {e.response.text}")
            raise

    def embed_batch(self, model: str, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a batch of texts.

        Args:
            model: Embedding model name
            texts: List of texts to embed

        Returns:
            List of embedding vectors.
        """
        payload = {
            "model": model,
            "input": texts,
        }

        try:
            response = self._client.post("/api/embed", json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("embeddings", [])
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama batch embed API error: {e.response.status_code}")
            raise

    @staticmethod
    def image_to_base64(image_path: str | Path) -> str:
        """Convert an image file to base64 string for vision model input."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            response = self._client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """List all available models on the Ollama server."""
        response = self._client.get("/api/tags")
        response.raise_for_status()
        result = response.json()
        return [m["name"] for m in result.get("models", [])]



# Singleton instance
ollama = OllamaClient()
