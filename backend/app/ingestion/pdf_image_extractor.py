import hashlib
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from backend.app.pipeline.schemas import ImageEvidenceRef


class PdfImageExtractor:
    def __init__(self, job_dir: Path, min_width: int = 120, min_height: int = 80):
        self.job_dir = job_dir
        self.min_width = min_width
        self.min_height = min_height
        self.image_dir = job_dir / "intermediate" / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, pdf_paths: Iterable[str]) -> List[ImageEvidenceRef]:
        try:
            import fitz
        except Exception:
            return []

        items: List[ImageEvidenceRef] = []
        for pdf_path in pdf_paths:
            path = Path(pdf_path)
            if path.suffix.lower() != ".pdf" or not path.exists():
                continue
            try:
                document = fitz.open(str(path))
            except Exception:
                continue
            with document:
                document_id = _document_id(path)
                for page_index in range(document.page_count):
                    page = document.load_page(page_index)
                    for image_index, bbox in enumerate(self._image_bboxes(page)):
                        item = self._save_crop(
                            path=path,
                            document_id=document_id,
                            page=page,
                            page_number=page_index + 1,
                            bbox=bbox,
                            index=image_index,
                            extraction_method="pymupdf_image",
                        )
                        if item:
                            items.append(item)
        return items

    def page_image_path(self, pdf_path: str, page_number: int) -> Optional[str]:
        try:
            import fitz
        except Exception:
            return None

        path = Path(pdf_path)
        if path.suffix.lower() != ".pdf" or not path.exists() or page_number < 1:
            return None
        image_id = f"page_{_stable_hash(str(path), str(page_number))}"
        target = self.image_dir / f"{image_id}.png"
        if target.exists():
            return str(target)
        try:
            document = fitz.open(str(path))
            with document:
                if page_number > document.page_count:
                    return None
                page = document.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pixmap.save(str(target))
                return str(target)
        except Exception:
            return None

    def crop_ratio(
        self,
        pdf_path: str,
        page_number: int,
        bbox_ratio: Sequence[float],
        extraction_method: str = "vlm_suggested_crop",
    ) -> Optional[ImageEvidenceRef]:
        try:
            import fitz
        except Exception:
            return None

        path = Path(pdf_path)
        if len(bbox_ratio) != 4 or path.suffix.lower() != ".pdf" or not path.exists() or page_number < 1:
            return None
        try:
            document = fitz.open(str(path))
            with document:
                if page_number > document.page_count:
                    return None
                page = document.load_page(page_number - 1)
                rect = page.rect
                x0, y0, x1, y1 = [_clamp(float(value), 0.0, 1.0) for value in bbox_ratio]
                bbox = [
                    rect.width * min(x0, x1),
                    rect.height * min(y0, y1),
                    rect.width * max(x0, x1),
                    rect.height * max(y0, y1),
                ]
                return self._save_crop(
                    path=path,
                    document_id=_document_id(path),
                    page=page,
                    page_number=page_number,
                    bbox=bbox,
                    index=0,
                    extraction_method=extraction_method,
                )
        except Exception:
            return None

    def _image_bboxes(self, page) -> List[List[float]]:
        bboxes: List[List[float]] = []
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1 and block.get("bbox"):
                    bboxes.append([float(value) for value in block["bbox"]])
        except Exception:
            pass
        try:
            for image in page.get_images(full=True):
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    bboxes.append([float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)])
        except Exception:
            pass
        return _dedupe_bboxes(bboxes)

    def _save_crop(
        self,
        path: Path,
        document_id: str,
        page,
        page_number: int,
        bbox: Sequence[float],
        index: int,
        extraction_method: str,
    ) -> Optional[ImageEvidenceRef]:
        try:
            import fitz
        except Exception:
            return None

        rect = fitz.Rect(*bbox) & page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            return None
        image_id = f"img_{_stable_hash(str(path), str(page_number), str(index), ','.join(f'{value:.2f}' for value in bbox))}"
        target = self.image_dir / f"{image_id}.png"
        try:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
            if pixmap.width < self.min_width or pixmap.height < self.min_height:
                return None
            pixmap.save(str(target))
        except Exception:
            return None
        page_image = self.page_image_path(str(path), page_number) or ""
        return ImageEvidenceRef(
            image_id=image_id,
            evidence_id=image_id,
            document_id=document_id,
            file_name=path.name,
            location=f"page {page_number}",
            page_number=page_number,
            bbox=[round(float(value), 2) for value in [rect.x0, rect.y0, rect.x1, rect.y1]],
            page_width=round(float(page.rect.width), 2),
            page_height=round(float(page.rect.height), 2),
            width=int(pixmap.width),
            height=int(pixmap.height),
            image_path=str(target),
            page_image_path=page_image,
            extraction_method=extraction_method,
            summary="PDF image crop",
            excerpt="PDF image crop",
        )


def _document_id(path: Path) -> str:
    return f"pdf-{_stable_hash(str(path.resolve()))}"


def _stable_hash(*parts: str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _dedupe_bboxes(bboxes: List[List[float]]) -> List[List[float]]:
    result: List[List[float]] = []
    seen = set()
    for bbox in bboxes:
        if len(bbox) != 4:
            continue
        key = tuple(round(value, 1) for value in bbox)
        if key in seen:
            continue
        seen.add(key)
        result.append(bbox)
    return result


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
