"""Phase E3: fact/cite check, chronology and case builder through the API."""
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.engine.ai.providers.base import LlmRefused, LlmUnavailable
from app.engine.ai.providers.fake import ScriptedLLM
from app.engine.rag.corpus import CorpusManifest, load_corpus_document
from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql, token_from
from tests.integration.test_documents_api import files

pytestmark = pytest.mark.asyncio
CORPUS = Path(__file__).parents[1] / "fixtures" / "corpus"

REPAIR = (
    b"REPAIR ESTIMATE\n\n1. Incident. The vehicle was damaged on 14 March 2026 in a collision at Kilimani. The insured reported it the next day.\n\n"
    b"2. Cost. The claimed amount is KES 450,000 including labour and parts.\n"
)
ABSTRACT = b"POLICE ABSTRACT\n\n1. Report. Reported at Kilimani police station on 15 March 2026 under OB number 44/2026.\n"
SIBLING = b"SIBLING FILE\n\n1. Facts. The warehouse of the sibling client burned down on 3 June 2025 causing KES 9,900,000 of loss.\n"


@pytest.fixture
async def acts(factory, embedder):  # type: ignore[no-untyped-def]
    m = {"id": "synthetic-insurance-test-act", "title": "Synthetic Insurance Test Act", "kind": "act", "licence": "fixture", "licence_confirmed": True}
    await load_corpus_document(factory, embedder, CorpusManifest(**m, version_date="2020-01-01"), (CORPUS / "synthetic_act_v1.txt").read_text())
    await load_corpus_document(factory, embedder, CorpusManifest(**m, version_date="2025-01-01"), (CORPUS / "synthetic_act_v2.txt").read_text())


async def setup(api, database: PgConfig):  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    _, para, _ = await firm_member(api, database, firm, "paralegal")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    sib = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Sibling"}, headers=adv)).json()["id"]
    matter = (await api.post(f"/v1/firms/{firm}/clients/{cid}/matters", json={"title": "Motor claim"}, headers=adv)).json()["id"]
    smatter = (await api.post(f"/v1/firms/{firm}/clients/{sib}/matters", json={"title": "Fire claim"}, headers=adv)).json()["id"]
    for name, data, cl, m in (("repair.txt", REPAIR, cid, matter), ("abstract.txt", ABSTRACT, cid, matter), ("fire.txt", SIBLING, sib, smatter)):
        r = await api.post(f"/v1/firms/{firm}/clients/{cl}/documents", headers=adv, **files(name, data, matter_id=m))
        assert r.status_code == 201, r.text
    return firm, adv, para, cid, matter


def judge_script(extract_claims: list[dict], judge):  # type: ignore[no-untyped-def]
    """First call extracts claims from the draft; second call judges them (via `judge(sources_by_marker)` -> results)."""
    def second(messages):  # type: ignore[no-untyped-def]
        user = messages[-1].content
        sources = {int(m.group(1)): m.group(2) for m in re.finditer(r"\[S(\d+)\][^\n]*?<<<(.*?)>>>", user, re.DOTALL)}
        return json.dumps({"results": judge(sources)})

    return ScriptedLLM(json.dumps({"claims": extract_claims}), second)


def quote_from(sources: dict[int, str], needle: str) -> tuple[int, str]:
    for k, text in sources.items():
        i = " ".join(text.split()).find(needle)
        if i >= 0:
            return k, needle
    raise AssertionError(f"{needle!r} not in any retrieved source: {list(sources.values())}")


# =============================================================================================================== cite-check
async def test_cite_check_verifies_real_sections_and_flags_what_it_cannot_find(api, database: PgConfig, acts) -> None:  # type: ignore[no-untyped-def]
    firm, adv, para, *_ = await setup(api, database)
    draft = ("Under section 5 of the Synthetic Insurance Test Act the claimant must notify within sixty days. "
             "Section 99 of the Synthetic Insurance Test Act allows a further year. See Kamau v Nation Media Ltd [2019] eKLR.")
    r = await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": draft}, headers=adv)
    assert r.status_code == 201, r.text
    out = r.json()
    cites = {i["text"]: i for i in out["items"] if i["kind"] == "citation"}
    s5 = next(v for k, v in cites.items() if "5" in k and "99" not in k)
    assert s5["verdict"] == "verified" and "sixty days" in s5["detail"]["snippet"] and s5["detail"]["version"] == "2025-01-01"
    s99 = next(v for k, v in cites.items() if "99" in k)
    assert s99["verdict"] == "not_found" and "sources available to Lygo" in s99["detail"]["note"]  # careful wording: absence is not fabrication
    assert {v["verdict"] for k, v in cites.items() if "Kamau" in k or "eKLR" in k} == {"not_found"}
    assert out["summary"]["citations"] == {"not_found": 3, "verified": 1} and out["summary"]["claim_check"].startswith("skipped")
    assert "not that the claim is true" in out["notice"]
    # persisted and retrievable; paralegals may run it too
    assert (await api.get(f"/v1/firms/{firm}/fact-checks/{out['id']}", headers=para)).json()["items"] == out["items"]
    assert [x["id"] for x in (await api.get(f"/v1/firms/{firm}/fact-checks", headers=adv)).json()["items"]] == [out["id"]]


async def test_cite_check_respects_the_as_at_date_and_act_ambiguity(api, database: PgConfig, acts, factory, embedder) -> None:  # type: ignore[no-untyped-def]
    firm, adv, *_ = await setup(api, database)
    old = (await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "section 5 of the Synthetic Insurance Test Act", "as_at": "2023-06-01"}, headers=adv)).json()
    assert "thirty days" in old["items"][0]["detail"]["snippet"] and old["items"][0]["detail"]["version"] == "2020-01-01"
    before = (await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "section 5 of the Synthetic Insurance Test Act", "as_at": "2010-01-01"}, headers=adv)).json()
    assert before["items"][0]["verdict"] == "not_found"  # the Act did not exist yet
    other = {"id": "second-test-act", "title": "Second Test Act", "kind": "act", "licence": "fixture", "licence_confirmed": True, "version_date": "2021-01-01"}
    await load_corpus_document(factory, embedder, CorpusManifest(**other), "PART I\n\n5. Other matters.\nA different rule about something else entirely.\n")
    bare = (await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "As provided by s.5, notice is required."}, headers=adv)).json()
    assert bare["items"][0]["verdict"] == "ambiguous" and len(bare["items"][0]["detail"]["candidates"]) >= 2  # never guess which Act


# ============================================================================================================== claim-check
async def test_claim_check_verdicts_survive_only_with_verified_evidence(api, database: PgConfig, acts, llm_box) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _, _cid, matter = await setup(api, database)
    draft = ("The vehicle was damaged on 14 March 2026. The claimed amount is KES 500,000. The driver was drunk. "
             "The incident happened on 20 March 2026. The insured owns two cars.")
    claims = [
        {"id": 1, "claim": "The vehicle was damaged on 14 March 2026", "quote": "The vehicle was damaged on 14 March 2026"},
        {"id": 2, "claim": "The claimed amount is KES 500,000", "quote": "The claimed amount is KES 500,000"},
        {"id": 3, "claim": "The driver was drunk", "quote": "The driver was drunk"},
        {"id": 4, "claim": "The incident happened on 20 March 2026", "quote": "The incident happened on 20 March 2026"},
        {"id": 5, "claim": "The insured owns two cars", "quote": "The insured owns two cars"},
    ]

    def judge(src):  # type: ignore[no-untyped-def]
        m1, q1 = quote_from(src, "The vehicle was damaged on 14 March 2026")
        m2, q2 = quote_from(src, "The claimed amount is KES 450,000")
        return [
            {"id": 1, "verdict": "supported", "evidence": [{"marker": m1, "quote": q1}]},  # correct
            {"id": 2, "verdict": "supported", "evidence": [{"marker": m2, "quote": q2}]},  # model wrongly vouches for 500,000
            {"id": 3, "verdict": "supported", "evidence": [{"marker": m1, "quote": "The driver was visibly intoxicated"}]},  # invented evidence
            {"id": 4, "verdict": "contradicted", "evidence": [{"marker": m1, "quote": q1}]},  # genuine conflict
            {"id": 5, "verdict": "not_found", "evidence": []},
        ]

    llm_box["llm"] = judge_script(claims, judge)
    r = await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": draft, "matter_id": matter}, headers=adv)
    assert r.status_code == 201, r.text
    got = {i["text"]: i for i in r.json()["items"] if i["kind"] == "claim"}
    assert got["The vehicle was damaged on 14 March 2026"]["verdict"] == "supported"
    assert got["The vehicle was damaged on 14 March 2026"]["detail"]["evidence"][0]["document"] == "repair"
    amount = got["The claimed amount is KES 500,000"]
    assert amount["verdict"] == "partially_supported" and "500000" in amount["detail"]["downgraded"]  # figures disagree => not 'supported'
    assert got["The driver was drunk"]["verdict"] == "not_found" and got["The driver was drunk"]["detail"]["downgraded"] == "unverifiable_evidence"
    assert got["The incident happened on 20 March 2026"]["verdict"] == "contradicted"
    assert got["The insured owns two cars"]["verdict"] == "not_found"
    s = r.json()["summary"]
    assert s["claim_check"] == "done" and s["downgraded"] == 2 and r.json()["model"] == "scripted-test"


async def test_claims_are_only_checked_against_the_chosen_matters_documents(api, database: PgConfig, acts, llm_box) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _, _cid, matter = await setup(api, database)
    seen_sources: list[str] = []

    def judge(src):  # type: ignore[no-untyped-def]
        seen_sources.extend(src.values())
        return [{"id": 1, "verdict": "not_found", "evidence": []}]

    llm_box["llm"] = judge_script([{"id": 1, "claim": "The warehouse burned down on 3 June 2025", "quote": "The warehouse burned down on 3 June 2025"}], judge)
    r = await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "The warehouse burned down on 3 June 2025.", "matter_id": matter}, headers=adv)
    assert r.status_code == 201
    assert all("warehouse" not in s and "9,900,000" not in s for s in seen_sources)  # another client's matter never reaches the checker
    assert r.json()["items"][-1]["verdict"] == "not_found"


async def test_claim_check_failure_modes(api, database: PgConfig, acts, llm_box) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _, _cid, matter = await setup(api, database)
    body = {"text": "Under section 5 of the Synthetic Insurance Test Act notice is needed. The vehicle was damaged on 14 March 2026.", "matter_id": matter}
    url = f"/v1/firms/{firm}/fact-checks"
    llm_box["llm"] = ScriptedLLM(LlmRefused("bio"))
    r = await api.post(url, json=body, headers=adv)
    assert r.status_code == 201 and r.json()["summary"]["claim_check"].startswith("unavailable") and r.json()["summary"]["citations"] == {"verified": 1}  # citation work is kept
    llm_box["llm"] = ScriptedLLM("no json", "still no json")
    assert (await api.post(url, json=body, headers=adv)).status_code == 502
    llm_box["llm"] = ScriptedLLM(LlmUnavailable("down"))
    assert (await api.post(url, json=body, headers=adv)).status_code == 503
    assert len((await api.get(url, headers=adv)).json()["items"]) == 1  # failed runs left nothing behind
    assert (await api.post(url, json={**body, "text": "x" * 20_001}, headers=adv)).status_code == 422
    assert (await api.post(url, json={**body, "matter_id": "00000000-0000-0000-0000-000000000001"}, headers=adv)).status_code == 404


async def test_fact_checks_are_firm_only_and_isolated(api, database: PgConfig, acts, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _para, cid, _matter = await setup(api, database)
    fc = (await api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "section 5 of the Synthetic Insurance Test Act"}, headers=adv)).json()
    _, portal, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=portal)
    for call in (api.get(f"/v1/firms/{firm}/fact-checks", headers=portal), api.get(f"/v1/firms/{firm}/fact-checks/{fc['id']}", headers=portal),
                 api.post(f"/v1/firms/{firm}/fact-checks", json={"text": "section 5"}, headers=portal)):
        assert (await call).status_code == 403
    other = new_firm(database)
    _, oadv, _ = await firm_member(api, database, other, "advocate")
    assert (await api.get(f"/v1/firms/{other}/fact-checks/{fc['id']}", headers=oadv)).status_code == 404
    assert (await api.get(f"/v1/firms/{other}/fact-checks", headers=oadv)).json()["items"] == []
    assert owner_sql(database, "SELECT count(*) FROM fact_check_items WHERE fact_check_id=%s", fc["id"])[0][0] >= 1


# ================================================================================================================ chronology
async def test_chronology_orders_every_dated_event_with_its_source(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, para, cid, matter = await setup(api, database)
    due = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    await api.post(f"/v1/firms/{firm}/matters/{matter}/deadlines", json={"title": "File claim form", "kind": "general", "due_at": "2026-04-20T09:00:00+00:00"}, headers=adv)
    await api.post(f"/v1/firms/{firm}/matters/{matter}/deadlines", json={"title": "Later deadline", "due_at": due}, headers=adv)
    await api.post(f"/v1/firms/{firm}/clients/{cid}/documents", headers=adv, **files("noise.txt", b"NOTE\n\n1. Dates. Leave on 31 February 2026 was never real, but the visit on 2nd April 2026 was.\n", matter_id=matter))
    ev = (await api.get(f"/v1/firms/{firm}/matters/{matter}/chronology", headers=para)).json()
    dates = [e["date"] for e in ev]
    assert dates == sorted(dates) and any(e["kind"] == "matter" and e["title"].startswith("Matter opened") for e in ev)  # opened today, so it sorts after the March documents
    by_date = {e["date"]: e for e in ev if e["kind"] == "document"}
    assert "2026-03-14" in by_date and "damaged" in by_date["2026-03-14"]["snippet"] and by_date["2026-03-14"]["title"] == "repair"
    assert by_date["2026-03-14"]["section_path"] == "1" and by_date["2026-03-14"]["document_id"]
    assert "2026-03-15" in by_date and "Kilimani police station" in by_date["2026-03-15"]["snippet"]  # a second document, merged in order
    assert "2026-04-02" in by_date and not any(e["date"].startswith("2026-02-31") for e in ev)  # ordinal parsed; impossible date skipped, no crash
    assert any(e["kind"] == "deadline" and e["title"] == "File claim form" and e["date"] == "2026-04-20" and e["status"] == "open" for e in ev)
    assert not any("warehouse" in e["snippet"] for e in ev)  # the sibling matter's file is not part of this chronology
    window = (await api.get(f"/v1/firms/{firm}/matters/{matter}/chronology", params={"after": "2026-03-15", "before": "2026-04-10"}, headers=adv)).json()
    assert [e["date"] for e in window if e["kind"] == "document"] == ["2026-03-15", "2026-04-02"]


async def test_chronology_is_firm_only_and_scoped(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _para, cid, matter = await setup(api, database)
    _, portal, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=portal)
    assert (await api.get(f"/v1/firms/{firm}/matters/{matter}/chronology", headers=portal)).status_code == 403
    other = new_firm(database)
    _, oadv, _ = await firm_member(api, database, other, "advocate")
    assert (await api.get(f"/v1/firms/{other}/matters/{matter}/chronology", headers=oadv)).status_code == 404
    assert (await api.get(f"/v1/firms/{firm}/matters/00000000-0000-0000-0000-000000000009/chronology", headers=adv)).status_code == 404


# ============================================================================================================= case builder
async def test_case_builder_produces_a_verified_advocate_only_outline(api, database: PgConfig, acts, llm_box, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, para, cid, matter = await setup(api, database)
    await api.post(f"/v1/firms/{firm}/matters/{matter}/facts", json={"key": "policy_status", "value": "Policy was active on the date of loss", "confirmed": True}, headers=adv)
    memo = (
        "## Issues\n- Whether the claim was notified in time?\n\n## Facts\n- The vehicle was damaged on 14 March 2026. [S1]\n\n"
        "## Law\n- A claimant must give notice within sixty days of the loss. [S2]\n- Section 99 allows a further year. [S2]\n\n"
        "## Evidence gaps\n- The sources do not include the policy wording. Obtain it before advising.\n"
    )
    llm_box["llm"] = ScriptedLLM(memo)
    r = await api.post(f"/v1/firms/{firm}/matters/{matter}/case-builder", json={"objective": "Establish that notice of the claim was given within sixty days of the loss of the vehicle"}, headers=adv)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ok" and out["approval_status"] == "not_required"
    assert "## Issues" in out["content"] and "## Law" in out["content"] and "damaged on 14 March 2026" in out["content"]  # structure and verified facts survive
    assert "Section 99" not in out["content"]  # an invented section is stripped from the outline
    sent = llm_box["llm"].calls[0]
    assert "build a case outline" in sent[0].content and "Policy was active on the date of loss" in sent[-1].content  # confirmed facts are used
    assert owner_sql(database, "SELECT prompt_version, task FROM messages WHERE id=%s", out["message_id"])[0] == ("case_build-v1", "case_build")
    chat = (await api.get(f"/v1/firms/{firm}/chats/{out['chat_id']}", headers=adv)).json()
    assert chat["audience"] == "advocate"
    # advocate-only
    assert (await api.post(f"/v1/firms/{firm}/matters/{matter}/case-builder", json={"objective": "anything at all"}, headers=para)).status_code == 403
    _, portal, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=portal)
    assert (await api.post(f"/v1/firms/{firm}/matters/{matter}/case-builder", json={"objective": "anything at all"}, headers=portal)).status_code == 403
    assert (await api.post(f"/v1/firms/{firm}/matters/00000000-0000-0000-0000-000000000009/case-builder", json={"objective": "anything at all"}, headers=adv)).status_code == 404
