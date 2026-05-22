from typing import Dict, List

from backend.app.pipeline.schemas import EvidencePlan, GenerationResult, StructuredBlock, StructuredListItem


def build_coverage_report(evidence_plan: EvidencePlan, generation: GenerationResult) -> Dict:
    mapped = {}
    section_to_chunks = {}
    section_to_images = {}
    for section in evidence_plan.sections:
        ids = [item.evidence_id for item in section.source_chunks]
        section_to_chunks[section.section_id] = ids
        section_to_images[section.section_id] = [item.evidence_id for item in section.image_items]
        for chunk_id in ids:
            mapped[chunk_id] = section.section_id
    used = {}
    used_images = {}
    for section in generation.sections:
        used[section.section_id] = sorted({chunk_id for block in section.blocks for chunk_id in _source_ids(block)})
        used_images[section.section_id] = sorted({image_id for block in section.blocks for image_id in block.image_evidence_ids})
    return {
        "job_id": evidence_plan.job_id,
        "mapped_source_chunks": mapped,
        "section_to_source_chunks": section_to_chunks,
        "section_to_image_items": section_to_images,
        "generated_section_to_used_source_chunks": used,
        "generated_section_to_used_image_items": used_images,
        "warnings": evidence_plan.warnings + generation.warnings,
    }


def build_provenance_report(evidence_plan: EvidencePlan, generation: GenerationResult) -> Dict:
    evidence_lookup = {}
    for section in evidence_plan.sections:
        for evidence in section.source_chunks + section.reference_items + section.image_items:
            evidence_lookup[evidence.evidence_id] = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence.dict()
    sections: List[Dict] = []
    for section in generation.sections:
        sections.append(
            {
                "section_id": section.section_id,
                "title": section.title,
                "blocks": [
                    {
                        "block_id": entry["block_id"],
                        "text": entry["text"],
                        "source_evidence_ids": entry["source_chunk_ids"],
                        "reference_evidence_ids": entry["reference_item_ids"],
                        "image_evidence_ids": entry["image_evidence_ids"],
                        "source_evidence": [evidence_lookup.get(item) for item in entry["source_chunk_ids"]],
                        "reference_evidence": [evidence_lookup.get(item) for item in entry["reference_item_ids"]],
                        "image_evidence": [evidence_lookup.get(item) for item in entry["image_evidence_ids"]],
                        "claims": entry["claims"],
                        "warnings": entry["warnings"],
                    }
                    for block in section.blocks
                    for entry in _provenance_entries(block)
                ],
                "warnings": section.warnings,
            }
        )
    return {"job_id": generation.job_id, "sections": sections, "warnings": generation.warnings}


def build_debug_report(job_id: str, status: Dict, logs: List[Dict], artifacts: Dict) -> Dict:
    return {
        "job_id": job_id,
        "status": status,
        "logs": logs,
        "artifacts": artifacts,
    }


def _source_ids(block: StructuredBlock) -> List[str]:
    ids = list(block.source_chunk_ids)
    for item in block.items:
        ids.extend(_item_source_ids(item))
    return ids


def _item_source_ids(item: StructuredListItem) -> List[str]:
    ids = list(item.source_chunk_ids)
    for child in item.items:
        ids.extend(_item_source_ids(child))
    return ids


def _provenance_entries(block: StructuredBlock) -> List[Dict]:
    if block.block_type in {"bullet", "bullet_list", "numbered", "numbered_list"} and block.items:
        return [
            entry
            for index, item in enumerate(block.items, start=1)
            for entry in _list_item_entries(block.block_id, item, f"{index}")
        ]
    if block.block_type == "table":
        entries = []
        for index, row in enumerate(block.rows, start=1):
            entries.append(
                {
                    "block_id": f"{block.block_id}-r{index}",
                    "text": " | ".join(row),
                    "source_chunk_ids": block.source_chunk_ids,
                    "reference_item_ids": block.reference_item_ids,
                    "image_evidence_ids": block.image_evidence_ids,
                    "claims": block.claims,
                    "warnings": block.warnings,
                }
            )
        return entries
    return [
        {
            "block_id": block.block_id,
            "text": block.content_md or block.text,
            "source_chunk_ids": block.source_chunk_ids,
            "reference_item_ids": block.reference_item_ids,
            "image_evidence_ids": block.image_evidence_ids,
            "claims": block.claims,
            "warnings": block.warnings,
        }
    ]


def _list_item_entries(prefix: str, item: StructuredListItem, path: str) -> List[Dict]:
    entry = {
        "block_id": f"{prefix}-i{path}",
        "text": item.content_md or item.text,
        "source_chunk_ids": item.source_chunk_ids,
        "reference_item_ids": item.reference_item_ids,
        "image_evidence_ids": [],
        "claims": [],
        "warnings": [],
    }
    child_entries = [
        child_entry
        for index, child in enumerate(item.items, start=1)
        for child_entry in _list_item_entries(prefix, child, f"{path}-{index}")
    ]
    return [entry] + child_entries
