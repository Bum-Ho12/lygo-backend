"""Tolerant JSON extraction from model output (code fences, leading prose)."""
import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class NotJson(ValueError): ...


def extract_json(text: str) -> Any:
    candidates = [text.strip(), *[m.strip() for m in _FENCE.findall(text)]]
    for start, end in (("{", "}"), ("[", "]")):
        i, j = text.find(start), text.rfind(end)
        if 0 <= i < j:
            candidates.append(text[i : j + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            continue
    raise NotJson("no valid JSON found in the model output")
