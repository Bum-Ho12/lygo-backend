"""Role -> permission matrix. See docs/03-multi-tenancy.md (RBAC).

Permissions are coarse capabilities checked at the router; finer checks (matter assignment) live in
domain services. Row-level isolation is enforced separately by the database.
"""
FIRM_READ = {
    "firm.read", "clients.read", "matters.read", "documents.read", "flags.read", "members.read", "tasks.read",
}
FIRM_WORK = FIRM_READ | {
    "clients.manage", "matters.manage", "documents.upload", "documents.tag", "flags.manage", "tasks.manage",
    "ai.use", "compare.run", "factcheck.run",
}
ADVOCATE = FIRM_WORK | {"ai.approve", "drafts.create", "reports.create", "research.run"}
ADMIN = ADVOCATE | {"firm.manage", "members.manage", "audit.read", "rules.manage", "usage.read"}
OWNER = ADMIN | {"storage.manage", "billing.manage", "data.export", "data.delete", "apikeys.manage"}

CLIENT_USER = {"firm.read", "clients.read", "matters.read", "documents.read", "documents.upload", "flags.read", "ai.use"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "firm_owner": frozenset(OWNER),
    "firm_admin": frozenset(ADMIN),
    "advocate": frozenset(ADVOCATE),
    "paralegal": frozenset(FIRM_WORK),
    "viewer": frozenset(FIRM_READ),
    "client_admin": frozenset(CLIENT_USER | {"clientusers.manage"}),
    "client_user": frozenset(CLIENT_USER),
    "platform_admin": frozenset({"platform.manage", "platform.read"}),
    "platform_support": frozenset({"platform.read"}),
}
CLIENT_ROLES = frozenset({"client_admin", "client_user"})
FIRM_ROLES = frozenset({"firm_owner", "firm_admin", "advocate", "paralegal", "viewer"})


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
