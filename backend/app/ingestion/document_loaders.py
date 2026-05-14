import csv
import re
import warnings as py_warnings
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from backend.app.ingestion.chunking import ChunkedText, chunk_text_with_metadata, summarize
from backend.app.ingestion.contextualizer import Contextualizer
from backend.app.ingestion.ocr_client import OcrClient
from backend.app.ingestion.section_refiner import SectionRefiner
from backend.app.pipeline.schemas import (
    ReferenceDocument,
    ReferenceItem,
    SourceChunk,
    SourceDocument,
    TemplateSection,
    TemplateRefinementSuggestion,
    TemplateStructure,
)


HEADING_PATTERNS = [
    re.compile(r"^\s*(第[一二三四五六七八九十\d]+[章節])\s*(.+)$"),
    re.compile(r"^\s*([一二三四五六七八九十]+、)\s*(.+)$"),
    re.compile(r"^\s*(\d+(?:\.\d+)*)[.)、]?\s+(.+)$"),
]


def load_source_pdf(
    path: str,
    ocr_client: OcrClient = None,
    chunk_size: int = 900,
    chunk_overlap: int = 120,
    chunk_method: str = "vanilla",
    contextualizer: Optional[Contextualizer] = None,
) -> SourceDocument:
    file_path = Path(path)
    warnings: List[str] = []
    raw_text, extraction_method = _extract_pdf_text(file_path, warnings, ocr_client)
    if not raw_text.strip():
        warnings.append("No source text extracted.")
    document_id = f"source-{uuid4().hex[:10]}"
    chunked = chunk_text_with_metadata(raw_text, chunk_size=chunk_size, overlap=chunk_overlap)
    chunks = []
    document_context = _document_context(raw_text, chunk_method, contextualizer)
    for index, chunk in enumerate(chunked):
        embedding_text = _embedding_text(raw_text, chunk, chunk_method, contextualizer, document_context)
        metadata = dict(chunk.metadata)
        metadata.update({"chunk_method": chunk_method, "extraction_method": extraction_method})
        chunks.append(
            SourceChunk(
                chunk_id=f"{document_id}-chunk-{index}",
                document_id=document_id,
                file_name=file_path.name,
                content=chunk.text,
                embedding_text=embedding_text,
                summary=summarize(chunk.text),
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                metadata=metadata,
            )
        )
    return SourceDocument(
        document_id=document_id,
        file_name=file_path.name,
        raw_text=raw_text,
        chunks=chunks,
        metadata={"extraction_method": extraction_method},
        warnings=warnings,
    )


def load_reference_file(
    path: str,
    ocr_client: OcrClient = None,
    chunk_size: int = 900,
    chunk_overlap: int = 120,
    chunk_method: str = "vanilla",
    contextualizer: Optional[Contextualizer] = None,
) -> ReferenceDocument:
    file_path = Path(path)
    ext = file_path.suffix.lower()
    if ext in {".xlsx", ".xls"}:
        return _load_excel(file_path)
    if ext == ".pdf":
        source_like = load_source_pdf(
            path,
            ocr_client=ocr_client,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunk_method=chunk_method,
            contextualizer=contextualizer,
        )
        return ReferenceDocument(
            document_id=source_like.document_id.replace("source-", "ref-"),
            file_name=file_path.name,
            file_type="pdf",
            items=[
                ReferenceItem(
                    item_id=chunk.chunk_id.replace("source-", "ref-"),
                    document_id=source_like.document_id.replace("source-", "ref-"),
                    file_name=file_path.name,
                    item_type="unstructured_chunk",
                    content=chunk.content,
                    embedding_text=chunk.embedding_text,
                    summary=chunk.summary,
                    location=_page_location(chunk.page_start, chunk.page_end),
                    metadata=chunk.metadata,
                )
                for chunk in source_like.chunks
            ],
            warnings=source_like.warnings,
        )
    if ext == ".md":
        return _load_text_reference(file_path, "markdown", chunk_size, chunk_overlap, chunk_method, contextualizer)
    return _load_text_reference(file_path, "txt", chunk_size, chunk_overlap, chunk_method, contextualizer)


def load_template_docx(
    path: str,
    section_detection_mode: str = "rules",
    section_refiner: Optional[SectionRefiner] = None,
) -> TemplateStructure:
    file_path = Path(path)
    warnings: List[str] = []
    sections: List[TemplateSection] = []
    try:
        from docx import Document

        document = Document(str(file_path))
        paragraphs = list(document.paragraphs)
        for index, paragraph in enumerate(paragraphs):
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = paragraph.style.name if paragraph.style else ""
            level = _heading_level(style_name, text)
            if level:
                sections.append(
                    TemplateSection(
                        section_id=f"section-{len(sections) + 1}",
                        title=text,
                        level=level,
                        start_block_index=index,
                        style_name=style_name,
                    )
                )
        for idx, section in enumerate(sections):
            next_start = sections[idx + 1].start_block_index if idx + 1 < len(sections) else len(paragraphs)
            section.end_block_index = next_start - 1
            section.existing_text = "\n".join(
                p.text.strip()
                for p in paragraphs[section.start_block_index + 1 : next_start]
                if p.text.strip()
            )
    except Exception as exc:
        warnings.append(f"DOCX heading extraction failed: {exc}")
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        sections = _sections_from_text(text)

    if not sections:
        warnings.append("No clear headings detected; using a single General SOP section.")
        sections = [
            TemplateSection(
                section_id="section-1",
                title="General SOP",
                level=1,
                start_block_index=0,
                existing_text="",
            )
        ]
    suggestions: List[TemplateRefinementSuggestion] = []
    if section_detection_mode == "rules_llm":
        suggestions = section_refiner.refine(file_path.name, sections) if section_refiner else []
        if suggestions:
            warnings.append(f"LLM section refinement produced {len(suggestions)} review suggestion(s).")
        else:
            warnings.append("LLM section refinement produced no suggestions; rule-based sections are unchanged.")

    return TemplateStructure(
        template_id=f"template-{uuid4().hex[:10]}",
        file_name=file_path.name,
        sections=sections,
        warnings=warnings,
        refinement_suggestions=suggestions,
    )


def _load_text_reference(
    file_path: Path,
    file_type: str,
    chunk_size: int,
    chunk_overlap: int,
    chunk_method: str,
    contextualizer: Optional[Contextualizer],
) -> ReferenceDocument:
    warnings: List[str] = []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        warnings.append(f"Could not read text file: {exc}")
        text = ""
    document_id = f"ref-{uuid4().hex[:10]}"
    items = []
    document_context = _document_context(text, chunk_method, contextualizer)
    for index, chunk in enumerate(chunk_text_with_metadata(text, chunk_size=chunk_size, overlap=chunk_overlap)):
        embedding_text = _embedding_text(text, chunk, chunk_method, contextualizer, document_context)
        metadata = dict(chunk.metadata)
        metadata["chunk_method"] = chunk_method
        items.append(
            ReferenceItem(
                item_id=f"{document_id}-item-{index}",
                document_id=document_id,
                file_name=file_path.name,
                item_type="unstructured_chunk",
                content=chunk.text,
                embedding_text=embedding_text,
                summary=summarize(chunk.text),
                location=f"chunk {index + 1}",
                metadata=metadata,
            )
        )
    return ReferenceDocument(
        document_id=document_id,
        file_name=file_path.name,
        file_type=file_type,
        items=items,
        warnings=warnings,
    )


def _load_excel(file_path: Path) -> ReferenceDocument:
    warnings: List[str] = []
    document_id = f"ref-{uuid4().hex[:10]}"
    rows: List[Dict[str, str]] = []
    try:
        import openpyxl

        with py_warnings.catch_warnings():
            py_warnings.filterwarnings(
                "ignore",
                message="Data Validation extension is not supported and will be removed",
                category=UserWarning,
                module="openpyxl.worksheet._reader",
            )
            workbook = openpyxl.load_workbook(str(file_path), data_only=True)
        sheet = workbook.active
        headers = [str(cell.value or "").strip() for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        for row in sheet.iter_rows(min_row=2, values_only=True):
            rows.append({headers[i] or f"column_{i + 1}": "" if value is None else str(value) for i, value in enumerate(row)})
    except Exception as exc:
        warnings.append(f"openpyxl failed, trying csv-style read: {exc}")
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                rows = list(csv.DictReader(handle))
        except Exception as csv_exc:
            warnings.append(f"Could not parse Excel/reference table: {csv_exc}")
    items: List[ReferenceItem] = []
    for index, row in enumerate(rows):
        raw = {k: v for k, v in row.items() if str(v).strip()}
        content = "\n".join(f"{key}: {value}" for key, value in raw.items())
        items.append(
            ReferenceItem(
                item_id=f"{document_id}-row-{index + 1}",
                document_id=document_id,
                file_name=file_path.name,
                item_type="structured_record",
                content=content,
                summary=summarize(content),
                location=f"row {index + 2}",
                metadata={"raw_fields": str(raw), "field_count": str(len(raw))},
            )
        )
    return ReferenceDocument(
        document_id=document_id,
        file_name=file_path.name,
        file_type="excel",
        items=items,
        warnings=warnings,
    )


def _heading_level(style_name: str, text: str) -> int:
    normalized = style_name.lower()
    if "heading 1" in normalized or "標題 1" in style_name:
        return 1
    if "heading 2" in normalized or "標題 2" in style_name:
        return 2
    for pattern in HEADING_PATTERNS:
        match = pattern.match(text)
        if match:
            marker = match.group(1)
            return 2 if "." in marker else 1
    return 0


def _sections_from_text(text: str) -> List[TemplateSection]:
    sections = []
    for index, line in enumerate(text.splitlines()):
        if _heading_level("", line.strip()):
            sections.append(
                TemplateSection(
                    section_id=f"section-{len(sections) + 1}",
                    title=line.strip(),
                    level=_heading_level("", line.strip()),
                    start_block_index=index,
                )
            )
    return sections


def _embedding_text(
    document_text: str,
    chunk: ChunkedText,
    chunk_method: str,
    contextualizer: Optional[Contextualizer],
    document_context: str = "",
) -> str:
    if chunk_method == "contextual":
        return f"Document Context: {document_context}\n\nChunk: {chunk.text}" if document_context else chunk.text
    if chunk_method == "anthropic":
        context = contextualizer.chunk_context(document_text, chunk.text) if contextualizer else ""
        return f"{context}\n{chunk.text}" if context else chunk.text
    return chunk.text


def _document_context(document_text: str, chunk_method: str, contextualizer: Optional[Contextualizer]) -> str:
    if chunk_method != "contextual":
        return ""
    return contextualizer.document_summary(document_text) if contextualizer else summarize(document_text, 500)


def _page_location(page_start: Optional[int], page_end: Optional[int]) -> Optional[str]:
    if not page_start:
        return None
    if page_end and page_end != page_start:
        return f"pages {page_start}-{page_end}"
    return f"page {page_start}"


def _extract_pdf_text(file_path: Path, warnings: List[str], ocr_client: OcrClient = None):
    if ocr_client and ocr_client.is_configured():
        result = ocr_client.extract_pdf_text(str(file_path))
        warnings.extend(result.warnings)
        if result.text.strip():
            return result.text, result.method
        warnings.append("Falling back to PyMuPDF text extraction after OCR produced no usable text.")

    try:
        text = _extract_text_pymupdf(file_path)
        if text.strip():
            return text, "pymupdf"
        warnings.append("PyMuPDF produced no usable text.")
    except Exception as exc:
        warnings.append(f"PyMuPDF text extraction unavailable or failed: {exc}")

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            warnings.append("Fell back to plain text read for PDF path.")
            return text, "text_read"
    except Exception as exc:
        warnings.append(f"Plain text fallback failed: {exc}")

    return "", "failed"


def _extract_text_pymupdf(file_path: Path) -> str:
    import fitz

    doc = fitz.open(str(file_path))
    page_texts = []
    try:
        for index, page in enumerate(doc):
            page_text = page.get_text().strip()
            if page_text:
                page_texts.append(f"<!-- Page {index + 1} -->\n{page_text}")
    finally:
        doc.close()
    return "\n\n".join(page_texts)
