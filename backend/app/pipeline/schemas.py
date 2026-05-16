from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobStatusValue(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    NEEDS_REVIEW = "needs_review"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class GateName(str, Enum):
    TEMPLATE = "template_review"
    EVIDENCE = "evidence_review"
    DRAFT = "draft_review"


class GenerationProfile(BaseModel):
    language: str = "zh-TW"
    tone: str = "professional"
    verbosity: str = "balanced"
    include_reference_cases: bool = True
    preserve_vendor_terminology: bool = True
    prioritize_safety: bool = True


class ReviewSettings(BaseModel):
    template_review_enabled: bool = True
    evidence_review_enabled: bool = True
    draft_review_enabled: bool = True


class ModelConfig(BaseModel):
    llm_model_id: Optional[str] = None


class ReviewDecision(BaseModel):
    gate: GateName
    status: str = "approved"
    auto_approved: bool = False
    global_feedback: str = ""
    per_section_feedback: Dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobStatus(BaseModel):
    job_id: str
    status: JobStatusValue
    owner_id: Optional[str] = None
    current_step: str = ""
    progress: float = 0.0
    message: Optional[str] = None
    error: Optional[str] = None
    review_settings: ReviewSettings = Field(default_factory=ReviewSettings)
    pending_gates: List[GateName] = Field(default_factory=list)


class JobLogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = "info"
    step: str
    message: str
    technical_detail: Optional[str] = None


class UploadedFiles(BaseModel):
    source_files: List[str] = Field(default_factory=list)
    reference_files: List[str] = Field(default_factory=list)
    template_file: Optional[str] = None


class TemplateBlock(BaseModel):
    block_id: str
    text: str
    style_name: Optional[str] = None
    source_type: str = "paragraph"
    order_index: int = 0
    metadata: Dict[str, str] = Field(default_factory=dict)


class TemplateSectionCandidate(BaseModel):
    candidate_id: str
    title: str
    level: int = 1
    source_block_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    detector: str = "rules"
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)


class TemplateSection(BaseModel):
    section_id: str
    title: str
    level: int = 1
    start_block_index: int = 0
    end_block_index: Optional[int] = None
    existing_text: str = ""
    style_name: Optional[str] = None
    source_block_ids: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    operation: str = "keep"
    reason: str = ""
    warnings: List[str] = Field(default_factory=list)


class TemplateRefinementSuggestion(BaseModel):
    operation: str
    title: str = ""
    target_section_id: Optional[str] = None
    reason: str = ""


class TemplateStructure(BaseModel):
    template_id: str
    file_name: str
    sections: List[TemplateSection]
    warnings: List[str] = Field(default_factory=list)
    refinement_suggestions: List[TemplateRefinementSuggestion] = Field(default_factory=list)
    blocks: List[TemplateBlock] = Field(default_factory=list)
    candidates: List[TemplateSectionCandidate] = Field(default_factory=list)
    resolution_id: str = ""
    refinement_mode: str = "rules"
    feedback_intent: str = "guidance"
    feedback: str = ""


class TemplateRefineRequest(BaseModel):
    feedback: str = ""


class SourceChunk(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    content: str
    embedding_text: Optional[str] = None
    summary: str = ""
    metadata: Dict[str, str] = Field(default_factory=dict)


class SourceDocument(BaseModel):
    document_id: str
    file_name: str
    raw_text: str
    chunks: List[SourceChunk]
    metadata: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ReferenceItem(BaseModel):
    item_id: str
    document_id: str
    file_name: str
    item_type: str
    content: str
    embedding_text: Optional[str] = None
    summary: str = ""
    location: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)


class ReferenceDocument(BaseModel):
    document_id: str
    file_name: str
    file_type: str
    items: List[ReferenceItem]
    metadata: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    evidence_id: str
    document_id: str
    file_name: str
    evidence_type: str
    location: Optional[str] = None
    summary: str
    excerpt: str
    score: float
    reason: str


class SectionEvidence(BaseModel):
    section_id: str
    section_title: str
    source_chunks: List[EvidenceRef] = Field(default_factory=list)
    reference_items: List[EvidenceRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DomainTermSuggestion(BaseModel):
    term: str
    category: str = "domain"
    confidence: float = 0.0
    reason: str = ""
    source_locations: List[str] = Field(default_factory=list)
    suggested_scope: str = "temporary"


class RetrievalMetadata(BaseModel):
    retrieval_mode: str = "dense_sparse_rrf"
    chunk_method: str = "vanilla"
    sparse_backend: str = "bm25s"
    tokenizer: str = "auto"
    script_normalization: str = "dual"
    domain_token_extraction: bool = True
    temporary_domain_terms: List[str] = Field(default_factory=list)
    source_top_k: int = 6
    reference_top_k: int = 5
    reference_prefilter_limit: int = 80
    rrf_k: int = 60
    sparse_fallback: bool = False
    section_resolution_id: str = ""
    section_queries: Dict[str, str] = Field(default_factory=dict)
    tokenization_report: Dict = Field(default_factory=dict)
    domain_term_suggestions: List[DomainTermSuggestion] = Field(default_factory=list)


class EvidencePlan(BaseModel):
    job_id: str
    template: TemplateStructure
    sections: List[SectionEvidence]
    warnings: List[str] = Field(default_factory=list)
    retrieval_metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)


class StructuredBlock(BaseModel):
    block_id: str
    block_type: str = "paragraph"
    text: str
    source_chunk_ids: List[str] = Field(default_factory=list)
    reference_item_ids: List[str] = Field(default_factory=list)
    claims: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class StructuredSectionDraft(BaseModel):
    section_id: str
    title: str
    blocks: List[StructuredBlock]
    warnings: List[str] = Field(default_factory=list)


class GenerationResult(BaseModel):
    job_id: str
    sections: List[StructuredSectionDraft]
    warnings: List[str] = Field(default_factory=list)


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    review_settings: ReviewSettings = Field(default_factory=ReviewSettings)
    generation_profile: GenerationProfile = Field(default_factory=GenerationProfile)
    model_selection: ModelConfig = Field(default_factory=ModelConfig, alias="model_config")


class GenerateRequest(BaseModel):
    generation_profile: GenerationProfile = Field(default_factory=GenerationProfile)
    global_feedback: str = ""


class RegenerateSectionRequest(BaseModel):
    feedback: str
    generation_profile: GenerationProfile = Field(default_factory=GenerationProfile)


class ReviewDecisionRequest(BaseModel):
    global_feedback: str = ""
    per_section_feedback: Dict[str, str] = Field(default_factory=dict)


def model_to_dict(model: BaseModel) -> Dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()
