import requests

from backend.app.core.config import ProviderConfig
from backend.app.ingestion.chunking import summarize
from backend.app.services.concurrency import limited_post


class Contextualizer:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.api_base and self.config.model)

    def document_summary(self, document_text: str) -> str:
        if not self.is_configured():
            return summarize(document_text, 500)
        prompt = (
            "Provide a concise document context for search retrieval. "
            "Capture the main topic, equipment, procedure type, and important entities. "
            "Keep it under 100 words.\n\n"
            f"Document:\n{document_text[:12000]}"
        )
        return self._chat(prompt) or summarize(document_text, 500)

    def chunk_context(self, document_text: str, chunk_text: str) -> str:
        if not self.is_configured():
            return ""
        prompt = (
            "<document>\n"
            f"{document_text[:12000]}\n"
            "</document>\n\n"
            "Here is the chunk we want to situate within the whole document:\n"
            "<chunk>\n"
            f"{chunk_text}\n"
            "</chunk>\n\n"
            "Give a short context that situates this chunk for search retrieval. "
            "Answer with only the context."
        )
        return self._chat(prompt, max_tokens=150)

    def _chat(self, prompt: str, max_tokens: int = 180) -> str:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "You create concise retrieval context. Do not add unsupported facts."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        try:
            response = limited_post(
                "llm",
                requests.post,
                url,
                headers=headers,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception:
            return ""
