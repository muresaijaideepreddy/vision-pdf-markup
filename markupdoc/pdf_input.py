"""PDF validation and raster previews; never silently skip scan pages."""

from __future__ import annotations

from pathlib import Path


def inspect_pdf(path: str | Path, max_pages: int = 30) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    path = Path(path)
    if max_pages < 1:
        raise ValueError("max_pages must be positive.")
    if path.suffix.lower() != ".pdf":
        raise ValueError("The marked-up input must be a PDF.")
    size = path.stat().st_size
    if size > 45 * 1024 * 1024:
        raise ValueError("PDF exceeds the prototype's 45 MiB limit. Split it into smaller documents.")
    with path.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError("Input does not have a PDF header.")
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported. Use an unlocked copy.")
        count = len(reader.pages)
    except PdfReadError as exc:
        raise ValueError("The scan PDF is damaged or unreadable.") from exc
    if not 1 <= count <= max_pages:
        raise ValueError(f"PDF has {count} pages; the configured limit is {max_pages}. No pages were processed.")
    return {"pages": count, "bytes": size, "filename": path.name}


def render_pdf_pages(path: str | Path, output_dir: str | Path, *, dpi: int = 170, max_pages: int = 30) -> list[Path]:
    import pypdfium2 as pdfium

    if not 72 <= dpi <= 300:
        raise ValueError("Preview dpi must be between 72 and 300.")
    info = inspect_pdf(path, max_pages=max_pages)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destinations = [output_dir / f"page-{i + 1:03d}.png" for i in range(info["pages"])]
    if any(p.exists() for p in destinations):
        raise ValueError("Preview files already exist; choose a new output folder.")
    with pdfium.PdfDocument(str(path)) as document:
        for index, destination in enumerate(destinations):
            page = document[index]
            try:
                width, height = page.get_size()
                if width * height * (dpi / 72) ** 2 > 40_000_000:
                    raise ValueError("PDF page is too large to render at this resolution.")
                bitmap = page.render(scale=dpi / 72)
                try:
                    bitmap.to_pil().save(destination)
                finally:
                    bitmap.close()
            finally:
                page.close()
    return destinations
