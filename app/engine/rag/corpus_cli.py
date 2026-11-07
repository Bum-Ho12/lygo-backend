"""python -m app.engine.rag.corpus_cli corpus/manifests/<id>.yaml path/to/text.txt   (run as the platform; see corpus/README.md)"""
import argparse
import asyncio
from pathlib import Path

import yaml

from app.db.session import make_sessionmaker
from app.engine.rag.corpus import CorpusManifest, load_corpus_document
from app.engine.rag.embeddings.factory import get_embedder


async def main(manifest_path: Path, text_path: Path) -> None:
    manifest = CorpusManifest(**yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    embedder = get_embedder()
    if embedder is None:
        raise SystemExit("No embedding model configured (set LLM_BASE_URL and EMBEDDING_MODEL).")
    doc, n = await load_corpus_document(make_sessionmaker(), embedder, manifest, text_path.read_text(encoding="utf-8"))
    print(f"{doc.manifest_id} @ {doc.version_date}: {n} chunk(s) written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", type=Path)
    ap.add_argument("text", type=Path)
    a = ap.parse_args()
    asyncio.run(main(a.manifest, a.text))
