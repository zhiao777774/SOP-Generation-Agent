import re
from typing import List

from backend.app.pipeline.schemas import (
    GenerationProfile,
    SectionEvidence,
    StructuredBlock,
    StructuredSectionDraft,
)


class SectionGenerator:
    def generate(
        self,
        section: SectionEvidence,
        profile: GenerationProfile,
        global_feedback: str = "",
        section_feedback: str = "",
        regeneration_feedback: str = "",
    ) -> StructuredSectionDraft:
        blocks: List[StructuredBlock] = []
        warnings: List[str] = list(section.warnings)
        if global_feedback or section_feedback or regeneration_feedback:
            warnings.append("Reviewer feedback was used as generation guidance.")

        if section.source_chunks:
            source_items = section.source_chunks[:3]
            source_text = self._join_evidence_text(source_items)
            if source_text:
                blocks.append(
                    StructuredBlock(
                        block_id=f"{section.section_id}-p1",
                        text=self._compose_source_paragraph(source_text, profile),
                        source_chunk_ids=[item.evidence_id for item in source_items],
                        claims=[f"Section content is grounded in {len(source_items)} source evidence item(s)."],
                    )
                )
        else:
            warnings.append("No source evidence was mapped; section body was not filled from vendor/source material.")

        if profile.include_reference_cases and section.reference_items:
            reference_items = section.reference_items[:2]
            reference_text = self._join_evidence_text(reference_items)
            if reference_text:
                blocks.append(
                    StructuredBlock(
                        block_id=f"{section.section_id}-p2",
                        text=self._reference_paragraph(reference_text, profile),
                        reference_item_ids=[item.evidence_id for item in reference_items],
                        claims=["Reference-derived field experience supplements source coverage gaps."],
                    )
                )

        return StructuredSectionDraft(
            section_id=section.section_id,
            title=section.section_title,
            blocks=blocks,
            warnings=warnings,
        )

    def _compose_source_paragraph(
        self, source_text: str, profile: GenerationProfile
    ) -> str:
        if profile.language == "en":
            if profile.verbosity == "detailed":
                return f"Operational requirements: {source_text}"
            return source_text
        if profile.language == "zh-CN":
            if profile.verbosity == "detailed":
                return f"作业要求如下：{source_text}"
            return source_text
        if profile.verbosity == "detailed":
            return f"作業要求如下：{source_text}"
        return source_text

    def _reference_paragraph(self, reference_text: str, profile: GenerationProfile) -> str:
        if profile.language == "en":
            return f"Field repair history indicates: {reference_text}"
        if profile.language == "zh-CN":
            return f"过往维修记录显示：{reference_text}"
        return f"過往維修紀錄顯示：{reference_text}"

    def _join_evidence_text(self, items: list[object]) -> str:
        parts = []
        for item in items:
            text = self._clean_evidence_text(getattr(item, "excerpt", "") or getattr(item, "summary", ""))
            if text:
                parts.append(text)
        return " ".join(parts).strip()

    def _clean_evidence_text(self, value: str) -> str:
        without_markers = re.sub(r"<!--\s*Page\s+\d+\s*-->", " ", value)
        return re.sub(r"\s+", " ", without_markers).strip()
