"""BYO storage: encrypted credentials, probes, routing of uploads, residency rules, crypto-shredding."""
import uuid

import boto3
import pytest
from moto import mock_aws

from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, new_firm, owner_sql
from tests.integration.test_documents_api import POLICY, upload_client

pytestmark = pytest.mark.asyncio
SECRET = "SuperSecretKeyValue1234567890"


def body(bucket: str, **kw):  # type: ignore[no-untyped-def]
    return {"name": "firm-s3", "region_country": "KE", "bucket": bucket, "region": "us-east-1", "prefix": "lygo",
            "access_key": "AKIATESTKEY", "secret_key": SECRET, **kw}


async def owner_and_client(api, pg):  # type: ignore[no-untyped-def]
    firm = new_firm(pg)
    _, owner, _ = await firm_member(api, pg, firm, "firm_owner")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=owner)).json()["id"]
    return firm, owner, cid


def make_bucket(name: str):  # type: ignore[no-untyped-def]
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=name)
    return s3


async def test_destination_lifecycle_and_upload_routing(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        s3 = make_bucket("firm-bucket")
        firm, owner, cid = await owner_and_client(api, database)
        base = f"/v1/firms/{firm}"
        r = await api.post(f"{base}/storage-destinations", json=body("firm-bucket"), headers=owner)
        assert r.status_code == 201, r.text
        dest = r.json()
        assert "secret_key" not in str(r.json()) and SECRET not in r.text and dest["is_primary"] is False
        # cannot become primary before a successful probe
        assert (await api.put(f"{base}/storage-policy/primary", json={"destination_id": dest["id"]}, headers=owner)).status_code == 409
        probed = (await api.post(f"{base}/storage-destinations/{dest['id']}/test", headers=owner)).json()
        assert probed["health"]["ok"] is True and probed["last_tested_at"]
        assert (await api.put(f"{base}/storage-policy/primary", json={"destination_id": dest["id"]}, headers=owner)).status_code == 200

        doc = (await upload_client(api, firm, cid, owner)).json()
        d = (await api.get(f"{base}/documents/{doc['id']}", headers=owner)).json()
        assert d["status"] == "processed" and d["doc_type"] == "insurance_policy"  # pipeline read the original back from the firm's bucket
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket="firm-bucket").get("Contents", [])]
        assert keys == [f"lygo/{firm}/client/{doc['id']}/original"]
        assert owner_sql(database, "SELECT storage_destination_id FROM documents WHERE id=%s", doc["id"])[0][0] == dest["id"]
        assert (await api.get(f"{base}/documents/{doc['id']}/content", headers=owner)).content == POLICY

        # switching back to Lygo-managed storage affects new uploads only; old documents stay readable
        assert (await api.put(f"{base}/storage-policy/primary", json={"destination_id": None}, headers=owner)).status_code == 200
        doc2 = (await upload_client(api, firm, cid, owner, name="other.txt", data=POLICY + b"\nmore text for a distinct hash")).json()
        assert owner_sql(database, "SELECT storage_destination_id FROM documents WHERE id=%s", doc2["id"])[0][0] == "test"
        assert (await api.get(f"{base}/documents/{doc['id']}/content", headers=owner)).content == POLICY
        assert (await api.delete(f"{base}/storage-destinations/{dest['id']}", headers=owner)).status_code == 409  # still holds a document


async def test_credentials_are_encrypted_never_returned_or_logged(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        make_bucket("b-cred")
        firm, owner, _ = await owner_and_client(api, database)
        base = f"/v1/firms/{firm}"
        dest = (await api.post(f"{base}/storage-destinations", json=body("b-cred"), headers=owner)).json()
        listing = await api.get(f"{base}/storage-destinations", headers=owner)
        assert SECRET not in listing.text and "AKIATESTKEY" not in listing.text and "credentials" not in listing.text
        blob = bytes(owner_sql(database, "SELECT credentials_enc FROM storage_destinations WHERE id=%s", dest["id"])[0][0])
        assert SECRET.encode() not in blob and b"AKIATESTKEY" not in blob and blob[:1] == b"\x01"
        cfg = str(owner_sql(database, "SELECT config FROM storage_destinations WHERE id=%s", dest["id"])[0][0])
        assert SECRET not in cfg
        audit = str(owner_sql(database, "SELECT meta, target FROM audit_events WHERE firm_id=%s", firm))
        assert SECRET not in audit and "AKIATESTKEY" not in audit
        assert (await api.post(f"{base}/storage-destinations", json=body("b-cred"), headers=owner)).status_code == 409  # unique name


async def test_cross_border_requires_a_recorded_basis(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        make_bucket("b-ng")
        firm, owner, _ = await owner_and_client(api, database)
        url = f"/v1/firms/{firm}/storage-destinations"
        r = await api.post(url, json=body("b-ng", name="lagos", region_country="NG"), headers=owner)
        assert r.status_code == 422 and r.json()["code"] == "transfer_basis_required"
        ok = await api.post(url, json=body("b-ng", name="lagos", region_country="ng", transfer_basis="Kenyan Standard Clauses signed 2026-10-01"), headers=owner)
        assert ok.status_code == 201 and ok.json()["region_country"] == "NG"
        assert (await api.post(url, json=body("b-ng", name="nairobi"), headers=owner)).status_code == 201  # Kenya needs no basis


async def test_probe_failure_blocks_primary(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        firm, owner, _ = await owner_and_client(api, database)
        base = f"/v1/firms/{firm}"
        dest = (await api.post(f"{base}/storage-destinations", json=body("bucket-that-does-not-exist"), headers=owner)).json()
        probed = (await api.post(f"{base}/storage-destinations/{dest['id']}/test", headers=owner)).json()
        assert probed["health"]["ok"] is False and probed["health"]["detail"]
        assert (await api.put(f"{base}/storage-policy/primary", json={"destination_id": dest["id"]}, headers=owner)).status_code == 409


async def test_only_owners_manage_storage_and_firms_are_isolated(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        make_bucket("b-iso")
        firm, owner, _ = await owner_and_client(api, database)
        other = new_firm(database)
        _, other_owner, _ = await firm_member(api, database, other, "firm_owner")
        _, admin, _ = await firm_member(api, database, firm, "firm_admin")
        _, adv, _ = await firm_member(api, database, firm, "advocate")
        dest = (await api.post(f"/v1/firms/{firm}/storage-destinations", json=body("b-iso"), headers=owner)).json()
        for h in (admin, adv):  # storage.manage is owner-only (docs/03)
            assert (await api.get(f"/v1/firms/{firm}/storage-destinations", headers=h)).status_code == 403
            assert (await api.post(f"/v1/firms/{firm}/storage-destinations", json=body("b-iso", name="x"), headers=h)).status_code == 403
        assert (await api.get(f"/v1/firms/{other}/storage-destinations", headers=other_owner)).json() == []
        assert (await api.post(f"/v1/firms/{other}/storage-destinations/{dest['id']}/test", headers=other_owner)).status_code == 404
        assert (await api.put(f"/v1/firms/{other}/storage-policy/primary", json={"destination_id": dest["id"]}, headers=other_owner)).status_code == 404
        assert (await api.delete(f"/v1/firms/{other}/storage-destinations/{dest['id']}", headers=other_owner)).status_code == 404


async def test_crypto_shredding_makes_byo_data_unreachable(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        make_bucket("b-shred")
        firm, owner, cid = await owner_and_client(api, database)
        base = f"/v1/firms/{firm}"
        dest = (await api.post(f"{base}/storage-destinations", json=body("b-shred"), headers=owner)).json()
        await api.post(f"{base}/storage-destinations/{dest['id']}/test", headers=owner)
        await api.put(f"{base}/storage-policy/primary", json={"destination_id": dest["id"]}, headers=owner)
        doc = (await upload_client(api, firm, cid, owner)).json()
        owner_sql(database, "UPDATE firms SET wrapped_dek = NULL WHERE id=%s", firm)  # offboarding: destroy the firm key
        r = await api.get(f"{base}/documents/{doc['id']}/content", headers=owner)
        assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"
        up = await upload_client(api, firm, cid, owner, name="n.txt", data=b"a brand new document after shredding")
        assert up.status_code == 503  # fails closed: nothing is written anywhere
        assert (await api.post(f"{base}/storage-destinations/{dest['id']}/test", headers=owner)).json()["health"]["ok"] is False
        assert uuid.UUID(dest["id"])
