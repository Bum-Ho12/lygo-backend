"""Comparison result models. See docs/adr/0005-comparison-engine.md."""
from enum import StrEnum

from pydantic import BaseModel, Field


class ChangeType(StrEnum):
    unchanged = "unchanged"
    modified = "modified"
    added = "added"
    removed = "removed"
    moved = "moved"  # same text, different number/heading position


class DiffOp(BaseModel):
    op: str  # replace | insert | delete
    left: str = ""
    right: str = ""


class FieldChange(BaseModel):
    kind: str  # money | duration | percent | date
    removed: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)


class SectionChange(BaseModel):
    change: ChangeType
    left_path: str | None = None
    right_path: str | None = None
    left_number: str = ""
    right_number: str = ""
    heading: str = ""
    similarity: float = 1.0
    diff: list[DiffOp] = Field(default_factory=list)
    field_changes: list[FieldChange] = Field(default_factory=list)
    # Deterministic materiality hints; an AI layer may later add an explanation (labelled AI-suggested).
    hints: list[str] = Field(default_factory=list)


class ComparisonSummary(BaseModel):
    unchanged: int = 0
    modified: int = 0
    added: int = 0
    removed: int = 0
    moved: int = 0
    flagged: int = 0  # changes carrying at least one hint


class ComparisonResult(BaseModel):
    items: list[SectionChange]
    summary: ComparisonSummary
