import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TAXONOMY = Path(__file__).parent / "taxonomy.yaml"


@dataclass(frozen=True)
class Classification:
    doc_type: str | None
    practice_area: str | None
    confidence: float
    sensitivity: str  # normal | sensitive


@lru_cache
def _load() -> dict:  # type: ignore[type-arg]
    raw = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    types = {
        name: {
            "practice_area": spec["practice_area"],
            "patterns": [(re.compile(p["p"], re.IGNORECASE), int(p["w"])) for p in spec["patterns"]],
        }
        for name, spec in raw["types"].items()
    }
    return {
        "min_score": int(raw["min_score"]),
        "types": types,
        "sensitive": [re.compile(p, re.IGNORECASE) for p in raw["sensitive_markers"]],
    }


def classify(text: str) -> Classification:
    cfg = _load()
    sample = text[:200_000]
    scores: dict[str, int] = {}
    for name, spec in cfg["types"].items():
        s = 0
        for pat, w in spec["patterns"]:
            if pat.search(sample):
                s += w
        scores[name] = s
    # deterministic tie-break: higher score, then name
    best, score = max(scores.items(), key=lambda kv: (kv[1], kv[0]), default=(None, 0))
    sensitive = "sensitive" if any(p.search(sample) for p in cfg["sensitive"]) else "normal"
    if best is None or score < cfg["min_score"]:
        return Classification(None, None, 0.0, sensitive)
    total = sum(scores.values()) or 1
    return Classification(best, cfg["types"][best]["practice_area"], round(score / total, 3), sensitive)
