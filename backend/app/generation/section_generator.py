from typing import Dict, List

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

        if section.source_chunks:
            source_text = " ".join(item.summary for item in section.source_chunks[:3])
            blocks.append(
                StructuredBlock(
                    block_id=f"{section.section_id}-p1",
                    text=self._compose_source_paragraph(section.section_title, source_text, profile),
                    source_chunk_ids=[item.evidence_id for item in section.source_chunks[:3]],
                    claims=[f"Section content is grounded in {len(section.source_chunks[:3])} source evidence item(s)."],
                )
            )
        else:
            warnings.append("Generated concise placeholder because no source evidence was mapped.")
            blocks.append(
                StructuredBlock(
                    block_id=f"{section.section_id}-p1",
                    text=self._placeholder_paragraph(profile),
                    warnings=["No source evidence mapped."],
                )
            )

        if profile.include_reference_cases and section.reference_items:
            reference_text = " ".join(item.summary for item in section.reference_items[:2])
            feedback_hint = self._feedback_hint(profile, global_feedback, section_feedback, regeneration_feedback)
            blocks.append(
                StructuredBlock(
                    block_id=f"{section.section_id}-p2",
                    text=self._reference_paragraph(reference_text, feedback_hint, profile),
                    reference_item_ids=[item.evidence_id for item in section.reference_items[:2]],
                    claims=["Reference-derived field experience supplements source coverage gaps."],
                )
            )

        if global_feedback or section_feedback or regeneration_feedback:
            blocks.append(
                StructuredBlock(
                    block_id=f"{section.section_id}-feedback",
                    text=self._feedback_paragraph(profile, global_feedback, section_feedback, regeneration_feedback),
                    warnings=["Reviewer feedback was applied as generation guidance."],
                )
            )

        return StructuredSectionDraft(
            section_id=section.section_id,
            title=section.section_title,
            blocks=blocks,
            warnings=warnings,
        )

    def _compose_source_paragraph(
        self, section_title: str, source_text: str, profile: GenerationProfile
    ) -> str:
        if profile.language == "en":
            if profile.verbosity == "concise":
                return f"Source-based SOP points for \"{section_title}\": {source_text}"
            if profile.verbosity == "detailed":
                return f"This section drafts the operating requirements, limits, and cautions for \"{section_title}\" from the mapped source evidence. Responsible engineers should verify these points before release: {source_text}"
            return f"Main source-based SOP draft content for \"{section_title}\": {source_text}"
        if profile.language == "zh-CN":
            prefix = "本章节依据原厂/来源文件整理"
            if profile.verbosity == "concise":
                return f"{prefix}「{section_title}」重点：{source_text}"
            if profile.verbosity == "detailed":
                return f"{prefix}「{section_title}」的作业要求、限制与注意事项。建议依序检查以下来源重点，并在正式发布前由负责工程师确认：{source_text}"
            return f"{prefix}「{section_title}」的主要 SOP 草稿内容：{source_text}"
        prefix = "本章節依據原廠/來源文件整理"
        if profile.verbosity == "concise":
            return f"{prefix}「{section_title}」重點：{source_text}"
        if profile.verbosity == "detailed":
            return f"{prefix}「{section_title}」的作業要求、限制與注意事項。建議依序檢查以下來源重點，並在正式發布前由負責工程師確認：{source_text}"
        return f"{prefix}「{section_title}」的主要 SOP 草稿內容：{source_text}"

    def _feedback_hint(self, profile: GenerationProfile, *values: str) -> str:
        joined = " ".join(value.strip() for value in values if value.strip())
        if not joined:
            return ""
        if profile.language == "en":
            return f" Reviewer feedback to consider: {joined}"
        if profile.language == "zh-CN":
            return f" 审核反馈要求注意：{joined}"
        return f" 審核回饋要求注意：{joined}"

    def _feedback_paragraph(
        self,
        profile: GenerationProfile,
        global_feedback: str,
        section_feedback: str,
        regeneration_feedback: str,
    ) -> str:
        parts = []
        if profile.language == "en":
            if global_feedback:
                parts.append(f"Global review guidance: {global_feedback}")
            if section_feedback:
                parts.append(f"Section review guidance: {section_feedback}")
            if regeneration_feedback:
                parts.append(f"Regeneration feedback: {regeneration_feedback}")
            return "; ".join(parts)
        if profile.language == "zh-CN":
            if global_feedback:
                parts.append(f"全域审核指示：{global_feedback}")
            if section_feedback:
                parts.append(f"章节审核指示：{section_feedback}")
            if regeneration_feedback:
                parts.append(f"重新生成反馈：{regeneration_feedback}")
            return "；".join(parts)
        if global_feedback:
            parts.append(f"全域審核指示：{global_feedback}")
        if section_feedback:
            parts.append(f"章節審核指示：{section_feedback}")
        if regeneration_feedback:
            parts.append(f"重新生成回饋：{regeneration_feedback}")
        return "；".join(parts)

    def _reference_paragraph(self, reference_text: str, feedback_hint: str, profile: GenerationProfile) -> str:
        if profile.language == "en":
            return f"Supplementary field repair experience may be included: {reference_text}{feedback_hint}"
        if profile.language == "zh-CN":
            return f"可纳入过往维修经验作为补充：{reference_text}{feedback_hint}"
        return f"可納入過往維修經驗作為補充：{reference_text}{feedback_hint}"

    def _placeholder_paragraph(self, profile: GenerationProfile) -> str:
        if profile.language == "en":
            return "This section does not yet have enough source evidence to form a complete SOP draft. Reviewers should add source material or adjust the template section."
        if profile.language == "zh-CN":
            return "本章节目前没有足够的原厂来源内容可形成完整 SOP 草稿，建议审核者补充来源或调整模板章节。"
        return "本章節目前沒有足夠的原廠來源內容可形成完整 SOP 草稿，建議審核者補充來源或調整模板章節。"
