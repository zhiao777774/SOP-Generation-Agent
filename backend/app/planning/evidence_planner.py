from typing import Callable, List, Optional, Tuple

from backend.app.indexing.embedding import EmbeddingClient, EmbeddingUnavailable, cosine
from backend.app.indexing.sparse import BM25Index, reciprocal_rank_fusion
from backend.app.indexing.tokenizer import SparseTokenizer, TokenizerConfig
from backend.app.ingestion.chunking import summarize
from backend.app.pipeline.schemas import (
    EvidencePlan,
    EvidenceRef,
    DomainTermSuggestion,
    ReferenceDocument,
    RetrievalMetadata,
    SectionEvidence,
    SourceDocument,
    TemplateStructure,
)


class EvidencePlanner:
    def __init__(
        self,
        embedding_client: EmbeddingClient,
        source_top_k: int = 6,
        reference_top_k: int = 5,
        reference_prefilter_limit: int = 80,
        source_threshold: float = 0.08,
        reference_threshold: float = 0.05,
        retrieval_mode: str = "dense_sparse_rrf",
        rrf_k: int = 60,
        chunk_method: str = "vanilla",
        tokenizer_config: Optional[TokenizerConfig] = None,
        domain_term_suggestions: Optional[List[DomainTermSuggestion]] = None,
    ):
        self.embedding_client = embedding_client
        self.source_top_k = source_top_k
        self.reference_top_k = reference_top_k
        self.reference_prefilter_limit = reference_prefilter_limit
        self.source_threshold = source_threshold
        self.reference_threshold = reference_threshold
        self.requested_retrieval_mode = retrieval_mode
        self.rrf_k = rrf_k
        self.chunk_method = chunk_method
        self.tokenizer_config = tokenizer_config or TokenizerConfig()
        self.domain_term_suggestions = domain_term_suggestions or []
        self.actual_retrieval_mode = retrieval_mode
        self.sparse_fallback = False

    def build(
        self,
        job_id: str,
        template: TemplateStructure,
        source_documents: List[SourceDocument],
        reference_documents: List[ReferenceDocument],
        progress_callback: Optional[Callable[[str, str, float], None]] = None,
    ) -> EvidencePlan:
        source_chunks = [chunk for document in source_documents for chunk in document.chunks]
        reference_items = [item for document in reference_documents for item in document.items]
        warnings = list(template.warnings)
        for document in source_documents:
            warnings.extend(document.warnings)
        for document in reference_documents:
            warnings.extend(document.warnings)

        source_bm25 = BM25Index(
            [chunk.content for chunk in source_chunks],
            tokenizer=SparseTokenizer(self.tokenizer_config),
        )
        reference_bm25 = BM25Index(
            [item.content for item in reference_items],
            tokenizer=SparseTokenizer(self.tokenizer_config),
        )

        source_embeddings = {}
        if self.requested_retrieval_mode != "sparse_only":
            if progress_callback:
                progress_callback("embed_source", f"Embedding {len(source_chunks)} source chunks.", 0.48)
            try:
                for index, chunk in enumerate(source_chunks):
                    source_embeddings[index] = self.embedding_client.embed(_embedding_text(chunk))
                    if progress_callback and (index + 1 == len(source_chunks) or index % 10 == 0):
                        progress_callback(
                            "embed_source",
                            f"Embedded {index + 1}/{len(source_chunks)} source chunks.",
                            0.48 + 0.06 * ((index + 1) / max(len(source_chunks), 1)),
                        )
            except EmbeddingUnavailable as exc:
                self._switch_to_sparse_only(warnings, progress_callback, str(exc))

        sections: List[SectionEvidence] = []
        section_queries = {}
        for section_index, section in enumerate(template.sections):
            section_query = f"{section.title}\n{section.existing_text}".strip()
            section_queries[section.section_id] = section_query
            source_ranked = self._rank_source(section_query, source_chunks, source_bm25, source_embeddings)
            selected_sources = [
                self._source_ref(chunk, score, self._rank_reason())
                for score, chunk in source_ranked[: self.source_top_k]
                if score >= self.source_threshold
            ]
            source_context = "\n".join(ref.excerpt for ref in selected_sources)
            reference_query = f"{section_query}\n{source_context}".strip()
            candidate_indices = self._prefilter_reference_items(reference_query, reference_bm25, reference_items)
            if progress_callback:
                progress_callback(
                    "rank_reference",
                    f"Ranking {len(candidate_indices)}/{len(reference_items)} candidate reference records for {section.title}.",
                    0.55 + 0.35 * (section_index / max(len(template.sections), 1)),
                )
            reference_ranked = self._rank_reference(reference_query, reference_items, candidate_indices, reference_bm25)
            selected_refs = [
                self._reference_ref(item, score, self._rank_reason(reference=True))
                for score, item in reference_ranked[: self.reference_top_k]
                if score >= self.reference_threshold
            ]
            section_warnings = []
            if not selected_sources:
                section_warnings.append("No confident source chunk mapped to this section.")
            sections.append(
                SectionEvidence(
                    section_id=section.section_id,
                    section_title=section.title,
                    source_chunks=selected_sources,
                    reference_items=selected_refs,
                    warnings=section_warnings,
                )
            )
            if progress_callback:
                progress_callback(
                    "rank_reference",
                    f"Planned evidence for {section_index + 1}/{len(template.sections)} sections.",
                    0.55 + 0.35 * ((section_index + 1) / max(len(template.sections), 1)),
                )
        if self.sparse_fallback and "Embedding provider unavailable; using sparse retrieval only." not in warnings:
            warnings.append("Embedding provider unavailable; using sparse retrieval only.")
        metadata = RetrievalMetadata(
            retrieval_mode=self.actual_retrieval_mode,
            chunk_method=self.chunk_method,
            sparse_backend="bm25s",
            tokenizer=source_bm25.tokenizer.name,
            script_normalization=self.tokenizer_config.script_normalization,
            domain_token_extraction=self.tokenizer_config.domain_token_extraction,
            temporary_domain_terms=sorted(self.tokenizer_config.temporary_terms.keys()),
            source_top_k=self.source_top_k,
            reference_top_k=self.reference_top_k,
            reference_prefilter_limit=self.reference_prefilter_limit,
            rrf_k=self.rrf_k,
            sparse_fallback=self.sparse_fallback,
            section_queries=section_queries,
            tokenization_report={
                "source": source_bm25.tokenization_report("source_chunks"),
                "reference": reference_bm25.tokenization_report("reference_items"),
            },
            domain_term_suggestions=self.domain_term_suggestions,
        )
        return EvidencePlan(job_id=job_id, template=template, sections=sections, warnings=warnings, retrieval_metadata=metadata)

    def _rank_source(self, query: str, chunks: List, bm25: BM25Index, source_embeddings: dict) -> List[Tuple[float, object]]:
        sparse_scores = bm25.ranked(query)
        if self.actual_retrieval_mode == "sparse_only":
            return [(score, chunks[index]) for index, score in _normalize_scores(sparse_scores)]
        try:
            query_embedding = self.embedding_client.embed(query)
        except EmbeddingUnavailable:
            self.actual_retrieval_mode = "sparse_only"
            self.sparse_fallback = True
            return [(score, chunks[index]) for index, score in _normalize_scores(sparse_scores)]
        dense_scores = [(index, cosine(query_embedding, embedding)) for index, embedding in source_embeddings.items()]
        return [(score, chunks[index]) for index, score in _fused_scores(dense_scores, sparse_scores, self.rrf_k)]

    def _rank_reference(self, query: str, items: List, candidate_indices: List[int], bm25: BM25Index) -> List[Tuple[float, object]]:
        sparse_scores = [(index, bm25.score(query, index)) for index in candidate_indices]
        sparse_scores.sort(key=lambda item: item[1], reverse=True)
        if self.actual_retrieval_mode == "sparse_only":
            return [(score, items[index]) for index, score in _normalize_scores(sparse_scores)]
        try:
            query_embedding = self.embedding_client.embed(query)
            dense_scores = [
                (index, cosine(query_embedding, self.embedding_client.embed(_embedding_text(items[index]))))
                for index in candidate_indices
            ]
        except EmbeddingUnavailable:
            self.actual_retrieval_mode = "sparse_only"
            self.sparse_fallback = True
            return [(score, items[index]) for index, score in _normalize_scores(sparse_scores)]
        return [(score, items[index]) for index, score in _fused_scores(dense_scores, sparse_scores, self.rrf_k)]

    def _prefilter_reference_items(self, query: str, bm25: BM25Index, reference_items: List) -> List[int]:
        ranked = bm25.ranked(query)
        positives = [index for index, score in ranked if score > 0]
        if len(positives) >= self.reference_prefilter_limit:
            return positives[: self.reference_prefilter_limit]
        selected = positives[:]
        seen = set(selected)
        for index, _score in ranked:
            if index in seen:
                continue
            selected.append(index)
            if len(selected) >= min(self.reference_prefilter_limit, len(reference_items)):
                break
        return selected

    def _switch_to_sparse_only(self, warnings: List[str], progress_callback, detail: str) -> None:
        self.actual_retrieval_mode = "sparse_only"
        self.sparse_fallback = True
        warning = "Embedding provider unavailable; using sparse retrieval only."
        warnings.append(warning)
        if progress_callback:
            progress_callback("retrieval_fallback", warning, 0.54)

    def _rank_reason(self, reference: bool = False) -> str:
        if self.actual_retrieval_mode == "sparse_only":
            return "BM25 sparse retrieval matched template section context"
        if reference:
            return "RRF fused dense embedding and BM25 sparse ranks for supplementary field experience"
        return "RRF fused dense embedding and BM25 sparse ranks for template section context"

    def _source_ref(self, chunk, score: float, reason: str) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=chunk.chunk_id,
            document_id=chunk.document_id,
            file_name=chunk.file_name,
            evidence_type="source",
            location=_page_location(chunk.page_start, chunk.page_end) or chunk.metadata.get("location"),
            summary=chunk.summary or summarize(chunk.content),
            excerpt=summarize(chunk.content, 600),
            score=round(float(score), 4),
            reason=reason,
        )

    def _reference_ref(self, item, score: float, reason: str) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=item.item_id,
            document_id=item.document_id,
            file_name=item.file_name,
            evidence_type="reference",
            location=item.location,
            summary=item.summary or summarize(item.content),
            excerpt=summarize(item.content, 600),
            score=round(float(score), 4),
            reason=reason,
        )


def _embedding_text(item) -> str:
    return item.embedding_text or item.content


def _fused_scores(dense_scores: List[Tuple[int, float]], sparse_scores: List[Tuple[int, float]], rrf_k: int) -> List[Tuple[int, float]]:
    dense_rank = [index for index, _score in sorted(dense_scores, key=lambda item: item[1], reverse=True)]
    sparse_rank = [index for index, _score in sorted(sparse_scores, key=lambda item: item[1], reverse=True)]
    return _normalize_scores(reciprocal_rank_fusion([dense_rank, sparse_rank], k=rrf_k))


def _normalize_scores(scores: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
    if not scores:
        return []
    max_score = max(score for _index, score in scores) or 1.0
    return [(index, score / max_score) for index, score in scores]


def _page_location(page_start, page_end) -> Optional[str]:
    if not page_start:
        return None
    if page_end and page_end != page_start:
        return f"pages {page_start}-{page_end}"
    return f"page {page_start}"
