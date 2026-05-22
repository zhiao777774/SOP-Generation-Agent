import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

from backend.app.core.config import ProviderConfig
from backend.app.pipeline.schemas import (
    EvidenceRef,
    GenerationProfile,
    ImageEvidenceRef,
    SectionEvidence,
    StructuredBlock,
    StructuredListItem,
    StructuredSectionDraft,
)


_LANG_INSTRUCTIONS = {
    "zh-TW": "請使用繁體中文撰寫。",
    "zh-CN": "请使用简体中文撰写。",
    "en": "Write in English.",
}

_TONE_INSTRUCTIONS = {
    "professional": "語氣專業、嚴謹，適合正式作業文件。",
    "instructive": "語氣指導性、清楚明確，適合操作手冊。",
    "neutral": "語氣中立、客觀陳述。",
}

_VERBOSITY_INSTRUCTIONS = {
    "detailed": "內容詳盡，包含完整說明、規格數值、操作條件與注意事項。",
    "balanced": "內容適中，涵蓋要點、必要條件與關鍵規格。",
    "concise": "內容精簡，僅保留可執行的核心要點。",
}

_PROHIBITED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"本章節依據",
        r"來源文件",
        r"原廠/來源文件整理",
        r"SOP\s*草稿",
        r"草稿內容",
        r"審核者",
        r"reviewer",
        r"建議.*補充來源",
        r"目前沒有足夠",
        r"以下是",
        r"vendor/source",
        r"source evidence",
        r"reference evidence",
        r"<!--\s*Page\s+\d+\s*-->",
        r"\bPage\s+\d+\b",
    ]
]


class SectionGenerator:
    def __init__(self, llm_config: Optional[ProviderConfig] = None):
        self.llm_config = llm_config

    def generate(
        self,
        section: SectionEvidence,
        profile: GenerationProfile,
        global_feedback: str = "",
        section_feedback: str = "",
        regeneration_feedback: str = "",
        llm_config: Optional[ProviderConfig] = None,
    ) -> StructuredSectionDraft:
        warnings: List[str] = list(section.warnings)
        if global_feedback or section_feedback or regeneration_feedback:
            warnings.append("Reviewer feedback was used as generation guidance.")

        source_items = list(section.source_chunks)
        reference_items = list(section.reference_items) if profile.include_reference_cases else []
        image_items = list(section.image_items)
        if not source_items and not reference_items and not image_items:
            warnings.append("No evidence was mapped; section body was not generated.")
            return self._draft(section, [], warnings)

        config = llm_config or self.llm_config
        if not self._is_configured(config):
            warnings.append("LLM generation is not configured; section body was not generated.")
            return self._draft(section, [], warnings)

        blocks, generation_warnings = self._generate_with_llm(
            section,
            profile,
            source_items,
            reference_items,
            image_items,
            global_feedback,
            section_feedback,
            regeneration_feedback,
            config,
        )
        warnings.extend(generation_warnings)
        return self._draft(section, blocks, warnings)

    def _generate_with_llm(
        self,
        section: SectionEvidence,
        profile: GenerationProfile,
        source_items: List[EvidenceRef],
        reference_items: List[EvidenceRef],
        image_items: List[ImageEvidenceRef],
        global_feedback: str,
        section_feedback: str,
        regeneration_feedback: str,
        config: ProviderConfig,
    ) -> tuple[List[StructuredBlock], List[str]]:
        warnings: List[str] = []
        prompt = self._build_prompt(
            section,
            profile,
            source_items,
            reference_items,
            image_items,
            global_feedback,
            section_feedback,
            regeneration_feedback,
        )
        correction = ""
        for attempt in range(2):
            raw, call_error = self._call_llm(config, prompt, correction)
            if not raw:
                detail = f" Detail: {call_error}" if call_error else ""
                warnings.append(f"LLM generation returned no content.{detail}")
                break
            blocks, validation_warnings = self._parse_and_validate_blocks(
                raw,
                section.section_id,
                {item.evidence_id for item in source_items},
                {item.evidence_id for item in reference_items},
                {item.evidence_id: item for item in image_items if item.insert_recommended},
            )
            if blocks:
                warnings.extend(validation_warnings)
                return self._ensure_image_blocks(section.section_id, blocks, image_items), warnings
            warnings.extend(validation_warnings)
            correction = self._correction_prompt(validation_warnings)
            if attempt == 0:
                warnings.append("LLM output contained prohibited wording or invalid structure; retrying once.")

        warnings.append("LLM generation failed validation; section body was not generated.")
        return [], warnings

    def _build_prompt(
        self,
        section: SectionEvidence,
        profile: GenerationProfile,
        source_items: List[EvidenceRef],
        reference_items: List[EvidenceRef],
        image_items: List[ImageEvidenceRef],
        global_feedback: str,
        section_feedback: str,
        regeneration_feedback: str,
    ) -> str:
        feedback = "\n".join(
            text
            for text in [global_feedback, section_feedback, regeneration_feedback]
            if text and text.strip()
        )
        source_payload = self._evidence_payload(source_items)
        reference_payload = self._evidence_payload(reference_items)
        image_payload = self._image_payload(image_items)
        schema = {
            "blocks": [
                {
                    "block_type": "paragraph|heading|bullet_list|numbered_list|table|callout|image",
                    "content_md": "paragraph, heading, or callout body with restricted inline markdown",
                    "level": 2,
                    "items": [
                        {
                            "content_md": "list item body with restricted inline markdown",
                            "source_chunk_ids": ["source evidence ids used by this item"],
                            "reference_item_ids": ["reference evidence ids used by this item"],
                            "items": [],
                        }
                    ],
                    "headers": ["table header"],
                    "rows": [["table cell with restricted inline markdown"]],
                    "image_evidence_ids": ["image evidence ids used by this image block"],
                    "caption_md": "short figure caption with restricted inline markdown",
                    "alt_text": "brief image description",
                    "source_chunk_ids": ["source evidence ids used by this block"],
                    "reference_item_ids": ["reference evidence ids used by this block"],
                    "claims": ["optional concise factual claim"],
                    "warnings": ["optional block warning"],
                }
            ],
            "warnings": ["optional section warning"],
        }
        return (
            f"任務：撰寫 SOP 章節「{section.section_title}」的正式內文。\n"
            "章節標題會由系統另外插入，輸出內容不得重複章節標題。\n\n"
            "風格要求：\n"
            f"- {_LANG_INSTRUCTIONS.get(profile.language, _LANG_INSTRUCTIONS['zh-TW'])}\n"
            f"- {_TONE_INSTRUCTIONS.get(profile.tone, _TONE_INSTRUCTIONS['professional'])}\n"
            f"- {_VERBOSITY_INSTRUCTIONS.get(profile.verbosity, _VERBOSITY_INSTRUCTIONS['balanced'])}\n"
            f"- {'保留' if profile.preserve_vendor_terminology else '可翻譯'}設備型號、規格數值、料號、錯誤碼與術語縮寫。\n"
            f"- {'優先強調安全注意事項。' if profile.prioritize_safety else '依一般章節重點安排內容。'}\n\n"
            "證據使用規則：\n"
            "- Source/vendor 文件是主要依據。\n"
            "- Reference 維修紀錄只能補充 source 未明確涵蓋的實務經驗。\n"
            "- 若 Reference 與 Source 衝突，正式內容以 Source 為準，並在 JSON warnings 記錄衝突。\n"
            "- 不得捏造未由 evidence 支援的規格、步驟或判定條件。\n"
            "- 每個 block 必須列出實際使用到的 source_chunk_ids 或 reference_item_ids。\n\n"
            "禁止輸出：\n"
            "- 不要寫前言、解釋、meta 註解、審閱建議、缺資料提醒或草稿聲明。\n"
            "- 不要出現「本章節依據」「來源文件」「SOP 草稿」「審核者」「目前沒有足夠」等字眼。\n"
            "- 不要輸出 page marker、檔名、row/page 來源標籤或 evidence label。\n\n"
            "格式規則：\n"
            "- 使用 block_type 表達結構，不要在 paragraph 裡用 Markdown 模擬表格或清單。\n"
            "- 只有當 image evidence 與章節高度相關時，才可使用 block_type=image。\n"
            "- paragraph、list item、table cell 只可使用 **bold**、*italic*、`inline code`。\n"
            "- 不要使用 raw HTML、link、image、markdown heading marker、markdown table pipe row。\n\n"
            f"Reviewer feedback:\n{feedback or '(none)'}\n\n"
            f"Source evidence:\n{json.dumps(source_payload, ensure_ascii=False, indent=2)}\n\n"
            f"Reference evidence:\n{json.dumps(reference_payload, ensure_ascii=False, indent=2)}\n\n"
            f"Image evidence:\n{json.dumps(image_payload, ensure_ascii=False, indent=2)}\n\n"
            "只輸出 JSON，不要 markdown code fence。JSON schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

    def _call_llm(self, config: ProviderConfig, prompt: str, correction: str = "") -> tuple[str, str]:
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert SOP technical writer. Return valid JSON only. "
                    "Write final-document body text grounded in provided evidence. "
                    "Do not include commentary, source labels, page markers, or draft/reviewer wording."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        if correction:
            messages.append({"role": "user", "content": correction})
        payload = {
            "model": config.model,
            "messages": messages,
            "temperature": 0.2,
        }
        last_error = ""
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{config.api_base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=config.timeout_seconds,
                )
                response.raise_for_status()
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if content:
                    return content, ""
                last_error = "LLM response contained an empty message."
            except requests.exceptions.Timeout:
                last_error = f"LLM request timed out after {config.timeout_seconds:g}s."
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                body = exc.response.text[:500] if exc.response is not None else ""
                last_error = f"LLM HTTP {status_code}. {body}".strip()
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if last_error and attempt < 2:
                last_error = f"{last_error} Attempt {attempt + 1}/3 failed."
            if last_error:
                if attempt == 2:
                    return "", last_error
                time.sleep(0.5 * (attempt + 1))
        return "", last_error

    def _parse_and_validate_blocks(
        self,
        raw: str,
        section_id: str,
        valid_source_ids: set[str],
        valid_reference_ids: set[str],
        valid_image_items: Dict[str, ImageEvidenceRef],
    ) -> tuple[List[StructuredBlock], List[str]]:
        warnings: List[str] = []
        try:
            parsed = self._extract_json(raw)
        except ValueError as exc:
            return [], [str(exc)]

        if not isinstance(parsed, dict) or not isinstance(parsed.get("blocks"), list):
            return [], ["LLM output must be a JSON object with a blocks array."]

        warnings.extend(str(warning) for warning in parsed.get("warnings", []) if str(warning).strip())
        blocks: List[StructuredBlock] = []
        for index, item in enumerate(parsed["blocks"]):
            if not isinstance(item, dict):
                warnings.append(f"Block {index + 1} is not an object.")
                continue
            block_type = self._block_type(item.get("block_type"))
            source_ids = self._valid_ids(item.get("source_chunk_ids"), valid_source_ids)
            reference_ids = self._valid_ids(item.get("reference_item_ids"), valid_reference_ids)
            image_ids = self._valid_ids(item.get("image_evidence_ids"), set(valid_image_items.keys()))
            if not image_ids and item.get("image_id"):
                image_ids = self._valid_ids([item.get("image_id")], set(valid_image_items.keys()))
            list_items, list_warnings = self._parse_list_items(
                item.get("items"),
                valid_source_ids,
                valid_reference_ids,
                f"Block {index + 1}",
            )
            warnings.extend(list_warnings)
            headers = self._table_values(item.get("headers"))
            rows = [self._table_values(row) for row in item.get("rows", []) if isinstance(row, list)]
            content = self._clean_evidence_text(str(item.get("content_md") or item.get("text") or ""))
            text = self._plain_text_for_block(block_type, content, list_items, headers, rows)
            block_source_ids = source_ids or self._list_source_ids(list_items)
            block_reference_ids = reference_ids or self._list_reference_ids(list_items)
            if block_type == "image":
                text = str(item.get("caption_md") or item.get("alt_text") or "").strip()
            if not text and block_type != "image":
                warnings.append(f"Block {index + 1} has empty content.")
                continue
            prohibited = self._prohibited_matches(" ".join([content, text, *headers, *[cell for row in rows for cell in row]]))
            if prohibited:
                warnings.append(f"Block {index + 1} contains prohibited wording: {', '.join(prohibited)}")
                continue
            if block_type == "image" and not image_ids:
                warnings.append(f"Block {index + 1} has no valid image evidence ids.")
                continue
            if block_type != "image" and not block_source_ids and not block_reference_ids:
                warnings.append(f"Block {index + 1} has no valid evidence ids.")
                continue
            invalid_ids = self._invalid_ids(item.get("source_chunk_ids"), valid_source_ids)
            invalid_ids.extend(self._invalid_ids(item.get("reference_item_ids"), valid_reference_ids))
            invalid_ids.extend(self._invalid_ids(item.get("image_evidence_ids"), set(valid_image_items.keys())))
            if invalid_ids:
                warnings.append(f"Block {index + 1} referenced unknown evidence ids: {', '.join(invalid_ids)}")
            image_item = valid_image_items.get(image_ids[0]) if image_ids else None
            blocks.append(
                StructuredBlock(
                    block_id=f"{section_id}-b{len(blocks) + 1}",
                    block_type=block_type,
                    text=text,
                    content_md=content,
                    level=self._safe_int(item.get("level")),
                    items=list_items,
                    headers=headers,
                    rows=rows,
                    callout_type=str(item.get("callout_type") or "note"),
                    source_chunk_ids=source_ids,
                    reference_item_ids=reference_ids,
                    image_evidence_ids=image_ids,
                    image_id=image_item.image_id if image_item else "",
                    image_path=image_item.image_path if image_item else "",
                    caption_md=self._clean_evidence_text(str(item.get("caption_md") or "")),
                    alt_text=self._clean_evidence_text(str(item.get("alt_text") or "")),
                    claims=self._string_list(item.get("claims")),
                    warnings=self._string_list(item.get("warnings")),
                )
            )
        return blocks, warnings

    def _parse_list_items(
        self,
        value: object,
        valid_source_ids: set[str],
        valid_reference_ids: set[str],
        context: str,
    ) -> tuple[List[StructuredListItem], List[str]]:
        if not isinstance(value, list):
            return [], []
        items: List[StructuredListItem] = []
        warnings: List[str] = []
        for index, raw_item in enumerate(value, start=1):
            if not isinstance(raw_item, dict):
                warnings.append(f"{context} list item {index} is not an object.")
                continue
            content = self._clean_evidence_text(str(raw_item.get("content_md") or raw_item.get("text") or ""))
            prohibited = self._prohibited_matches(content)
            if not content:
                warnings.append(f"{context} list item {index} has empty content.")
                continue
            if prohibited:
                warnings.append(f"{context} list item {index} contains prohibited wording: {', '.join(prohibited)}")
                continue
            child_items, child_warnings = self._parse_list_items(
                raw_item.get("items"),
                valid_source_ids,
                valid_reference_ids,
                f"{context} list item {index}",
            )
            warnings.extend(child_warnings)
            items.append(
                StructuredListItem(
                    content_md=content,
                    text=content,
                    source_chunk_ids=self._valid_ids(raw_item.get("source_chunk_ids"), valid_source_ids),
                    reference_item_ids=self._valid_ids(raw_item.get("reference_item_ids"), valid_reference_ids),
                    items=child_items,
                )
            )
        return items, warnings

    def _evidence_payload(self, items: List[EvidenceRef]) -> List[Dict[str, str]]:
        payload = []
        for item in items:
            text = self._clean_evidence_text(item.excerpt or item.summary)
            if not text:
                continue
            payload.append(
                {
                    "id": item.evidence_id,
                    "file_name": item.file_name,
                    "location": item.location or "",
                    "text": text,
                }
            )
        return payload

    def _image_payload(self, items: List[ImageEvidenceRef]) -> List[Dict[str, str]]:
        payload = []
        for item in items:
            payload.append(
                {
                    "id": item.evidence_id,
                    "file_name": item.file_name,
                    "location": item.location or "",
                    "caption": item.caption,
                    "reason": item.reason,
                    "relevance": f"{item.score:.2f}",
                    "insert_recommended": str(item.insert_recommended).lower(),
                    "extraction_method": item.extraction_method,
                }
            )
        return payload

    def _extract_json(self, raw: str) -> Dict[str, Any]:
        stripped = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fenced:
            stripped = fenced.group(1)
        if not stripped.startswith("{"):
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("LLM output was not valid JSON.")
            stripped = stripped[start : end + 1]
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM output was not valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("LLM output JSON must be an object.")
        return parsed

    def _valid_ids(self, value: object, valid_ids: set[str]) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item) in valid_ids]

    def _invalid_ids(self, value: object, valid_ids: set[str]) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item) and str(item) not in valid_ids]

    def _block_type(self, value: object) -> str:
        block_type = str(value or "paragraph").strip().lower()
        aliases = {
            "bullet": "bullet_list",
            "numbered": "numbered_list",
        }
        block_type = aliases.get(block_type, block_type)
        if block_type not in {"paragraph", "heading", "bullet_list", "numbered_list", "table", "callout", "image"}:
            return "paragraph"
        return block_type

    def _table_values(self, value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        return [self._clean_evidence_text(str(item)) for item in value]

    def _string_list(self, value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _prohibited_matches(self, text: str) -> List[str]:
        return [pattern.pattern for pattern in _PROHIBITED_PATTERNS if pattern.search(text)]

    def _safe_int(self, value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _plain_text_for_block(
        self,
        block_type: str,
        content: str,
        items: List[StructuredListItem],
        headers: List[str],
        rows: List[List[str]],
    ) -> str:
        if block_type in {"bullet_list", "numbered_list"}:
            return "\n".join(self._list_text(item) for item in items) or content
        if block_type == "table":
            return "\n".join(" | ".join(row) for row in ([headers] if headers else []) + rows)
        if block_type == "image":
            return content
        return content

    def _list_text(self, item: StructuredListItem) -> str:
        lines = [item.content_md or item.text]
        for child in item.items:
            lines.append(self._list_text(child))
        return "\n".join(line for line in lines if line)

    def _list_source_ids(self, items: List[StructuredListItem]) -> List[str]:
        ids: List[str] = []
        for item in items:
            ids.extend(item.source_chunk_ids)
            ids.extend(self._list_source_ids(item.items))
        return ids

    def _list_reference_ids(self, items: List[StructuredListItem]) -> List[str]:
        ids: List[str] = []
        for item in items:
            ids.extend(item.reference_item_ids)
            ids.extend(self._list_reference_ids(item.items))
        return ids

    def _correction_prompt(self, warnings: List[str]) -> str:
        return (
            "Revise the previous answer. Return valid JSON only. Fix these validation errors:\n"
            + "\n".join(f"- {warning}" for warning in warnings[-8:])
            + "\nDo not include source labels, page markers, draft/reviewer wording, or unsupported facts."
        )

    def _clean_evidence_text(self, value: str) -> str:
        without_markers = re.sub(r"<!--\s*Page\s+\d+\s*-->", " ", value)
        return re.sub(r"\s+", " ", without_markers).strip()

    def _is_configured(self, config: Optional[ProviderConfig]) -> bool:
        return bool(config and config.api_base and config.model)

    def _draft(
        self,
        section: SectionEvidence,
        blocks: List[StructuredBlock],
        warnings: List[str],
    ) -> StructuredSectionDraft:
        return StructuredSectionDraft(
            section_id=section.section_id,
            title=section.section_title,
            blocks=blocks,
            warnings=warnings,
        )

    def _ensure_image_blocks(
        self,
        section_id: str,
        blocks: List[StructuredBlock],
        image_items: List[ImageEvidenceRef],
    ) -> List[StructuredBlock]:
        used_ids = {image_id for block in blocks for image_id in block.image_evidence_ids}
        next_index = len(blocks) + 1
        for image in image_items:
            if not image.insert_recommended:
                continue
            if image.evidence_id in used_ids:
                continue
            blocks.append(
                StructuredBlock(
                    block_id=f"{section_id}-b{next_index}",
                    block_type="image",
                    text=image.caption or image.alt_text,
                    content_md=image.caption or image.alt_text,
                    image_evidence_ids=[image.evidence_id],
                    image_id=image.image_id,
                    image_path=image.image_path,
                    caption_md=image.caption,
                    alt_text=image.alt_text,
                    claims=[f"Image relevance score {image.score:.2f}"],
                )
            )
            next_index += 1
        return blocks
