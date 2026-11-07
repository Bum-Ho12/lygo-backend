"""Rules Engine models. See docs/04-ai-engine.md and ADR 0003 / 0004."""
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_PATTERN_LEN = 300


class Severity(StrEnum):
    info = "info"
    caution = "caution"
    warning = "warning"
    critical = "critical"


class When(BaseModel):
    clause_matches: list[str] = Field(default_factory=list)  # any regex matches some section
    clause_absent: list[str] = Field(default_factory=list)  # no section text matches any regex
    section_absent: list[str] = Field(default_factory=list)  # no section heading matches any regex

    @field_validator("clause_matches", "clause_absent", "section_absent")
    @classmethod
    def valid_patterns(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if len(p) > MAX_PATTERN_LEN:
                raise ValueError(f"pattern too long ({len(p)} > {MAX_PATTERN_LEN})")
            try:
                re.compile(p, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid regex {p!r}: {exc}") from exc
        return patterns

    @model_validator(mode="after")
    def exactly_one_condition(self) -> "When":
        used = [n for n in ("clause_matches", "clause_absent", "section_absent") if getattr(self, n)]
        if len(used) != 1:
            raise ValueError("a rule's `when` must use exactly one condition")
        return self


class Rule(BaseModel):
    id: str
    severity: Severity
    title: str
    explain: str
    when: When
    extract: dict[str, str] = Field(default_factory=dict)  # name -> "$1" capture reference
    source_refs: list[str] = Field(default_factory=list)
    doc_types: list[str] = Field(default_factory=list)  # overrides pack-level when set


class RulePack(BaseModel):
    pack: str
    version: str
    practice_area: str = "general"
    doc_types: list[str] = Field(default_factory=list)
    rules: list[Rule]

    @model_validator(mode="after")
    def unique_ids(self) -> "RulePack":
        ids = [r.id for r in self.rules]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")
        return self


class Span(BaseModel):
    section_path: str
    section_number: str = ""
    page: int | None = None
    start: int | None = None  # offsets within the section's full text
    end: int | None = None


class Flag(BaseModel):
    rule_id: str
    pack: str
    pack_version: str
    severity: Severity
    title: str
    explanation: str
    span: Span | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
