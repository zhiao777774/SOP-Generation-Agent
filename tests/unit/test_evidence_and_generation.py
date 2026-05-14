from backend.app.core.config import ProviderConfig
from backend.app.generation.section_generator import SectionGenerator
from backend.app.indexing.embedding import EmbeddingClient
from backend.app.indexing.sparse import BM25Index, reciprocal_rank_fusion
from backend.app.indexing.tokenizer import SparseTokenizer, TokenizerConfig
from backend.app.ingestion.chunking import chunk_text_with_metadata
from backend.app.ingestion.document_loaders import load_source_pdf
from backend.app.pipeline.schemas import DomainTermSuggestion, GenerationProfile, ReferenceDocument, ReferenceItem, SourceChunk, SourceDocument, TemplateRefinementSuggestion, TemplateSection, TemplateStructure
from backend.app.planning.evidence_planner import EvidencePlanner


def test_evidence_plan_keeps_source_and_reference_separate():
    template = TemplateStructure(
        template_id="t1",
        file_name="template.docx",
        sections=[TemplateSection(section_id="s1", title="Pump maintenance", level=1)],
    )
    source = SourceDocument(
        document_id="source-1",
        file_name="manual.pdf",
        raw_text="Pump maintenance requires lockout and pressure release.",
        chunks=[
            SourceChunk(
                chunk_id="source-1-c1",
                document_id="source-1",
                file_name="manual.pdf",
                content="Pump maintenance requires lockout and pressure release.",
                summary="Pump maintenance requires lockout and pressure release.",
            )
        ],
    )
    reference = ReferenceDocument(
        document_id="ref-1",
        file_name="records.txt",
        file_type="txt",
        items=[
            ReferenceItem(
                item_id="ref-1-i1",
                document_id="ref-1",
                file_name="records.txt",
                item_type="unstructured_chunk",
                content="Past field repair found pump seal wear after pressure alarms.",
                summary="Past field repair found pump seal wear after pressure alarms.",
            )
        ],
    )
    planner = EvidencePlanner(EmbeddingClient(ProviderConfig(None, None, None)), source_threshold=-1)

    plan = planner.build("job-1", template, [source], [reference])

    section = plan.sections[0]
    assert section.source_chunks[0].evidence_type == "source"
    assert section.reference_items[0].evidence_type == "reference"
    assert section.source_chunks[0].evidence_id == "source-1-c1"
    assert section.reference_items[0].evidence_id == "ref-1-i1"
    assert plan.retrieval_metadata.sparse_fallback is True
    assert "Embedding provider unavailable; using sparse retrieval only." in plan.warnings


def test_page_aware_chunking_preserves_page_range():
    text = "<!-- Page 1 -->\nAlpha repair.\n\n<!-- Page 2 -->\nBeta acceptance."

    [chunk] = chunk_text_with_metadata(text, chunk_size=200, overlap=20)

    assert chunk.page_start == 1
    assert chunk.page_end == 2


def test_contextual_chunking_keeps_original_content_separate(tmp_path):
    path = tmp_path / "manual.pdf"
    path.write_text("Pump maintenance requires lockout.\n\nRelease pressure before repair.", encoding="utf-8")

    document = load_source_pdf(str(path), chunk_method="contextual")

    assert document.chunks[0].content.startswith("Pump maintenance")
    assert document.chunks[0].embedding_text.startswith("Document Context:")
    assert document.chunks[0].content != document.chunks[0].embedding_text


def test_bm25_and_rrf_rank_expected_items():
    bm25 = BM25Index(["pump seal repair", "unrelated safety checklist"])

    ranked = bm25.ranked("pump repair")
    fused = reciprocal_rank_fusion([[1, 0], [0, 1]], k=60)

    assert ranked[0][0] == 0
    assert fused[0][1] == fused[1][1]


def test_domain_token_preservation_adds_model_and_error_tokens():
    tokenizer = SparseTokenizer(TokenizerConfig(cjk_tokenizer="regex", script_normalization="none", domain_token_extraction=True))

    record = tokenizer.tokenize_with_record("AXM2 維修 ERR-207，BIOS_V1.03 #8 M4L8 1.5Nm 200rpm PCBA required.")

    assert "axm2" in record.final_tokens
    assert "err-207" in record.preserved_domain_tokens
    assert "bios_v1.03" in record.preserved_domain_tokens
    assert "#8 m4l8" in record.preserved_domain_tokens
    assert "1.5nm" in record.preserved_domain_tokens
    assert "200rpm" in record.preserved_domain_tokens
    assert "pcba" in record.preserved_domain_tokens


def test_opencc_dual_normalization_adds_simplified_shadow_tokens():
    import pytest

    pytest.importorskip("opencc")
    tokenizer = SparseTokenizer(TokenizerConfig(cjk_tokenizer="regex", script_normalization="dual"))

    record = tokenizer.tokenize_with_record("無法開機")

    assert "無法開機".lower() in record.final_tokens
    assert "无法开机" in record.final_tokens


def test_temporary_terms_are_merged_into_final_tokens():
    tokenizer = SparseTokenizer(
        TokenizerConfig(
            cjk_tokenizer="regex",
            script_normalization="none",
            temporary_terms={"起子控制器數據讀取失敗": 0.95},
        )
    )

    record = tokenizer.tokenize_with_record("設備發生起子控制器數據讀取失敗，已重啟程式。")

    assert "起子控制器數據讀取失敗" in record.temporary_dictionary_hits
    assert "起子控制器數據讀取失敗" in record.final_tokens


def test_sparse_only_retrieval_uses_bm25_without_embedding():
    template = TemplateStructure(
        template_id="t1",
        file_name="template.docx",
        sections=[TemplateSection(section_id="s1", title="Seal repair", level=1)],
    )
    source = SourceDocument(
        document_id="source-1",
        file_name="manual.pdf",
        raw_text="Seal repair procedure.",
        chunks=[
            SourceChunk(chunk_id="source-1-c1", document_id="source-1", file_name="manual.pdf", content="Seal repair procedure."),
            SourceChunk(chunk_id="source-1-c2", document_id="source-1", file_name="manual.pdf", content="General safety checklist."),
        ],
    )

    planner = EvidencePlanner(
        EmbeddingClient(ProviderConfig(None, None, None)),
        retrieval_mode="sparse_only",
        source_threshold=0,
        reference_threshold=0,
        domain_term_suggestions=[
            DomainTermSuggestion(
                term="SealWear",
                category="failure",
                confidence=0.9,
                suggested_scope="temporary",
            )
        ],
    )
    plan = planner.build("job-1", template, [source], [])

    assert plan.retrieval_metadata.retrieval_mode == "sparse_only"
    assert plan.retrieval_metadata.sparse_backend == "bm25s"
    assert "source" in plan.retrieval_metadata.tokenization_report
    assert plan.retrieval_metadata.domain_term_suggestions[0].term == "SealWear"
    assert plan.sections[0].source_chunks[0].evidence_id == "source-1-c1"


def test_template_refinement_suggestions_are_serialized():
    template = TemplateStructure(
        template_id="t1",
        file_name="template.docx",
        sections=[TemplateSection(section_id="s1", title="Repair", level=1)],
        refinement_suggestions=[
            TemplateRefinementSuggestion(operation="rename", target_section_id="s1", title="Repair Procedure", reason="More specific")
        ],
    )

    assert template.model_dump(mode="json")["refinement_suggestions"][0]["operation"] == "rename"


def test_generated_blocks_carry_paragraph_level_provenance():
    generator = SectionGenerator()
    section = type(
        "Section",
        (),
        {
            "section_id": "s1",
            "section_title": "Pump maintenance",
            "warnings": [],
            "source_chunks": [
                type("Evidence", (), {"evidence_id": "source-1-c1", "summary": "Lockout before maintenance."})()
            ],
            "reference_items": [
                type("Evidence", (), {"evidence_id": "ref-1-i1", "summary": "Seal wear was observed in prior repairs."})()
            ],
        },
    )()

    draft = generator.generate(section, GenerationProfile())

    assert draft.blocks[0].source_chunk_ids == ["source-1-c1"]
    assert draft.blocks[1].reference_item_ids == ["ref-1-i1"]
    assert draft.blocks[0].claims


def test_generation_language_changes_body_copy_without_section_title():
    generator = SectionGenerator()
    section = type(
        "Section",
        (),
        {
            "section_id": "s1",
            "section_title": "維修程序",
            "warnings": [],
            "source_chunks": [
                type("Evidence", (), {"evidence_id": "source-1-c1", "summary": "Release pressure before repair."})()
            ],
            "reference_items": [],
        },
    )()

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert draft.title == "維修程序"
    assert draft.blocks[0].text.startswith("Main source-based SOP draft content")
