"""Practice-area pack registry. See docs/adr/0004-practice-area-packs.md.

A pack is a directory under `app/engine/packs/<id>/` with `pack.yaml` (metadata) and `rules.yaml`.
Packs that have not been reviewed by an advocate (`reviewed_by` empty) are available in local/staging
but are not loadable in production.
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.core.config import settings
from app.engine.rules.models import RulePack

PACKS_DIR = Path(__file__).parent


class PackMeta(BaseModel):
    id: str
    title: str
    practice_area: str
    version: str
    doc_types: list[str] = []
    reviewed_by: str = ""  # advocate who signed the pack off; empty = draft

    @property
    def reviewed(self) -> bool:
        return bool(self.reviewed_by.strip())


class PackNotAvailable(Exception): ...


def _read(path: Path) -> dict:  # type: ignore[type-arg]
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a mapping")
    return data


def list_packs(base: Path = PACKS_DIR) -> list[PackMeta]:
    return sorted(
        (PackMeta(**_read(p / "pack.yaml")) for p in base.iterdir() if (p / "pack.yaml").is_file()),
        key=lambda m: m.id,
    )


def load_rule_pack(pack_id: str, base: Path = PACKS_DIR, *, allow_unreviewed: bool | None = None) -> RulePack:
    d = base / pack_id
    if not (d / "pack.yaml").is_file():
        raise PackNotAvailable(f"unknown pack {pack_id!r}")
    meta = PackMeta(**_read(d / "pack.yaml"))
    if allow_unreviewed is None:
        allow_unreviewed = not settings.is_production
    if not meta.reviewed and not allow_unreviewed:
        raise PackNotAvailable(f"pack {pack_id!r} has not been reviewed by an advocate")
    raw = _read(d / "rules.yaml")
    raw.setdefault("pack", meta.id)
    raw.setdefault("version", meta.version)
    raw.setdefault("practice_area", meta.practice_area)
    raw.setdefault("doc_types", meta.doc_types)
    return RulePack(**raw)


@lru_cache
def cached_rule_pack(pack_id: str) -> RulePack:
    return load_rule_pack(pack_id)
