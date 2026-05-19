from pathlib import Path
from typing import Dict, List

from backend.app.core.config import AppConfig, ProviderConfig
from backend.app.core.model_catalog import ModelCatalog
from backend.app.generation.section_generator import SectionGenerator
from backend.app.indexing.domain_terms import DomainTermSuggester
from backend.app.indexing.embedding import EmbeddingClient
from backend.app.indexing.tokenizer import TokenizerConfig
from backend.app.ingestion.contextualizer import Contextualizer
from backend.app.ingestion.ocr_client import OcrClient
from backend.app.ingestion.document_loaders import (
    load_reference_file,
    load_source_pdf,
    load_template_docx,
)
from backend.app.ingestion.section_refiner import SectionRefiner
from backend.app.pipeline.schemas import (
    EvidencePlan,
    GateName,
    GenerationProfile,
    GenerationResult,
    JobStatus,
    JobStatusValue,
    ModelConfig,
    ReferenceDocument,
    ReviewDecision,
    ReviewDecisionRequest,
    ReviewSettings,
    SourceDocument,
    StructuredSectionDraft,
    TemplateStructure,
    DomainTermSuggestion,
    UploadedFiles,
)
from backend.app.planning.evidence_planner import EvidencePlanner
from backend.app.rendering.docx_renderer import DocxRenderer
from backend.app.reports.report_builders import (
    build_coverage_report,
    build_debug_report,
    build_provenance_report,
)
from backend.app.services.artifact_service import ArtifactService


class JobService:
    def __init__(self, config: AppConfig, artifacts: ArtifactService):
        self.config = config
        self.artifacts = artifacts
        self.model_catalog = ModelCatalog(config.models_path)
        self.generator = SectionGenerator()
        self.renderer = DocxRenderer()

    def analyze(self, job_id: str) -> TemplateStructure:
        self.artifacts.update_status(
            job_id,
            JobStatusValue.ANALYZING,
            "parse_inputs",
            0.15,
            "Parsing uploaded files.",
            pending_gates=[],
        )
        self._reset_analysis_dependents(job_id)
        uploaded = self.artifacts.read_json(job_id, "uploaded_files", UploadedFiles)
        if not uploaded.source_files:
            raise ValueError("At least one source PDF is required.")
        if not uploaded.template_file:
            raise ValueError("A DOCX template is required.")

        ocr_client = OcrClient(self._provider_config(job_id, "ocr"))
        llm_config = self._provider_config(job_id, "llm")
        contextualizer = Contextualizer(llm_config) if self.config.chunk_method in {"contextual", "anthropic"} else None
        source_docs = []
        for index, path in enumerate(uploaded.source_files):
            self.artifacts.update_status(
                job_id,
                JobStatusValue.ANALYZING,
                "parse_source",
                0.18 + 0.08 * (index / max(len(uploaded.source_files), 1)),
                f"Parsing source file {index + 1}/{len(uploaded.source_files)}: {Path(path).name}",
            )
            source_docs.append(
                load_source_pdf(
                    path,
                    ocr_client=ocr_client,
                    chunk_size=self.config.chunk_size,
                    chunk_overlap=self.config.chunk_overlap,
                    chunk_method=self.config.chunk_method,
                    contextualizer=contextualizer,
                )
            )
        reference_docs = []
        for index, path in enumerate(uploaded.reference_files):
            self.artifacts.update_status(
                job_id,
                JobStatusValue.ANALYZING,
                "parse_reference",
                0.28 + 0.14 * (index / max(len(uploaded.reference_files), 1)),
                f"Parsing reference file {index + 1}/{len(uploaded.reference_files)}: {Path(path).name}",
            )
            reference_docs.append(
                load_reference_file(
                    path,
                    ocr_client=ocr_client,
                    chunk_size=self.config.chunk_size,
                    chunk_overlap=self.config.chunk_overlap,
                    chunk_method=self.config.chunk_method,
                    contextualizer=contextualizer,
                )
            )
        self.artifacts.update_status(
            job_id,
            JobStatusValue.ANALYZING,
            "parse_template",
            0.44,
            f"Parsing template: {Path(uploaded.template_file).name}",
        )
        template = load_template_docx(
            uploaded.template_file,
            section_detection_mode=self.config.section_detection_mode,
            section_refiner=SectionRefiner(llm_config),
        )
        self.artifacts.write_json(job_id, "source_docs", {"documents": source_docs})
        self.artifacts.write_json(job_id, "reference_docs", {"documents": reference_docs})
        self.artifacts.write_json(job_id, "template_structure", template)
        self.artifacts.write_json(job_id, "template_section_resolution", template)
        self.artifacts.write_json(job_id, "template_blocks", {"blocks": template.blocks})
        self.artifacts.write_json(job_id, "template_section_candidates", {"candidates": template.candidates})
        domain_suggestions = DomainTermSuggester(
            llm_config,
            enabled=self.config.domain_term_llm_enabled,
            confidence_threshold=self.config.domain_term_confidence_threshold,
        ).suggest(source_docs, reference_docs)
        self.artifacts.write_json(job_id, "domain_term_suggestions", {"suggestions": domain_suggestions})

        status = self.artifacts.read_json(job_id, "status", JobStatus)
        self._auto_or_pending(job_id, status.review_settings, [GateName.TEMPLATE])
        template_pending = self._pending_template_gate(job_id, status.review_settings)
        if template_pending:
            self.artifacts.update_status(
                job_id,
                JobStatusValue.NEEDS_REVIEW,
                "template_review_ready",
                0.55,
                "Template section proposal is ready for review.",
                pending_gates=template_pending,
            )
            return template

        self.artifacts.write_json(job_id, "approved_template_sections", template)
        self._build_and_store_evidence_plan(job_id, template)
        self._auto_or_pending(job_id, status.review_settings, [GateName.EVIDENCE])
        pending = self._pending_pre_generation_gates(job_id, status.review_settings)
        self.artifacts.update_status(
            job_id,
            JobStatusValue.NEEDS_REVIEW if pending else JobStatusValue.UPLOADED,
            "analysis_ready",
            0.65,
            "Analysis and evidence planning completed.",
            pending_gates=pending,
        )
        return template

    def refine_template_sections(self, job_id: str, feedback: str) -> TemplateStructure:
        template = self.artifacts.read_json(job_id, "template_structure", TemplateStructure)
        refined = SectionRefiner(self._provider_config(job_id, "llm")).refine(
            file_name=template.file_name,
            template_id=template.template_id,
            blocks=template.blocks,
            candidates=template.candidates,
            fallback_sections=template.sections,
            feedback=feedback,
        )
        self.artifacts.write_json(job_id, "template_structure", refined)
        self.artifacts.write_json(job_id, "template_section_resolution", refined)
        self.artifacts.delete_artifacts(
            job_id,
            [
                "review_template_review",
                "review_evidence_review",
                "review_draft_review",
                "evidence_plan",
                "generated_sections",
                "coverage_report",
                "provenance_report",
                "debug_report",
                "tokenization_report",
                "final_sop.docx",
            ],
        )
        self.artifacts.append_log(
            job_id,
            "template_refinement",
            "Template section proposal refined from reviewer feedback.",
            f"feedback_intent={refined.feedback_intent}; mode={refined.refinement_mode}",
        )
        status = self.artifacts.read_json(job_id, "status", JobStatus)
        pending = self._pending_template_gate(job_id, status.review_settings)
        self.artifacts.update_status(
            job_id,
            JobStatusValue.NEEDS_REVIEW if pending else JobStatusValue.UPLOADED,
            "template_review_ready",
            0.55,
            "Template section proposal was updated from feedback.",
            pending_gates=pending,
        )
        return refined

    def replan_evidence(self, job_id: str, request: ReviewDecisionRequest) -> EvidencePlan:
        if self.artifacts.maybe_read_json(job_id, "review_evidence_review"):
            raise PermissionError("Evidence plan is already approved; re-plan requires reopening review.")
        template = (
            self.artifacts.maybe_read_json(job_id, "approved_template_sections", TemplateStructure)
            or self.artifacts.read_json(job_id, "template_section_resolution", TemplateStructure)
        )
        self.artifacts.delete_artifacts(
            job_id,
            [
                "review_evidence_review",
                "review_draft_review",
                "generated_sections",
                "coverage_report",
                "provenance_report",
                "debug_report",
                "tokenization_report",
                "final_sop.docx",
            ],
        )
        self.artifacts.append_log(
            job_id,
            "evidence_replan",
            "Evidence plan replanned from reviewer feedback.",
            f"global_feedback={request.global_feedback}; per_section={request.per_section_feedback}",
        )
        evidence_plan = self._build_and_store_evidence_plan(
            job_id,
            template,
            global_feedback=request.global_feedback,
            section_feedback=request.per_section_feedback,
        )
        status = self.artifacts.read_json(job_id, "status", JobStatus)
        pending = self._pending_pre_generation_gates(job_id, status.review_settings)
        self.artifacts.update_status(
            job_id,
            JobStatusValue.NEEDS_REVIEW if pending else JobStatusValue.UPLOADED,
            "analysis_ready",
            0.65,
            "Evidence plan was updated from feedback.",
            pending_gates=pending,
        )
        return evidence_plan

    def approve_gate(self, job_id: str, gate: GateName, request: ReviewDecisionRequest) -> ReviewDecision:
        decision = ReviewDecision(
            gate=gate,
            auto_approved=False,
            global_feedback=request.global_feedback,
            per_section_feedback=request.per_section_feedback,
        )
        status = self.artifacts.read_json(job_id, "status", JobStatus)
        if gate == GateName.TEMPLATE:
            self.artifacts.write_json(job_id, f"review_{gate.value}", decision)
            self.artifacts.append_log(job_id, gate.value, "Template section proposal approved by user.")
            template = self.artifacts.read_json(job_id, "template_section_resolution", TemplateStructure)
            self.artifacts.write_json(job_id, "approved_template_sections", template)
            self.artifacts.delete_artifacts(
                job_id,
                [
                    "review_evidence_review",
                    "review_draft_review",
                    "evidence_plan",
                    "generated_sections",
                    "coverage_report",
                    "provenance_report",
                    "debug_report",
                    "tokenization_report",
                    "final_sop.docx",
                ],
            )
            self._build_and_store_evidence_plan(job_id, template)
            self._auto_or_pending(job_id, status.review_settings, [GateName.EVIDENCE])
            pending = self._pending_pre_generation_gates(job_id, status.review_settings)
            status_value = JobStatusValue.NEEDS_REVIEW if pending else JobStatusValue.UPLOADED
            self.artifacts.update_status(
                job_id,
                status_value,
                "analysis_ready",
                0.65,
                "Template approved and evidence planning completed.",
                pending_gates=pending,
            )
        elif gate == GateName.EVIDENCE:
            self.artifacts.write_json(job_id, f"review_{gate.value}", decision)
            self.artifacts.append_log(job_id, gate.value, "Evidence plan approved by user.")
            pending = self._pending_pre_generation_gates(job_id, status.review_settings)
            self.artifacts.update_status(
                job_id,
                JobStatusValue.NEEDS_REVIEW if pending else JobStatusValue.UPLOADED,
                "review_updated",
                status.progress,
                "Evidence review decision recorded.",
                pending_gates=pending,
            )
        elif gate == GateName.DRAFT:
            self.artifacts.write_json(job_id, f"review_{gate.value}", decision)
            self.artifacts.append_log(job_id, gate.value, "Draft approved by user.")
            self.artifacts.update_status(
                job_id,
                JobStatusValue.COMPLETED,
                "completed",
                1.0,
                "Draft approved and final artifacts are ready.",
                pending_gates=[],
            )
        return decision

    def _build_and_store_evidence_plan(
        self,
        job_id: str,
        template: TemplateStructure,
        global_feedback: str = "",
        section_feedback: Dict[str, str] = None,
    ) -> EvidencePlan:
        source_docs = [
            SourceDocument(**document)
            for document in self.artifacts.read_json(job_id, "source_docs")["documents"]
        ]
        reference_docs = [
            ReferenceDocument(**document)
            for document in self.artifacts.read_json(job_id, "reference_docs")["documents"]
        ]
        domain_suggestions = self.artifacts.maybe_read_json(job_id, "domain_term_suggestions") or {"suggestions": []}
        parsed_suggestions = [
            suggestion if isinstance(suggestion, DomainTermSuggestion) else DomainTermSuggestion(**suggestion)
            for suggestion in domain_suggestions.get("suggestions", [])
        ]
        temporary_terms = {
            suggestion.term: max(float(suggestion.confidence), 1.0)
            for suggestion in parsed_suggestions
            if suggestion.suggested_scope in {"temporary", "candidate_permanent"}
        }
        self.artifacts.update_status(
            job_id,
            JobStatusValue.ANALYZING,
            "plan_evidence",
            0.58,
            "Planning section-level source and reference evidence.",
        )
        evidence_planner = EvidencePlanner(
            EmbeddingClient(self._provider_config(job_id, "embedding")),
            source_top_k=self.config.source_top_k,
            reference_top_k=self.config.reference_top_k,
            reference_prefilter_limit=self.config.reference_prefilter_limit,
            source_threshold=self.config.source_score_threshold,
            reference_threshold=self.config.reference_score_threshold,
            retrieval_mode=self.config.retrieval_mode,
            rrf_k=self.config.rrf_k,
            chunk_method=self.config.chunk_method,
            tokenizer_config=TokenizerConfig(
                cjk_tokenizer=self.config.cjk_tokenizer,
                script_normalization=self.config.script_normalization,
                ckiptagger_data_dir=self.config.ckiptagger_data_dir,
                domain_dict_path=self.config.domain_dict_path,
                ckiptagger_dict_mode=self.config.ckiptagger_dict_mode,
                jieba_dict_path=self.config.jieba_dict_path,
                domain_token_extraction=self.config.domain_token_extraction,
                temporary_terms=temporary_terms,
            ),
            domain_term_suggestions=parsed_suggestions,
        )
        evidence_plan = evidence_planner.build(
            job_id,
            template,
            source_docs,
            reference_docs,
            progress_callback=lambda step, message, progress: self.artifacts.update_status(
                job_id,
                JobStatusValue.ANALYZING,
                step,
                progress,
                message,
            ),
            global_feedback=global_feedback,
            section_feedback=section_feedback or {},
        )
        self.artifacts.write_json(job_id, "evidence_plan", evidence_plan)
        self.artifacts.write_json(
            job_id,
            "tokenization_report",
            evidence_plan.retrieval_metadata.tokenization_report,
        )
        return evidence_plan

    def generate(self, job_id: str, profile: GenerationProfile, global_feedback: str = "") -> GenerationResult:
        status = self.artifacts.read_json(job_id, "status", JobStatus)
        missing = self._pending_pre_generation_gates(job_id, status.review_settings)
        if missing:
            raise PermissionError(f"Pending review gates must be approved before generation: {missing}")
        evidence_plan = self.artifacts.read_json(job_id, "evidence_plan", EvidencePlan)
        self.artifacts.update_status(
            job_id, JobStatusValue.GENERATING, "generate_sections", 0.75, "Generating structured section drafts."
        )
        feedback = self._collect_feedback(job_id)
        llm_config = self._provider_config(job_id, "llm")
        sections = [
            self.generator.generate(
                section,
                profile,
                global_feedback=" ".join([global_feedback, feedback["global"]]).strip(),
                section_feedback=feedback["per_section"].get(section.section_id, ""),
                llm_config=llm_config,
            )
            for section in evidence_plan.sections
        ]
        generation = GenerationResult(job_id=job_id, sections=sections)
        self._persist_generation(job_id, evidence_plan, generation)
        self._auto_or_pending(job_id, status.review_settings, [GateName.DRAFT])
        pending = self._pending_draft_gate(job_id, status.review_settings)
        self.artifacts.update_status(
            job_id,
            JobStatusValue.NEEDS_REVIEW,
            "draft_ready",
            0.95,
            "Draft generated. Review is pending." if pending else "Draft generated. Draft review was auto-approved.",
            pending_gates=pending,
        )
        return generation

    def regenerate_section(
        self, job_id: str, section_id: str, feedback: str, profile: GenerationProfile
    ) -> GenerationResult:
        evidence_plan = self.artifacts.read_json(job_id, "evidence_plan", EvidencePlan)
        generation = self.artifacts.read_json(job_id, "generated_sections", GenerationResult)
        feedback_bundle = self._collect_feedback(job_id)
        target = next((section for section in evidence_plan.sections if section.section_id == section_id), None)
        if not target:
            raise ValueError(f"Unknown section_id: {section_id}")
        llm_config = self._provider_config(job_id, "llm")
        regenerated = self.generator.generate(
            target,
            profile,
            global_feedback=feedback_bundle["global"],
            section_feedback=feedback_bundle["per_section"].get(section_id, ""),
            regeneration_feedback=feedback,
            llm_config=llm_config,
        )
        updated_sections: List[StructuredSectionDraft] = [
            regenerated if section.section_id == section_id else section for section in generation.sections
        ]
        updated = GenerationResult(job_id=job_id, sections=updated_sections, warnings=generation.warnings)
        self.artifacts.append_log(job_id, "regenerate_section", f"Regenerated section {section_id}.")
        self._persist_generation(job_id, evidence_plan, updated)
        return updated

    def _persist_generation(
        self, job_id: str, evidence_plan: EvidencePlan, generation: GenerationResult
    ) -> None:
        self.artifacts.write_json(job_id, "generated_sections", generation)
        coverage = build_coverage_report(evidence_plan, generation)
        provenance = build_provenance_report(evidence_plan, generation)
        self.artifacts.write_json(job_id, "coverage_report", coverage)
        self.artifacts.write_json(job_id, "provenance_report", provenance)
        template_path = self.artifacts.read_json(job_id, "uploaded_files", UploadedFiles).template_file
        output = self.artifacts.job_dir(job_id) / "outputs" / "final_sop.docx"
        self.renderer.render(template_path, evidence_plan.template, generation, output)
        debug = build_debug_report(
            job_id,
            self.artifacts.read_json(job_id, "status"),
            self.artifacts.read_logs(job_id),
            self.artifacts.list_artifacts(job_id),
        )
        self.artifacts.write_json(job_id, "debug_report", debug)

    def _reset_analysis_dependents(self, job_id: str) -> None:
        self.artifacts.delete_artifacts(
            job_id,
            [
                "source_docs",
                "reference_docs",
                "template_structure",
                "template_section_resolution",
                "template_blocks",
                "template_section_candidates",
                "approved_template_sections",
                "evidence_plan",
                "review_template_review",
                "review_evidence_review",
                "review_draft_review",
                "generated_sections",
                "coverage_report",
                "provenance_report",
                "debug_report",
                "tokenization_report",
                "domain_term_suggestions",
                "final_sop.docx",
            ],
        )
        self.artifacts.append_log(
            job_id,
            "reset_analysis_dependents",
            "Cleared previous review decisions, draft, reports, and DOCX before re-analysis.",
        )

    def _auto_or_pending(self, job_id: str, settings: ReviewSettings, gates: List[GateName]) -> None:
        enabled = {
            GateName.TEMPLATE: settings.template_review_enabled,
            GateName.EVIDENCE: settings.evidence_review_enabled,
            GateName.DRAFT: settings.draft_review_enabled,
        }
        for gate in gates:
            if enabled[gate]:
                continue
            name = f"review_{gate.value}"
            if self.artifacts.maybe_read_json(job_id, name):
                continue
            decision = ReviewDecision(gate=gate, auto_approved=True)
            self.artifacts.write_json(job_id, name, decision)
            self.artifacts.append_log(job_id, gate.value, "Review gate auto-approved because it is disabled.")

    def _pending_pre_generation_gates(self, job_id: str, settings: ReviewSettings) -> List[GateName]:
        pending = []
        if settings.template_review_enabled and not self.artifacts.maybe_read_json(job_id, "review_template_review"):
            pending.append(GateName.TEMPLATE)
        has_evidence_plan = self.artifacts.maybe_read_json(job_id, "evidence_plan") is not None
        if (
            settings.evidence_review_enabled
            and has_evidence_plan
            and not self.artifacts.maybe_read_json(job_id, "review_evidence_review")
        ):
            pending.append(GateName.EVIDENCE)
        return pending

    def _pending_template_gate(self, job_id: str, settings: ReviewSettings) -> List[GateName]:
        if settings.template_review_enabled and not self.artifacts.maybe_read_json(job_id, "review_template_review"):
            return [GateName.TEMPLATE]
        return []

    def _pending_draft_gate(self, job_id: str, settings: ReviewSettings) -> List[GateName]:
        if settings.draft_review_enabled and not self.artifacts.maybe_read_json(job_id, "review_draft_review"):
            return [GateName.DRAFT]
        return []

    def _collect_feedback(self, job_id: str) -> Dict:
        global_parts: List[str] = []
        per_section: Dict[str, str] = {}
        for gate in [GateName.TEMPLATE, GateName.EVIDENCE, GateName.DRAFT]:
            decision = self.artifacts.maybe_read_json(job_id, f"review_{gate.value}", ReviewDecision)
            if not decision:
                continue
            if decision.global_feedback:
                global_parts.append(decision.global_feedback)
            for section_id, feedback in decision.per_section_feedback.items():
                per_section[section_id] = " ".join([per_section.get(section_id, ""), feedback]).strip()
        return {"global": " ".join(global_parts), "per_section": per_section}

    def _provider_config(self, job_id: str, provider: str) -> ProviderConfig:
        model_config = self.artifacts.maybe_read_json(job_id, "model_config", ModelConfig) or ModelConfig()
        if provider == "llm":
            try:
                return self.model_catalog.resolve_llm(model_config.llm_model_id)
            except ValueError:
                return self.config.llm
        return {
            "embedding": self.config.embedding,
            "ocr": self.config.ocr,
        }[provider]
