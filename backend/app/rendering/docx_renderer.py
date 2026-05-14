from pathlib import Path
from typing import List, Optional

from backend.app.pipeline.schemas import GenerationResult, StructuredBlock, TemplateStructure


class DocxRenderer:
    def render(
        self,
        template_path: Optional[str],
        template_structure: TemplateStructure,
        generation: GenerationResult,
        output_path: Path,
    ) -> Path:
        try:
            from docx import Document
        except Exception as exc:
            output_path.write_text(
                f"DOCX dependency unavailable: {exc}\n\n{self._plain_text(generation)}",
                encoding="utf-8",
            )
            return output_path

        if template_path and Path(template_path).suffix.lower() == ".docx" and Path(template_path).exists():
            document = Document(template_path)
        else:
            document = Document()
            document.add_heading(template_structure.file_name or "Generated SOP", level=1)

        section_by_id = {section.section_id: section for section in generation.sections}
        inserted = set()
        paragraphs = list(document.paragraphs)
        for template_section in template_structure.sections:
            draft = section_by_id.get(template_section.section_id)
            if not draft:
                continue
            if template_section.start_block_index < len(paragraphs):
                anchor = paragraphs[template_section.start_block_index]
                self._insert_blocks_after(anchor, draft.blocks)
                inserted.add(draft.section_id)

        for draft in generation.sections:
            if draft.section_id in inserted:
                continue
            document.add_heading(draft.title, level=1)
            for block in draft.blocks:
                self._append_block(document, block)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(output_path))
        return output_path

    def _insert_blocks_after(self, anchor, blocks: List[StructuredBlock]) -> None:
        current = anchor
        for block in blocks:
            new_paragraph = self._insert_paragraph_after(current, block.text)
            current = new_paragraph

    def _insert_paragraph_after(self, paragraph, text: str):
        from docx.oxml import OxmlElement
        from docx.text.paragraph import Paragraph

        new_p = OxmlElement("w:p")
        paragraph._p.addnext(new_p)
        new_paragraph = Paragraph(new_p, paragraph._parent)
        new_paragraph.style = paragraph.style
        new_paragraph.add_run(text)
        return new_paragraph

    def _append_block(self, document, block: StructuredBlock) -> None:
        if block.block_type == "heading":
            document.add_heading(block.text, level=2)
        elif block.block_type == "bullet":
            document.add_paragraph(block.text, style="List Bullet")
        elif block.block_type == "numbered":
            document.add_paragraph(block.text, style="List Number")
        else:
            document.add_paragraph(block.text)

    def _plain_text(self, generation: GenerationResult) -> str:
        lines = []
        for section in generation.sections:
            lines.append(section.title)
            lines.extend(block.text for block in section.blocks)
            lines.append("")
        return "\n".join(lines)
