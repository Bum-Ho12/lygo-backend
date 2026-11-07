"""Document-level comparison of typed fields (e.g. two policies: sum insured, premium, notice window)."""
from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, Field


class FieldDiff(BaseModel):
    kind: str
    key: str
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)
    status: str  # changed | only_left | only_right


def diff_fields(left: Iterable[tuple[str, str, str]], right: Iterable[tuple[str, str, str]]) -> list[FieldDiff]:
    """Inputs are (kind, key, normalised value). Unchanged fields are omitted; deterministic ordering."""
    def group(rows: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str], list[str]]:
        g: dict[tuple[str, str], list[str]] = defaultdict(list)
        for kind, key, val in rows:
            g[(kind, key)].append(val)
        return {k: sorted(v) for k, v in g.items()}

    lg, rg = group(left), group(right)
    out: list[FieldDiff] = []
    for kind, key in sorted(set(lg) | set(rg)):
        lv, rv = lg.get((kind, key), []), rg.get((kind, key), [])
        if lv == rv:
            continue
        status = "changed" if lv and rv else ("only_left" if lv else "only_right")
        out.append(FieldDiff(kind=kind, key=key, left=lv, right=rv, status=status))
    return out
