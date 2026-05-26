import json
import re
from typing import Dict, List
from uuid import uuid4

import requests

from backend.app.core.config import ProviderConfig
from backend.app.pipeline.schemas import (
    TemplateBlock,
    TemplateRefinementSuggestion,
    TemplateSection,
    TemplateSectionCandidate,
    TemplateStructure,
)
from backend.app.services.concurrency import limited_post


class SectionRefiner:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.api_base and self.config.model)

    def refine(
        self,
        file_name: str,
        template_id: str,
        blocks: List[TemplateBlock],
        candidates: List[TemplateSectionCandidate],
        fallback_sections: List[TemplateSection],
        feedback: str = "",
    ) -> TemplateStructure:
        if not self.is_configured():
            return _fallback_structure(
                file_name,
                template_id,
                blocks,
                candidates,
                fallback_sections,
                ["LLM section refinement is not configured; using rule-based section proposal."],
                feedback=feedback,
            )

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You resolve SOP DOCX template section boundaries. "
                        "Return JSON only. Do not generate SOP content."
                    ),
                },
                {
                    "role": "user",
                    "content": _prompt(file_name, blocks, candidates, fallback_sections, feedback),
                },
            ],
            "temperature": 0,
        }
        try:
            response = limited_post(
                "llm",
                requests.post,
                f"{self.config.api_base.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            raw = _extract_json(content)
            return _structure_from_llm(file_name, template_id, blocks, candidates, fallback_sections, raw, feedback)
        except Exception as exc:
            return _fallback_structure(
                file_name,
                template_id,
                blocks,
                candidates,
                fallback_sections,
                ["LLM section refinement failed; using rule-based section proposal."],
                feedback=feedback,
            )


def _prompt(
    file_name: str,
    blocks: List[TemplateBlock],
    candidates: List[TemplateSectionCandidate],
    fallback_sections: List[TemplateSection],
    feedback: str,
) -> str:
    block_lines = "\n".join(
        (
            f"- id={block.block_id}; order={block.order_index}; source={block.source_type}; "
            f"style={block.style_name or ''}; metadata={json.dumps(block.metadata, ensure_ascii=False)}; "
            f"text={block.text[:500]}"
        )
        for block in blocks[:180]
    )
    candidate_lines = "\n".join(
        (
            f"- id={candidate.candidate_id}; title={candidate.title}; level={candidate.level}; "
            f"blocks={candidate.source_block_ids}; confidence={candidate.confidence}; "
            f"warnings={candidate.warnings}; metadata={json.dumps(candidate.metadata, ensure_ascii=False)}; "
            f"reason={candidate.reason}"
        )
        for candidate in candidates
    )
    fallback_lines = "\n".join(
        (
            f"- id={section.section_id}; title={section.title}; level={section.level}; "
            f"blocks={section.source_block_ids}; existing_text={section.existing_text[:300]}"
        )
        for section in fallback_sections
    )
    return (
        "Resolve which template sections should be filled by SOP generation.\n"
        "Use reviewer feedback as guidance by default. Treat it as subset/replacement only if it clearly says "
        "only these sections, only fill these, replace all sections, or an equivalent instruction.\n"
        "Output JSON with this shape:\n"
        "{\n"
        '  "feedback_intent": "guidance|subset|replace|unclear",\n'
        '  "sections": [\n'
        '    {"title": "...", "level": 1, "source_block_ids": ["block-1"], '
        '"confidence": 0.0, "operation": "keep|rename|split|merge|ignore|add", "reason": "..."}\n'
        "  ],\n"
        '  "warnings": ["..."],\n'
        '  "suggestions": [{"operation": "...", "title": "...", "target_section_id": "...", "reason": "..."}]\n'
        "}\n"
        "Rules:\n"
        "- Prefer existing block ids when possible.\n"
        "- Do not invent machine facts or SOP content.\n"
        "- Use raw template blocks to understand the full document shape, then use rule candidates as hints.\n"
        "- You may keep, rename, merge, split, add, or ignore sections; rules are high-recall candidates, not final truth.\n"
        "- Ignore document titles, cover-page labels, product names, approval blocks, revision tables, headers, footers, "
        "and administrative metadata unless reviewer feedback explicitly asks to fill them.\n"
        "- Blocks marked metadata looks_like_document_title=true, looks_like_metadata_label=true, or "
        "section_recommendation=ignore should normally be excluded from sections.\n"
        "- Keep fixed metadata/footer/header blocks out of fillable sections.\n"
        "- Include sections that should be generated/filled, not every label in the template.\n\n"
        f"File: {file_name}\n\n"
        f"Reviewer feedback:\n{feedback or '(none)'}\n\n"
        f"Raw template blocks:\n{block_lines or '(none)'}\n\n"
        f"Rule candidates:\n{candidate_lines or '(none)'}\n\n"
        f"Rule fallback proposal:\n{fallback_lines or '(none)'}"
    )


def _structure_from_llm(
    file_name: str,
    template_id: str,
    blocks: List[TemplateBlock],
    candidates: List[TemplateSectionCandidate],
    fallback_sections: List[TemplateSection],
    raw: Dict,
    feedback: str,
) -> TemplateStructure:
    known_blocks = {block.block_id for block in blocks}
    warnings = [str(item) for item in raw.get("warnings", []) if str(item).strip()]
    sections: List[TemplateSection] = []
    for item in raw.get("sections", []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        block_ids = [str(block_id) for block_id in item.get("source_block_ids", []) if str(block_id) in known_blocks]
        missing_ids = [str(block_id) for block_id in item.get("source_block_ids", []) if str(block_id) not in known_blocks]
        if missing_ids:
            warnings.append(f"LLM referenced unknown template block ids for {title}: {', '.join(missing_ids)}")
        sections.append(
            TemplateSection(
                section_id=f"section-{len(sections) + 1}",
                title=title,
                level=_safe_int(item.get("level"), 1),
                start_block_index=_first_block_index(blocks, block_ids, len(sections)),
                end_block_index=None,
                existing_text=_existing_text_for_blocks(blocks, block_ids),
                style_name="llm-refined",
                source_block_ids=block_ids,
                confidence=_safe_float(item.get("confidence"), 0.75),
                operation=str(item.get("operation") or "keep"),
                reason=str(item.get("reason") or "LLM-assisted section resolution."),
            )
        )
    if not sections:
        return _fallback_structure(
            file_name,
            template_id,
            blocks,
            candidates,
            fallback_sections,
            warnings + ["LLM returned no usable sections; using rule-based section proposal."],
            feedback=feedback,
        )
    suggestions = [
        TemplateRefinementSuggestion(**item)
        for item in raw.get("suggestions", [])
        if isinstance(item, dict)
    ]
    return TemplateStructure(
        template_id=template_id,
        file_name=file_name,
        sections=sections,
        warnings=warnings,
        refinement_suggestions=suggestions,
        blocks=blocks,
        candidates=candidates,
        resolution_id=f"resolution-{uuid4().hex[:10]}",
        refinement_mode="llm",
        feedback_intent=str(raw.get("feedback_intent") or "guidance"),
        feedback=feedback,
    )


def _fallback_structure(
    file_name: str,
    template_id: str,
    blocks: List[TemplateBlock],
    candidates: List[TemplateSectionCandidate],
    fallback_sections: List[TemplateSection],
    warnings: List[str],
    feedback: str = "",
) -> TemplateStructure:
    sections, feedback_warnings = _apply_explicit_exclusion_feedback(fallback_sections, feedback)
    next_warnings = [*warnings, *feedback_warnings]
    return TemplateStructure(
        template_id=template_id,
        file_name=file_name,
        sections=sections,
        warnings=next_warnings,
        blocks=blocks,
        candidates=candidates,
        resolution_id=f"resolution-{uuid4().hex[:10]}",
        refinement_mode="rules",
        feedback_intent="guidance",
        feedback=feedback,
    )


def _apply_explicit_exclusion_feedback(
    sections: List[TemplateSection],
    feedback: str,
) -> tuple[List[TemplateSection], List[str]]:
    if not feedback.strip():
        return sections, []
    excluded_titles: List[str] = []
    kept: List[TemplateSection] = []
    feedback_lines = [line for line in re.split(r"[\n\r;；。]+", feedback) if line.strip()]
    for section in sections:
        if _feedback_explicitly_excludes_section(section.title, feedback_lines):
            excluded_titles.append(section.title)
            continue
        kept.append(section)
    if not excluded_titles:
        return sections, []
    if not kept:
        return sections, ["Reviewer feedback appeared to exclude every section; kept rule-based proposal for safety."]
    return kept, [f"Applied reviewer feedback to exclude section(s): {', '.join(excluded_titles)}"]


def _feedback_explicitly_excludes_section(title: str, feedback_lines: List[str]) -> bool:
    aliases = _section_title_aliases(title)
    return any(
        _line_has_exclusion_intent(line) and any(alias and alias in _normalize_section_text(line) for alias in aliases)
        for line in feedback_lines
    )


def _section_title_aliases(title: str) -> List[str]:
    without_numbering = re.sub(r"^\s*\d+[\.\)\、]?\s*", "", title).strip()
    aliases = {_normalize_section_text(title), _normalize_section_text(without_numbering)}
    return [alias for alias in aliases if alias]


def _line_has_exclusion_intent(line: str) -> bool:
    normalized = line.lower()
    return any(
        marker in normalized
        for marker in (
            "不用",
            "不要",
            "不需要",
            "不填",
            "移除",
            "刪除",
            "删除",
            "排除",
            "略過",
            "跳過",
            "ignore",
            "remove",
            "exclude",
            "skip",
            "not needed",
        )
    )


def _normalize_section_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).lower()


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


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _first_block_index(blocks: List[TemplateBlock], block_ids: List[str], fallback: int) -> int:
    index_by_id = {block.block_id: block.order_index for block in blocks}
    indexes = [index_by_id[block_id] for block_id in block_ids if block_id in index_by_id]
    return min(indexes) if indexes else fallback


def _existing_text_for_blocks(blocks: List[TemplateBlock], block_ids: List[str]) -> str:
    if not block_ids:
        return ""
    seen = set(block_ids)
    return "\n".join(block.text for block in blocks if block.block_id in seen)
