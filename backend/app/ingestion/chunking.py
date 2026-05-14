import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ChunkedText:
    text: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    metadata: Dict[str, str] = field(default_factory=dict)


def summarize(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] + ("..." if len(compact) > limit else "")


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    return [chunk.text for chunk in chunk_text_with_metadata(text, chunk_size, overlap)]


def chunk_text_with_metadata(text: str, chunk_size: int = 900, overlap: int = 120) -> List[ChunkedText]:
    cleaned = text.strip()
    if not cleaned:
        return []
    effective_overlap = min(max(overlap, 0), max(chunk_size - 1, 0))
    page_markers = _page_markers(cleaned)
    chunks: List[ChunkedText] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        if end < len(cleaned):
            natural = _natural_break(cleaned, start, end)
            if natural > start:
                end = natural
        content = cleaned[start:end].strip()
        if content:
            page_start = _page_for_offset(page_markers, start)
            page_end = _page_for_offset(page_markers, max(end - 1, start))
            chunks.append(
                ChunkedText(
                    text=content,
                    page_start=page_start,
                    page_end=page_end or page_start,
                    metadata={"char_start": str(start), "char_end": str(end)},
                )
            )
        if end >= len(cleaned):
            break
        next_start = end - effective_overlap
        start = next_start if next_start > start else end
    return chunks


def _natural_break(text: str, start: int, end: int) -> int:
    search_start = max(start, end - 180)
    window = text[search_start:end]
    candidates = [
        window.rfind("\n\n"),
        window.rfind("\n"),
        window.rfind("。"),
        window.rfind(". "),
    ]
    best = max(candidates)
    if best == -1:
        return end
    return search_start + best + 1


def _page_markers(text: str) -> List[tuple[int, int]]:
    return [(match.start(), int(match.group(1))) for match in re.finditer(r"<!-- Page (\d+) -->", text)]


def _page_for_offset(markers: List[tuple[int, int]], offset: int) -> Optional[int]:
    current = None
    for marker_offset, page in markers:
        if marker_offset > offset:
            break
        current = page
    return current
