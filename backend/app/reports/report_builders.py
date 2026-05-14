from typing import Dict, List

from backend.app.pipeline.schemas import EvidencePlan, GenerationResult


def build_coverage_report(evidence_plan: EvidencePlan, generation: GenerationResult) -> Dict:
    mapped = {}
    section_to_chunks = {}
    for section in evidence_plan.sections:
        ids = [item.evidence_id for item in section.source_chunks]
        section_to_chunks[section.section_id] = ids
        for chunk_id in ids:
            mapped[chunk_id] = section.section_id
    used = {}
    for section in generation.sections:
        used[section.section_id] = sorted(
            {chunk_id for block in section.blocks for chunk_id in block.source_chunk_ids}
        )
    return {
        "job_id": evidence_plan.job_id,
        "mapped_source_chunks": mapped,
        "section_to_source_chunks": section_to_chunks,
        "generated_section_to_used_source_chunks": used,
        "warnings": evidence_plan.warnings + generation.warnings,
    }


def build_provenance_report(evidence_plan: EvidencePlan, generation: GenerationResult) -> Dict:
    evidence_lookup = {}
    for section in evidence_plan.sections:
        for evidence in section.source_chunks + section.reference_items:
            evidence_lookup[evidence.evidence_id] = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence.dict()
    sections: List[Dict] = []
    for section in generation.sections:
        sections.append(
            {
                "section_id": section.section_id,
                "title": section.title,
                "blocks": [
                    {
                        "block_id": block.block_id,
                        "text": block.text,
                        "source_evidence": [evidence_lookup.get(item) for item in block.source_chunk_ids],
                        "reference_evidence": [evidence_lookup.get(item) for item in block.reference_item_ids],
                        "claims": block.claims,
                        "warnings": block.warnings,
                    }
                    for block in section.blocks
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
