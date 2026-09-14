"""Local Chinese OCR for scanned CNINFO disclosure PDFs."""

from hashlib import sha256
from pathlib import Path
import subprocess
import tempfile

import fitz


OCR_EXTRACTOR_VERSION = "cninfo_pdf_tesseract_chi_sim_v1"


def ocr_cninfo_pdf(source_url: str, cache_dir: Path) -> tuple[str, str]:
    path = cache_dir / f"cninfo_pdf_{sha256(source_url.encode('utf-8')).hexdigest()}.pdf"
    if not path.exists():
        raise RuntimeError("CNINFO PDF artifact is unavailable for OCR")
    document = fitz.open(path)
    pages = []
    with tempfile.TemporaryDirectory(prefix="cninfo_ocr_") as temp_dir:
        for index, page in enumerate(document):
            image_path = Path(temp_dir) / f"page_{index + 1}.png"
            page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(str(image_path))
            result = subprocess.run(["tesseract", str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                                    text=True, capture_output=True, check=False, timeout=90)
            if result.returncode:
                raise RuntimeError("Tesseract OCR failed: " + result.stderr.strip())
            pages.append(result.stdout.strip())
    text = "\n\n".join(item for item in pages if item)
    if not text:
        raise RuntimeError("Tesseract OCR returned no text")
    return text, sha256(path.read_bytes()).hexdigest()
