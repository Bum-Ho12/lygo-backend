"""OCR engine interface. Engines are pluggable (Tesseract/PaddleOCR/cloud) behind this."""
import io
import shutil
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float  # 0..1 mean word confidence


class OcrEngine(Protocol):
    @property
    def available(self) -> bool: ...
    def recognize(self, image_png: bytes) -> OcrResult: ...


class TesseractOcr:
    def __init__(self, langs: str | None = None) -> None:
        self.langs = langs or settings.ocr_langs

    @property
    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def recognize(self, image_png: bytes) -> OcrResult:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_png))
        data = pytesseract.image_to_data(img, lang=self.langs, output_type=pytesseract.Output.DICT)
        words, confs = [], []
        for w, c in zip(data["text"], data["conf"], strict=False):
            if w.strip() and float(c) >= 0:
                words.append(w)
                confs.append(float(c) / 100)
        text = pytesseract.image_to_string(img, lang=self.langs)
        return OcrResult(text=text.strip(), confidence=sum(confs) / len(confs) if confs else 0.0)


class UnavailableOcr:
    available = False

    def recognize(self, image_png: bytes) -> OcrResult:  # pragma: no cover - guarded by `available`
        raise RuntimeError("OCR engine unavailable")


def get_ocr() -> OcrEngine:
    """FastAPI/worker dependency; override in tests."""
    return TesseractOcr()
