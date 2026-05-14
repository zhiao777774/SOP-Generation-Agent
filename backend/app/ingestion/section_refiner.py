import json
from typing import List

import requests

from backend.app.core.config import ProviderConfig
from backend.app.pipeline.schemas import TemplateRefinementSuggestion, TemplateSection


class SectionRefiner:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.api_base and self.config.model)

    def refine(self, file_name: str, sections: List[TemplateSection]) -> List[TemplateRefinementSuggestion]:
        if not self.is_configured():
            return []
        section_lines = "\n".join(
            f"- id={section.section_id}; level={section.level}; title={section.title}; existing_text={section.existing_text[:500]}"
            for section in sections
        )
        prompt = (
            "Review these DOCX template sections for SOP generation. "
            "Suggest only merge, split, ignore, or rename operations when the rule-based detection likely needs refinement. "
            "Do not invent content. Return compact JSON only with a top-level suggestions array. "
            "Each suggestion must have operation, title, target_section_id, and reason.\n\n"
            f"File: {file_name}\nSections:\n{section_lines}"
        )
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "You review SOP template section boundaries and return JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        try:
            response = requests.post(
                f"{self.config.api_base.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            raw = _extract_json(content)
            return [TemplateRefinementSuggestion(**item) for item in raw.get("suggestions", []) if isinstance(item, dict)]
        except Exception:
            return []


def _extract_json(value: str) -> dict:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)
