from backend.app.core.config import ProviderConfig
from backend.app.ingestion.section_refiner import SectionRefiner
from backend.app.pipeline.schemas import TemplateBlock, TemplateSection, TemplateSectionCandidate


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


def test_section_refiner_uses_llm_json_without_direct_feedback_replacement(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return _FakeResponse(
            """
            {
              "feedback_intent": "guidance",
              "sections": [
                {
                  "title": "Repair Procedure",
                  "level": 1,
                  "source_block_ids": ["block-1"],
                  "confidence": 0.88,
                  "operation": "rename",
                  "reason": "Reviewer guidance indicates this heading is the repair procedure."
                }
              ],
              "warnings": [],
              "suggestions": []
            }
            """
        )

    monkeypatch.setattr("backend.app.ingestion.section_refiner.requests.post", fake_post)

    result = SectionRefiner(ProviderConfig("http://llm.test/v1", "key", "model")).refine(
        file_name="template.docx",
        template_id="template-1",
        blocks=[TemplateBlock(block_id="block-1", text="Procedure", order_index=0)],
        candidates=[
            TemplateSectionCandidate(
                candidate_id="candidate-1",
                title="Procedure",
                source_block_ids=["block-1"],
                confidence=0.7,
            )
        ],
        fallback_sections=[TemplateSection(section_id="section-1", title="Procedure")],
        feedback="Please clarify the section naming.",
    )

    assert result.refinement_mode == "llm"
    assert result.feedback_intent == "guidance"
    assert result.sections[0].title == "Repair Procedure"
    assert result.sections[0].source_block_ids == ["block-1"]


def test_section_refiner_falls_back_when_unconfigured():
    fallback = [TemplateSection(section_id="section-1", title="Procedure")]

    result = SectionRefiner(ProviderConfig(None, None, None)).refine(
        file_name="template.docx",
        template_id="template-1",
        blocks=[],
        candidates=[],
        fallback_sections=fallback,
        feedback="1. Safety\n2. Repair",
    )

    assert result.refinement_mode == "rules"
    assert [section.title for section in result.sections] == ["Procedure"]
    assert any("not configured" in warning for warning in result.warnings)


def test_section_refiner_fallback_applies_explicit_exclusion_feedback():
    fallback = [
        TemplateSection(section_id="section-1", title="1. Document Control"),
        TemplateSection(section_id="section-2", title="Revision history:"),
        TemplateSection(section_id="section-3", title="2. Repair Procedure"),
    ]

    result = SectionRefiner(ProviderConfig(None, None, None)).refine(
        file_name="template.docx",
        template_id="template-1",
        blocks=[],
        candidates=[],
        fallback_sections=fallback,
        feedback="Revision history不用",
    )

    assert result.refinement_mode == "rules"
    assert [section.title for section in result.sections] == ["1. Document Control", "2. Repair Procedure"]
    assert any("Revision history" in warning for warning in result.warnings)


def test_section_refiner_fallback_does_not_treat_plain_section_list_as_replacement():
    fallback = [TemplateSection(section_id="section-1", title="1. Maintenance")]

    result = SectionRefiner(ProviderConfig(None, None, None)).refine(
        file_name="template.docx",
        template_id="template-1",
        blocks=[],
        candidates=[],
        fallback_sections=fallback,
        feedback="1. Safety Requirements\n2. Repair Procedure",
    )

    assert [section.title for section in result.sections] == ["1. Maintenance"]


def test_section_refiner_prompt_includes_template_shape_and_ignore_hints(monkeypatch):
    captured = {}

    def fake_post(*_args, **kwargs):
        captured["payload"] = kwargs["json"]
        return _FakeResponse(
            """
            {
              "feedback_intent": "guidance",
              "sections": [
                {
                  "title": "Repair Procedure",
                  "level": 1,
                  "source_block_ids": ["block-2"],
                  "confidence": 0.88,
                  "operation": "keep",
                  "reason": "Repair Procedure is a fillable SOP body section."
                }
              ],
              "warnings": [],
              "suggestions": []
            }
            """
        )

    monkeypatch.setattr("backend.app.ingestion.section_refiner.requests.post", fake_post)

    SectionRefiner(ProviderConfig("http://llm.test/v1", "key", "model")).refine(
        file_name="template.docx",
        template_id="template-1",
        blocks=[
            TemplateBlock(
                block_id="block-1",
                text="AXM2 Repair SOP",
                order_index=0,
                metadata={"looks_like_document_title": "true", "section_recommendation": "ignore"},
            ),
            TemplateBlock(block_id="block-2", text="Repair Procedure", order_index=1),
        ],
        candidates=[
            TemplateSectionCandidate(
                candidate_id="candidate-1",
                title="Repair Procedure",
                source_block_ids=["block-2"],
                confidence=0.7,
            )
        ],
        fallback_sections=[TemplateSection(section_id="section-1", title="Repair Procedure")],
    )

    prompt = captured["payload"]["messages"][1]["content"]
    assert "Raw template blocks" in prompt
    assert "Rule candidates" in prompt
    assert "looks_like_document_title" in prompt
    assert "section_recommendation=ignore" in prompt
    assert "document titles" in prompt
