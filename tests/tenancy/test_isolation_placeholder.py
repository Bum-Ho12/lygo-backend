"""Cross-tenant isolation tests. See docs/03-multi-tenancy.md and docs/13-testing-strategy.md.

Set-up to implement: two firms (A, B), each with two clients; seed documents, chunks, flags,
chats. For each list/read/search/retrieval endpoint assert that A's callers see zero B rows,
and that client A1 sees zero rows of client A2.
"""
import pytest


@pytest.mark.skip(reason="implement with first tenant tables")
def test_firm_a_cannot_read_firm_b_documents() -> None: ...
