import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

from backend.app.core.config import ProviderConfig
from backend.app.ingestion.pdf_image_extractor import PdfImageExtractor
from backend.app.pipeline.schemas import EvidencePlan, ImageEvidenceRef, SectionEvidence


@dataclass(frozen=True)
class ImagePlanningConfig:
    relevance_threshold: float = 0.75
    top_k_per_section: int = 3
    max_inserts_per_section: int = 1
    crop_fallback_enabled: bool = True


class ImageEvidencePlanner:
    def __init__(
        self,
        vlm_config: ProviderConfig,
        extractor: PdfImageExtractor,
        config: ImagePlanningConfig,
    ):
        self.vlm_config = vlm_config
        self.extractor = extractor
        self.config = config

    def attach_images(
        self,
        plan: EvidencePlan,
        uploaded_source_paths: Iterable[str],
        uploaded_reference_paths: Iterable[str],
        progress_callback: Optional[Callable[[str, str, float], None]] = None,
    ) -> EvidencePlan:
        if self.config.max_inserts_per_section <= 0 or self.config.top_k_per_section <= 0:
            plan.warnings.append("Image insertion is disabled by image top-k/max-insert settings.")
            return plan
        if not self._is_configured_vlm():
            plan.warnings.append("Selected model does not support image input; image evidence planning was skipped.")
            return plan

        pdf_paths = [path for path in [*uploaded_source_paths, *uploaded_reference_paths] if Path(path).suffix.lower() == ".pdf"]
        if not pdf_paths:
            return plan

        if progress_callback:
            progress_callback("extract_images", "Extracting image crops from source/reference PDFs.", 0.91)
        candidates = self.extractor.extract(pdf_paths)
        candidates_by_key = _candidates_by_file_page(candidates)
        file_path_by_name = {Path(path).name: path for path in pdf_paths}

        total_sections = max(len(plan.sections), 1)
        for section_index, section in enumerate(plan.sections):
            if progress_callback:
                progress_callback(
                    "rank_images",
                    f"Scoring image relevance for {section_index + 1}/{total_sections} sections.",
                    0.92 + 0.03 * (section_index / total_sections),
                )
            section_candidates = self._section_candidates(section, candidates_by_key)
            scored = self._score_candidates(section, section_candidates)
            if section_candidates and not scored:
                section.warnings.append("Image candidates were detected, but VLM relevance scoring returned no usable result.")
            if not scored and self.config.crop_fallback_enabled:
                scored = self._score_vlm_crop_fallback(section, file_path_by_name)
            selected = [
                item
                for item in sorted(scored, key=lambda candidate: candidate.score, reverse=True)
                if item.score >= self.config.relevance_threshold
            ][: self.config.top_k_per_section]
            for image_index, item in enumerate(selected):
                item.insert_recommended = image_index < self.config.max_inserts_per_section
            section.image_items = selected
            if scored and not section.image_items:
                section.warnings.append(
                    f"Image candidates were found but none reached relevance threshold {self.config.relevance_threshold:.2f}."
                )
        return plan

    def _section_candidates(
        self,
        section: SectionEvidence,
        candidates_by_key: Dict[tuple[str, int], List[ImageEvidenceRef]],
    ) -> List[ImageEvidenceRef]:
        selected: List[ImageEvidenceRef] = []
        for file_name, page_number in _section_file_pages(section):
            selected.extend(candidates_by_key.get((file_name, page_number), []))
        return selected

    def _score_candidates(self, section: SectionEvidence, candidates: List[ImageEvidenceRef]) -> List[ImageEvidenceRef]:
        scored: List[ImageEvidenceRef] = []
        for candidate in candidates:
            page_image_path = candidate.page_image_path or ""
            result = self._score_image(
                section=section,
                crop_image_path=candidate.image_path,
                page_image_path=page_image_path,
            )
            if not result:
                continue
            candidate.score = round(result["relevance"], 4)
            candidate.reason = result["reason"]
            candidate.caption = result["caption"]
            candidate.alt_text = result["alt_text"]
            candidate.summary = result["caption"] or result["reason"]
            candidate.excerpt = result["reason"]
            scored.append(candidate)
        return scored

    def _score_vlm_crop_fallback(
        self,
        section: SectionEvidence,
        file_path_by_name: Dict[str, str],
    ) -> List[ImageEvidenceRef]:
        scored: List[ImageEvidenceRef] = []
        seen_pages = set()
        for file_name, page_number in _section_file_pages(section):
            key = (file_name, page_number)
            if key in seen_pages:
                continue
            seen_pages.add(key)
            file_path = file_path_by_name.get(file_name)
            if not file_path:
                continue
            page_image_path = self.extractor.page_image_path(file_path, page_number)
            if not page_image_path:
                continue
            crop = self._suggest_crop(section, page_image_path)
            if not crop or not crop.get("has_relevant_crop"):
                continue
            candidate = self.extractor.crop_ratio(
                file_path,
                page_number,
                crop.get("bbox") or [],
                extraction_method="vlm_suggested_crop",
            )
            if not candidate:
                continue
            candidate.score = round(float(crop.get("relevance") or 0), 4)
            candidate.reason = str(crop.get("reason") or "VLM suggested this crop for the section.")
            candidate.caption = str(crop.get("caption") or "")
            candidate.alt_text = str(crop.get("alt_text") or candidate.caption)
            candidate.summary = candidate.caption or candidate.reason
            candidate.excerpt = candidate.reason
            scored.append(candidate)
        return scored

    def _score_image(self, section: SectionEvidence, crop_image_path: str, page_image_path: str) -> Optional[Dict]:
        content = [
            {
                "type": "text",
                "text": (
                    "Judge whether this PDF image crop is useful for the SOP section. "
                    "Return JSON only with relevance 0-1, reason, caption, alt_text.\n\n"
                    f"Section context:\n{_section_context(section)}"
                ),
            },
            {"type": "image_url", "image_url": {"url": _data_url(crop_image_path)}},
        ]
        if page_image_path:
            content.append({"type": "image_url", "image_url": {"url": _data_url(page_image_path)}})
        return self._call_json(content, required_keys={"relevance", "reason", "caption", "alt_text"})

    def _suggest_crop(self, section: SectionEvidence, page_image_path: str) -> Optional[Dict]:
        content = [
            {
                "type": "text",
                "text": (
                    "Inspect the full PDF page image and decide whether a visual region should be cropped "
                    "for this SOP section. Return JSON only: has_relevant_crop boolean, bbox normalized "
                    "[x0,y0,x1,y1] from 0 to 1, relevance 0-1, reason, caption, alt_text.\n\n"
                    f"Section context:\n{_section_context(section)}"
                ),
            },
            {"type": "image_url", "image_url": {"url": _data_url(page_image_path)}},
        ]
        return self._call_json(content, required_keys={"has_relevant_crop", "bbox", "relevance"})

    def _call_json(self, content: List[Dict], required_keys: set[str]) -> Optional[Dict]:
        headers = {"Content-Type": "application/json"}
        if self.vlm_config.api_key:
            headers["Authorization"] = f"Bearer {self.vlm_config.api_key}"
        payload = {
            "model": self.vlm_config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful SOP evidence reviewer. "
                        "Score image relevance only when the visual content supports the section. "
                        "Return compact valid JSON only."
                    ),
                },
                {"role": "user", "content": content},
            ],
            "temperature": 0,
        }
        try:
            response = requests.post(
                f"{self.vlm_config.api_base.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.vlm_config.timeout_seconds,
            )
            response.raise_for_status()
            raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json(raw)
            if not required_keys.issubset(parsed.keys()):
                return None
            if "relevance" in parsed:
                parsed["relevance"] = _clamp(float(parsed.get("relevance") or 0), 0.0, 1.0)
            return parsed
        except Exception:
            return None

    def _is_configured_vlm(self) -> bool:
        return bool(self.vlm_config.api_base and self.vlm_config.model and self.vlm_config.supports_images)


def _section_file_pages(section: SectionEvidence) -> List[tuple[str, int]]:
    pages: List[tuple[str, int]] = []
    for item in [*section.source_chunks, *section.reference_items]:
        for page_number in _pages_from_location(item.location or ""):
            pages.append((item.file_name, page_number))
    return pages


def _pages_from_location(location: str) -> List[int]:
    match = re.search(r"pages?\s+(\d+)(?:\s*[-–]\s*(\d+))?", location, re.IGNORECASE)
    if not match:
        return []
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        end = start
    return list(range(start, min(end, start + 3) + 1))


def _candidates_by_file_page(candidates: Iterable[ImageEvidenceRef]) -> Dict[tuple[str, int], List[ImageEvidenceRef]]:
    result: Dict[tuple[str, int], List[ImageEvidenceRef]] = {}
    for candidate in candidates:
        if candidate.page_number is None:
            continue
        result.setdefault((candidate.file_name, candidate.page_number), []).append(candidate)
    return result


def _section_context(section: SectionEvidence) -> str:
    excerpts = [item.summary or item.excerpt for item in [*section.source_chunks[:2], *section.reference_items[:2]]]
    excerpts = [text for text in excerpts if text]
    return "\n".join([section.section_title, *excerpts])[:3000]


def _data_url(path: str) -> str:
    data = Path(path).read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _extract_json(raw: str) -> Dict:
    stripped = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        stripped = stripped[start : end + 1]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
