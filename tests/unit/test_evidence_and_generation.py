import json as json_module

import requests

from backend.app.core.config import ProviderConfig
from backend.app.generation.section_generator import SectionGenerator
from backend.app.indexing.embedding import EmbeddingClient
from backend.app.indexing.sparse import BM25Index, reciprocal_rank_fusion
from backend.app.indexing.tokenizer import SparseTokenizer, TokenizerConfig
from backend.app.ingestion.chunking import chunk_text_with_metadata
from backend.app.ingestion.document_loaders import load_reference_file, load_source_file, load_source_pdf
from backend.app.pipeline.schemas import DomainTermSuggestion, EvidenceRef, GenerationProfile, ImageEvidenceRef, ReferenceDocument, ReferenceItem, SectionEvidence, SourceChunk, SourceDocument, TemplateRefinementSuggestion, TemplateSection, TemplateStructure
from backend.app.planning.evidence_planner import EvidencePlanner


class FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


def evidence(
    evidence_id: str,
    evidence_type: str,
    text: str,
    *,
    file_name: str = "manual.pdf",
    location: str = "page 1",
    summary: str = "",
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        document_id=evidence_id.split("-")[0],
        file_name=file_name,
        evidence_type=evidence_type,
        location=location,
        summary=summary or text[:120],
        excerpt=text,
        score=1.0,
        reason="test evidence",
    )


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


def test_source_text_file_is_loaded_as_primary_source(tmp_path):
    path = tmp_path / "manual.txt"
    path.write_text("Lockout is required before pressure release.", encoding="utf-8")

    document = load_source_file(str(path))

    assert document.file_name == "manual.txt"
    assert document.metadata["extraction_method"] == "text_read"
    assert document.chunks[0].content.startswith("Lockout is required")


def test_csv_reference_file_is_loaded_as_rows(tmp_path):
    path = tmp_path / "records.csv"
    path.write_text("symptom,fix\npressure alarm,replace seal\n", encoding="utf-8")

    document = load_reference_file(str(path))

    assert document.file_type == "csv"
    assert document.items[0].location == "row 2"
    assert "symptom: pressure alarm" in document.items[0].content


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


def test_llm_generated_blocks_carry_paragraph_level_provenance(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "payload": json, "timeout": timeout})
        return FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "block_type": "paragraph",
                            "text": "Lockout before maintenance and release pressure before repair.",
                            "source_chunk_ids": ["source-1-c1"],
                            "reference_item_ids": [],
                            "claims": ["Lockout and pressure release are required."],
                        },
                        {
                            "block_type": "bullet",
                            "text": "Inspect seal wear patterns from prior repair records.",
                            "source_chunk_ids": [],
                            "reference_item_ids": ["ref-1-i1"],
                        },
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", "secret", "sop-writer", timeout_seconds=9))
    section = SectionEvidence(
        section_id="s1",
        section_title="Pump maintenance",
        source_chunks=[evidence("source-1-c1", "source", "Lockout before maintenance. Release pressure before repair.")],
        reference_items=[
            evidence(
                "ref-1-i1",
                "reference",
                "Seal wear was observed in prior repairs.",
                file_name="records.xlsx",
                location="row 7",
            )
        ],
    )

    draft = generator.generate(section, GenerationProfile())

    assert calls[0]["url"] == "http://llm.example/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["payload"]["model"] == "sop-writer"
    assert draft.blocks[0].source_chunk_ids == ["source-1-c1"]
    assert draft.blocks[1].reference_item_ids == ["ref-1-i1"]
    assert draft.blocks[0].claims == ["Lockout and pressure release are required."]


def test_generation_appends_recommended_image_blocks(monkeypatch, tmp_path):
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"fake")

    def fake_post(url, headers, json, timeout):
        return FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "block_type": "paragraph",
                            "text": "確認操作介面顯示正常後再執行生產參數設定。",
                            "source_chunk_ids": ["source-1-c1"],
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "sop-writer"))
    section = SectionEvidence(
        section_id="s1",
        section_title="操作程序",
        source_chunks=[evidence("source-1-c1", "source", "操作介面包含生產參數設定畫面。")],
        image_items=[
            ImageEvidenceRef(
                image_id="img-1",
                evidence_id="img-1",
                document_id="source-1",
                file_name="manual.pdf",
                location="page 2",
                image_path=str(image_path),
                caption="操作介面示意圖",
                alt_text="HMI operation screen",
                score=0.91,
                reason="The crop shows the operation screen for this section.",
                insert_recommended=True,
            ),
            ImageEvidenceRef(
                image_id="img-2",
                evidence_id="img-2",
                document_id="source-1",
                file_name="manual.pdf",
                location="page 3",
                image_path=str(image_path),
                caption="非預設插入圖片",
                score=0.88,
                reason="Relevant but below insertion limit.",
                insert_recommended=False,
            ),
        ],
    )

    draft = generator.generate(section, GenerationProfile())

    assert [block.block_type for block in draft.blocks] == ["paragraph", "image"]
    assert draft.blocks[1].image_evidence_ids == ["img-1"]
    assert draft.blocks[1].caption_md == "操作介面示意圖"


def test_generation_language_guidance_does_not_include_section_title_in_body(monkeypatch):
    captured_prompts = []

    def fake_post(url, headers, json, timeout):
        captured_prompts.append(json["messages"][1]["content"])
        return FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "text": "Release pressure before repair.",
                            "source_chunk_ids": ["source-1-c1"],
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "sop-writer"))
    section = SectionEvidence(
        section_id="s1",
        section_title="維修程序",
        source_chunks=[evidence("source-1-c1", "source", "Release pressure before repair.")],
    )

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert "Write in English." in captured_prompts[0]
    assert draft.title == "維修程序"
    assert draft.blocks[0].text == "Release pressure before repair."
    assert "維修程序" not in draft.blocks[0].text


def test_generation_retries_and_removes_page_markers_and_review_language(monkeypatch):
    responses = [
        FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "text": "本章節依據原廠/來源文件整理「1. Document Control」的主要 SOP 草稿內容：<!-- Page 1 --> Lockout before maintenance.",
                            "source_chunk_ids": ["source-1-c1"],
                        }
                    ]
                }
            )
        ),
        FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "text": "執行維護前，應完成能源隔離並確認設備處於安全狀態。",
                            "source_chunk_ids": ["source-1-c1"],
                        }
                    ]
                }
            )
        ),
    ]

    def fake_post(url, headers, json, timeout):
        return responses.pop(0)

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "sop-writer"))
    section = SectionEvidence(
        section_id="s1",
        section_title="1. Document Control",
        source_chunks=[evidence("source-1-c1", "source", "<!-- Page 1 --> Lockout before maintenance.")],
    )

    draft = generator.generate(
        section,
        GenerationProfile(language="zh-TW"),
        global_feedback="Use formal SOP language.",
    )

    assert draft.blocks[0].text == "執行維護前，應完成能源隔離並確認設備處於安全狀態。"
    assert "<!--" not in draft.blocks[0].text
    assert "本章節依據" not in draft.blocks[0].text
    assert "審核" not in draft.blocks[0].text
    assert "Reviewer feedback was used as generation guidance." in draft.warnings
    assert any("prohibited wording" in warning for warning in draft.warnings)


def test_generation_does_not_write_missing_source_placeholder_into_body():
    generator = SectionGenerator()
    section = SectionEvidence(
        section_id="s1",
        section_title="Acceptance Criteria",
    )

    draft = generator.generate(section, GenerationProfile(language="zh-TW"))

    assert draft.blocks == []
    assert draft.warnings == ["No evidence was mapped; section body was not generated."]


def test_generation_without_llm_does_not_concatenate_evidence_into_body():
    generator = SectionGenerator()
    section = SectionEvidence(
        section_id="s1",
        section_title="Repair Procedure",
        source_chunks=[
            evidence(
                "source-1-c1",
                "source",
                "Check fixture interference, screw seating, fastening angle, driver speed profile, and retry logs.",
            )
        ],
    )

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert draft.blocks == []
    assert draft.warnings == ["LLM generation is not configured; section body was not generated."]


def test_generation_prefers_full_excerpt_over_truncated_summary(monkeypatch):
    captured_prompts = []

    def fake_post(url, headers, json, timeout):
        captured_prompts.append(json["messages"][1]["content"])
        return FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "text": "Check fixture interference, screw seating, fastening angle, driver speed profile, and retry logs.",
                            "source_chunk_ids": ["source-1-c1"],
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "sop-writer"))
    section = SectionEvidence(
        section_id="s1",
        section_title="Repair Procedure",
        source_chunks=[
            evidence(
                "source-1-c1",
                "source",
                "Check fixture interference, screw seating, fastening angle, driver speed profile, and retry logs.",
                summary="Check fixture interference...",
            )
        ],
    )

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert "Check fixture interference..." not in captured_prompts[0]
    assert "driver speed profile, and retry logs." in captured_prompts[0]
    assert draft.blocks[0].text == "Check fixture interference, screw seating, fastening angle, driver speed profile, and retry logs."


def test_generation_accepts_rich_list_and_table_blocks(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return FakeLLMResponse(
            json_module.dumps(
                {
                    "blocks": [
                        {
                            "block_type": "bullet_list",
                            "items": [
                                {
                                    "content_md": "Inspect **valve** leakage.",
                                    "source_chunk_ids": ["source-1-c1"],
                                }
                            ],
                        },
                        {
                            "block_type": "table",
                            "headers": ["Item", "Action"],
                            "rows": [["Valve", "Replace if leakage is found"]],
                            "source_chunk_ids": ["source-1-c1"],
                        },
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "sop-writer"))
    section = SectionEvidence(
        section_id="s1",
        section_title="Repair Procedure",
        source_chunks=[evidence("source-1-c1", "source", "Inspect valve leakage and replace if needed.")],
    )

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert draft.blocks[0].block_type == "bullet_list"
    assert draft.blocks[0].items[0].content_md == "Inspect **valve** leakage."
    assert draft.blocks[0].items[0].source_chunk_ids == ["source-1-c1"]
    assert draft.blocks[1].block_type == "table"
    assert draft.blocks[1].headers == ["Item", "Action"]
    assert draft.blocks[1].rows == [["Valve", "Replace if leakage is found"]]


def test_generation_reports_llm_timeout_detail(monkeypatch):
    def fake_post(url, headers, json, timeout):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr("backend.app.generation.section_generator.requests.post", fake_post)
    generator = SectionGenerator(ProviderConfig("http://llm.example/v1", None, "slow-model", timeout_seconds=3))
    section = SectionEvidence(
        section_id="s1",
        section_title="Repair Procedure",
        source_chunks=[evidence("source-1-c1", "source", "Inspect valve leakage.")],
    )

    draft = generator.generate(section, GenerationProfile(language="en"))

    assert draft.blocks == []
    assert any("timed out after 3s" in warning for warning in draft.warnings)
