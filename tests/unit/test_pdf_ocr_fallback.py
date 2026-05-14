from pathlib import Path

from backend.app.core.config import ProviderConfig
from backend.app.ingestion.document_loaders import load_source_pdf
from backend.app.ingestion.ocr_client import ExtractionResult, OcrClient


class FakeOcrClient(OcrClient):
    def __init__(self, result: ExtractionResult):
        super().__init__(ProviderConfig(api_base="http://ocr.local/v1", api_key="key", model="ocr-model"))
        self.result = result

    def extract_pdf_text(self, file_path: str) -> ExtractionResult:
        return self.result


def test_source_pdf_uses_ocr_text_when_configured(tmp_path: Path):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_text("fallback text should not be used", encoding="utf-8")
    ocr = FakeOcrClient(
        ExtractionResult(text="<!-- Page 1 -->\nOCR extracted maintenance procedure", method="ocr")
    )

    document = load_source_pdf(str(pdf_path), ocr_client=ocr)

    assert "OCR extracted maintenance procedure" in document.raw_text
    assert document.metadata["extraction_method"] == "ocr"
    assert document.chunks[0].page_start == 1


def test_source_pdf_falls_back_when_ocr_returns_empty(tmp_path: Path):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_text("Plain text fallback content", encoding="utf-8")
    ocr = FakeOcrClient(
        ExtractionResult(text="", warnings=["OCR produced no usable text."], method="ocr")
    )

    document = load_source_pdf(str(pdf_path), ocr_client=ocr)

    assert "Plain text fallback content" in document.raw_text
    assert document.metadata["extraction_method"] == "text_read"
    assert any("Falling back to PyMuPDF" in warning for warning in document.warnings)
