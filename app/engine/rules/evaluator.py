"""Deterministic rule evaluation: same input + same pack => same flags."""
import re
from collections.abc import Iterable

from app.engine.rules.models import Flag, Rule, RulePack, Span
from app.ingestion.models import DocumentText


def _applies(rule: Rule, pack: RulePack, doc_type: str | None) -> bool:
    """A rule scoped to document types runs only on a document classified as one of them.

    An unclassified document (doc_type None) gets only unscoped rules: running contract or insurance
    checks on a payslip or medical report produces false "missing clause" flags.
    """
    types = rule.doc_types or pack.doc_types
    return not types or doc_type in types


def _format(explain: str, extracted: dict[str, str]) -> str:
    try:
        return explain.format(**extracted)
    except (KeyError, IndexError):
        return explain


def _extract(rule: Rule, m: re.Match[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, ref in rule.extract.items():
        g = re.fullmatch(r"\$(\d+)", ref.strip())
        if g and int(g.group(1)) <= (m.re.groups or 0):
            val = m.group(int(g.group(1)))
            if val is not None:
                out[name] = val
    return out


def _flag(rule: Rule, pack: RulePack, span: Span | None, extracted: dict[str, str]) -> Flag:
    return Flag(
        rule_id=rule.id,
        pack=pack.pack,
        pack_version=pack.version,
        severity=rule.severity,
        title=rule.title,
        explanation=" ".join(_format(rule.explain, extracted).split()),
        span=span,
        extracted=dict(extracted),
        source_refs=rule.source_refs,
    )


def evaluate_rule(rule: Rule, pack: RulePack, doc: DocumentText) -> list[Flag]:
    w = rule.when
    flags: list[Flag] = []
    if w.clause_matches:
        patterns = [re.compile(p, re.IGNORECASE) for p in w.clause_matches]
        for section in doc.sections:
            text = section.full_text
            for pat in patterns:
                m = pat.search(text)
                if m:
                    span = Span(
                        section_path=section.path, section_number=section.number,
                        page=section.page, start=m.start(), end=m.end(),
                    )
                    flags.append(_flag(rule, pack, span, _extract(rule, m)))
                    break  # one flag per section per rule
    elif w.clause_absent:
        patterns = [re.compile(p, re.IGNORECASE) for p in w.clause_absent]
        if not any(p.search(s.full_text) for s in doc.sections for p in patterns):
            flags.append(_flag(rule, pack, None, {}))
    elif w.section_absent:
        patterns = [re.compile(p, re.IGNORECASE) for p in w.section_absent]
        if not any(p.search(s.heading) for s in doc.sections for p in patterns):
            flags.append(_flag(rule, pack, None, {}))
    return flags


def evaluate_pack(pack: RulePack, doc: DocumentText, *, disabled_rules: Iterable[str] = ()) -> list[Flag]:
    skip = set(disabled_rules)
    flags: list[Flag] = []
    for rule in pack.rules:
        if rule.id in skip or not _applies(rule, pack, doc.doc_type):
            continue
        flags.extend(evaluate_rule(rule, pack, doc))
    return flags
