from typing import Dict, Iterable, List

import requests

from backend.app.core.config import ProviderConfig


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self._remote_disabled = False

    def embed(self, text: str) -> List[float]:
        if not self.config.api_base or not self.config.model:
            raise EmbeddingUnavailable("Embedding provider is not configured.")
        if self._remote_disabled:
            raise EmbeddingUnavailable("Embedding provider was disabled after a previous request failure.")
        try:
            return self._remote_embed(text)
        except Exception as exc:
            self._remote_disabled = True
            raise EmbeddingUnavailable(f"Embedding provider request failed: {exc}") from exc

    def _remote_embed(self, text: str) -> List[float]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        is_openai = "/v1/" in self.config.api_base
        payload = {"model": self.config.model}
        payload["input" if is_openai else "prompt"] = text
        response = requests.post(
            self.config.api_base,
            headers=headers,
            json=payload,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if "data" in data:
            return data["data"][0]["embedding"]
        return data["embedding"]


def cosine(left: List[float], right: List[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[i] * right[i] for i in range(size))


def batch_embeddings(client: EmbeddingClient, values: Iterable[str]) -> Dict[str, List[float]]:
    return {value: client.embed(value) for value in values}
