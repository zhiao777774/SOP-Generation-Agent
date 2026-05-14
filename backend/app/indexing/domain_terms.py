import json
from collections import Counter
from typing import Iterable, List

import requests

from backend.app.core.config import ProviderConfig
from backend.app.indexing.tokenizer import DOMAIN_TOKEN_PATTERN
from backend.app.ingestion.chunking import summarize
from backend.app.pipeline.schemas import DomainTermSuggestion, ReferenceDocument, SourceDocument


class DomainTermSuggester:
    def __init__(
        self,
        config: ProviderConfig,
        enabled: bool = False,
        confidence_threshold: float = 0.75,
    ):
        self.config = config
        self.enabled = enabled
        self.confidence_threshold = confidence_threshold

    def suggest(
        self,
        source_documents: List[SourceDocument],
        reference_documents: List[ReferenceDocument],
    ) -> List[DomainTermSuggestion]:
        if not self.enabled or not self.config.api_base or not self.config.model:
            return []
        candidates = _candidate_terms(source_documents, reference_documents)
        if not candidates:
            return []
        payload = self._chat(candidates)
        suggestions = _parse_suggestions(payload)
        return [
            suggestion
            for suggestion in suggestions
            if suggestion.confidence >= self.confidence_threshold
        ]

    def _chat(self, candidates: List[dict]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.api_base.rstrip('/')}/chat/completions"
        prompt = (
            "You identify SOP maintenance domain terms for temporary BM25 tokenization. "
            "Return JSON only as an array of objects with keys: term, category, confidence, "
            "reason, source_locations, suggested_scope. Use suggested_scope temporary unless the "
            "term is stable across equipment/process documents. Preserve equipment codes exactly.\n\n"
            f"Candidates:\n{json.dumps(candidates[:120], ensure_ascii=False)}"
        )
        response = requests.post(
            url,
            headers=headers,
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": "Return strict JSON only. Do not add unsupported facts."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1200,
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def _candidate_terms(
    source_documents: List[SourceDocument], reference_documents: List[ReferenceDocument]
) -> List[dict]:
    counts: Counter[str] = Counter()
    locations = {}
    for location, text in _iter_texts(source_documents, reference_documents):
        for token in DOMAIN_TOKEN_PATTERN.findall(text):
            value = " ".join(str(token).strip().split())
            if len(value) < 2:
                continue
            counts[value] += 1
            locations.setdefault(value, location)
        for phrase in _cjk_phrases(text):
            counts[phrase] += 1
            locations.setdefault(phrase, location)
    return [
        {
            "term": term,
            "count": count,
            "example_location": locations.get(term, ""),
        }
        for term, count in counts.most_common(160)
        if count >= 2 or len(term) >= 4
    ]


def _iter_texts(
    source_documents: List[SourceDocument], reference_documents: List[ReferenceDocument]
) -> Iterable[tuple[str, str]]:
    for document in source_documents:
        for chunk in document.chunks:
            yield f"{chunk.file_name}:{chunk.page_start or ''}", summarize(chunk.content, 1200)
    for document in reference_documents:
        for item in document.items:
            yield f"{item.file_name}:{item.location or ''}", summarize(item.content, 1200)


def _cjk_phrases(text: str) -> List[str]:
    import re

    return re.findall(r"[\u4e00-\u9fff]{2,12}", text)


def _parse_suggestions(payload: str) -> List[DomainTermSuggestion]:
    if not payload:
        return []
    cleaned = payload.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    suggestions: List[DomainTermSuggestion] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("term"):
            continue
        try:
            suggestions.append(DomainTermSuggestion(**item))
        except Exception:
            continue
    return suggestions
