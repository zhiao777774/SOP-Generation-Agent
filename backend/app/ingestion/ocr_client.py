import base64
from dataclasses import dataclass, field
from typing import List, Optional

import requests

from backend.app.core.config import ProviderConfig


@dataclass
class ExtractionResult:
    text: str
    warnings: List[str] = field(default_factory=list)
    method: str = "unknown"


class OcrClient:
    def __init__(self, config: ProviderConfig, prompt: Optional[str] = None):
        self.config = config
        self.prompt = prompt or "<image>\nFree OCR. Preserve page text, tables, warnings, steps, and numeric values."

    def is_configured(self) -> bool:
        return bool(self.config.api_base and self.config.model)

    def extract_pdf_text(self, file_path: str) -> ExtractionResult:
        if not self.is_configured():
            return ExtractionResult(text="", warnings=["OCR provider is not configured."], method="ocr_skipped")
        try:
            import fitz
        except Exception as exc:
            return ExtractionResult(text="", warnings=[f"PyMuPDF is required to render OCR pages: {exc}"], method="ocr_failed")

        warnings: List[str] = []
        page_texts: List[str] = []
        try:
            doc = fitz.open(file_path)
            try:
                for page_index, page in enumerate(doc):
                    page_number = page_index + 1
                    try:
                        image_data_url = self._render_page_to_data_url(page)
                        page_text = self._ocr_image(image_data_url, page_number)
                        if page_text.strip():
                            page_texts.append(f"<!-- Page {page_number} -->\n{page_text.strip()}")
                        else:
                            warnings.append(f"OCR returned empty text for page {page_number}.")
                    except Exception as exc:
                        warnings.append(f"OCR failed for page {page_number}: {exc}")
            finally:
                doc.close()
        except Exception as exc:
            return ExtractionResult(text="", warnings=[f"OCR PDF open/render failed: {exc}"], method="ocr_failed")

        text = "\n\n".join(page_texts)
        if not text.strip():
            warnings.append("OCR produced no usable text.")
        return ExtractionResult(text=text, warnings=warnings, method="ocr")

    def _render_page_to_data_url(self, page) -> str:
        import fitz

        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        encoded = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    def _ocr_image(self, image_data_url: str, page_number: int) -> str:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            "temperature": 0,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected OCR response for page {page_number}: {data}") from exc
