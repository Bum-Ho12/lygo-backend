from pathlib import Path

import pytest

from app.engine.packs.registry import PackNotAvailable, list_packs, load_rule_pack
from app.engine.rules.evaluator import evaluate_pack
from app.engine.rules.models import Rule, RulePack, Severity, When
from app.ingestion.structure import parse_structure

FIX = Path(__file__).parents[2] / "fixtures"


def _doc(name: str, doc_type: str):
    return parse_structure((FIX / name).read_text(), doc_type)


def test_insurance_pack_flags_policy() -> None:
    pack = load_rule_pack("insurance")
    flags = evaluate_pack(pack, _doc("insurance_policy.txt", "insurance_policy"))
    by_id = {f.rule_id: f for f in flags}
    assert "ins.exclusion.pre_existing" in by_id
    claim = by_id["ins.claim.notice_window"]
    assert claim.severity == Severity.critical and claim.extracted == {"deadline_days": "14"}
    assert "14 days" in claim.explanation and claim.span and claim.span.section_number == "2"
    assert "ins.missing.policy_schedule" not in by_id  # a "Policy Schedule" heading exists


def test_core_contract_pack_v1_vs_v2() -> None:
    pack = load_rule_pack("core_contract")
    v1 = {f.rule_id for f in evaluate_pack(pack, _doc("contract_v1.txt", "contract"))}
    v2 = {f.rule_id for f in evaluate_pack(pack, _doc("contract_v2.txt", "contract"))}
    assert "core.governing_law.missing" not in v1 and "core.governing_law.missing" in v2
    assert "core.auto_renewal" in v2 and "core.auto_renewal" not in v1
    assert "core.termination.missing" not in v1 | v2
    notice = [f for f in evaluate_pack(pack, _doc("contract_v2.txt", "contract")) if f.rule_id == "core.notice_period"]
    assert notice and notice[0].extracted["notice_days"] == "90"


def test_evaluation_is_deterministic() -> None:
    pack, doc = load_rule_pack("core_contract"), _doc("contract_v2.txt", "contract")
    assert evaluate_pack(pack, doc) == evaluate_pack(pack, doc)


def test_disabled_rule_and_doc_type_filter() -> None:
    pack, doc = load_rule_pack("core_contract"), _doc("contract_v2.txt", "contract")
    assert not [f for f in evaluate_pack(pack, doc, disabled_rules=["core.auto_renewal"]) if f.rule_id == "core.auto_renewal"]
    assert evaluate_pack(pack, _doc("contract_v2.txt", "insurance_policy")) == []  # pack does not apply


def test_rule_validation() -> None:
    with pytest.raises(ValueError):
        When(clause_matches=["(unclosed"])
    with pytest.raises(ValueError):
        When(clause_matches=["a"], clause_absent=["b"])  # exactly one condition
    with pytest.raises(ValueError):
        When(clause_matches=["x" * 301])
    r = Rule(id="a", severity="info", title="t", explain="e", when=When(clause_matches=["x"]))
    with pytest.raises(ValueError):
        RulePack(pack="p", version="1", rules=[r, r])


def test_registry_lists_packs_and_blocks_unreviewed_in_production() -> None:
    assert {m.id for m in list_packs()} >= {"core_contract", "insurance"}
    with pytest.raises(PackNotAvailable):
        load_rule_pack("insurance", allow_unreviewed=False)
    with pytest.raises(PackNotAvailable):
        load_rule_pack("does_not_exist")
