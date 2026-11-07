"""Normalised document text shared by ingestion, rules and comparison."""
from pydantic import BaseModel, Field


class Section(BaseModel):
    """One clause/section. `number` is the legal numbering ('7', '7.1'), `path` the full tree path."""

    number: str = ""
    heading: str = ""
    text: str = ""
    page: int | None = None
    path: str = ""  # e.g. "7/7.1"; stable within a document

    @property
    def full_text(self) -> str:
        return f"{self.heading}\n{self.text}".strip()


class DocumentText(BaseModel):
    sections: list[Section] = Field(default_factory=list)
    doc_type: str | None = None

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.full_text for s in self.sections)
