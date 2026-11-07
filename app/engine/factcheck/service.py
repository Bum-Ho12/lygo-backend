"""Fact-check orchestration. Transactions never span an LLM call; retrieval runs under the caller's own tenant context."""
import uuid
from dataclasses import asdict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFound, ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.factcheck import FactCheck, FactCheckItem
from app.db.models.tenancy import Matter
from app.domain.audit import service as audit
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.providers.base import LlmError, LLMProvider, LlmRefused
from app.engine.factcheck import claims as claims_mod
from app.engine.factcheck.citations import CiteResult, check_references, extract_references
from app.engine.rag.embeddings.base import Embedder, EmbeddingError
from app.engine.rag.retrieval.hybrid import search

MAX_INPUT_CHARS = 20_000
EVIDENCE_PER_CLAIM = 3
MAX_EVIDENCE_SOURCES = 12
NOTICE = (
    "Draft aid for advocate review. 'Supported' means supported by the documents found, not that the claim is true. "
    "'Not found' means not present in the sources available to Lygo; it does not mean the authority or fact does not exist."
)


def _summary(cites: list[CiteResult], claims: list[claims_mod.ClaimResult], claim_check: str) -> dict[str, object]:
    count = lambda xs: {k: sum(1 for x in xs if x == k) for k in sorted(set(xs))}
    return {
        "citations": count([c.verdict for c in cites]), "claims": count([c.verdict for c in claims]), "claim_check": claim_check,
        "downgraded": sum(1 for c in claims if c.downgraded),
    }


async def run_fact_check(
    factory: async_sessionmaker[AsyncSession], llm: LLMProvider, embedder: Embedder, ctx: TenantContext, draft: str, *,
    matter_id: uuid.UUID | None, as_at: date,
) -> uuid.UUID:
    assert ctx.firm_id
    draft = draft.strip()
    if not draft or len(draft) > MAX_INPUT_CHARS:
        raise ProblemError(422, "invalid_text", "Invalid text", f"Provide between 1 and {MAX_INPUT_CHARS} characters to check.")
    async with tenant_session(factory, ctx) as db:
        if matter_id is not None and await db.get(Matter, matter_id) is None:
            raise NotFound("matter")
        cites = await check_references(db, extract_references(draft), as_at)

    results: list[claims_mod.ClaimResult] = []
    claim_check, model = "skipped: no matter selected (claims are only checked against one matter's own documents)", ""
    if matter_id is not None:
        try:
            extracted = await claims_mod.extract_claims(llm, draft)
            sources: list[Source] = []
            async with tenant_session(factory, ctx) as db:
                seen: set[str] = set()
                for ec in extracted:
                    for h in await search(db, embedder, ec.claim, scopes={"client"}, limit=EVIDENCE_PER_CLAIM, matter_id=matter_id):
                        cid = str(h.citation.chunk_id)
                        if cid not in seen and len(sources) < MAX_EVIDENCE_SOURCES:
                            seen.add(cid)
                            sources.append(Source(len(sources) + 1, cid, h.citation.title, h.citation.section_ref, h.text[:2400]))
            results = await claims_mod.judge_claims(llm, extracted, sources)
            claim_check, model = "done", llm.model
        except LlmRefused:
            claim_check = "unavailable: the language model declined this text"
        except (LlmError, EmbeddingError) as exc:
            raise ProblemError(503, "ai_unavailable", "AI unavailable", "The checker is temporarily unavailable. Citation checks were not saved; try again.") from exc
        except ValueError as exc:
            raise ProblemError(502, "ai_bad_output", "Checker output unusable", "The language model returned something we could not verify. Try again.") from exc

    fc_id = uuid.uuid4()
    async with tenant_session(factory, ctx) as db:
        db.add(FactCheck(id=fc_id, firm_id=ctx.firm_id, matter_id=matter_id, created_by=ctx.user_id, input_text=draft, as_at=as_at.isoformat(),
                         summary=_summary(cites, results, claim_check), model=model or None, prompt_version=claims_mod.PROMPT_VERSION if model else None))
        await db.flush()
        n = 0
        for cite in cites:
            db.add(FactCheckItem(firm_id=ctx.firm_id, fact_check_id=fc_id, ord=n, kind="citation", content=cite.reference.text, verdict=cite.verdict,
                                 detail={"reference_kind": cite.reference.kind, **cite.detail}))
            n += 1
        for r in results:
            db.add(FactCheckItem(firm_id=ctx.firm_id, fact_check_id=fc_id, ord=n, kind="claim", content=r.claim, verdict=r.verdict,
                                 detail={k: v for k, v in asdict(r).items() if k not in {"id", "claim", "verdict"} and v}))
            n += 1
        await audit.record(db, ctx, "factcheck.run", target=f"factcheck:{fc_id}", meta={"citations": len(cites), "claims": len(results), "matter": str(matter_id) if matter_id else None})
    return fc_id
