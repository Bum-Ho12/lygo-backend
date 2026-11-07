"""File-type detection from content (never from the client-supplied Content-Type or extension alone)."""
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_BY_EXT = {
    ".pdf": "pdf", ".docx": "docx", ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image",
    ".tiff": "image", ".txt": "text", ".csv": "text", ".md": "text",
}


class UnsupportedFile(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Sniffed:
    kind: str  # pdf | docx | image | text
    mime: str


def _detect(f: IO[bytes]) -> Sniffed:
    f.seek(0)
    head = f.read(16)
    if head.startswith(b"%PDF-"):
        return Sniffed("pdf", "application/pdf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("image", "image/png")
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed("image", "image/jpeg")
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return Sniffed("image", "image/tiff")
    if head.startswith(b"PK\x03\x04"):
        f.seek(0)
        try:
            with zipfile.ZipFile(f) as z:  # names only; nothing is extracted
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise UnsupportedFile("corrupt archive") from exc
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return Sniffed("docx", DOCX_MIME)
        raise UnsupportedFile("archives are not accepted (only .docx is supported)")
    f.seek(0)
    sample = f.read(8192)
    if sample and b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return Sniffed("text", "text/plain")
        except UnicodeDecodeError:
            pass
    raise UnsupportedFile("unrecognised or binary file type")


def sniff(f: IO[bytes], filename: str) -> Sniffed:
    """Detect the type and require it to agree with the filename extension."""
    s = _detect(f)
    expected = _BY_EXT.get(Path(filename).suffix.lower())
    if expected is None:
        raise UnsupportedFile(f"unsupported file extension {Path(filename).suffix!r}")
    if expected != s.kind:
        raise UnsupportedFile(f"file content ({s.kind}) does not match its extension")
    f.seek(0)
    return s
